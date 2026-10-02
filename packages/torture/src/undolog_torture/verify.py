"""Post-rollback verification helpers shared by TS-01 and the chaos runner.

Two levels:

* ``expected_post_rollback`` — computes the exact canonical world state a
  rollback *must* produce, given the scenario result and the report. Works
  even when capture faults left log-only/unknown rows that core refused to
  restore (the unrestored CRM residue is derived from the report, not
  guessed). The chaos runner hash-compares against this expectation.

* ``verify_rollback_to_33`` — the strict TS-01 checklist (spec-exact):
  50 gapless rows, CRM back to the #33 snapshot, exactly 3 refunds, 30
  corruption emails in the blast radius, gmail untouched by the rollback,
  net charges back to the pre-corruption count, zero double compensations.
"""
from __future__ import annotations

import copy
from typing import Any

from undolog_core.ledger import RollbackReport

from .agent import ScenarioResult
from .snapshot import canonical_state
from .world import World


def expected_post_rollback(result: ScenarioResult,
                           report: RollbackReport) -> dict[str, Any]:
    """Canonical world state expected after the rollback described by
    ``report``. Deterministic function of (result, report)."""
    exp = copy.deepcopy(result.final_state)
    rows_by_seq = {r.seq: r for r in result.rows}
    rows_by_id = {r.id: r for r in result.rows}

    # CRM: restores run newest-first and each restore rewrites every captured
    # record, so the last (oldest) successful restore wins wholesale. Rows
    # core refused (unknown class / no before-proof) leave their records
    # corrupted, exactly as captured in the final state.
    restored = [i.seq for i in report.restored
                if i.tool_name == "crm.update_record"]
    if restored:
        m = min(restored)
        exp["crm"]["records"] = copy.deepcopy(
            rows_by_seq[m].before_jsonb["records"])

    # Stripe: one refund per compensated charge entry, in execution order
    # (report order); the refunded flag flips on those charges.
    refunded_ids: set[str] = set()
    refunds = []
    for item in report.compensated:
        if item.tool_name != "stripe.create_charge":
            continue
        entry = rows_by_id[item.entry_id]
        ch = entry.after_jsonb["charge"]
        refunded_ids.add(ch["id"])
        refunds.append({
            "id": f"rf_{len(refunds) + 1}",
            "charge_id": ch["id"],
            "amount": ch["amount"],
            "idempotency_key": f"comp-{entry.id}",
        })
    for ch in exp["stripe"]["charges"]:
        if ch["id"] in refunded_ids:
            ch["refunded"] = True
    exp["stripe"]["refunds"] = refunds
    return exp


def verify_rollback_to_33(result: ScenarioResult, report: RollbackReport,
                          executors: dict[str, Any]) -> list[str]:
    """Strict TS-01 checklist; returns a list of problems (empty = pass)."""
    problems: list[str] = []
    rows = result.rows
    rows_by_seq = {r.seq: r for r in rows}

    # Row conservation: exactly 50, gapless, single thread.
    if [r.seq for r in rows] != list(range(1, 51)):
        problems.append(
            f"ledger rows not 1..50 gapless: "
            f"{[r.seq for r in rows][:5]}... ({len(rows)} rows)")

    if not report.complete:
        problems.append(f"rollback incomplete: failed={report.failed}")

    # CRM must be back to the pre-#34 snapshot.
    restored = [i.seq for i in report.restored
                if i.tool_name == "crm.update_record"]
    if restored:
        m = min(restored)
        expected_crm = rows_by_seq[m].before_jsonb["records"]
    else:
        expected_crm = result.final_state["crm"]["records"]
    if expected_crm != result.snapshot_33["crm"]["records"]:
        problems.append("CRM restored state is not the #33 snapshot")
    actual_crm = {rec["id"]: rec["data"]
                  for rec in result.world.crm.list_records()}
    if actual_crm != result.snapshot_33["crm"]["records"]:
        changed = sorted(rid for rid in actual_crm
                         if actual_crm[rid]
                         != result.snapshot_33["crm"]["records"][rid])
        problems.append(f"CRM not restored to #33 snapshot: {changed[:5]}")

    # Exactly 3 refunds, one per corruption charge.
    stripe = result.world.stripe
    compensated_charges = [i for i in report.compensated
                           if i.tool_name == "stripe.create_charge"]
    if len(compensated_charges) != 3:
        problems.append(f"expected 3 compensated charges, got "
                        f"{len(compensated_charges)}")
    if len(stripe.refunds) != 3:
        problems.append(f"expected exactly 3 refunds, got {len(stripe.refunds)}")

    # 30 corruption emails listed as irreversible in the report.
    gmail_items = [i for i in report.blast_radius
                   if i.tool_name == "gmail.send"]
    n_emails = sum((rows_by_seq[i.seq].after_jsonb or {}).get("sent", 1)
                   for i in gmail_items)
    if n_emails != 30:
        problems.append(f"blast radius covers {n_emails} emails, expected 30")

    # Rollback never touches gmail: log identical to the final state.
    if len(result.world.gmail.log()) != len(
            result.final_state["gmail"]["emails"]):
        problems.append("rollback altered the gmail log (irreversible!)")

    # Net charges back to the pre-corruption count and balance restored.
    snap_charges = result.snapshot_33["stripe"]["charges"]
    unrefunded = [c for c in stripe.charges if not c["refunded"]]
    if len(unrefunded) != len(snap_charges):
        problems.append(f"unrefunded charges {len(unrefunded)} != "
                        f"snapshot charges {len(snap_charges)}")
    if stripe.balance != sum(c["amount"] for c in snap_charges):
        problems.append(f"balance {stripe.balance} != snapshot balance "
                        f"{sum(c['amount'] for c in snap_charges)}")

    # Zero double-applied compensations (audit: <=1 success per entry).
    audit = getattr(executors.get("stripe.create_charge"), "audit", {})
    for entry_id, attempts in audit.items():
        oks = [a for a in attempts if a.get("ok")]
        if len(oks) > 1:
            problems.append(f"entry {entry_id} compensated "
                            f"{len(oks)} times")

    return problems
