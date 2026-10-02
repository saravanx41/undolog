"""wrap_tool: LangGraph thread_id -> ledger thread binding."""
import pytest
from langchain_core.runnables import RunnableConfig
from sqlmodel import Session, select

from undolog_core import EntryClass, EntryStatus, FrozenError, LedgerEntry
from undolog_langgraph import SnapshotAdapter, wrap_tool

from conftest import make_world_snapshot


def _rows(engine, thread_id):
    with Session(engine) as s:
        return s.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()


def test_wrapped_tool_lands_in_ledger_with_langgraph_thread_id(ledger, engine, world):
    snapshot = make_world_snapshot(world)
    cfg = {"configurable": {"thread_id": "wrap-basic"}}

    @wrap_tool(ledger, tool_name="cart.add_item", adapter=SnapshotAdapter(snapshot))
    def add_item(item: str, qty: int, config: RunnableConfig) -> str:
        """Add an item to the cart."""
        world["cart"][item] = world["cart"].get(item, 0) + qty
        return f"added {qty}x {item}"

    assert add_item("book", 2, config=cfg) == "added 2x book"
    assert world["cart"] == {"book": 2}

    rows = _rows(engine, "wrap-basic")
    assert len(rows) == 1
    row = rows[0]
    assert row.seq == 1
    assert row.tool_name == "cart.add_item"
    assert row.status == EntryStatus.APPLIED
    assert row.class_ == EntryClass.REVERSIBLE  # from the test registry
    assert row.before_jsonb["cart"] == {}
    assert row.after_jsonb["result"] == "added 2x book"


def test_wrap_tool_direct_call_form(ledger, engine, world):
    snapshot = make_world_snapshot(world)
    cfg = {"configurable": {"thread_id": "wrap-direct"}}

    def add_item(item: str, config: RunnableConfig) -> str:
        """Add an item."""
        world["cart"][item] = 1
        return "ok"

    wrapped = wrap_tool(
        ledger, add_item, tool_name="cart.add_item", adapter=SnapshotAdapter(snapshot)
    )
    assert wrapped("pen", config=cfg) == "ok"
    assert len(_rows(engine, "wrap-direct")) == 1


def test_wrapped_tool_records_failure_and_reraises(ledger, engine, world):
    snapshot = make_world_snapshot(world)
    cfg = {"configurable": {"thread_id": "wrap-fail"}}

    @wrap_tool(ledger, tool_name="cart.add_item", adapter=SnapshotAdapter(snapshot))
    def add_item(item: str, config: RunnableConfig) -> str:
        """Add an item."""
        raise ValueError("boom")

    with pytest.raises(ValueError):
        add_item("book", config=cfg)
    rows = _rows(engine, "wrap-fail")
    assert len(rows) == 1
    assert rows[0].status == EntryStatus.FAILED
    assert rows[0].after_jsonb is None


def test_wrapped_tool_requires_langgraph_thread_id(ledger, world):
    snapshot = make_world_snapshot(world)

    @wrap_tool(ledger, tool_name="cart.add_item", adapter=SnapshotAdapter(snapshot))
    def add_item(item: str, config: RunnableConfig) -> str:
        """Add an item."""
        return "ok"

    with pytest.raises(Exception, match="RunnableConfig"):
        add_item("book")  # no RunnableConfig injected


def test_wrapped_tool_on_frozen_thread_raises_before_side_effect(ledger, engine, world):
    snapshot = make_world_snapshot(world)
    cfg = {"configurable": {"thread_id": "wrap-frozen"}}

    @wrap_tool(ledger, tool_name="cart.add_item", adapter=SnapshotAdapter(snapshot))
    def add_item(item: str, config: RunnableConfig) -> str:
        """Add an item."""
        world["cart"][item] = 1
        return "ok"

    assert add_item("book", config=cfg) == "ok"
    ledger.freeze("wrap-frozen")
    try:
        with pytest.raises(FrozenError):
            add_item("pen", config=cfg)
        assert world["cart"] == {"book": 1}  # fn never ran
        assert len(_rows(engine, "wrap-frozen")) == 1  # no new row
    finally:
        ledger.unfreeze("wrap-frozen")
