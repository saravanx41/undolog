"""undolog-core: ledger schema, interceptor, taxonomy, rollback engine.

Zero framework dependencies. LangGraph lives in packages/langgraph and is a
plugin, never a foundation — core must never import it.
"""
from .exceptions import FrozenError, UndologError
from .ledger import (
    Ledger,
    PlanItem,
    RollbackExecutor,
    RollbackItem,
    RollbackReport,
    ThreadLedger,
    next_seq,
)
from .models import (
    Compensation,
    EntryClass,
    EntryStatus,
    LedgerEntry,
    ThreadCounter,
    ThreadFreeze,
    ToolRegistryRow,
)
from .registry import Registry, ToolSpec, load_registry, lookup as lookup_tool

__all__ = [
    "Compensation",
    "EntryClass",
    "EntryStatus",
    "FrozenError",
    "Ledger",
    "LedgerEntry",
    "PlanItem",
    "Registry",
    "RollbackExecutor",
    "RollbackItem",
    "RollbackReport",
    "ThreadCounter",
    "ThreadFreeze",
    "ThreadLedger",
    "ToolRegistryRow",
    "ToolSpec",
    "UndologError",
    "load_registry",
    "lookup_tool",
    "next_seq",
]
