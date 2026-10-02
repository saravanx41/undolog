"""Torture-test registry: the core seed registry plus the mock-world CRM tools.

The core seed (tools.yaml) stays untouched — it has no CRM entries, so the
mock world adds two: crm.update_record and crm.read_record, both class
"reversible" with a restore_before compensation recipe (the adapter captures
the full CRM state before each call, so any write can be restored).
"""
from __future__ import annotations

from undolog_core.registry import Registry, ToolSpec, load_registry

_CRM_TOOLS = {
    "crm.update_record": ToolSpec(
        tool_name="crm.update_record",
        entry_class="reversible",
        compensation={"type": "restore_before"},
        snapshot_capable=True,
    ),
    "crm.read_record": ToolSpec(
        tool_name="crm.read_record",
        entry_class="reversible",
        compensation={"type": "restore_before"},
        snapshot_capable=True,
    ),
    # Demo tools (Task 3.4) with minimal in-world fakes:
    "calendar.slot_hold": ToolSpec(
        tool_name="calendar.slot_hold",
        entry_class="compensatable",
        compensation={"type": "release_hold", "target": "calendar"},
        snapshot_capable=False,
    ),
    "notes.add": ToolSpec(
        tool_name="notes.add",
        entry_class="reversible",
        compensation={"type": "restore_before"},
        snapshot_capable=True,
    ),
}


def load_torture_registry() -> Registry:
    """Core seed registry + mock-CRM and demo tool specs (14 total)."""
    seed = load_registry()
    tools = {spec.tool_name: spec for spec in seed}
    tools.update(_CRM_TOOLS)
    return Registry(tools)
