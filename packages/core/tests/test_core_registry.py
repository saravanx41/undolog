"""Task 1.3 (parts 1-3): seed registry, wrap() taxonomy validation, warnings."""
import logging

from sqlmodel import Session, select

from undolog_core import registry as reg
from undolog_core.ledger import Ledger
from undolog_core.models import LedgerEntry


def test_seed_registry_loads_exactly_ten_tools():
    r = reg.load_registry()
    assert len(r) == 10


def test_gmail_send_is_irreversible():
    spec = reg.load_registry().lookup("gmail.send")
    assert spec is not None
    assert spec.entry_class == "irreversible"
    assert spec.compensation["type"] == "none"


def test_seed_covers_all_three_classes():
    classes = {spec.entry_class for spec in reg.load_registry()}
    assert {"reversible", "compensatable", "irreversible"} <= classes


def test_lookup_unknown_tool_returns_none():
    assert reg.load_registry().lookup("definitely.not.a.tool") is None


def test_wrap_records_registry_class_for_known_tool(engine):
    ledger = Ledger(engine)
    bound = ledger.for_thread("t-reg-known")

    @bound.wrap(tool_name="stripe.create_charge")
    def charge(amount):
        return f"ch_{amount}"

    charge(100)
    with Session(engine) as session:
        row = session.exec(select(LedgerEntry).where(LedgerEntry.thread_id == "t-reg-known")).one()
    assert row.class_ == "compensatable"


def test_wrap_unknown_tool_records_unknown_and_warns(engine, caplog):
    ledger = Ledger(engine)
    bound = ledger.for_thread("t-reg-unknown")

    with caplog.at_level(logging.WARNING):
        @bound.wrap(tool_name="mystery.tool")
        def call():
            return 1

        assert call() == 1

    with Session(engine) as session:
        row = session.exec(select(LedgerEntry).where(LedgerEntry.thread_id == "t-reg-unknown")).one()
    assert row.class_ == "unknown"
    assert any("mystery.tool" in rec.getMessage() for rec in caplog.records)
