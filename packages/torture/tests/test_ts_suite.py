"""Task 2.4 — torture suite TS-01..TS-05, TS-07, TS-08 (see SPEC.md).

The pass gate: each TS-01..TS-07 test is parameterized over N consecutive
seeds (UNDOLOG_TORTURE_RUNS, default 3; the orchestrator sets 100) with
seed-derived random fault injection enabled. Zero lost ledger rows, zero
double-applied compensations.
"""
import os
import threading
import time

import pytest
from sqlmodel import Session

from undolog_core import FrozenError, Ledger
from undolog_core.models import EntryClass, EntryStatus
from undolog_torture import (
    DEFAULT_SEED,
    build_worlds,
    canonical_state,
    hash_world,
    run_scenario,
)
from undolog_torture.agent import StripeChargeAdapter
from undolog_torture.executors import (
    CrmExecutor,
    StripeExecutor,
    build_executors,
)
from undolog_torture.faults import FaultConfig
from undolog_torture.registry_ext import load_torture_registry
from undolog_torture.verify import verify_rollback_to_33

RUNS = int(os.environ.get("UNDOLOG_TORTURE_RUNS", "3"))
SEEDS = [DEFAULT_SEED + i for i in range(RUNS)]


def _ledger(engine):
    return Ledger(engine, registry=load_torture_registry())


def _rollback(engine, result, faults=None, **kwargs):
    ledger = _ledger(engine)
    executors = build_executors(result.world, faults=faults)
    report = ledger.rollback(result.rows[-1].thread_id, 33, executors,
                             **kwargs)
    return ledger, executors, report


# --------------------------------------------------------------------------
# TS-01 — happy-path rollback
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts01_happy_path_rollback(engine, seed):
    thread_id = f"ts01-{seed}"
    result = run_scenario(engine, seed=seed, thread_id=thread_id)
    ledger, executors, report = _rollback(engine, result)

    problems = verify_rollback_to_33(result, report, executors)
    assert problems == [], f"TS-01 seed={seed}: " + "; ".join(problems)

    # Report shape per spec.
    assert report.complete
    assert len(report.compensated) == 3
    assert len(report.restored) == 9            # corruption CRM batches
    assert len(report.blast_radius) == 5        # 5 gmail.send batch entries
    # Rollback is idempotent: re-running changes nothing and refunds nothing.
    world_before = hash_world(canonical_state(result.world))
    report2 = ledger.rollback(thread_id, 33, build_executors(result.world))
    assert report2.complete and not report2.compensated and not report2.restored
    assert hash_world(canonical_state(result.world)) == world_before
    assert len(result.world.stripe.refunds) == 3


# --------------------------------------------------------------------------
# TS-02 — mid-rollback compensation failure (refund 500s 2x then succeeds)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts02_refund_fails_twice_then_succeeds(engine, seed):
    thread_id = f"ts02-{seed}"
    result = run_scenario(engine, seed=seed, thread_id=thread_id)
    faults = FaultConfig(comp_fail_n=2)
    ledger, executors, report = _rollback(engine, result, faults=faults,
                                          max_attempts=3, base_backoff=0.01)

    assert report.complete, report.failed
    # Exactly 3 refunds eventually issued, one per corruption charge.
    assert len(result.world.stripe.refunds) == 3
    # Retry audit trail on the executor shim: the newest charge entry was
    # attempted 3 times (fail, fail, ok); every entry compensated exactly once.
    audit = executors["stripe.create_charge"].audit
    assert len(audit) == 3
    per_entry = list(audit.values())
    assert sorted(len(a) for a in per_entry) == [1, 1, 3]
    three = max(per_entry, key=len)
    assert [a["ok"] for a in three] == [False, False, True]
    assert all("500" in a["error"] for a in three if not a["ok"])
    # Ledger rows all conserved; no row lost or duplicated.
    assert len(result.rows) == 50
    assert [r.seq for r in result.rows] == list(range(1, 51))
    # The retried entry ended compensated.
    from sqlmodel import select
    from undolog_core.models import LedgerEntry
    with Session(engine) as session:
        rows = session.exec(select(LedgerEntry)
                            .where(LedgerEntry.thread_id == thread_id)
                            .order_by(LedgerEntry.seq)).all()
    assert all(r.status is not EntryStatus.FAILED for r in rows)


@pytest.mark.parametrize("seed", SEEDS[:1])
def test_ts02_exhaustion_halts_unfreezes_and_reports_partial(engine, seed):
    thread_id = f"ts02x-{seed}"
    result = run_scenario(engine, seed=seed, thread_id=thread_id)
    faults = FaultConfig(comp_fail_n=10 ** 9)  # never succeeds
    ledger, executors, report = _rollback(engine, result, faults=faults,
                                          max_attempts=3, base_backoff=0.01)

    # Never claims success; exact partial state reported.
    assert not report.complete
    assert len(report.failed) == 1
    failed_item = report.failed[0]
    # Halt: entries older than the failed one were not processed.
    older_restored = [i.seq for i in report.restored if i.seq < failed_item.seq]
    assert older_restored == []
    # No refund for the failed entry; partial state is exact:
    # refunded charges == entries compensated before the halt.
    compensated_charges = [i for i in report.compensated
                           if i.tool_name == "stripe.create_charge"]
    assert len(result.world.stripe.refunds) == len(compensated_charges)
    assert failed_item.seq not in [r.seq for r in compensated_charges]
    # Freeze released despite the halt: new side effects flow again.
    post = run_scenario(engine, seed=seed + 500, thread_id=thread_id + "-post")
    assert len(post.rows) == 50
    # Resume after fixing the fault: the automatic rollback completes the
    # remaining entries; the exhausted one was marked failed and is excluded
    # from automatic re-runs (core semantics), so ops compensates it manually
    # with its deterministic key — idempotently.
    ledger2 = _ledger(engine)
    executors2 = build_executors(result.world)
    report2 = ledger2.rollback(thread_id, 33, executors2, base_backoff=0.01)
    assert report2.complete
    assert len(result.world.stripe.refunds) == 2   # everything except seq 45
    failed_entry = next(r for r in result.rows if r.seq == failed_item.seq)
    executors2["stripe.create_charge"].compensate(
        failed_entry, f"comp-{failed_entry.id}")
    assert len(result.world.stripe.refunds) == 3
    # The deterministic key dedups: replaying the manual compensation
    # returns the original refund instead of double-refunding.
    replay = result.world.stripe.create_refund(
        failed_entry.after_jsonb["charge"]["id"],
        idempotency_key=f"comp-{failed_entry.id}")
    assert replay["id"] == result.world.stripe.refunds[-1]["id"]
    assert len(result.world.stripe.refunds) == 3


# --------------------------------------------------------------------------
# TS-03 — racing agent: side effects rejected while rollback holds the freeze
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts03_racing_agent_rejected_during_rollback(engine, seed):
    thread_id = f"ts03-{seed}"
    result = run_scenario(engine, seed=seed, thread_id=thread_id)
    ledger = _ledger(engine)

    class SlowStripe(StripeExecutor):
        def compensate(self, entry, idempotency_key):
            time.sleep(0.3)  # widen the freeze window
            super().compensate(entry, idempotency_key)

    executors = build_executors(result.world)
    executors["stripe.create_charge"] = SlowStripe(result.world.stripe)
    report_box = {}

    def do_rollback():
        report_box["report"] = ledger.rollback(
            thread_id, 33, executors, base_backoff=0.01)

    t = threading.Thread(target=do_rollback)
    t.start()

    # Wait until the freeze flag is visible, then race it.
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        with Session(engine) as session:
            from undolog_core.models import ThreadFreeze
            if session.get(ThreadFreeze, thread_id) is not None:
                break
        time.sleep(0.005)
    else:
        pytest.fail("rollback never froze the thread")

    effects = {"n": 0}
    tl = ledger.for_thread(thread_id)

    @tl.wrap(adapter=None, tool_name="crm.update_record")
    def racer_update(record_id, data):
        effects["n"] += 1
        return result.world.crm.update(record_id, data)

    rejected = 0
    for i in range(50):
        try:
            racer_update(f"rec_{i:03d}", {"race": i})
        except FrozenError:
            rejected += 1

    t.join(timeout=30)
    assert not t.is_alive()
    assert rejected == 50, f"{rejected}/50 calls rejected"
    assert effects["n"] == 0, "side effect executed on a frozen thread"
    # No ledger rows from the rejected calls: count is still exactly 50.
    with Session(engine) as session:
        from sqlmodel import select
        from undolog_core.models import LedgerEntry
        rows = session.exec(select(LedgerEntry)
                            .where(LedgerEntry.thread_id == thread_id)).all()
    assert len(rows) == 50
    assert report_box["report"].complete


# --------------------------------------------------------------------------
# TS-04 — duplicate side effects: same idempotency key replayed 5x
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts04_replay_same_call_five_times(engine, seed):
    thread_id = f"ts04-{seed}"
    world = build_worlds(seed)
    ledger = _ledger(engine)
    tl = ledger.for_thread(thread_id)

    @tl.wrap(adapter=StripeChargeAdapter(world), tool_name="stripe.create_charge")
    def charge(amount, card_token, req_id, idempotency_key=None):
        return world.stripe.create_charge(amount, card_token, idempotency_key)

    args = dict(amount=4200, card_token="card_dup", req_id="only-call",
                idempotency_key=f"dup-key-{seed}")
    for _ in range(5):
        charge(**args)

    assert len(world.stripe.charges) == 1, "exactly 1 external effect"
    with Session(engine) as session:
        from sqlmodel import select
        from undolog_core.models import LedgerEntry
        rows = session.exec(select(LedgerEntry)
                            .where(LedgerEntry.thread_id == thread_id)).all()
    assert len(rows) == 1, "exactly 1 ledger row"

    executors = {"stripe.create_charge": StripeExecutor(world.stripe)}
    report = ledger.rollback(thread_id, 0, executors)
    assert report.complete
    assert len(world.stripe.refunds) == 1, "compensation refunds exactly once"
    assert world.stripe.refunds[0]["charge_id"] == world.stripe.charges[0]["id"]
    # Idempotent rollback replay refunds nothing more.
    ledger.rollback(thread_id, 0, executors)
    assert len(world.stripe.refunds) == 1


# --------------------------------------------------------------------------
# TS-05 — blast-radius control: 400 sends, freeze at send #12
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts05_freeze_at_twelve_prevents_the_rest(engine, seed):
    thread_id = f"ts05-{seed}"
    world = build_worlds(seed)
    ledger = _ledger(engine)
    tl = ledger.for_thread(thread_id)

    class ProofAdapter:
        def capture_before(self):
            return {"sent_count": len(world.gmail.sent)}

        def capture_after(self, result):
            return {"sent_count": len(world.gmail.sent), "sent": 1,
                    "messages": [dict(result)]}

    @tl.wrap(adapter=ProofAdapter(), tool_name="gmail.send")
    def send_one(email, req_id):
        return world.gmail.send(**email)

    t0 = time.perf_counter()
    sent = prevented = 0
    for i in range(400):
        try:
            send_one({"to": f"victim{i}@x.example", "subject": f"spam {i}",
                      "body": f"payload {i}",
                      "idempotency_key": f"spam-{i}"}, req_id=f"s{i:03d}")
            sent += 1
            if sent == 12:
                ledger.freeze(thread_id)          # watchdog freeze
                t_freeze = time.perf_counter()
        except FrozenError:
            prevented += 1
    ledger.unfreeze(thread_id)

    assert sent == 12 and prevented == 388
    assert t_freeze - t0 < 2.0, f"time-to-freeze {t_freeze - t0:.2f}s"
    # Report enumerates all 12 with recipient, content hash, and why.
    report = ledger.rollback(thread_id, 0, {}, dry_run=True)
    emails = []
    by_id = {}
    with Session(engine) as session:
        from sqlmodel import select
        from undolog_core.models import LedgerEntry
        for r in session.exec(select(LedgerEntry)
                              .where(LedgerEntry.thread_id == thread_id)):
            by_id[r.id] = r
    for item in report.blast_radius:
        entry = by_id[item.entry_id]
        after = entry.after_jsonb or {}
        msgs = after.get("messages") or ([after["message"]] if after.get("message") else [])
        for m in msgs:
            emails.append({"to": m["to"], "body_sha256": m["body_sha256"],
                           "why": item.reason})
    assert len(emails) == 12
    assert all(e["to"] and e["body_sha256"] and e["why"] for e in emails)
    assert len({e["body_sha256"] for e in emails}) == 12


# --------------------------------------------------------------------------
# TS-07 — concurrent threads on shared CRM; roll back thread B only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts07_rollback_one_thread_only(engine, seed):
    world = build_worlds(seed)
    ledger = _ledger(engine)
    all_ids = sorted(world.crm.records)
    subsets = {"A": all_ids[0:15], "B": all_ids[15:30], "C": all_ids[30:45]}
    barrier = threading.Barrier(3)
    errors = []

    def worker(tid, ids):
        tl = ledger.for_thread(tid)
        for i, rid in enumerate(ids):
            class A:  # per-call adapter: capture only the record being written
                def capture_before(self, _rid=rid):
                    return {"records": {_rid: dict(world.crm.records[_rid])}}

                def capture_after(self, result):
                    return {"result": result}

            wrapped = tl.wrap(
                lambda record_id, data, req_id: world.crm.update(record_id, data),
                adapter=A(), tool_name="crm.update_record")
            try:
                barrier.wait(timeout=10)   # interleave the three threads
                wrapped(rid, {"owner": tid, "round": i}, req_id=f"{tid}-{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"ts07-{t}-{seed}", subsets[t]))
               for t in "ABC"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []

    from sqlmodel import select
    from undolog_core.models import LedgerEntry
    with Session(engine) as session:
        for tid in "ABC":
            rows = session.exec(
                select(LedgerEntry)
                .where(LedgerEntry.thread_id == f"ts07-{tid}-{seed}")
                .order_by(LedgerEntry.seq)
            ).all()
            assert [r.seq for r in rows] == list(range(1, 16))
            assert all(r.status is EntryStatus.APPLIED for r in rows)

    pre = {rid: dict(world.crm.records[rid]) for rid in all_ids}
    ledger_b = _ledger(engine)
    executors = {"crm.update_record": CrmExecutor(world.crm)}
    report = ledger_b.rollback(f"ts07-B-{seed}", 0, executors)
    assert report.complete

    seeded = build_worlds(seed).crm.records
    for rid in subsets["B"]:
        assert world.crm.records[rid] == seeded[rid], \
            f"B record {rid} not rolled back"
    for tid in ("A", "C"):
        for rid in subsets[tid]:
            assert world.crm.records[rid] == pre[rid], \
                f"{tid} record {rid} touched by B's rollback"
    with Session(engine) as session:
        b_rows = session.exec(select(LedgerEntry).where(
            LedgerEntry.thread_id == f"ts07-B-{seed}")).all()
        assert all(r.status is EntryStatus.COMPENSATED for r in b_rows)


# --------------------------------------------------------------------------
# TS-08 — capture_before timeout on 20% of calls
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_ts08_capture_timeouts_log_only_and_never_restored_uncaptured(engine, seed):
    import random as _random
    from undolog_torture.faults import FlakyCaptureAdapter
    from undolog_torture.ledger_ext import EscalatingLedger

    thread_id = f"ts08-{seed}"
    world = build_worlds(seed)
    ledger = EscalatingLedger(engine, registry=load_torture_registry())
    tl = ledger.for_thread(thread_id)
    rng = _random.Random(seed)
    ids = sorted(world.crm.records)[:30]
    shim_rng = _random.Random(f"ts08-{seed}")

    for i, rid in enumerate(ids):
        inner = _CountingAdapter(world, rid)
        shim = FlakyCaptureAdapter(inner, pct=0.2, rng=shim_rng)
        wrapped = tl.wrap(
            lambda record_id, data, req_id: world.crm.update(record_id, data),
            adapter=shim, tool_name="crm.update_record")
        wrapped(rid, {"ts08": i, "k": rng.randrange(1 << 20)},
                req_id=f"ts08-{i}")

    from sqlmodel import select
    from undolog_core.models import LedgerEntry
    with Session(engine) as session:
        rows = session.exec(select(LedgerEntry)
                            .where(LedgerEntry.thread_id == thread_id)
                            .order_by(LedgerEntry.seq)).all()
    assert len(rows) == 30
    log_only = [r for r in rows if r.log_only]
    assert len(log_only) > 0, "20% hook should fire within 30 calls"
    assert all(r.class_ is EntryClass.UNKNOWN for r in log_only)
    assert all(r.before_jsonb is None for r in log_only)
    assert len(log_only) < 30

    # Rollback refuses every uncaptured row; captured rows restore cleanly.
    executors = {"crm.update_record": CrmExecutor(world.crm)}
    report = ledger.rollback(thread_id, 0, executors)
    assert report.complete
    refused = {i.seq for i in report.blast_radius}
    assert refused == {r.seq for r in log_only}
    assert all(i.reason for i in report.blast_radius)
    # Never "restores" a value it didn't capture: refused records keep the
    # value the agent wrote; restored records are back to seed values.
    seeded = build_worlds(seed).crm.records
    restored = {i.seq for i in report.restored}
    for r in rows:
        if r.seq in restored:
            rid = ids[r.seq - 1]
            assert world.crm.records[rid] == seeded[rid]
        else:
            pass  # asserted concretely below
    # Concretely: uncaptured rows' written values survive.
    for r in log_only:
        rid = ids[r.seq - 1]
        assert world.crm.records[rid].get("ts08") == r.seq - 1


class _CountingAdapter:
    def __init__(self, world, rid):
        self._world = world
        self._rid = rid

    def capture_before(self):
        return {"records": {self._rid: dict(self._world.crm.records[self._rid])}}

    def capture_after(self, result):
        return {"result": result}
