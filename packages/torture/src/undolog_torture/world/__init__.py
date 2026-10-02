"""The mock world: one container holding all in-memory services."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .calendar import CalendarWorld
from .crm import CRMWorld
from .gmail import GmailWorld
from .notes import NotesWorld
from .stripe import StripeWorld

__all__ = ["World", "build_worlds", "GmailWorld", "StripeWorld", "CRMWorld",
           "CalendarWorld", "NotesWorld",
           "build_gmail_app", "build_stripe_app", "build_crm_app"]


@dataclass
class World:
    gmail: GmailWorld
    stripe: StripeWorld
    crm: CRMWorld
    calendar: CalendarWorld = field(default_factory=CalendarWorld)
    notes: NotesWorld = field(default_factory=NotesWorld)


def build_worlds(seed: int) -> World:
    """Fresh deterministic world. One RNG stream, consumed in fixed order."""
    rng = random.Random(seed)
    crm = CRMWorld.seeded(rng)
    return World(gmail=GmailWorld(), stripe=StripeWorld(), crm=crm)


from .gmail import build_gmail_app  # noqa: E402,F401
from .stripe import build_stripe_app  # noqa: E402,F401
from .crm import build_crm_app  # noqa: E402,F401
