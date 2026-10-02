"""TS-04-partial — out-of-process mock Stripe over real HTTP.

The mock Stripe app (world/stripe.py) runs in its own process (uvicorn on
an ephemeral localhost port), state persisted to a file so a SIGKILL +
restart reloads it. The wrapped tool call and the rollback refund both
cross the network boundary via httpx. The process kill mid-test proves the
idempotency guarantee survives the server dying and coming back.
"""
# Full external-API idempotency verification pending sandbox key (Stripe/Razorpay) — see issue #1.
import subprocess
import sys
import time

import httpx
import pytest
from sqlmodel import Session, select

from undolog_core import Ledger
from undolog_core.models import LedgerEntry
from undolog_torture.registry_ext import load_torture_registry
from undolog_torture.executors import StripeExecutor
from undolog_torture.stripe_server import HttpStripe, MockStripeServer

pytestmark = pytest.mark.ts04_partial


@pytest.fixture(scope="session")
def stripe_server(tmp_path_factory):
    """Start the mock Stripe server out-of-process; tear down at the end."""
    state_file = tmp_path_factory.mktemp("stripe-state") / "world.json"
    server = MockStripeServer(state_file)
    server.start()
    yield server
    server.stop()


def test_ts04_partial_http_replay_survives_process_kill(engine, stripe_server):
    thread_id = "ts04-partial"
    base = stripe_server.base_url
    remote = HttpStripe(base)          # HTTP view of the server-side world
    ledger = Ledger(engine, registry=load_torture_registry())
    tl = ledger.for_thread(thread_id)

    class HttpChargeAdapter:
        def capture_before(self):
            return {"charge_count": len(remote.charges()),
                    "balance": remote.balance()}

        def capture_after(self, result):
            return {"charge": result, "balance": remote.balance()}

    @tl.wrap(adapter=HttpChargeAdapter(), tool_name="stripe.create_charge")
    def charge(amount, card_token, req_id, idempotency_key=None):
        r = httpx.post(f"{base}/charges",
                       json={"amount": amount, "card_token": card_token,
                             "idempotency_key": idempotency_key}, timeout=10)
        r.raise_for_status()
        return r.json()

    # --- replay the same tool call 5x -------------------------------------
    args = dict(amount=4200, card_token="card_dup", req_id="only-call",
                idempotency_key="dup-key-partial")
    for _ in range(5):
        charge(**args)

    # Exactly 1 external effect over HTTP ...
    assert len(remote.charges()) == 1
    assert remote.charges()[0]["amount"] == 4200
    # ... and exactly 1 ledger row (in-process ledger dedups the replays).
    with Session(engine) as session:
        rows = session.exec(select(LedgerEntry)
                            .where(LedgerEntry.thread_id == thread_id)).all()
    assert len(rows) == 1

    # --- kill the mock server mid-test; the boundary is real --------------
    stripe_server.kill()
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{base}/charges", timeout=2)
    assert stripe_server.proc is None or stripe_server.proc.returncode is not None

    # --- restart from the persisted state file ----------------------------
    stripe_server.start()
    assert len(remote.charges()) == 1, "state must survive the SIGKILL"
    assert len(remote.refunds()) == 0

    # --- rollback refunds exactly once, across the process boundary -------
    executors = {"stripe.create_charge": StripeExecutor(remote)}
    report = ledger.rollback(thread_id, 0, executors)
    assert report.complete
    assert len(remote.refunds()) == 1, "compensation refunds exactly once"
    assert remote.refunds()[0]["charge_id"] == remote.charges()[0]["id"]
    assert remote.charges()[0]["refunded"] is True

    # Idempotent rollback replay refunds nothing more — even after another
    # server restart (the refund idempotency key is server-side state).
    stripe_server.kill()
    stripe_server.start()
    ledger.rollback(thread_id, 0, {"stripe.create_charge": StripeExecutor(remote)})
    assert len(remote.refunds()) == 1
    assert len(remote.charges()) == 1
