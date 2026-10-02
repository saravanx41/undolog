"""undolog_core.ledger — the Ledger and the wrap() side-effect interceptor.

Usage::

    ledger = Ledger(engine)                # engine: SQLAlchemy engine
    bound = ledger.for_thread(thread_id)   # bind to an agent thread

    @bound.wrap                            # bare decorator
    def f(...): ...

    @bound.wrap(adapter=MyAdapter(), tool_name="stripe.create_charge")
    def charge(...): ...                   # decorator factory

    wrapped = bound.wrap(f, adapter=...)   # direct call

An adapter exposes ``capture_before() -> dict`` and
``capture_after(result) -> dict``; the returned dicts are stored as
before_jsonb / after_jsonb proof.

Guarantees:

* Append-only: the migration revokes UPDATE/DELETE/TRUNCATE on the app
  role, so rows can never be altered or removed through the app's DB role.
* A row with status="applied" is written only AFTER fn completes and (with
  an adapter) after capture_after() succeeds. A crash or exception mid-fn
  therefore never leaves a row claiming "applied". If capture_after fails
  after a successful fn, the row is written status="applied", log_only=True,
  after_jsonb=NULL — an applied row never asserts proof it cannot show.
* Adapter capture failures demote the call to log-only mode: the row is
  written with log_only=True, proof fields NULL, and the class escalated
  to "unknown" (the least-trusted class).
* seq is gapless per thread, allocated from an atomic counter row
  (INSERT ... ON CONFLICT DO UPDATE ... RETURNING) in the same transaction
  as the ledger insert.
* ``compensate`` is accepted and reserved for the Week 2 compensation
  executor; wrap() records the call but does not run compensations yet.

Rollback planning (Week 2 starts here): ``Ledger.plan_rollback(entries)``
returns plan items in reverse order. An entry whose class is "irreversible"
can NEVER yield a compensation step — it yields a "refuse" item carrying a
blast-radius report reason instead.

Freeze (2.1): ``Ledger.freeze(thread_id)`` takes a SESSION-scoped advisory
lock (stable hash of thread_id) plus a ``thread_freezes`` flag row that the
interceptor checks at wrap-entry; wrapped calls on a frozen thread raise
FrozenError before fn runs and before any row is written. The lock makes
the freeze exclusive cross-connection/cross-process; ``unfreeze()`` clears
both. A second concurrent freeze gets a clear FrozenError (try-lock), and
re-freezing from the holder is a no-op.

Rollback engine (2.2): ``Ledger.rollback(thread_id, to_seq, executors=...,
dry_run=...)`` freezes, walks applied entries newest-first, and undoes them
via per-tool executors (``restore_before`` / ``compensate(entry, key)``).
Irreversible/unknown entries (and entries with no executor or no
before-proof) go to the report's blast_radius and are never executed.
Failures retry with exponential backoff, then halt with complete=False
after marking the entry failed; the freeze is always released (finally).
Compensation keys are deterministic (``comp-{entry.id}``) and compensated
entries are skipped on re-run, so replays never double-compensate.
``dry_run=True`` previews the report without freezing, executing, or
mutating anything.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Protocol, Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .exceptions import FrozenError
from .models import EntryClass, EntryStatus, LedgerEntry, ThreadCounter, ThreadFreeze
from .registry import Registry, load_registry

log = logging.getLogger(__name__)


def _freeze_key(thread_id: str) -> int:
    """Stable 64-bit advisory-lock key for a thread_id (fits Postgres bigint)."""
    digest = hashlib.sha256(f"undolog-freeze:{thread_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF

log = logging.getLogger(__name__)


def next_seq(session: Session, thread_id: str) -> int:
    """Atomically increment and return the gapless per-thread sequence.

    Implemented as an upsert on a per-thread counter row: the conflicting
    update takes a row lock, so concurrent callers serialize on the counter
    and every committed transaction observes a unique, gap-free seq.
    """
    table = ThreadCounter.__table__
    stmt = (
        pg_insert(table)
        .values(thread_id=thread_id, seq=1)
        .on_conflict_do_update(
            index_elements=["thread_id"],
            set_={"seq": table.c.seq + 1},
        )
        .returning(table.c.seq)
    )
    return session.execute(stmt).scalar_one()


def _hash_args(args: tuple, kwargs: dict) -> str:
    material = repr((args, sorted(kwargs.items())))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanItem:
    """One entry of a rollback plan (Week 2 executes these)."""

    entry_id: Optional[UUID]
    tool_name: str
    entry_class: str
    action: str  # "compensate" | "refuse"
    reason: Optional[str] = None
    compensation: Optional[dict] = None


class RollbackExecutor(Protocol):
    """Executor protocol for undoing one ledger entry.

    Core cannot know Stripe/CRM/etc. specifics, so rollback() is handed a
    mapping of tool_name -> executor. Implementations perform the actual
    restore/compensation against the external system.
    """

    def restore_before(self, entry: LedgerEntry) -> None:
        """Restore the before-state for a reversible entry."""
        ...

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        """Run the registered compensation for a compensatable entry.

        ``idempotency_key`` is deterministic (``comp-{entry.id}``) so a
        retried or replayed rollback never double-compensates.
        """
        ...


@dataclass(frozen=True)
class RollbackItem:
    """One entry in a rollback report."""

    entry_id: Optional[UUID]
    seq: int
    tool_name: str
    entry_class: str
    action: str  # "restore" | "compensate" | "refuse"
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class RollbackReport:
    """Plain report of a rollback (dry_run previews populate the same lists)."""

    thread_id: str
    to_seq: int
    restored: list[RollbackItem] = field(default_factory=list)
    compensated: list[RollbackItem] = field(default_factory=list)
    blast_radius: list[RollbackItem] = field(default_factory=list)
    failed: list[RollbackItem] = field(default_factory=list)
    complete: bool = True


class Ledger:
    """Entry point: owns the engine and the Tool Safety Registry."""

    def __init__(self, engine, registry: Optional[Registry] = None):
        self.engine = engine
        self.registry = registry if registry is not None else load_registry()
        # thread_id -> connection holding the freeze advisory lock.
        self._freeze_conns: dict[str, object] = {}

    def for_thread(self, thread_id: str) -> "ThreadLedger":
        """Bind the ledger to one agent thread; returns the wrap() context."""
        return ThreadLedger(self, thread_id)

    def plan_rollback(self, entries: Sequence[LedgerEntry]) -> list[PlanItem]:
        """Plan a rollback for the given entries, newest first.

        Irreversible entries can never yield a compensation step: they
        produce a "refuse" item whose reason is a blast-radius report
        entry. Reversible/compensatable entries produce "compensate"
        items carrying the registry recipe.
        """
        plan: list[PlanItem] = []
        for entry in reversed(list(entries)):
            entry_class = EntryClass(entry.class_)
            if entry_class is EntryClass.IRREVERSIBLE:
                plan.append(
                    PlanItem(
                        entry_id=entry.id,
                        tool_name=entry.tool_name,
                        entry_class=entry_class.value,
                        action="refuse",
                        reason=(
                            f"irreversible tool {entry.tool_name}: no compensation "
                            "exists; rollback refused. Blast-radius report entry: "
                            "the external effect already happened and must be "
                            "mitigated out of band."
                        ),
                        compensation=None,
                    )
                )
                continue
            spec = self.registry.lookup(entry.tool_name)
            plan.append(
                PlanItem(
                    entry_id=entry.id,
                    tool_name=entry.tool_name,
                    entry_class=entry_class.value,
                    action="compensate",
                    compensation=dict(spec.compensation) if spec else None,
                )
            )
        return plan

    # -- freeze ---------------------------------------------------------

    def freeze(self, thread_id: str) -> None:
        """Freeze a thread: no wrapped side effects until unfreeze().

        Two mechanisms, deliberately layered:

        * A SESSION-scoped Postgres advisory lock (``pg_try_advisory_lock``)
          keyed by a stable hash of thread_id. Session scope (not transaction
          scope) is chosen because the freeze must survive across idle time
          until unfreeze() — a xact lock would release at the first commit.
          Try-lock semantics: if another session already froze the thread,
          raise FrozenError("already frozen") instead of blocking.
        * A flag row in ``thread_freezes`` — the cheap SELECT that the
          interceptor checks at wrap-entry. The advisory lock is what makes
          the freeze exclusive and serializes concurrent freeze()/rollback()
          attempts; the row is overwritten on each successful freeze, which
          also reaps stale rows left by a crashed freezer (its lock died
          with its connection).

        Re-freezing a thread this Ledger already froze is a clean no-op.
        """
        if thread_id in self._freeze_conns:
            return  # this Ledger already holds the freeze
        key = _freeze_key(thread_id)
        conn = self.engine.connect()
        acquired = conn.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar()
        if not acquired:
            conn.close()
            raise FrozenError(
                f"cannot freeze thread {thread_id!r}: already frozen by "
                "another session"
            )
        try:
            # Core DML on the checked-out connection: a Session would join
            # the lock SELECT's open transaction via savepoint and never
            # actually commit the flag row.
            conn.execute(
                sa.delete(ThreadFreeze).where(ThreadFreeze.thread_id == thread_id)
            )
            conn.execute(
                sa.insert(ThreadFreeze).values(thread_id=thread_id)
            )
            conn.commit()
        except Exception:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            conn.close()
            raise
        self._freeze_conns[thread_id] = conn

    def unfreeze(self, thread_id: str) -> None:
        """Release a freeze held by this Ledger; no-op if we do not hold it.

        Never clears another holder's flag row: if this Ledger does not own
        the freeze, unfreeze() does nothing.
        """
        key = _freeze_key(thread_id)
        conn = self._freeze_conns.pop(thread_id, None)
        if conn is None:
            return
        try:
            conn.execute(
                sa.delete(ThreadFreeze).where(ThreadFreeze.thread_id == thread_id)
            )
            conn.commit()
            conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        finally:
            conn.close()

    def _raise_if_frozen(self, thread_id: str) -> None:
        """Cheap wrap-entry check: refuse if the thread is frozen."""
        with Session(self.engine) as session:
            if session.get(ThreadFreeze, thread_id) is not None:
                raise FrozenError(
                    f"thread {thread_id!r} is frozen; refusing to execute "
                    "a side effect"
                )

    # -- rollback engine -------------------------------------------------

    def rollback(
        self,
        thread_id: str,
        to_seq: int,
        executors: Optional[Mapping[str, RollbackExecutor]] = None,
        *,
        dry_run: bool = False,
        max_attempts: int = 3,
        base_backoff: float = 0.05,
    ) -> RollbackReport:
        """Roll back a thread to seq ``to_seq`` (entries with seq > to_seq).

        Executing mode freezes the thread first and always unfreezes in a
        finally — a freeze never leaks. Walks applied entries newest-first;
        per entry:

        * reversible    -> executor.restore_before(entry)
        * compensatable -> executor.compensate(entry, "comp-{entry.id}")
        * irreversible / unknown / no executor / no before-proof ->
          reported in ``blast_radius`` and NEVER passed to an executor.

        Executor failures are retried (max_attempts, exponential backoff);
        on exhaustion the entry is marked status="failed", the engine halts
        with report.complete=False (exact partial state, never claims
        success), and the freeze is released. Already-compensated entries
        are skipped, so re-running a completed rollback is a no-op.

        ``dry_run=True`` previews the same report without freezing, calling
        executors, or changing any status.
        """
        executors = executors or {}
        report = RollbackReport(thread_id=thread_id, to_seq=to_seq)
        if not dry_run:
            self.freeze(thread_id)
        try:
            with Session(self.engine) as session:
                entries = session.exec(
                    select(LedgerEntry)
                    .where(LedgerEntry.thread_id == thread_id)
                    .where(LedgerEntry.seq > to_seq)
                    .where(LedgerEntry.status == EntryStatus.APPLIED)
                    .order_by(LedgerEntry.seq.desc())
                ).all()
                for entry in entries:
                    item = self._rollback_entry(
                        session, entry, executors, report,
                        dry_run=dry_run, max_attempts=max_attempts,
                        base_backoff=base_backoff,
                    )
                    if item == "halt":
                        break
            return report
        finally:
            if not dry_run:
                self.unfreeze(thread_id)

    def _rollback_entry(self, session, entry, executors, report, *,
                        dry_run, max_attempts, base_backoff) -> Optional[str]:
        """Handle one entry; returns "halt" to stop the walk, else None."""
        entry_class = EntryClass(entry.class_)

        if entry_class in (EntryClass.IRREVERSIBLE, EntryClass.UNKNOWN):
            report.blast_radius.append(RollbackItem(
                entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
                entry_class=entry_class.value, action="refuse",
                reason=(
                    "irreversible/unknown class: no compensation exists; "
                    "blast-radius report entry, external effect must be "
                    "mitigated out of band"
                ),
            ))
            return None

        executor = executors.get(entry.tool_name)
        if executor is None:
            report.blast_radius.append(RollbackItem(
                entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
                entry_class=entry_class.value, action="refuse",
                reason=f"no rollback executor registered for {entry.tool_name}",
            ))
            return None

        if entry_class is EntryClass.REVERSIBLE and entry.before_jsonb is None:
            report.blast_radius.append(RollbackItem(
                entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
                entry_class=entry_class.value, action="refuse",
                reason="reversible entry without before-proof (log-only) "
                       "cannot be restored",
            ))
            return None

        action = "restore" if entry_class is EntryClass.REVERSIBLE else "compensate"
        comp_key = f"comp-{entry.id}"

        if dry_run:
            (report.restored if entry_class is EntryClass.REVERSIBLE
             else report.compensated).append(RollbackItem(
                entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
                entry_class=entry_class.value, action=action,
                idempotency_key=comp_key,
            ))
            return None

        if not self._execute_entry(
            executor, entry, entry_class, comp_key, max_attempts, base_backoff
        ):
            entry.status = EntryStatus.FAILED
            session.add(entry)
            session.commit()
            report.failed.append(RollbackItem(
                entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
                entry_class=entry_class.value, action=action,
                reason=f"executor exhausted {max_attempts} attempts",
                idempotency_key=comp_key,
            ))
            report.complete = False
            log.error(
                "rollback of %r halted at seq=%s (%r): executor failed "
                "%s attempts", entry.thread_id, entry.seq, entry.tool_name,
                max_attempts,
            )
            return "halt"

        entry.status = EntryStatus.COMPENSATED
        session.add(entry)
        session.commit()
        (report.restored if entry_class is EntryClass.REVERSIBLE
         else report.compensated).append(RollbackItem(
            entry_id=entry.id, seq=entry.seq, tool_name=entry.tool_name,
            entry_class=entry_class.value, action=action,
            idempotency_key=comp_key if entry_class is EntryClass.COMPENSATABLE else None,
        ))
        return None

    @staticmethod
    def _execute_entry(executor, entry, entry_class, comp_key,
                       max_attempts, base_backoff) -> bool:
        """Run the executor for one entry with retries; True on success."""
        for attempt in range(1, max_attempts + 1):
            try:
                if entry_class is EntryClass.REVERSIBLE:
                    executor.restore_before(entry)
                else:
                    executor.compensate(entry, comp_key)
                return True
            except Exception:
                if attempt >= max_attempts:
                    return False
                time.sleep(base_backoff * (2 ** (attempt - 1)))
        return False  # pragma: no cover


class ThreadLedger:
    """Ledger bound to one thread_id; exposes wrap()."""

    def __init__(self, ledger: Ledger, thread_id: str):
        self._ledger = ledger
        self.thread_id = thread_id

    def wrap(
        self,
        fn: Optional[Callable] = None,
        *,
        compensate: Optional[Callable] = None,
        adapter: Optional[object] = None,
        tool_name: Optional[str] = None,
    ):
        """Decorator / decorator-factory / direct wrapper.

        Usable as ``@bound.wrap``, ``@bound.wrap(adapter=..., tool_name=...)``
        or ``bound.wrap(fn, ...)``.
        """
        def decorator(f: Callable) -> Callable:
            resolved_tool = tool_name or f.__qualname__

            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                return self._run(
                    f, args, kwargs,
                    adapter=adapter, tool_name=resolved_tool, compensate=compensate,
                )

            return wrapper

        return decorator(fn) if fn is not None else decorator

    def _run(self, fn, args, kwargs, adapter, tool_name, compensate) -> object:
        self._ledger._raise_if_frozen(self.thread_id)  # before fn, before any write
        args_hash = _hash_args(args, kwargs)
        idempotency_key = hashlib.sha256(
            f"{self.thread_id}|{tool_name}|{args_hash}".encode("utf-8")
        ).hexdigest()

        spec = self._ledger.registry.lookup(tool_name)
        if spec is None:
            log.warning(
                "tool %r is not in the safety registry; "
                "recording class 'unknown' (log-only trust)",
                tool_name,
            )
            entry_class = EntryClass.UNKNOWN
        else:
            entry_class = EntryClass(spec.entry_class)

        before = after = None
        log_only = False

        if adapter is not None:
            try:
                before = adapter.capture_before()
            except Exception:
                log_only = True
                entry_class = EntryClass.UNKNOWN  # escalate: capture failed
                log.warning(
                    "capture_before failed for %r; proceeding log-only",
                    tool_name, exc_info=True,
                )

        try:
            result = fn(*args, **kwargs)
        except Exception:
            # fn failed: record the attempt, never an "applied" row.
            self._write(
                idempotency_key, tool_name, args_hash, entry_class,
                EntryStatus.FAILED, before_jsonb=before, after_jsonb=None,
                log_only=log_only,
            )
            raise

        if adapter is not None:
            try:
                after = adapter.capture_after(result)
            except Exception:
                log_only = True
                after = None
                entry_class = EntryClass.UNKNOWN  # escalate: capture failed
                log.warning(
                    "capture_after failed for %r; recording without proof "
                    "(log-only)", tool_name, exc_info=True,
                )

        # Only reached after fn succeeded (and, with an adapter, after
        # capture_after): this is the sole path that writes status="applied".
        self._write(
            idempotency_key, tool_name, args_hash, entry_class,
            EntryStatus.APPLIED, before_jsonb=before, after_jsonb=after,
            log_only=log_only,
        )
        return result

    def _write(self, idempotency_key, tool_name, args_hash, entry_class,
               status, before_jsonb, after_jsonb, log_only) -> None:
        with Session(self._ledger.engine) as session:
            seq = next_seq(session, self.thread_id)
            session.add(
                LedgerEntry(
                    thread_id=self.thread_id,
                    seq=seq,
                    tool_name=tool_name,
                    args_hash=args_hash,
                    idempotency_key=idempotency_key,
                    before_jsonb=before_jsonb,
                    after_jsonb=after_jsonb,
                    class_=entry_class,
                    status=status,
                    log_only=log_only,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # Duplicate idempotency key: an earlier attempt of the same
                # call already recorded a row. The ledger stays append-only.
                session.rollback()
                log.info(
                    "idempotency key collision for %r; skipping duplicate row",
                    tool_name,
                )
