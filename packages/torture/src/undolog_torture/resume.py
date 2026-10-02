"""Repair / resume helpers for the SIGKILL scenarios (TS-06).

After a process dies mid-tool or mid-compensation, the in-memory mock world
died with it. These helpers rebuild world state from the ledger's proof
columns (the after_jsonb of stripe.create_charge rows carries the full
charge record) and produce a plain-language report of what a human must do.
"""
from __future__ import annotations

from sqlmodel import Session, select

from undolog_core.models import EntryStatus, LedgerEntry

from .world import World, build_worlds


def rebuild_stripe_world(engine, thread_id: str, seed: int = 1337) -> World:
    """Rebuild the mock world from the ledger: stripe charges come from
    after-proof; CRM/gmail come from the deterministic seed (they carry no
    compensation relevance for the TS-06 scenarios)."""
    world = build_worlds(seed)
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .where(LedgerEntry.tool_name == "stripe.create_charge")
            .where(LedgerEntry.status == EntryStatus.APPLIED)
            .order_by(LedgerEntry.seq)
        ).all()
    world.stripe.charges = []
    for row in rows:
        charge = dict(row.after_jsonb["charge"])
        charge["refunded"] = False
        world.stripe.charges.append(charge)
    return world


def human_report(engine, thread_id: str, to_seq: int = 0,
                 expected_calls: int | None = None) -> list[str]:
    """What a human must do after an interrupted run, in plain language."""
    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all()
    notes: list[str] = []
    if expected_calls is not None and len(rows) < expected_calls:
        missing = expected_calls - len(rows)
        notes.append(
            f"{missing} tool call(s) have no ledger row: interrupted "
            "mid-tool. Verify the external system manually before "
            "re-issuing; do not assume the side effect happened (no "
            "\"applied\" row exists)."
        )
    for row in rows:
        if row.seq > to_seq and row.status == EntryStatus.APPLIED:
            notes.append(
                f"seq {row.seq} ({row.tool_name}) is still applied above "
                f"to_seq={to_seq}: compensation incomplete; resume the "
                "rollback or compensate manually with the deterministic key."
            )
        elif row.status == EntryStatus.FAILED:
            notes.append(
                f"seq {row.seq} ({row.tool_name}) failed: inspect before "
                "resuming."
            )
    return notes
