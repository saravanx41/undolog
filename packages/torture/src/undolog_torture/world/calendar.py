"""Mock Calendar: slot holds (minimal in-world fake for the registry tool
calendar.slot_hold, which has no Week-1 mock backend).

Holds are compensatable: a hold can be released. Release is idempotent by
slot_id, mirroring the stripe refund semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CalendarWorld:
    holds: dict[str, dict[str, Any]] = field(default_factory=dict)

    def hold(self, slot_id: str, attendee: str,
             duration_min: int) -> dict[str, Any]:
        if slot_id in self.holds:
            return dict(self.holds[slot_id])   # re-hold: original slot
        hold = {
            "slot_id": slot_id,
            "attendee": attendee,
            "duration_min": duration_min,
        }
        self.holds[slot_id] = hold
        return dict(hold)

    def release(self, slot_id: str) -> Optional[dict[str, Any]]:
        """Release a hold; idempotent (releasing twice is a no-op)."""
        return self.holds.pop(slot_id, None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {sid: dict(h) for sid, h in sorted(self.holds.items())}
