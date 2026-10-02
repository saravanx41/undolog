"""TS-06 — process death mid-tool and mid-compensation (subprocess harness).

A child process (undolog_torture.sigchild) runs real wrapped calls against
the same Postgres schema, reaches a deterministic kill point announced on
stdout, and is SIGKILLed there. The parent then inspects the ledger and
exercises the resume/repair path.
"""
import os
import signal

import pytest
from sqlmodel import Session, select

from undolog_core.models import EntryStatus, LedgerEntry
from undolog_torture import DEFAULT_SEED
from undolog_torture.executors import StripeExecutor, build_executors
from undolog_torture.resume import human_report, rebuild_stripe_world
from undolog_torture.sigkill_harness import spawn_and_kill

RUNS = int(os.environ.get("UNDOLOG_TORTURE_RUNS", "3"))
SEEDS = [DEFAULT_SEED + i for i in range(RUNS)]


def _rows(engine, thread_id):
    with Session(engine) as session:
        return list(session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all())


@pytest.mark.parametrize("seed", SEEDS)
def test_ts06_sigkill_mid_tool(engine, schema, seed):
    thread_id = f"ts06-tool-{seed}"
    rc, lines = spawn_and_kill(schema, "mid_tool", thread_id)
    assert rc == -signal.SIGKILL
    assert any("IN_TOOL" in line for line in lines)

    # On restart: the two completed calls are there; the killed call left
    # no row at all (wrap writes only after fn returns) and no phantom
    # "applied" row claims it happened.
    rows = _rows(engine, thread_id)
    assert [r.seq for r in rows] == [1, 2]
    assert all(r.status is EntryStatus.APPLIED for r in rows)

    # The repair path reports exactly what a human must do.
    notes = human_report(engine, thread_id, expected_calls=3)
    assert any("no ledger row" in n for n in notes), notes

    # Resume completes: rebuild the world from ledger proof, re-issue the
    # interrupted call (safe: it died before its side effect), roll back.
    world = rebuild_stripe_world(engine, thread_id)
    assert len(world.stripe.charges) == 2
    from undolog_torture.agent import StripeChargeAdapter
    from undolog_torture.registry_ext import load_torture_registry
    from undolog_core import Ledger
    ledger = Ledger(engine, registry=load_torture_registry())
    tl = ledger.for_thread(thread_id)

    @tl.wrap(adapter=StripeChargeAdapter(world), tool_name="stripe.create_charge")
    def charge(amount, card_token, req_id, idempotency_key=None):
        return world.stripe.create_charge(amount, card_token, idempotency_key)

    charge(3000, "c3", req_id="r3", idempotency_key="k3")
    rows = _rows(engine, thread_id)
    assert [r.seq for r in rows] == [1, 2, 3]   # gapless: no seq was lost
    report = ledger.rollback(
        thread_id, 0, {"stripe.create_charge": StripeExecutor(world.stripe)})
    assert report.complete
    assert len(world.stripe.refunds) == 3


@pytest.mark.parametrize("seed", SEEDS)
def test_ts06_sigkill_mid_compensation(engine, schema, seed):
    thread_id = f"ts06-comp-{seed}"
    rc, lines = spawn_and_kill(schema, "mid_compensation", thread_id)
    assert rc == -signal.SIGKILL
    assert any("IN_COMPENSATION" in line for line in lines)

    # On restart: all three charges still "applied", none "compensated" —
    # the interrupted compensation never completed and no phantom status
    # claims it did. The freeze died with the child (advisory lock), so the
    # thread is writable again.
    rows = _rows(engine, thread_id)
    assert [r.seq for r in rows] == [1, 2, 3]
    assert all(r.status is EntryStatus.APPLIED for r in rows)

    # Repair path: rebuild world state from ledger proof; the report lists
    # the uncompensated charges for a human.
    world = rebuild_stripe_world(engine, thread_id)
    assert len(world.stripe.charges) == 3
    assert all(not c["refunded"] for c in world.stripe.charges)
    notes = human_report(engine, thread_id, to_seq=0)
    assert len(notes) == 3 and all("still applied" in n for n in notes)

    # Resume completes: rollback refunds each charge exactly once.
    from undolog_torture.registry_ext import load_torture_registry
    from undolog_core import Ledger
    ledger = Ledger(engine, registry=load_torture_registry())
    executors = {"stripe.create_charge": StripeExecutor(world.stripe)}
    report = ledger.rollback(thread_id, 0, executors)
    assert report.complete
    assert len(world.stripe.refunds) == 3
    assert all(c["refunded"] for c in world.stripe.charges)
    # Idempotent: a second rollback refunds nothing more.
    ledger.rollback(thread_id, 0, executors)
    assert len(world.stripe.refunds) == 3
