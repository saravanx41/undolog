"""Mock Stripe: in-memory charges/refunds + FastAPI app builder.

Semantics that matter for the torture tests:
* Charge dedup by idempotency key: replaying the same key returns the
  original charge and never double-charges.
* Refund dedup by idempotency key: replaying returns the original refund.
* A charge can be refunded at most once — a second, distinct refund attempt
  is rejected with 409.
* Ids are sequential (ch_1, rf_1, ...) so world state is deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import FastAPI, HTTPException


@dataclass
class StripeWorld:
    charges: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)

    @property
    def balance(self) -> int:
        """Net held balance in cents."""
        total = sum(c["amount"] for c in self.charges)
        refunded = sum(r["amount"] for r in self.refunds)
        return total - refunded

    def create_charge(self, amount: int, card_token: str,
                      idempotency_key: Optional[str] = None) -> dict[str, Any]:
        if idempotency_key is not None:
            for ch in self.charges:
                if ch["idempotency_key"] == idempotency_key:
                    return dict(ch)  # replay: original charge, no new effect
        charge = {
            "id": f"ch_{len(self.charges) + 1}",
            "amount": amount,
            "card_token": card_token,
            "idempotency_key": idempotency_key,
            "refunded": False,
        }
        self.charges.append(charge)
        return dict(charge)

    def create_refund(self, charge_id: str,
                      idempotency_key: Optional[str] = None) -> dict[str, Any]:
        for rf in self.refunds:
            if rf["idempotency_key"] == idempotency_key:
                return dict(rf)  # replay: original refund, no new effect
        charge = next((c for c in self.charges if c["id"] == charge_id), None)
        if charge is None:
            raise KeyError(f"no such charge {charge_id!r}")
        if charge["refunded"]:
            raise ValueError(f"charge {charge_id} already refunded")
        charge["refunded"] = True
        refund = {
            "id": f"rf_{len(self.refunds) + 1}",
            "charge_id": charge_id,
            "amount": charge["amount"],
            "idempotency_key": idempotency_key,
        }
        self.refunds.append(refund)
        return dict(refund)

    def ledger(self) -> dict[str, Any]:
        return {
            "charges": [dict(c) for c in self.charges],
            "refunds": [dict(r) for r in self.refunds],
        }


def build_stripe_app(world: StripeWorld) -> FastAPI:
    app = FastAPI(title="mock-stripe")

    @app.post("/charges")
    def create_charge(payload: dict[str, Any]) -> dict[str, Any]:
        return world.create_charge(
            amount=int(payload["amount"]),
            card_token=payload["card_token"],
            idempotency_key=payload.get("idempotency_key"),
        )

    @app.post("/refunds")
    def create_refund(payload: dict[str, Any]):
        try:
            return world.create_refund(
                charge_id=payload["charge_id"],
                idempotency_key=payload.get("idempotency_key"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/charges")
    def list_charges() -> list[dict[str, Any]]:
        return [dict(c) for c in world.charges]

    @app.get("/refunds")
    def list_refunds() -> list[dict[str, Any]]:
        return [dict(r) for r in world.refunds]

    @app.get("/balance")
    def get_balance() -> dict[str, int]:
        return {"balance": world.balance, "held": world.balance}

    return app
