"""The scripted runaway agent: exactly 50 wrapped actions, deterministic.

Layout (one action = one wrapped tool call = one ledger row, seq 1..50):

Benign phase, actions #1–#33:
  #1–#10   crm.update_record   single record writes (10 distinct records)
  #11–#16  crm.read_record     reads
  #17–#23  stripe.create_charge  charges on 7 distinct cards
  #24–#26  gmail.send          3 emails
  #27–#33  4 crm.update_record + 3 crm.read_record

Corruption phase, actions #34–#50:
  #34–#40  crm.update_record   batch overwrites, 2 records each  -> 14 records
  #41–#42  crm.update_record   batch overwrites, 3 records each  -> 6 records
           (20 CRM records overwritten in total)
  #43–#45  stripe.create_charge double-charges 3 benign-phase cards
           (one extra charge per card, same amount -> 3 extra charges)
  #46–#50  gmail.send          batch sends, 6 emails per call    -> 30 emails

Determinism: every random choice comes from random.Random(seed), consumed
in a fixed order. Two runs with the same seed produce identical world state
and identical ledger contents.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel import Session, select
from sqlalchemy import Engine

from undolog_core import Ledger
from undolog_core.models import LedgerEntry

from .faults import FaultConfig, FlakyCaptureAdapter
from .ledger_ext import EscalatingLedger
from .registry_ext import load_torture_registry
from .snapshot import canonical_state, world_diff
from .world import World, build_worlds

DEFAULT_SEED = 1337

CARD_TOKENS = [f"card_{i}" for i in range(1, 8)]
EMAIL_RECIPIENTS = [f"user{i}@evil.example" for i in range(1, 11)]


# --------------------------------------------------------------------------
# Adapters: capture_before/after proof for the ledger rows.
# --------------------------------------------------------------------------

class CRMUpdateAdapter:
    def __init__(self, world: World):
        self._crm = world.crm

    def capture_before(self) -> dict:
        return {"records": {r["id"]: r["data"] for r in self._crm.list_records()}}

    def capture_after(self, result: Any) -> dict:
        return {"result": result}


class CRMReadAdapter:
    def __init__(self, world: World):
        self._crm = world.crm

    def capture_before(self) -> dict:
        return {"record_count": len(self._crm.records)}

    def capture_after(self, result: Any) -> dict:
        return {"result": result}


class StripeChargeAdapter:
    def __init__(self, world: World):
        self._stripe = world.stripe

    def capture_before(self) -> dict:
        return {
            "charge_count": len(self._stripe.charges),
            "balance": self._stripe.balance,
        }

    def capture_after(self, result: Any) -> dict:
        return {"charge": result, "balance": self._stripe.balance}


class GmailSendAdapter:
    def __init__(self, world: World):
        self._gmail = world.gmail

    def capture_before(self) -> dict:
        return {"sent_count": len(self._gmail.sent)}

    def capture_after(self, result: Any) -> dict:
        msgs = result if isinstance(result, list) else [result]
        return {
            "sent_count": len(self._gmail.sent),
            "sent": len(msgs),
            "messages": [dict(m) for m in msgs],
        }


# --------------------------------------------------------------------------
# Scenario result
# --------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    world: World
    snapshot_33: dict          # canonical world state after action #33
    final_state: dict          # canonical world state after action #50
    diff: dict                 # world_diff(snapshot_33, final_state)
    rows: list[LedgerEntry]    # ledger rows for the thread, seq 1..50
    duration: float


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

def run_scenario(
    engine: Engine,
    seed: int = DEFAULT_SEED,
    thread_id: str = "runaway-1",
    faults: Optional[FaultConfig] = None,
) -> ScenarioResult:
    """Run the 50-action corruption scenario and return everything a test
    needs: world handles, the #33 snapshot, the final state, their diff,
    and the ledger rows.

    ``faults`` (Task 2.3) injects deterministically into the run:
    capture_before timeouts (rows demoted to log-only class=unknown via the
    EscalatingLedger) and duplicate replays of scenario actions (the mock
    world and the ledger both dedup, so the run is unchanged).
    """
    faults = faults or FaultConfig()
    start = time.perf_counter()
    rng = random.Random(seed)
    world = build_worlds(seed)
    capture_pct = faults.capture_timeout_pct
    if capture_pct > 0:
        ledger: Ledger = EscalatingLedger(
            engine, registry=load_torture_registry())
        cap_rng = random.Random(f"capture-{seed}-{thread_id}")
    else:
        ledger = Ledger(engine, registry=load_torture_registry())
        cap_rng = None
    tl = ledger.for_thread(thread_id)

    def maybe_shim(adapter):
        if cap_rng is None:
            return adapter
        return FlakyCaptureAdapter(adapter, capture_pct, rng=cap_rng)

    # --- wrapped tools ---------------------------------------------------
    # Every call carries a unique req_id: the ledger idempotency key is
    # derived from (thread, tool, args), so identical args would legitimately
    # dedup to one row — distinct actions must be distinct calls.
    @tl.wrap(adapter=maybe_shim(CRMUpdateAdapter(world)), tool_name="crm.update_record")
    def crm_update(record_id: str, data: dict, req_id: str) -> dict:
        return world.crm.update(record_id, data)

    @tl.wrap(adapter=maybe_shim(CRMUpdateAdapter(world)), tool_name="crm.update_record")
    def crm_update_many(updates: dict, req_id: str) -> dict:
        return world.crm.update_many(updates)

    @tl.wrap(adapter=maybe_shim(CRMReadAdapter(world)), tool_name="crm.read_record")
    def crm_read(record_id: str, req_id: str) -> dict:
        return world.crm.get(record_id)

    @tl.wrap(adapter=maybe_shim(StripeChargeAdapter(world)), tool_name="stripe.create_charge")
    def stripe_charge(amount: int, card_token: str, req_id: str,
                      idempotency_key: Optional[str] = None) -> dict:
        return world.stripe.create_charge(amount, card_token, idempotency_key)

    @tl.wrap(adapter=maybe_shim(GmailSendAdapter(world)), tool_name="gmail.send")
    def gmail_send(emails: list[dict], req_id: str) -> list[dict]:
        return world.gmail.send_many(emails)

    all_ids = sorted(world.crm.records)
    counter = iter(range(1, 51))
    req = lambda: f"req-{next(counter):02d}"  # noqa: E731

    actions: list[tuple] = []

    def do(fn, *args, **kwargs):
        actions.append((fn, args, kwargs))
        return fn(*args, **kwargs)

    # --- benign phase: actions #1–#33 ------------------------------------
    for record_id in rng.sample(all_ids, 10):                      # #1–#10
        do(crm_update, record_id, _benign_crm_payload(rng), req_id=req())

    for _ in range(6):                                             # #11–#16
        do(crm_read, rng.choice(all_ids), req_id=req())

    benign_charges: list[tuple[str, int]] = []
    for card in CARD_TOKENS:                                       # #17–#23
        amount = rng.randrange(500, 5001, 100)
        benign_charges.append((card, amount))
        do(stripe_charge, amount, card, req_id=req(),
                      idempotency_key=f"benign-{card}-{seed}")

    for _ in range(3):                                             # #24–#26
        do(gmail_send, [_email(rng, tag="benign")], req_id=req())

    for i in range(7):                                             # #27–#33
        if i % 2 == 0:
            do(crm_update, rng.choice(all_ids), _benign_crm_payload(rng),
                       req_id=req())
        else:
            do(crm_read, rng.choice(all_ids), req_id=req())

    # --- snapshot after action #33 ---------------------------------------
    snapshot_33 = canonical_state(world)

    # --- corruption phase: actions #34–#50 --------------------------------
    corruption_ids = rng.sample(all_ids, 20)
    pairs = [corruption_ids[i:i + 2] for i in range(0, 14, 2)]      # 7 pairs
    triples = [corruption_ids[14:17], corruption_ids[17:20]]        # 2 triples
    batches = (
        [{rid: _corrupt_crm_payload(rng, i * 2 + j) for j, rid in
          enumerate(pair)} for i, pair in enumerate(pairs)]
        + [{rid: _corrupt_crm_payload(rng, 14 + t * 3 + j) for j, rid in
            enumerate(triple)} for t, triple in enumerate(triples)]
    )
    # ^ #34–#40 overwrite 2 records each (14), #41–#42 overwrite 3 each (6).
    for batch in batches:                                          # #34–#42
        do(crm_update_many, batch, req_id=req())

    for card, amount in benign_charges[:3]:                        # #43–#45
        do(stripe_charge, amount, card, req_id=req(),
                      idempotency_key=f"corrupt-dup-{card}-{seed}")

    for _ in range(5):                                             # #46–#50
        do(gmail_send, [_email(rng, tag="corrupt") for _ in range(6)],
                   req_id=req())

    # --- fault hook 4: duplicate replays -----------------------------------
    # Re-execute N distinct actions with byte-identical args. The mock world
    # (stripe/gmail idempotency dedup, crm overwrite idempotence) and the
    # ledger (idempotency key) must absorb these without any visible change.
    if faults.replay_n > 0 and actions:
        rep_rng = random.Random(f"replay-{seed}-{thread_id}")
        picks = rep_rng.sample(range(len(actions)),
                               k=min(faults.replay_n, len(actions)))
        for idx in sorted(picks):
            fn, a, k = actions[idx]
            fn(*a, **k)

    # --- collect results --------------------------------------------------
    final_state = canonical_state(world)
    with Session(engine) as session:
        rows = list(session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all())

    return ScenarioResult(
        world=world,
        snapshot_33=snapshot_33,
        final_state=final_state,
        diff=world_diff(snapshot_33, final_state),
        rows=rows,
        duration=time.perf_counter() - start,
    )


def _benign_crm_payload(rng: random.Random) -> dict:
    return {
        "note": f"touch by agent ({rng.randrange(1 << 30)})",
        "tier": rng.choice(["free", "pro", "enterprise"]),
    }


def _corrupt_crm_payload(rng: random.Random, n: int) -> dict:
    return {
        "compromised": True,
        "exfil_batch": n,
        "blob": f"{rng.randrange(1 << 30):x}",
    }


def _email(rng: random.Random, tag: str) -> dict:
    k = rng.randrange(1 << 30)
    return {
        "to": rng.choice(EMAIL_RECIPIENTS),
        "subject": f"[{tag}] urgent action required #{k}",
        "body": f"{tag} payload {k}",
        "idempotency_key": f"{tag}-{k}",
    }
