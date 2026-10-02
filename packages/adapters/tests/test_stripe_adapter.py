"""Stripe adapter tests.

Offline tests run against a mocked stripe module (same attribute shape as
the real SDK: Balance.retrieve / Charge.create / Refund.create) with real
idempotency-key semantics, proving the plumbing: replayed compensation with
the same key refunds exactly once.

The TS-04 scenario additionally runs against REAL Stripe test mode when
STRIPE_SECRET_KEY is set; it is skipped otherwise.
"""
import os
from uuid import uuid4

import pytest
from sqlmodel import Session, select
from undolog_core.ledger import Ledger
from undolog_core.models import EntryClass, EntryStatus, LedgerEntry

from undolog_adapters import StripeAdapter, StripeCompensationExecutor


class _FakeBalance:
    def __init__(self, world):
        self._world = world

    def retrieve(self):
        return {"available": [{"amount": self._world.balance, "currency": "usd"}]}


class _FakeCharge:
    def __init__(self, world):
        self._world = world

    def create(self, amount, currency, source, idempotency_key=None):
        for ch in self._world.charges:
            if ch["idempotency_key"] == idempotency_key:
                return dict(ch)  # replay: original charge, no new effect
        charge = {
            "id": f"ch_{len(self._world.charges) + 1}",
            "amount": amount,
            "currency": currency,
            "source": source,
            "status": "succeeded",
            "idempotency_key": idempotency_key,
        }
        self._world.charges.append(charge)
        return dict(charge)


class _FakeRefund:
    def __init__(self, world):
        self._world = world

    def create(self, charge, idempotency_key=None):
        self._world.refund_calls.append(
            {"charge": charge, "idempotency_key": idempotency_key}
        )
        for rf in self._world.refunds:
            if rf["idempotency_key"] == idempotency_key:
                return dict(rf)  # replay: original refund, no new effect
        ch = next((c for c in self._world.charges if c["id"] == charge), None)
        if ch is None:
            raise RuntimeError(f"No such charge: {charge!r}")
        if any(r["charge"] == charge for r in self._world.refunds):
            raise RuntimeError(f"Charge already refunded: {charge!r}")
        refund = {
            "id": f"rf_{len(self._world.refunds) + 1}",
            "charge": charge,
            "idempotency_key": idempotency_key,
        }
        self._world.refunds.append(refund)
        return dict(refund)


class FakeStripeWorld:
    """In-memory stand-in for the stripe module."""

    def __init__(self):
        self.charges: list[dict] = []
        self.refunds: list[dict] = []
        self.refund_calls: list[dict] = []
        self.Balance = _FakeBalance(self)
        self.Charge = _FakeCharge(self)
        self.Refund = _FakeRefund(self)

    @property
    def balance(self):
        return sum(c["amount"] for c in self.charges)


@pytest.fixture()
def fake_stripe():
    return FakeStripeWorld()


def _entry(after_charge_id, amount=4200):
    return LedgerEntry(
        id=uuid4(),
        thread_id="t-stripe",
        seq=1,
        tool_name="stripe.create_charge",
        args_hash="h",
        idempotency_key="k",
        before_jsonb={"balance": {"available": [{"amount": 0, "currency": "usd"}]}},
        after_jsonb={"charge": {"id": after_charge_id, "amount": amount,
                                "currency": "usd", "status": "succeeded"}},
        class_=EntryClass.COMPENSATABLE,
        status=EntryStatus.APPLIED,
    )


# --------------------------------------------------------------------------
# Offline unit tests (no DB, no network)
# --------------------------------------------------------------------------

def test_capture_before_reads_balance(fake_stripe):
    adapter = StripeAdapter(stripe_module=fake_stripe)
    before = adapter.capture_before()
    assert before["balance"]["available"][0]["amount"] == 0


def test_capture_after_extracts_charge_from_dict_result(fake_stripe):
    adapter = StripeAdapter(stripe_module=fake_stripe)
    charge = fake_stripe.Charge.create(
        amount=4200, currency="usd", source="tok_visa", idempotency_key="k1"
    )
    after = adapter.capture_after(charge)
    assert after["charge"]["id"] == charge["id"]
    assert after["charge"]["amount"] == 4200
    assert after["charge"]["status"] == "succeeded"


def test_capture_after_extracts_charge_from_stripe_object(fake_stripe):
    """Stripe SDK results are StripeObjects (attr access), not plain dicts."""

    class StripeObjectLike:
        id = "ch_99"
        amount = 100
        currency = "usd"
        status = "succeeded"

    adapter = StripeAdapter(stripe_module=fake_stripe)
    after = adapter.capture_after(StripeObjectLike())
    assert after["charge"]["id"] == "ch_99"


def test_compensate_refunds_with_the_engine_key(fake_stripe):
    fake_stripe.charges.append({"id": "ch_1", "amount": 4200})
    executor = StripeCompensationExecutor(stripe_module=fake_stripe)
    executor.compensate(_entry("ch_1"), "comp-abc")
    assert len(fake_stripe.refunds) == 1
    assert fake_stripe.refund_calls[0]["idempotency_key"] == "comp-abc"
    assert fake_stripe.refund_calls[0]["charge"] == "ch_1"


def test_compensation_replay_same_key_refunds_exactly_once(fake_stripe):
    """Replaying compensation with the same key never double-refunds."""
    fake_stripe.charges.append({"id": "ch_1", "amount": 4200})
    executor = StripeCompensationExecutor(stripe_module=fake_stripe)
    entry = _entry("ch_1")
    for _ in range(5):
        executor.compensate(entry, "comp-abc")
    assert len(fake_stripe.refunds) == 1
    assert len(fake_stripe.refund_calls) == 5  # every call used the same key
    assert all(c["idempotency_key"] == "comp-abc" for c in fake_stripe.refund_calls)


def test_compensate_requires_after_proof_charge_id(fake_stripe):
    executor = StripeCompensationExecutor(stripe_module=fake_stripe)
    with pytest.raises(RuntimeError, match="after-proof"):
        executor.compensate(_entry(None), "comp-abc")


def test_restore_before_is_rejected_for_stripe_entries(fake_stripe):
    executor = StripeCompensationExecutor(stripe_module=fake_stripe)
    with pytest.raises(RuntimeError, match="restore_before"):
        executor.restore_before(_entry("ch_1"))


# --------------------------------------------------------------------------
# Offline integration: full wrap -> ledger -> rollback plumbing
# --------------------------------------------------------------------------

def _ts04_scenario(engine, stripe_module, thread_id, charges_sink, refunds_sink):
    """TS-04 against any stripe-module-shaped object (mock or real).

    ``charges_sink`` / ``refunds_sink`` collect the created effects (the
    real stripe module has no in-memory lists to inspect).
    """
    ledger = Ledger(engine)
    tl = ledger.for_thread(thread_id)
    adapter = StripeAdapter(stripe_module=stripe_module)

    @tl.wrap(adapter=adapter, tool_name="stripe.create_charge")
    def charge(amount, idempotency_key=None):
        ch = stripe_module.Charge.create(
            amount=amount,
            currency="usd",
            source="tok_visa",
            idempotency_key=idempotency_key,
        )
        charges_sink.append({"id": ch["id"], "amount": amount})
        return ch

    for _ in range(5):
        charge(4200, idempotency_key="dup-key-1")

    # wrap's ledger dedups the row; the charge endpoint dedups the effect.
    # The wrapped fn still executes on replays, so count distinct effects.
    assert len({c["id"] for c in charges_sink}) == 1, "exactly 1 external effect"
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()
    assert len(rows) == 1, "exactly 1 ledger row"
    entry_id = rows[0].id

    executors = {
        "stripe.create_charge": StripeCompensationExecutor(
            stripe_module=stripe_module
        )
    }
    report = ledger.rollback(thread_id, 0, executors)
    assert report.complete
    assert len(refunds_sink) == 1, "compensation refunds exactly once"
    assert refunds_sink[0]["charge"] == charges_sink[0]["id"]
    # The engine's deterministic key is what made the refund idempotent.
    assert refunds_sink[0]["idempotency_key"] == f"comp-{entry_id}"

    # Idempotent rollback replay refunds nothing more.
    report2 = ledger.rollback(thread_id, 0, executors)
    assert report2.complete
    assert len(refunds_sink) == 1
    return charges_sink[0]


def test_ts04_scenario_with_ledger_and_mocked_stripe(engine, fake_stripe):
    _ts04_scenario(
        engine, fake_stripe, "stripe-ts04-mock",
        charges_sink=fake_stripe.charges, refunds_sink=fake_stripe.refunds,
    )


# --------------------------------------------------------------------------
# TS-04 against REAL Stripe test mode (skipped without a key)
# --------------------------------------------------------------------------

REAL_STRIPE = pytest.mark.skipif(
    not os.environ.get("STRIPE_SECRET_KEY"),
    reason="needs Stripe test-mode key (STRIPE_SECRET_KEY)",
)


@REAL_STRIPE
def test_ts04_real_stripe_test_mode(engine):
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    refunds_sink = []
    real_refund_create = stripe.Refund.create

    # Record effects server-side without changing SDK behavior.
    def _recording_create(*args, **kwargs):
        rf = real_refund_create(*args, **kwargs)
        refunds_sink.append(
            {"charge": rf["charge"], "idempotency_key": kwargs.get("idempotency_key")}
        )
        return rf

    stripe.Refund.create = staticmethod(_recording_create)
    try:
        charge = _ts04_scenario(
            engine, stripe, f"stripe-ts04-live-{uuid4().hex[:8]}",
            charges_sink=[], refunds_sink=refunds_sink,
        )
    finally:
        stripe.Refund.create = real_refund_create

    refunds = stripe.Refund.list(charge=charge["id"])
    assert len(refunds.data) == 1, "exactly one refund exists server-side"
