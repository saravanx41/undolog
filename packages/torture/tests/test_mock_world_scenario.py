"""Task 1.4 — mock world + scripted runaway-agent corruption scenario.

Covers the acceptance criteria:
1. 50-action scenario runs in-process end-to-end in under 5 seconds.
2. Same seed -> identical world-state hash and identical ledger rows.
3. Exactly 50 ledger rows, seq 1..50 gapless, single thread.
4. snapshot@33 vs final differs in exactly: 20 CRM records, +3 charges, +30 emails.
5. Registry classes: gmail.send=irreversible, stripe.create_charge=compensatable,
   crm update rows=reversible.

Plus unit tests for each mock server (idempotency, refunds, overwrite).
"""
import asyncio
import hashlib
import time

import httpx
import pytest
from sqlmodel import Session, select

from undolog_core.models import EntryClass, EntryStatus, LedgerEntry
from undolog_torture import load_torture_registry
from undolog_torture import (
    DEFAULT_SEED,
    build_worlds,
    hash_world,
    run_scenario,
    world_diff,
)
from undolog_torture.world import build_gmail_app, build_stripe_app, build_crm_app


# --------------------------------------------------------------------------
# Mock-server unit tests (in-process via httpx ASGITransport)
# --------------------------------------------------------------------------

def asgi_request(app, method, path, **kwargs):
    """Call a FastAPI app in-process through its ASGI interface."""

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://mock"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(_run())

def test_gmail_send_and_query_log():
    world = build_worlds(DEFAULT_SEED)
    app = build_gmail_app(world.gmail)
    r = asgi_request(app, "POST", "/send",
                     json={"to": "a@x.com", "subject": "hi", "body": "hi"})
    assert r.status_code == 200
    msg = r.json()
    assert msg["to"] == "a@x.com"
    r2 = asgi_request(app, "POST", "/send",
                      json={"to": "b@x.com", "subject": "yo", "body": "yo"})
    assert r2.json()["id"] != msg["id"]
    log = asgi_request(app, "GET", "/log").json()
    assert len(log) == 2
    assert [m["to"] for m in log] == ["a@x.com", "b@x.com"]
    assert log[0]["body_sha256"] == hashlib.sha256(b"hi").hexdigest()


def test_stripe_idempotency_key_replay_returns_original_charge():
    world = build_worlds(DEFAULT_SEED)
    app = build_stripe_app(world.stripe)
    payload = {"amount": 1250, "card_token": "tok_a",
               "idempotency_key": "key-1"}
    c1 = asgi_request(app, "POST", "/charges", json=payload).json()
    # Replay the SAME idempotency key: must return the original charge
    # and must NOT create a second charge.
    c2 = asgi_request(app, "POST", "/charges",
                      json=dict(payload, amount=9999)).json()
    assert c2["id"] == c1["id"]
    assert c2["amount"] == 1250
    assert len(asgi_request(app, "GET", "/charges").json()) == 1
    assert world.stripe.balance == 1250


def test_stripe_refund_idempotent_and_no_double_refund():
    world = build_worlds(DEFAULT_SEED)
    app = build_stripe_app(world.stripe)
    ch = asgi_request(app, "POST", "/charges",
                      json={"amount": 500, "card_token": "tok_b",
                            "idempotency_key": "key-2"}).json()
    r1 = asgi_request(app, "POST", "/refunds",
                      json={"charge_id": ch["id"],
                            "idempotency_key": "rf-1"}).json()
    # Same refund idempotency key replays the original refund.
    r2 = asgi_request(app, "POST", "/refunds",
                      json={"charge_id": ch["id"],
                            "idempotency_key": "rf-1"}).json()
    assert r2["id"] == r1["id"]
    assert len(asgi_request(app, "GET", "/refunds").json()) == 1
    # A NEW refund against the same charge is rejected (no double refund).
    r3 = asgi_request(app, "POST", "/refunds",
                      json={"charge_id": ch["id"],
                            "idempotency_key": "rf-2"})
    assert r3.status_code == 409
    assert len(asgi_request(app, "GET", "/refunds").json()) == 1
    assert asgi_request(app, "GET", "/balance").json() == {
        "balance": 0, "held": 0,
    }


def test_crm_seed_read_overwrite_and_list():
    world = build_worlds(DEFAULT_SEED)
    app = build_crm_app(world.crm)
    rec = asgi_request(app, "GET", "/records/rec_000").json()
    assert rec["id"] == "rec_000"
    before = dict(rec["data"])
    r = asgi_request(app, "PUT", "/records/rec_000",
                     json={"data": {"hacked": True}})
    assert r.status_code == 200
    after = asgi_request(app, "GET", "/records/rec_000").json()["data"]
    assert after == {"hacked": True} and after != before
    all_recs = asgi_request(app, "GET", "/records").json()
    assert len(all_recs) == len(world.crm.records)


# --------------------------------------------------------------------------
# Scenario acceptance tests
# --------------------------------------------------------------------------

def test_scenario_runs_end_to_end_under_5s(engine):
    start = time.perf_counter()
    result = run_scenario(engine, seed=DEFAULT_SEED, thread_id="t-speed")
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"scenario took {elapsed:.2f}s"
    assert result.duration < 5.0
    assert len(result.rows) == 50


def test_deterministic_same_seed_identical_world_and_ledger(make_engine):
    e1, e2 = make_engine(), make_engine()
    r1 = run_scenario(e1, seed=DEFAULT_SEED, thread_id="determinism-check")
    r2 = run_scenario(e2, seed=DEFAULT_SEED, thread_id="determinism-check")
    assert hash_world(r1.final_state) == hash_world(r2.final_state)
    assert r1.final_state == r2.final_state

    def ledger_signature(rows):
        return [
            (r.seq, r.tool_name, r.class_.value, r.idempotency_key,
             r.status.value)
            for r in rows
        ]

    sig1 = ledger_signature(r1.rows)
    sig2 = ledger_signature(r2.rows)
    assert len(sig1) == len(sig2) == 50
    # Identical seq/tool/class/idempotency-key/status sequence across runs
    # (thread_id differs by design — it is part of the ledger idempotency key).
    assert sig1 == sig2


def test_different_seed_differs(engine):
    r1 = run_scenario(engine, seed=1, thread_id="t-seed-a")
    r2 = run_scenario(engine, seed=2, thread_id="t-seed-b")
    assert hash_world(r1.final_state) != hash_world(r2.final_state)


def test_ledger_rows_50_gapless_single_thread(engine):
    result = run_scenario(engine, seed=DEFAULT_SEED, thread_id="t-shape")
    rows = result.rows
    assert len(rows) == 50
    assert [r.seq for r in rows] == list(range(1, 51))
    assert {r.thread_id for r in rows} == {"t-shape"}
    assert all(r.status is EntryStatus.APPLIED for r in rows)
    # Cross-check straight from the database, not just the return value.
    with Session(engine) as session:
        db_rows = session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == "t-shape")
            .order_by(LedgerEntry.seq)
        ).all()
    assert [r.seq for r in db_rows] == list(range(1, 51))


def test_snapshot33_vs_final_diff_exact(engine):
    result = run_scenario(engine, seed=DEFAULT_SEED, thread_id="t-diff")
    diff = world_diff(result.snapshot_33, result.final_state)
    assert diff["crm_changed"] == 20
    assert len(diff["crm_changed_ids"]) == 20
    assert diff["charges_added"] == 3
    assert diff["refunds_added"] == 0
    assert diff["emails_added"] == 30
    # The double-charged cards were already charged in the benign phase:
    # each of the 3 extra charges duplicates an earlier (token, amount) pair.
    benign_charges = result.snapshot_33["stripe"]["charges"]
    final_charges = result.final_state["stripe"]["charges"]
    extra = final_charges[len(benign_charges):]
    assert len(extra) == 3
    benign_pairs = {(c["card_token"], c["amount"]) for c in benign_charges}
    for c in extra:
        assert (c["card_token"], c["amount"]) in benign_pairs


def test_registry_classes_on_ledger_rows(engine):
    result = run_scenario(engine, seed=DEFAULT_SEED, thread_id="t-class")
    for row in result.rows:
        if row.tool_name == "gmail.send":
            assert row.class_ is EntryClass.IRREVERSIBLE, row.seq
        elif row.tool_name == "stripe.create_charge":
            assert row.class_ is EntryClass.COMPENSATABLE, row.seq
        elif row.tool_name == "crm.update_record":
            assert row.class_ is EntryClass.REVERSIBLE, row.seq
        elif row.tool_name == "crm.read_record":
            assert row.class_ is EntryClass.REVERSIBLE, row.seq
        else:
            pytest.fail(f"unexpected tool_name {row.tool_name!r} at seq {row.seq}")

    # Counts per tool line up with the script layout.
    counts = {}
    for row in result.rows:
        counts[row.tool_name] = counts.get(row.tool_name, 0) + 1
    assert counts["gmail.send"] == 3 + 5            # 3 benign + 5 corruption batches
    assert counts["stripe.create_charge"] == 7 + 3  # 7 benign + 3 double-charges
    assert counts["crm.update_record"] == 14 + 9    # 14 benign + 9 corruption batches
    assert counts["crm.read_record"] == 9           # 6 + 3 benign reads


def test_registry_seed_contains_mock_world_tools():
    reg = load_torture_registry()
    for name in ("gmail.send", "stripe.create_charge", "crm.update_record",
                 "crm.read_record"):
        assert reg.lookup(name) is not None, name
