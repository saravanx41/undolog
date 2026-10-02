"""LangGraph adapter: maps LangGraph thread_id -> undolog ledger thread.

A wrapped tool reads the RunnableConfig that LangGraph injects into tool
functions (declare a ``config: RunnableConfig`` parameter, as usual) and
binds the call to ``ledger.for_thread(config["configurable"]["thread_id"])``
at call time — one wrapped tool serves every thread.

Supervision flow (freeze/rollback) is driven from the outside via
``freeze_agent`` / ``rollback_agent``; the agent graph itself needs no
changes beyond wrapping its tools. Resuming an interrupted graph is plain
``graph.invoke(None, config)``: for static interrupts (``interrupt_before``
/ ``interrupt_after``) ``Command(resume=...)`` is not accepted by current
langgraph (1.2.x raises UnboundLocalError in PregelLoop._first), so the
adapter does not use it.
"""
from __future__ import annotations

import copy
import functools
from typing import Callable, Mapping, Optional

from undolog_core import Ledger, RollbackExecutor, RollbackReport, UndologError

__all__ = [
    "LangGraphConfigError",
    "SnapshotAdapter",
    "UndologLangGraph",
    "freeze_agent",
    "rollback_agent",
    "thread_id_from_config",
    "unfreeze_agent",
    "wrap_tool",
]


class LangGraphConfigError(UndologError):
    """The LangGraph config / graph does not provide what the adapter needs."""


def thread_id_from_config(config: Mapping) -> str:
    """Extract the LangGraph thread_id; it IS the undolog ledger thread_id."""
    try:
        configurable = config["configurable"]
    except (TypeError, KeyError):
        raise LangGraphConfigError(
            "expected a LangGraph RunnableConfig with a 'configurable' mapping"
        ) from None
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise LangGraphConfigError(
            "LangGraph config has no 'configurable.thread_id'; the ledger "
            "thread is keyed by the LangGraph thread_id"
        )
    return thread_id


def _extract_config(kwargs: dict) -> Mapping:
    config = kwargs.get("config")
    if config is None:
        # The tool may have named its RunnableConfig parameter something
        # other than ``config``; find whichever kwarg looks like one.
        for value in kwargs.values():
            if isinstance(value, Mapping) and isinstance(
                value.get("configurable"), Mapping
            ):
                config = value
                break
    if config is None:
        raise LangGraphConfigError(
            "wrap_tool needs the LangGraph RunnableConfig injected into the "
            "tool to key the ledger thread; declare a parameter like "
            "`config: RunnableConfig` on the tool function"
        )
    return config


def wrap_tool(
    ledger: Ledger,
    fn: Optional[Callable] = None,
    *,
    tool_name: Optional[str] = None,
    adapter: Optional[object] = None,
    compensate: Optional[Callable] = None,
):
    """Wrap a LangGraph tool fn so every call lands in the ledger.

    The LangGraph ``thread_id`` (from the injected RunnableConfig) is used
    as the ledger thread_id, so one wrapped tool works across threads and no
    graph code changes are needed. Usable as::

        @wrap_tool(ledger, tool_name="stripe.create_charge", adapter=...)
        def charge(amount: int, config: RunnableConfig) -> dict: ...

        wrapped = wrap_tool(ledger, fn, tool_name=...)
    """

    def decorator(f: Callable) -> Callable:
        resolved_tool = tool_name or f.__name__

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            thread_id = thread_id_from_config(_extract_config(kwargs))
            return ledger.for_thread(thread_id).wrap(
                f, adapter=adapter, tool_name=resolved_tool, compensate=compensate
            )(*args, **kwargs)

        return wrapper

    return decorator(fn) if fn is not None else decorator


def _require_checkpointer(graph: object) -> None:
    if getattr(graph, "checkpointer", None) is None:
        raise LangGraphConfigError(
            "agent graph was compiled without a checkpointer; freeze/rollback "
            "require one so the graph can be interrupted and resumed"
        )


def freeze_agent(ledger: Ledger, graph: object, config: Mapping) -> None:
    """Freeze the ledger thread of the agent run identified by ``config``.

    Wrapped tools called under this thread_id raise FrozenError before any
    side effect or ledger write until ``unfreeze_agent`` (or a completed
    ``rollback_agent``, which always releases its freeze).
    """
    _require_checkpointer(graph)
    ledger.freeze(thread_id_from_config(config))


def unfreeze_agent(ledger: Ledger, graph: object, config: Mapping) -> None:
    """Release a freeze held by this ledger (no-op if not frozen)."""
    _require_checkpointer(graph)
    ledger.unfreeze(thread_id_from_config(config))


def rollback_agent(
    ledger: Ledger,
    graph: object,
    config: Mapping,
    to_seq: int,
    executors: Optional[Mapping[str, RollbackExecutor]] = None,
    **rollback_kwargs,
) -> RollbackReport:
    """Roll the ledger thread of this agent run back to seq ``to_seq``.

    The thread is frozen for the duration and released even on failure, so
    the graph can afterwards be resumed from its interrupted checkpoint with
    ``graph.invoke(None, config)``.
    """
    _require_checkpointer(graph)
    return ledger.rollback(
        thread_id_from_config(config), to_seq, executors, **rollback_kwargs
    )


class SnapshotAdapter:
    """Proof adapter for tools backed by simple dict state.

    ``capture_before`` deep-copies whatever ``snapshot()`` returns (e.g. the
    piece of world state the tool mutates); ``capture_after`` records the
    tool's return value, which is what compensatable executors read back
    (``entry.after_jsonb["result"]``).
    """

    def __init__(self, snapshot: Callable[[], dict]):
        self._snapshot = snapshot

    def capture_before(self) -> dict:
        return copy.deepcopy(self._snapshot())

    def capture_after(self, result: object) -> dict:
        return {"result": result}


class UndologLangGraph:
    """Ledger bound to LangGraph: the ergonomic single entry point.

    ``integration = UndologLangGraph(ledger)`` then
    ``@integration.wrap_tool(...)`` on tool functions and
    ``integration.freeze_agent(graph, config)`` /
    ``integration.rollback_agent(graph, config, to_seq, executors)``.
    """

    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def thread_id(self, config: Mapping) -> str:
        return thread_id_from_config(config)

    def wrap_tool(self, fn: Optional[Callable] = None, **kwargs) -> Callable:
        return wrap_tool(self.ledger, fn, **kwargs)

    def freeze_agent(self, graph: object, config: Mapping) -> None:
        freeze_agent(self.ledger, graph, config)

    def unfreeze_agent(self, graph: object, config: Mapping) -> None:
        unfreeze_agent(self.ledger, graph, config)

    def rollback_agent(
        self,
        graph: object,
        config: Mapping,
        to_seq: int,
        executors: Optional[Mapping[str, RollbackExecutor]] = None,
        **rollback_kwargs,
    ) -> RollbackReport:
        return rollback_agent(
            self.ledger, graph, config, to_seq, executors, **rollback_kwargs
        )
