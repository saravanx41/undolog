"""Task 3.4 — PocketOS demo scenario (the launch video).

Acceptance:
1. `python -m undolog_torture.demo` runs end-to-end (exit 0, report
   sections printed).
2. Deterministic: two runs -> identical world-state hash and ledger rows.
3. Post-rollback: CRM equals the pre-corruption snapshot, the rogue charge
   is refunded, rogue emails remain sent but are listed as irreversible.
4. Freeze demonstrably stopped the rogue phase: exactly 12 applied actions;
   post-#12 entries are marked compensated by the rollback; the prevented
   attempts left no rows and no side effects.
"""
import os
import subprocess
import sys
import uuid

import pytest
from sqlmodel import Session, select

from undolog_core.models import EntryStatus, LedgerEntry
from undolog_torture.dbutil import DEFAULT_DATABASE_URL, drop_schema
from undolog_torture.demo import DEFAULT_DEMO_SEED, run_demo
from undolog_torture.snapshot import hash_world

ROGUE_THREAD = "pocketos-demo"


def _sig(rows):
    return [(r.seq, r.tool_name, r.class_.value, r.idempotency_key,
             r.status.value) for r in rows]


def test_demo_acceptance(engine):
    res = run_demo(engine=engine, seed=DEFAULT_DEMO_SEED, fast=True,
                   narrate=None)

    # Exactly 18 ledger rows: 12 benign + 6 rogue that landed pre-freeze.
    rows = res.rows
    assert [r.seq for r in rows] == list(range(1, 19))
    # Precise post-rollback status model:
    #   seq 1..12  applied      — benign work, untouched by the rollback
    #   seq 13,14,16,17 compensated — CRM overwrites, the fraudulent charge,
    #                                 the rogue note: undone by the rollback
    #   seq 15,18  applied      — rogue emails: irreversible, refused into
    #                             the blast radius (effect remains, listed)
    assert all(r.status is EntryStatus.APPLIED for r in rows[:12])
    by_seq = {r.seq: r.status for r in rows}
    assert by_seq[13] is EntryStatus.COMPENSATED
    assert by_seq[14] is EntryStatus.COMPENSATED
    assert by_seq[16] is EntryStatus.COMPENSATED
    assert by_seq[17] is EntryStatus.COMPENSATED
    assert by_seq[15] is EntryStatus.APPLIED
    assert by_seq[18] is EntryStatus.APPLIED
    # The post-#12 entries the rollback could undo were marked compensated;
    # the irreversible ones are exactly the blast radius.
    assert {i.seq for i in res.report.blast_radius} == {15, 18}

    # The freeze stopped the rogue phase: 6 further attempts rejected,
    # leaving no ledger rows and no world effects.
    assert res.prevented == 6
    assert res.prevented_frozen_errors == 6
    with Session(engine) as session:
        all_rows = session.exec(select(LedgerEntry)).all()
    assert len([r for r in all_rows
                if r.thread_id == ROGUE_THREAD]) == 18

    # CRM restored to the pre-corruption snapshot (#12).
    actual_crm = {rec["id"]: rec["data"]
                  for rec in res.world.crm.list_records()}
    assert actual_crm == res.snapshot_12["crm"]["records"]

    # The rogue calendar hold... there is none (rogue holds were prevented);
    # benign holds survive.
    assert len(res.world.calendar.holds) == 2

    # The rogue note was restored away; benign notes survive.
    assert len(res.world.notes.notes) == 2
    assert all("rogue" not in n["title"]
               for n in res.world.notes.notes.values())

    # Exactly one refund — the rogue charge — and the charge is marked
    # refunded exactly once.
    assert len(res.world.stripe.refunds) == 1
    refunded = [c for c in res.world.stripe.charges if c["refunded"]]
    assert len(refunded) == 1
    benign = [c for c in res.world.stripe.charges if not c["refunded"]]
    assert len(benign) == 1

    # Rogue emails remain SENT but are enumerated as irreversible.
    assert len(res.world.gmail.log()) == 3   # 1 benign welcome + 2 rogue
    assert len(res.blast_emails) == 2
    for e in res.blast_emails:
        assert e["to"] == "attacker@evil.example"
        assert len(e["body_sha256"]) == 64
        assert e["why"]
    log = res.world.gmail.log()
    assert sum(1 for m in log if "rogue" in m["subject"]) == 2

    # Dry-run preview matched the executed report (same plan).
    assert [i.seq for i in res.dry_report.restored] == \
        [i.seq for i in res.report.restored]
    assert [i.seq for i in res.dry_report.compensated] == \
        [i.seq for i in res.report.compensated]
    assert [i.seq for i in res.dry_report.blast_radius] == \
        [i.seq for i in res.report.blast_radius]

    # World-state hashes: corruption changed the world, rollback did NOT
    # restore the irreversible part (emails), so the final hash differs
    # from the pre-corruption hash only through the irreversible residue.
    assert res.hash_before_corruption != res.hash_after_corruption
    assert res.hash_after_rollback != res.hash_after_corruption
    assert res.hash_after_rollback == hash_world(res.final_state)

    # Exactly 6 distinct tools were used.
    assert {r.tool_name for r in rows} == {
        "crm.read_record", "crm.update_record", "stripe.create_charge",
        "gmail.send", "calendar.slot_hold", "notes.add",
    }


def test_demo_deterministic(make_engine):
    e1, e2 = make_engine(), make_engine()
    r1 = run_demo(engine=e1, seed=DEFAULT_DEMO_SEED, fast=True, narrate=None)
    r2 = run_demo(engine=e2, seed=DEFAULT_DEMO_SEED, fast=True, narrate=None)
    assert r1.hash_after_rollback == r2.hash_after_rollback
    assert r1.final_state == r2.final_state
    assert _sig(r1.rows) == _sig(r2.rows)


def test_demo_different_seed_differs(make_engine):
    e1, e2 = make_engine(), make_engine()
    r1 = run_demo(engine=e1, seed=1, fast=True, narrate=None)
    r2 = run_demo(engine=e2, seed=2, fast=True, narrate=None)
    assert r1.hash_after_rollback != r2.hash_after_rollback


def test_demo_no_rollback_leaves_rows_applied(engine):
    res = run_demo(engine=engine, seed=DEFAULT_DEMO_SEED, fast=True,
                   narrate=None, rollback=False, thread_id="pocketos-norb")
    assert res.report is None and res.dry_report is None
    rows = res.rows
    assert [r.seq for r in rows] == list(range(1, 19))
    # The rogue actions are still applied — nothing was compensated.
    assert all(r.status is EntryStatus.APPLIED for r in rows)

    # The full compensation plan is still previewable afterwards (this is
    # what `python -m undolog_torture.api rollback-preview` shells out to).
    from undolog_core import Ledger
    from undolog_torture.executors import build_executors
    from undolog_torture.registry_ext import load_torture_registry
    ledger = Ledger(engine, registry=load_torture_registry())
    preview = ledger.rollback("pocketos-norb", 12,
                              build_executors(res.world), dry_run=True)
    assert sorted(i.seq for i in preview.restored) == [13, 16, 17]
    assert sorted(i.seq for i in preview.compensated) == [14]
    assert sorted(i.seq for i in preview.blast_radius) == [15, 18]


def test_demo_cli(tmp_path):
    schema = f"test_demo_cli_{uuid.uuid4().hex[:8]}"
    env = dict(os.environ)
    env["DATABASE_URL"] = DEFAULT_DATABASE_URL
    proc = subprocess.run(
        [sys.executable, "-m", "undolog_torture.demo",
         "--fast", "--schema", schema, "--seed", "7"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    try:
        assert proc.returncode == 0, proc.stderr[-3000:]
        out = proc.stdout
        for section in ("PocketOS", "minute 2", "ROGUE", "FREEZE",
                        "DRY-RUN", "BLAST RADIUS", "SUMMARY",
                        "restored:", "refunded:", "irreversible:",
                        "world-state hash"):
            assert section in out, f"missing section {section!r}"
        assert "2 irreversible" in out
        # Recipients are listed.
        assert "attacker@evil.example" in out
    finally:
        drop_schema(DEFAULT_DATABASE_URL, schema)
