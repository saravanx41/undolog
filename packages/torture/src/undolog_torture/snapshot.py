"""Canonical world-state snapshot, hashing, and diffing.

`canonical_state` reduces the whole mock world to plain JSON-able data.
`hash_world` digests it with sha256 over sorted-key JSON so two runs with
the same seed produce the same hash. `world_diff` counts exactly what
changed between two canonical states (used to prove the corruption blast
radius: 20 CRM records, +3 charges, +30 emails).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .world import World


def canonical_state(world: World) -> dict[str, Any]:
    """Full canonical snapshot of gmail + stripe + crm state."""
    return {
        "gmail": {"emails": world.gmail.log()},
        "stripe": world.stripe.ledger(),
        "crm": {
            "records": {
                rec["id"]: rec["data"] for rec in world.crm.list_records()
            }
        },
        "calendar": {"holds": world.calendar.snapshot()},
        "notes": {"notes": world.notes.snapshot()},
    }


def hash_world(state: dict[str, Any]) -> str:
    """Deterministic content hash over a canonical state."""
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def world_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between two canonical states.

    Returns counts plus the concrete ids, sufficient for tests to assert
    the exact corruption blast radius.
    """
    crm_before = before["crm"]["records"]
    crm_after = after["crm"]["records"]
    changed_ids = sorted(
        rid for rid in crm_after if crm_before.get(rid) != crm_after[rid]
    )

    charges_before = before["stripe"]["charges"]
    charges_after = after["stripe"]["charges"]
    refunds_before = before["stripe"]["refunds"]
    refunds_after = after["stripe"]["refunds"]

    emails_before = before["gmail"]["emails"]
    emails_after = after["gmail"]["emails"]

    return {
        "crm_changed": len(changed_ids),
        "crm_changed_ids": changed_ids,
        "charges_added": len(charges_after) - len(charges_before),
        "charges_before": len(charges_before),
        "charges_after": len(charges_after),
        "refunds_added": len(refunds_after) - len(refunds_before),
        "emails_added": len(emails_after) - len(emails_before),
        "emails_before": len(emails_before),
        "emails_after": len(emails_after),
    }
