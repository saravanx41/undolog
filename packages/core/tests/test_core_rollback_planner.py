"""Task 1.3 (part 4): the rollback planner refuses irreversible rows."""
from sqlmodel import Session, select

from undolog_core.ledger import Ledger
from undolog_core.models import LedgerEntry


def _entry(engine, ledger, thread_id, tool_name):
    bound = ledger.for_thread(thread_id)

    @bound.wrap(tool_name=tool_name)
    def call():
        return "done"

    call()
    with Session(engine) as session:
        return session.exec(select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)).one()


def test_irreversible_row_yields_refuse_item_not_compensation(engine):
    ledger = Ledger(engine)
    entry = _entry(engine, ledger, "t-plan-irr", "gmail.send")

    plan = ledger.plan_rollback([entry])

    assert len(plan) == 1
    item = plan[0]
    assert item.action == "refuse"
    assert item.entry_class == "irreversible"
    assert item.tool_name == "gmail.send"
    assert "blast" in (item.reason or "").lower()
    # an irreversible row can NEVER yield a compensation step
    assert all(p.action != "compensate" for p in plan)


def test_compensatable_and_reversible_rows_yield_compensation(engine):
    ledger = Ledger(engine)
    charge = _entry(engine, ledger, "t-plan-comp", "stripe.create_charge")
    block = _entry(engine, ledger, "t-plan-rev", "notion.append_block")

    plan = ledger.plan_rollback([charge, block])

    by_tool = {p.tool_name: p for p in plan}
    assert by_tool["stripe.create_charge"].action == "compensate"
    assert by_tool["stripe.create_charge"].compensation["type"] == "refund"
    assert by_tool["stripe.create_charge"].compensation["target"] == "stripe"
    assert by_tool["notion.append_block"].action == "compensate"
    assert by_tool["notion.append_block"].compensation["type"] == "restore_before"


def test_plan_is_reverse_order(engine):
    ledger = Ledger(engine)
    first = _entry(engine, ledger, "t-plan-order", "notion.append_block")
    second = _entry(engine, ledger, "t-plan-order-2", "notion.update_page")

    plan = ledger.plan_rollback([first, second])
    assert [p.tool_name for p in plan] == ["notion.update_page", "notion.append_block"]
