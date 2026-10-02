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
    # Raw adapter spec from the tool file (base_url/auth/capture/restore/
    # compensate keys), or a dict of any other unknown keys. Core stores it
    # verbatim and does not interpret it; adapters do.
    adapter: Optional[dict] = None


class Registry:
    """An immutable-ish lookup of tool name -> ToolSpec."""

    def __init__(self, tools: Optional[Mapping[str, ToolSpec]] = None):
        self._tools: dict[str, ToolSpec] = dict(tools or {})

    @staticmethod
    def _make_spec(name: str, spec: Mapping) -> ToolSpec:
        entry_class = spec["class"]
        if entry_class not in ALLOWED_CLASSES:
            raise ValueError(
                f"tool {name!r}: invalid class {entry_class!r}; "
                f"allowed: {ALLOWED_CLASSES}"
            )
        if "adapter" in spec:
            # Explicit adapter spec (mapping or null), stored verbatim.
            adapter = spec["adapter"]
            if adapter is not None and not isinstance(adapter, dict):
                raise ValueError(
                    f"tool {name!r}: adapter spec must be a mapping or null"
                )
        else:
            # Unknown keys are preserved (not interpreted) so non-core
            # consumers can round-trip their own per-tool config.
            extras = {
                k: v
                for k, v in spec.items()
                if k not in ("class", "compensation", "snapshot_capable")
            }
            adapter = extras or None
        return ToolSpec(
            tool_name=name,
            entry_class=entry_class,
            compensation=dict(spec.get("compensation") or {"type": "none"}),
            snapshot_capable=bool(spec.get("snapshot_capable", False)),
            adapter=adapter,
        )

    @classmethod
    def from_mapping(cls, data: Mapping) -> "Registry":
        tools = {}
        for name, spec in (data.get("tools") or {}).items():
            tools[name] = cls._make_spec(name, spec)
        return cls(tools)

    @classmethod
    def from_dir(cls, registry_dir) -> "Registry":
        """Load one tool per *.yaml file; the filename stem is the tool name.

        Each file holds a single tool mapping (class/adapter/...). A nested
        ``tool:`` key overrides the filename as the tool name.
        """
        tools = {}
        for path in sorted(Path(registry_dir).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{path}: expected a mapping, got {type(data).__name__}")
            name = data.pop("tool", None) or path.stem
            tools[name] = cls._make_spec(name, data)
        return cls(tools)

    def merged(self, other: "Registry") -> "Registry":
        """A new Registry with other's tools overlaid on this one's."""
        tools = dict(self._tools)
        tools.update(other._tools)
        return Registry(tools)

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


def load_registry(path=None, registry_dir=None) -> Registry:
    """Load a Registry.

    path=None loads the shipped seed registry. registry_dir, when given, is
    a directory of per-tool *.yaml files merged OVER the seed (directory
    wins on conflicts).
    """
    registry = Registry.from_yaml(path)
    if registry_dir is not None:
        registry = registry.merged(Registry.from_dir(registry_dir))
    return registry


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
