"""Task 2.2: rollback engine — dry run, execution, retries, idempotency."""
import pytest
from sqlmodel import Session, select

from undolog_core import Ledger
from undolog_core.ledger import next_seq
from undolog_core.models import LedgerEntry


@pytest.fixture(scope="module")
def ledger(engine):
    return Ledger(engine)

# tool, class pairs cycling through the seed registry classes
TOOL_PLAN = [
    ("notion.append_block", "reversible"),
    ("notion.update_page", "reversible"),
    ("stripe.create_charge", "compensatable"),
    ("slack.post_message", "compensatable"),
    ("gmail.send", "irreversible"),
    ("http.webhook_post", "irreversible"),
]
UNKNOWN_EVERY = 11  # every 11th entry is an unregistered tool -> class unknown
N_ENTRIES = 50
TO_SEQ = 33


class FakeExecutor:
    """Records restore_before / compensate calls; optionally always fails."""

    def __init__(self, fail=False):
        self.fail = fail
        self.restore_attempts = 0
        self.compensate_attempts = 0
        self.restored = []            # list[entry_id]
        self.compensated_calls = []   # list[(entry_id, idempotency_key)]

    def restore_before(self, entry):
        self.restore_attempts += 1
        if self.fail:
            raise RuntimeError("restore failed")
        self.restored.append(entry.id)

    def compensate(self, entry, idempotency_key):
        self.compensate_attempts += 1
        if self.fail:
            raise RuntimeError("compensate failed")
        self.compensated_calls.append((entry.id, idempotency_key))


def seed_thread(engine, thread_id, n=N_ENTRIES):
    """Insert n synthetic applied entries; returns {seq: (tool, class)}."""
    plan = {}
    with Session(engine) as session:
        for i in range(1, n + 1):
            tool, cls = TOOL_PLAN[(i - 1) % len(TOOL_PLAN)]
            if i % UNKNOWN_EVERY == 0:
                tool, cls = "mystery.tool", "unknown"
            plan[i] = (tool, cls)
            session.add(
                LedgerEntry(
                    thread_id=thread_id,
                    seq=next_seq(session, thread_id),
                    tool_name=tool,
                    args_hash=f"h{i}",
                    idempotency_key=f"seed-{thread_id}-{i}",
                    before_jsonb={"state": f"before-{i}"} if cls == "reversible" else None,
                    after_jsonb={"state": f"after-{i}"} if cls == "reversible" else None,
                    class_=cls,
                    status="applied",
                )
            )
        session.commit()
    return plan


def _executors(fail_compensatable=False):
    ex = {}
    for tool, cls in TOOL_PLAN:
        if cls == "irreversible":
            continue
        ex[tool] = FakeExecutor(fail=(fail_compensatable and cls == "compensatable"))
    return ex


def _rows_by_seq(engine, thread_id):
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()
    return {r.seq: r for r in rows}


def test_dry_run_previews_without_touching(engine, ledger):
    plan = seed_thread(engine, "t-rb-dry")
    executors = _executors()

    report = ledger.rollback("t-rb-dry", to_seq=TO_SEQ, executors=executors, dry_run=True)

    assert report.thread_id == "t-rb-dry"
    assert report.to_seq == TO_SEQ
    assert report.complete
    scoped = range(TO_SEQ + 1, N_ENTRIES + 1)
    assert len(report.restored) + len(report.compensated) + len(report.blast_radius) == len(list(scoped))
    # every scoped entry appears in exactly one list, with its real class
    for item in report.restored:
        assert plan[item.seq][1] == "reversible"
    for item in report.compensated:
        assert plan[item.seq][1] == "compensatable"
        assert item.idempotency_key == f"comp-{item.entry_id}"
    for item in report.blast_radius:
        assert plan[item.seq][1] in ("irreversible", "unknown")

    # nothing was executed or mutated
    for ex in executors.values():
        assert ex.restored == []
        assert ex.compensated_calls == []
    rows = _rows_by_seq(engine, "t-rb-dry")
    assert all(r.status == "applied" for r in rows.values())


def test_execute_rollback_restores_compensates_reports_blast_radius(engine, ledger):
    plan = seed_thread(engine, "t-rb-exec")
    executors = _executors()

    report = ledger.rollback("t-rb-exec", to_seq=TO_SEQ, executors=executors)

    assert report.complete
    scoped = set(range(TO_SEQ + 1, N_ENTRIES + 1))
    assert len(report.restored) + len(report.compensated) + len(report.blast_radius) == len(scoped)

    # reversible entries restored via restore_before with their before_jsonb
    for item in report.restored:
        tool, cls = plan[item.seq]
        assert cls == "reversible"
        assert item.action == "restore"
        assert executors[tool].restored.count(item.entry_id) == 1
        row = _rows_by_seq(engine, "t-rb-exec")[item.seq]
        assert row.before_jsonb == {"state": f"before-{item.seq}"}

    # compensatable entries compensated exactly once, deterministic key
    for item in report.compensated:
        tool, cls = plan[item.seq]
        assert cls == "compensatable"
        assert item.action == "compensate"
        assert item.idempotency_key == f"comp-{item.entry_id}"
        assert executors[tool].compensated_calls.count((item.entry_id, f"comp-{item.entry_id}")) == 1

    # irreversible/unknown in blast radius, never passed to any executor
    for item in report.blast_radius:
        assert plan[item.seq][1] in ("irreversible", "unknown")
        assert item.action == "refuse"
    all_calls = [c for ex in executors.values() for c in (ex.restored + [x[0] for x in ex.compensated_calls])]
    blast_ids = {i.entry_id for i in report.blast_radius}
    assert not (set(all_calls) & blast_ids)


def test_statuses_after_rollback_and_counts(engine, ledger):
    seed_thread(engine, "t-rb-status")
    executors = _executors()

    report = ledger.rollback("t-rb-status", to_seq=TO_SEQ, executors=executors)

    rows = _rows_by_seq(engine, "t-rb-status")
    for seq in range(1, TO_SEQ + 1):
        assert rows[seq].status == "applied", f"entry <= to_seq must be untouched (seq={seq})"
    for seq in range(TO_SEQ + 1, N_ENTRIES + 1):
        if rows[seq].class_ in ("reversible", "compensatable"):
            expected = "compensated"
        else:  # refused (irreversible/unknown): reported, never touched
            expected = "applied"
        assert rows[seq].status == expected, f"seq={seq}"
    assert len(report.restored) == sum(1 for s in range(TO_SEQ + 1, N_ENTRIES + 1) if rows[s].class_ == "reversible")
    assert len(report.compensated) == sum(1 for s in range(TO_SEQ + 1, N_ENTRIES + 1) if rows[s].class_ == "compensatable")
    assert len(report.blast_radius) == sum(
        1 for s in range(TO_SEQ + 1, N_ENTRIES + 1) if rows[s].class_ in ("irreversible", "unknown")
    )
    assert report.failed == []


def test_executor_failure_retries_then_halts_and_unfreezes(engine, ledger):
    seed_thread(engine, "t-rb-fail")
    executors = _executors(fail_compensatable=True)

    report = ledger.rollback(
        "t-rb-fail", to_seq=TO_SEQ, executors=executors, max_attempts=3, base_backoff=0.01
    )

    assert not report.complete
    assert len(report.failed) == 1
    failed_item = report.failed[0]
    # retried per policy: exactly 3 attempts on the failing entry
    total_compensate_attempts = sum(
        ex.compensate_attempts for ex in executors.values() if ex.fail
    )
    assert total_compensate_attempts == 3
    rows = _rows_by_seq(engine, "t-rb-fail")
    failed_row = rows[failed_item.seq]
    assert failed_row.status == "failed"
    assert failed_row.status != "compensated"
    # halted: entries below the failure were not processed
    assert any(rows[s].status == "applied" for s in range(TO_SEQ + 1, failed_item.seq))
    # unfrozen again: wrapped calls work
    box = []
    bound = ledger.for_thread("t-rb-fail")

    @bound.wrap
    def act():
        box.append(True)

    act()
    assert box == [True]


def test_rerun_after_completed_rollback_is_noop(engine, ledger):
    seed_thread(engine, "t-rb-rerun")
    executors = _executors()

    first = ledger.rollback("t-rb-rerun", to_seq=TO_SEQ, executors=executors)
    assert first.complete
    calls_after_first = {
        tool: (len(ex.restored), len(ex.compensated_calls)) for tool, ex in executors.items()
    }

    second = ledger.rollback("t-rb-rerun", to_seq=TO_SEQ, executors=executors)

    assert second.complete
    assert second.restored == []
    assert second.compensated == []
    assert second.failed == []
    # deterministic compensation keys: no double compensation
    for tool, ex in executors.items():
        assert (len(ex.restored), len(ex.compensated_calls)) == calls_after_first[tool]
