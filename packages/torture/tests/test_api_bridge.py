"""api.py bridge regression tests (Task 4.1 live-tour findings).

1. World truthfulness: after `run_demo(rollback=False)` the ledger
   describes a CORRUPTED world. The bridge must build executors against a
   truthful reconstruction of that world (replay in a scratch schema), not
   a fresh benign one — otherwise restores/compensations fail and the
   engine halts with complete=False.

2. Why-panel context: demo rows must carry non-null prompt_context_ref
   with distinct benign ("minute-1/...") vs rogue ("rogue/...") phases.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from undolog_core.models import LedgerEntry

from undolog_torture import api
from undolog_torture.dbutil import DEFAULT_DATABASE_URL
from undolog_torture.demo import run_demo

SEED = 20260


@pytest.fixture(scope="module")
def no_rollback_demo(engine):
    """The live-tour state: demo ran, rogue tail frozen, nothing rolled
    back. Rows 13-18 are status=applied on this module's schema."""
    return run_demo(engine=engine, seed=SEED, fast=True, narrate=None,
                    rollback=False)


def test_api_rollback_against_no_rollback_demo(no_rollback_demo, schema,
                                               monkeypatch):
    monkeypatch.setenv("UNDOLOG_SCHEMA", schema)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    api._WORLD_CACHE.clear()          # per-test isolation of the cache

    # Preview first (the UI's first call), then execute.
    preview = api.rollback("pocketos-demo", 12, dry_run=True, seed=SEED)
    assert preview["complete"] is True
    assert preview["summary"] == {"restored": 3, "compensated": 1,
                                  "irreversible": 2, "failed": 0}

    result = api.rollback("pocketos-demo", 12, dry_run=False, seed=SEED)
    assert result["complete"] is True, result["failed"]
    assert result["summary"] == {"restored": 3, "compensated": 1,
                                 "irreversible": 2, "failed": 0}
    # The world reconstruction must be the corrupted one: restoring the
    # rogue note and refunding the rogue charge only work if they exist.
    tools_restored = sorted(i["tool_name"] for i in result["restored"])
    assert tools_restored == ["crm.update_record", "crm.update_record",
                              "notes.add"]
    assert [i["tool_name"] for i in result["compensated"]] == \
        ["stripe.create_charge"]
    assert sorted(i["seq"] for i in result["blast_radius"]) == [15, 18]

    # Idempotent re-run: nothing left to restore or compensate (the two
    # irreversible emails stay applied, so core re-enumerates them in the
    # blast radius every time — a report, not an action), and the cached
    # world proves no second refund was issued.
    again = api.rollback("pocketos-demo", 12, dry_run=False, seed=SEED)
    assert again["complete"] is True
    assert again["summary"] == {"restored": 0, "compensated": 0,
                                "irreversible": 2, "failed": 0}
    cached_world = api._WORLD_CACHE[SEED]
    assert len(cached_world.stripe.refunds) == 1
    assert sum(1 for c in cached_world.stripe.charges if c["refunded"]) == 1

    # The scratch schema used for the world replay must be gone.
    admin = create_engine(DEFAULT_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        left = conn.execute(text(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name LIKE 'undolog_api_scratch_%'")).scalar()
    admin.dispose()
    assert left == 0, "scratch schema leaked"


def test_demo_rows_carry_prompt_context(engine):
    run_demo(engine=engine, seed=SEED, fast=True, narrate=None,
             rollback=False, thread_id="pocketos-ctx")
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == "pocketos-ctx")
            .order_by(LedgerEntry.seq)
        ).all()
    assert len(rows) == 18
    assert all(r.prompt_context_ref for r in rows)
    benign = {r.prompt_context_ref for r in rows if r.seq <= 12}
    rogue = {r.prompt_context_ref for r in rows if r.seq > 12}
    assert all(p.startswith("minute-1/") for p in benign)
    assert all(p.startswith("rogue/") for p in rogue)
    assert benign and rogue and not (benign & rogue)
