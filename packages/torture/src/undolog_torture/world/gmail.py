"""Mock Gmail: in-memory sent-email log + FastAPI app builder.

Sends are permanent — the world offers no delete/unsend, matching the
registry class "irreversible" for gmail.send.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI


@dataclass
class GmailWorld:
    """In-memory sent-email log. Ids are sequential (msg_1, msg_2, ...).

    Sends carrying an idempotency_key dedup to the original message — a
    replayed send never lands twice (mirrors the stripe mock and makes
    duplicate-delivery fault injection visible as a no-op).
    """

    sent: list[dict[str, Any]] = field(default_factory=list)
    _by_key: dict[str, dict[str, Any]] = field(default_factory=dict)

    def send(self, to: str, subject: str, body: str,
             idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key is not None and idempotency_key in self._by_key:
            return dict(self._by_key[idempotency_key])  # replay: no new email
        msg = {
            "id": f"msg_{len(self.sent) + 1}",
            "to": to,
            "subject": subject,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": idempotency_key,
        }
        self.sent.append(msg)
        if idempotency_key is not None:
            self._by_key[idempotency_key] = msg
        return dict(msg)

    def send_many(self, emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.send(**e) for e in emails]

    def log(self) -> list[dict[str, Any]]:
        return [dict(m) for m in self.sent]


def build_gmail_app(world: GmailWorld) -> FastAPI:
    app = FastAPI(title="mock-gmail")

    @app.post("/send")
    def send(email: dict[str, Any]) -> dict[str, Any]:
        return world.send(
            to=email["to"],
            subject=email["subject"],
            body=email.get("body", ""),
            idempotency_key=email.get("idempotency_key"),
        )

    @app.get("/log")
    def get_log() -> list[dict[str, Any]]:
        return world.log()

    return app
