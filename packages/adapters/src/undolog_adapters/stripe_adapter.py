"""Stripe adapter + compensation executor (real Stripe SDK, test-mode ready).

StripeAdapter captures proof around ``stripe.create_charge``:

* before-value: an account balance read (``Balance.retrieve``);
* after-value: the created charge (id/amount/currency/status), extracted
  from the wrapped fn's return value — a stripe Charge object, a dict, or
  anything an ``extractor`` callable can turn into one.

StripeCompensationExecutor refunds the charge recorded in the entry's
after-proof. The idempotency key is supplied by the rollback engine
(``comp-{entry.id}``) and passed straight to ``Refund.create``: Stripe's
idempotency layer dedups replays, so a retried or replayed rollback never
double-refunds.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import stripe

from undolog_core.models import LedgerEntry


def _charge_ref(obj: Any) -> dict:
    """Normalize a stripe Charge (StripeObject or dict) to a small dict."""
    get = obj.get if isinstance(obj, dict) else lambda k, d=None: getattr(obj, k, d)
    return {
        "id": get("id"),
        "amount": get("amount"),
        "currency": get("currency"),
        "status": get("status"),
    }


class StripeAdapter:
    """Capture adapter for ``stripe.create_charge`` (compensatable)."""

    def __init__(
        self,
        stripe_module: Optional[Any] = None,
        extractor: Optional[Callable[[Any], Any]] = None,
    ):
        self._stripe = stripe_module if stripe_module is not None else stripe
        # extractor: fn result -> charge-like object (identity by default).
        self._extractor = extractor or (lambda result: result)

    def capture_before(self) -> dict:
        balance = self._stripe.Balance.retrieve()
        return {"balance": dict(balance)}

    def capture_after(self, result: Any) -> dict:
        return {"charge": _charge_ref(self._extractor(result))}


class StripeCompensationExecutor:
    """Refund the charge from the entry's after-proof, idempotent by key."""

    def __init__(self, stripe_module: Optional[Any] = None):
        self._stripe = stripe_module if stripe_module is not None else stripe

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        charge_id = ((entry.after_jsonb or {}).get("charge") or {}).get("id")
        if not charge_id:
            raise RuntimeError(
                "stripe entry missing after-proof charge id; cannot refund"
            )
        self._stripe.Refund.create(
            charge=charge_id, idempotency_key=idempotency_key
        )

    def restore_before(self, entry: LedgerEntry) -> None:
        raise RuntimeError(
            "stripe charge entries are compensatable; "
            "restore_before() must never be called"
        )
