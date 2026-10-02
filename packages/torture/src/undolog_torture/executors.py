"""RollbackExecutor implementations bridging the mock world.

* CrmExecutor      — reversible: restores exactly the records present in the
                     entry's before-proof (per-record or full snapshot).
* StripeExecutor   — compensatable: refunds the charge recorded in the
                     entry's after-proof, with the deterministic compensation
                     idempotency key. Optionally injects refund 500s
                     (FaultConfig.comp_fail_n) and records a retry audit
                     trail for TS-02.
* GmailExecutor    — irreversible: must NEVER be called; gmail rows are
                     refused into the blast radius by core. Raising here
                     turns any core regression into a loud test failure.

``build_executors(world, faults)`` returns the full tool->executor mapping
used by every TS scenario and by the chaos runner.
"""
from __future__ import annotations

from typing import Any, Optional

from undolog_core.models import LedgerEntry

from .faults import FaultConfig
from .world import World


class CrmExecutor:
    """Restore before-state for crm.update_record / crm.read_record rows."""

    def __init__(self, crm, faults: Optional[FaultConfig] = None):
        self._crm = crm
        self.audit: dict[Any, list[dict]] = {}

    def restore_before(self, entry: LedgerEntry) -> None:
        attempts = self.audit.setdefault(entry.id, [])
        records = (entry.before_jsonb or {}).get("records")
        if not records:
            attempts.append({"ok": False, "error": "no before-proof records"})
            raise RuntimeError("crm entry without before-proof records")
        for rid, data in records.items():
            self._crm.records[rid] = dict(data)
        attempts.append({"ok": True})

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        raise RuntimeError(
            "crm entries are reversible; compensate() must never be called")


class StripeExecutor:
    """Refund the charge recorded in after-proof (idempotent by key)."""

    def __init__(self, stripe, faults: Optional[FaultConfig] = None):
        self._stripe = stripe
        faults = faults or FaultConfig()
        self._failures_left = faults.comp_fail_n
        self._fail_forever = faults.comp_fail_forever
        self.audit: dict[Any, list[dict]] = {}

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        attempts = self.audit.setdefault(entry.id, [])
        n = len(attempts) + 1
        if self._fail_forever or self._failures_left > 0:
            if not self._fail_forever:
                self._failures_left -= 1
            error = "mock stripe 500: refund endpoint failed"
            attempts.append({"attempt": n, "ok": False, "error": error})
            raise RuntimeError(error)
        charge_id = ((entry.after_jsonb or {}).get("charge") or {}).get("id")
        if not charge_id:
            error = "stripe entry missing after-proof charge id"
            attempts.append({"attempt": n, "ok": False, "error": error})
            raise RuntimeError(error)
        self._stripe.create_refund(charge_id, idempotency_key=idempotency_key)
        attempts.append({"attempt": n, "ok": True})

    def restore_before(self, entry: LedgerEntry) -> None:
        raise RuntimeError(
            "stripe charge entries are compensatable; "
            "restore_before() must never be called")


class GmailExecutor:
    """Irreversible — core must refuse gmail.send rows before executors."""

    def __init__(self, gmail, faults: Optional[FaultConfig] = None):
        self._gmail = gmail
        self.audit: dict[Any, list[dict]] = {}

    def restore_before(self, entry: LedgerEntry) -> None:
        raise RuntimeError("gmail.send is irreversible: restore refused")

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        raise RuntimeError("gmail.send is irreversible: compensate refused")


def build_executors(world: World, faults: Optional[FaultConfig] = None
                    ) -> dict[str, object]:
    """The standard tool->executor mapping for the mock world."""
    return {
        "crm.update_record": CrmExecutor(world.crm, faults),
        "crm.read_record": CrmExecutor(world.crm, faults),
        "stripe.create_charge": StripeExecutor(world.stripe, faults),
        "gmail.send": GmailExecutor(world.gmail, faults),
        "calendar.slot_hold": CalendarExecutor(world.calendar, faults),
        "notes.add": NotesExecutor(world.notes, faults),
    }


class CalendarExecutor:
    """Release a held slot (compensatable). Idempotent by slot_id."""

    def __init__(self, calendar, faults: Optional[FaultConfig] = None):
        self._calendar = calendar
        self.audit: dict[Any, list[dict]] = {}

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        attempts = self.audit.setdefault(entry.id, [])
        slot_id = ((entry.after_jsonb or {}).get("hold") or {}).get("slot_id")
        if not slot_id:
            error = "calendar entry missing after-proof slot_id"
            attempts.append({"ok": False, "error": error})
            raise RuntimeError(error)
        self._calendar.release(slot_id)
        attempts.append({"ok": True})

    def restore_before(self, entry: LedgerEntry) -> None:
        raise RuntimeError(
            "calendar slot holds are compensatable; "
            "restore_before() must never be called")


class NotesExecutor:
    """Restore the notes map captured before the add (reversible)."""

    def __init__(self, notes, faults: Optional[FaultConfig] = None):
        self._notes = notes
        self.audit: dict[Any, list[dict]] = {}

    def restore_before(self, entry: LedgerEntry) -> None:
        attempts = self.audit.setdefault(entry.id, [])
        notes = (entry.before_jsonb or {}).get("notes")
        if notes is None:
            error = "notes entry without before-proof notes map"
            attempts.append({"ok": False, "error": error})
            raise RuntimeError(error)
        self._notes.notes = {nid: dict(n) for nid, n in notes.items()}
        attempts.append({"ok": True})

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        raise RuntimeError(
            "notes.add entries are reversible; compensate() never called")
