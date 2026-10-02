"""Loader glue + the developer-facing register_tools() entry point.

``registry_dir`` defaults to the bundled public registry
(packages/adapters/registry/tools/) — one YAML file per tool, merged over
the core seed registry by the core loader.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Union

from undolog_core.ledger import Ledger
from undolog_core.registry import load_registry as _core_load_registry

from .rest import RestAdapter, RestError

# packages/adapters/registry/tools (this file lives in src/undolog_adapters).
DEFAULT_REGISTRY_DIR = (
    Path(__file__).resolve().parents[2] / "registry" / "tools"
)


def load_tool_registry(registry_dir: Optional[Union[str, Path]] = None):
    """Core seed registry merged with the per-tool files in registry_dir."""
    return _core_load_registry(
        registry_dir=str(registry_dir or DEFAULT_REGISTRY_DIR)
    )


def register_tools(
    ledger: Ledger,
    tool_or_list: Union[str, list, tuple],
    env: Optional[Mapping] = None,
    registry_dir: Optional[Union[str, Path]] = None,
) -> dict[str, Optional[RestAdapter]]:
    """Teach a Ledger about REST tools and hand back their adapters.

    Effect:

    * ``ledger.registry`` is replaced with the seed registry merged over
      ``registry_dir`` (dir wins), so wrap() records each tool's class.
    * Returns ``{tool_name: RestAdapter}`` — the same adapter object serves
      capture (``rt.bind(**kwargs)`` / ``rt.wrap_call(...)`` for wrap()) and
      rollback (pass the mapping, minus None entries, as ``executors=``).
    * Tools whose spec says ``adapter: null`` (e.g. slack.chat_postMessage)
      map to None: ledger + containment only, no HTTP ever happens.

    Auth env vars (GITHUB_TOKEN, AWS_SIGNATURE, ...) are read lazily from
    ``env`` (default os.environ) at the first HTTP call; a missing token
    raises ``RestError`` naming the tool and the env var.
    """
    names = [tool_or_list] if isinstance(tool_or_list, str) else list(tool_or_list)
    registry = load_tool_registry(registry_dir)
    ledger.registry = registry
    adapters: dict[str, Optional[RestAdapter]] = {}
    for name in names:
        spec = registry.lookup(name)
        if spec is None:
            raise RestError(
                f"tool {name!r} not found in registry dir "
                f"{registry_dir or DEFAULT_REGISTRY_DIR}"
            )
        adapter_spec = spec.adapter or {}
        if isinstance(adapter_spec, dict) and adapter_spec.get("type") == "rest":
            adapters[name] = RestAdapter(name, adapter_spec, env=env)
        else:
            adapters[name] = None
    return adapters
