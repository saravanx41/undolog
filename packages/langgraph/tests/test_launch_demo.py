"""Launch-demo acceptance scenario (Task 3.1), in-process.

A ReAct agent with wrapped tools runs against in-memory world state. After
two good tool calls it goes rogue: it purges the cart and charges $999, and
the model then raises RogueSignal. The supervisor (not the agent author)
interrupts the graph at the checkpoint boundary, freezes the thread, rolls
the ledger back to the last good seq, and resumes the graph from the
interrupted checkpoint — which persists in PostgresSaver across connections.
"""
import copy
import inspect
import os

import psycopg
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlmodel import Session, select

from undolog_core import EntryStatus, FrozenError, LedgerEntry
from undolog_langgraph import (
    SnapshotAdapter,
    freeze_agent,
    rollback_agent,
    wrap_tool,
)
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt import create_react_agent

from scripted_model import RogueSignal, ScriptedModel

THREAD_ID = "launch-demo"


# ---------------------------------------------------------------------------
# Agent-author code. The ONLY change vs. a plain LangGraph agent is that the
# tool functions are passed through wrap_tool(); the graph construction below
# contains no freeze/rollback logic (asserted at the end of the test).
#
# Each tool snapshots only the state slice it owns: ToolNode executes the
# tool calls of one turn concurrently, so a whole-world snapshot would race
# with a sibling tool's effect.
# ---------------------------------------------------------------------------

def _slice(world, *keys):
    return lambda: {k: copy.deepcopy(world[k]) for k in keys}


def build_tools(world, ledger):
    @wrap_tool(ledger, tool_name="cart.add_item",
               adapter=SnapshotAdapter(_slice(world, "cart")))
    def add_item(item: str, qty: int, config: RunnableConfig) -> str:
        """Add an item to the shopping cart."""
        world["cart"][item] = world["cart"].get(item, 0) + qty
        return f"added {qty}x {item}"

    @wrap_tool(ledger, tool_name="notes.set",
               adapter=SnapshotAdapter(_slice(world, "notes")))
    def set_note(key: str, value: str, config: RunnableConfig) -> str:
        """Set a note."""
        world["notes"][key] = value
        return "noted"

    @wrap_tool(ledger, tool_name="cart.purge",
               adapter=SnapshotAdapter(_slice(world, "cart", "notes")))
    def purge(config: RunnableConfig) -> str:
        """Empty the cart and all notes."""
        world["cart"].clear()
        world["notes"].clear()
        return "purged"

    @wrap_tool(ledger, tool_name="stripe.create_charge",
               adapter=SnapshotAdapter(_slice(world, "charges")))
    def charge(amount: int, config: RunnableConfig) -> dict:
        """Charge the customer's card."""
        world["charges"].append(amount)
        return {"amount": amount, "charge_id": f"ch_{len(world['charges'])}"}

    return [add_item, set_note, purge, charge]


def build_agent(world, ledger, checkpointer, model):
    tools = build_tools(world, ledger)
    return create_react_agent(model, tools, checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Rollback executors (supervisor side).
# ---------------------------------------------------------------------------

class RestoreWorldExecutor:
    """Reversible tools: restore the state slice from before-proof."""

    def __init__(self, world):
        self.world = world

    def restore_before(self, entry):
        for key, value in entry.before_jsonb.items():
            self.world[key] = copy.deepcopy(value)

    def compensate(self, entry, idempotency_key):  # pragma: no cover
        raise AssertionError("reversible entries must be restored, not compensated")


class RefundExecutor:
    """stripe.create_charge: refund the charged amount."""

    def __init__(self, world):
        self.world = world
        self.keys = []

    def restore_before(self, entry):  # pragma: no cover
        raise AssertionError("compensatable entries must be compensated, not restored")

    def compensate(self, entry, idempotency_key):
        self.keys.append(idempotency_key)
        amount = entry.after_jsonb["result"]["amount"]
        if amount in self.world["charges"]:
            self.world["charges"].remove(amount)


def _ledger_rows(engine, thread_id):
    with Session(engine) as s:
        return s.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all()


def test_launch_demo_rollback_resume(engine, ledger, world, checkpointer, schema):
    # The scripted model is the scenario harness (the "misbehaving LLM"):
    # two good turns, then it goes rogue by raising RogueSignal.
    steps = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "add_item", "args": {"item": "book", "qty": 2}, "id": "a1"},
                {"name": "set_note", "args": {"key": "gift", "value": "wrap"}, "id": "a2"},
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "purge", "args": {}, "id": "r1"},
                {"name": "charge", "args": {"amount": 999}, "id": "r2"},
            ],
        ),
        RogueSignal("agent went rogue"),
    ]
    graph = build_agent(world, ledger, checkpointer, ScriptedModel(steps=steps))
    cfg = {"configurable": {"thread_id": THREAD_ID}}

    # -- run 1: start; the graph interrupts before the (first) agent node.
    graph.invoke({"messages": [HumanMessage("order a book, gift-wrapped")]}, cfg,
                 interrupt_before=["agent"])
    assert graph.get_state(cfg).next == ("agent",)

    # -- run 2: good turn — add_item + set_note land in the ledger. (ToolNode
    # executes tool calls concurrently, so seq order is not call order.)
    graph.invoke(None, cfg, interrupt_before=["agent"])
    rows = _ledger_rows(engine, THREAD_ID)
    assert {r.seq for r in rows} == {1, 2}
    assert {r.tool_name for r in rows} == {"cart.add_item", "notes.set"}
    assert all(r.status == EntryStatus.APPLIED for r in rows)
    assert world["cart"] == {"book": 2}

    # -- run 3: the agent goes rogue — purge + $999 charge are APPLIED, then
    # the graph interrupts again before the model's next (poisoned) turn.
    graph.invoke(None, cfg, interrupt_before=["agent"])
    rows = _ledger_rows(engine, THREAD_ID)
    assert {r.seq for r in rows} == {1, 2, 3, 4}
    assert all(r.status == EntryStatus.APPLIED for r in rows)
    assert {r.tool_name for r in rows if r.seq > 2} == {
        "cart.purge", "stripe.create_charge",
    }
    assert world["cart"] == {} and world["notes"] == {}
    assert world["charges"] == [999]
    assert graph.get_state(cfg).next == ("agent",)  # interrupted, clean boundary

    # The checkpointer persisted the interrupted run (fresh connection).
    url = os.environ.get("DATABASE_URL",
                         "postgresql://undolog:undolog@localhost:5432/undolog")
    conn2 = psycopg.connect(url, autocommit=True)
    try:
        conn2.execute(f'SET search_path TO "{schema}"')
        remote = PostgresSaver(conn2).get_tuple(cfg)
        assert remote is not None
        assert len(remote.checkpoint["channel_values"]["messages"]) == 7
    finally:
        conn2.close()

    # -- freeze: further side effects on this thread are refused.
    freeze_agent(ledger, graph, cfg)
    wrapped_charge = build_tools(world, ledger)[3]
    with pytest.raises(FrozenError):
        wrapped_charge(100, config=cfg)
    assert world["charges"] == [999]  # refused before fn ran
    assert len(_ledger_rows(engine, THREAD_ID)) == 4  # and before any row write

    # -- rollback to the ledger seq of the last good action (seq 2).
    refund = RefundExecutor(world)
    report = rollback_agent(
        ledger, graph, cfg, to_seq=2,
        executors={
            "stripe.create_charge": refund,
            "cart.purge": RestoreWorldExecutor(world),
        },
    )
    assert report.complete
    assert {i.tool_name for i in report.restored} == {"cart.purge"}
    assert {i.tool_name for i in report.compensated} == {"stripe.create_charge"}
    assert {i.seq for i in report.restored + report.compensated} == {3, 4}
    assert report.blast_radius == []
    # compensated effects are gone from world state; good effects remain.
    assert world["cart"] == {"book": 2}
    assert world["notes"] == {"gift": "wrap"}
    assert world["charges"] == []
    rows = _ledger_rows(engine, THREAD_ID)
    assert [r.status for r in rows] == [
        EntryStatus.APPLIED, EntryStatus.APPLIED,
        EntryStatus.COMPENSATED, EntryStatus.COMPENSATED,
    ]
    assert len(set(refund.keys)) == 1  # deterministic idempotency key

    # -- resume the interrupted graph from the persisted checkpoint. The
    # model's rogue turn raises once; the retry (post-rollback) completes.
    with pytest.raises(RogueSignal):
        graph.invoke(None, cfg)
    out = graph.invoke(None, cfg)
    assert graph.get_state(cfg).next == ()
    assert out["messages"][-1].content == "Recovered: run complete."

    # world stayed clean through the resume; no new ledger rows (the
    # recovered turn made no tool calls).
    assert world["cart"] == {"book": 2}
    assert world["charges"] == []
    assert len(_ledger_rows(engine, THREAD_ID)) == 4

    # The agent author's graph code contains no freeze/rollback logic — the
    # only undolog surface it touches is wrap_tool.
    src = inspect.getsource(build_agent) + inspect.getsource(build_tools)
    assert "wrap_tool" in src
    for forbidden in ("freeze", "rollback", "RogueSignal", "interrupt"):
        assert forbidden not in src
