"""freeze_agent / rollback_agent helpers operating on a LangGraph config."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlmodel import Session, select

from undolog_core import EntryStatus, FrozenError, LedgerEntry
from undolog_langgraph import (
    SnapshotAdapter,
    freeze_agent,
    rollback_agent,
    thread_id_from_config,
    unfreeze_agent,
    wrap_tool,
)
from langgraph.prebuilt import create_react_agent

from conftest import make_world_snapshot
from scripted_model import ScriptedModel


def _make_charge_tool(world, ledger):
    snapshot = make_world_snapshot(world)

    @wrap_tool(ledger, tool_name="stripe.create_charge", adapter=SnapshotAdapter(snapshot))
    def charge(amount: int, config: RunnableConfig) -> dict:
        """Charge the customer's card."""
        world["charges"].append(amount)
        return {"amount": amount, "charge_id": f"ch_{len(world['charges'])}"}

    return charge


def _make_graph(world, ledger, checkpointer, amounts, thread_id):
    charge = _make_charge_tool(world, ledger)
    steps = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "charge", "args": {"amount": a}, "id": f"c{i}"}
                for i, a in enumerate(amounts)
            ],
        )
    ]
    graph = create_react_agent(
        ScriptedModel(steps=steps), [charge], checkpointer=checkpointer
    )
    return graph, {"configurable": {"thread_id": thread_id}}


class _RefundExecutor:
    def __init__(self, world):
        self.world = world
        self.keys = []

    def restore_before(self, entry):  # pragma: no cover - wrong protocol path
        raise AssertionError("compensatable entries must be compensated, not restored")

    def compensate(self, entry, idempotency_key):
        self.keys.append(idempotency_key)
        self.world["charges"].remove(entry.after_jsonb["result"]["amount"])


def test_thread_id_from_config():
    assert thread_id_from_config({"configurable": {"thread_id": "t1"}}) == "t1"
    with pytest.raises(Exception, match="thread_id"):
        thread_id_from_config({"configurable": {}})
    with pytest.raises(Exception, match="configurable"):
        thread_id_from_config({})


def test_freeze_agent_freezes_ledger_thread(ledger, world, checkpointer):
    graph, cfg = _make_graph(world, ledger, checkpointer, [10], "freeze-agent")
    freeze_agent(ledger, graph, cfg)
    try:
        charge = _make_charge_tool(world, ledger)
        with pytest.raises(FrozenError):
            charge(99, config=cfg)
        assert world["charges"] == []
    finally:
        unfreeze_agent(ledger, graph, cfg)
    # unfrozen again: wrapped tool works
    charge = _make_charge_tool(world, ledger)
    charge(7, config=cfg)
    assert world["charges"] == [7]


def test_freeze_agent_requires_checkpointer(ledger, world):
    charge = _make_charge_tool(world, ledger)
    graph_no_cp = create_react_agent(ScriptedModel(steps=[]), [charge])
    cfg = {"configurable": {"thread_id": "no-cp"}}
    with pytest.raises(Exception, match="checkpointer"):
        freeze_agent(ledger, graph_no_cp, cfg)


def test_rollback_agent_compensates_and_unfreezes(ledger, engine, world, checkpointer):
    thread_id = "rollback-agent"
    graph, cfg = _make_graph(world, ledger, checkpointer, [10, 20, 30], thread_id)
    out = graph.invoke({"messages": [HumanMessage("charge thrice")]}, cfg)
    assert out["messages"][-1].content == "Recovered: run complete."
    # ToolNode runs the three calls concurrently, so list order is not
    # call order.
    assert sorted(world["charges"]) == [10, 20, 30]

    with Session(engine) as s:
        rows = s.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all()
    assert [r.seq for r in rows] == [1, 2, 3]
    assert all(r.status == EntryStatus.APPLIED for r in rows)

    executor = _RefundExecutor(world)
    report = rollback_agent(
        ledger, graph, cfg, to_seq=0, executors={"stripe.create_charge": executor}
    )
    assert report.complete
    assert sorted(i.seq for i in report.compensated) == [1, 2, 3]
    assert [i.seq for i in report.restored] == []
    assert report.blast_radius == []
    # deterministic idempotency keys, one per undone entry
    assert len(set(executor.keys)) == 3
    assert world["charges"] == []

    with Session(engine) as s:
        rows = s.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all()
    assert [r.status for r in rows] == [
        EntryStatus.COMPENSATED,
        EntryStatus.COMPENSATED,
        EntryStatus.COMPENSATED,
    ]
    # freeze released by rollback: thread usable again
    charge = _make_charge_tool(world, ledger)
    charge(5, config=cfg)
    assert world["charges"] == [5]
