"""Tool Safety Registry — the YAML taxonomy that classifies agent tools.

The seed registry ships as package data (tools.yaml next to this module).
`wrap()` consults a Registry to decide the ledger class for each call:
known tools record their registry class; unknown tools are recorded as
class "unknown" with a logged warning.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Mapping, Optional

import yaml

log = logging.getLogger(__name__)

ALLOWED_CLASSES = ("reversible", "compensatable", "irreversible")


@dataclass(frozen=True)
class ToolSpec:
    tool_name: str
    entry_class: str
    compensation: dict
    snapshot_capable: bool = False


class Registry:
    """An immutable-ish lookup of tool name -> ToolSpec."""

    def __init__(self, tools: Optional[Mapping[str, ToolSpec]] = None):
        self._tools: dict[str, ToolSpec] = dict(tools or {})

    @classmethod
    def from_mapping(cls, data: Mapping) -> "Registry":
        tools = {}
        for name, spec in (data.get("tools") or {}).items():
            entry_class = spec["class"]
            if entry_class not in ALLOWED_CLASSES:
                raise ValueError(
                    f"tool {name!r}: invalid class {entry_class!r}; "
                    f"allowed: {ALLOWED_CLASSES}"
                )
            tools[name] = ToolSpec(
                tool_name=name,
                entry_class=entry_class,
                compensation=dict(spec.get("compensation") or {"type": "none"}),
                snapshot_capable=bool(spec.get("snapshot_capable", False)),
            )
        return cls(tools)

    @classmethod
    def from_yaml(cls, path=None) -> "Registry":
        if path is None:
            text = (
                resources.files(__package__)
                .joinpath("tools.yaml")
                .read_text(encoding="utf-8")
            )
        else:
            text = Path(path).read_text(encoding="utf-8")
        return cls.from_mapping(yaml.safe_load(text))

    def lookup(self, tool_name: str) -> Optional[ToolSpec]:
        return self._tools.get(tool_name)

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())


_default: Optional[Registry] = None
_default_lock = threading.Lock()


def load_registry(path=None) -> Registry:
    """Load a Registry; path=None loads the shipped seed registry."""
    return Registry.from_yaml(path)


def default_registry() -> Registry:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = load_registry()
    return _default


def lookup(tool_name: str) -> Optional[ToolSpec]:
    """Look up a tool in the shipped seed registry."""
    return default_registry().lookup(tool_name)
