"""Task 2.1: freeze — cross-connection lock/flag that blocks wrapped calls."""
import pytest
from sqlmodel import Session, select

from undolog_core import FrozenError, Ledger
from undolog_core.models import LedgerEntry


@pytest.fixture(scope="module")
def ledger(engine):
    return Ledger(engine)


def _wrapped_call(ledger, thread_id, box):
    bound = ledger.for_thread(thread_id)

    @bound.wrap
    def act():
        box.append(True)
        return "ok"

    return act


def test_frozen_thread_rejects_wrapped_call(ledger):
    ledger.freeze("t-fz-1")
    try:
        box = []
        act = _wrapped_call(ledger, "t-fz-1", box)
        with pytest.raises(FrozenError):
            act()
        assert box == []  # fn never executed
        with Session(ledger.engine) as session:
            rows = session.exec(
                select(LedgerEntry).where(LedgerEntry.thread_id == "t-fz-1")
            ).all()
        assert rows == []  # no applied row (no row at all)
    finally:
        ledger.unfreeze("t-fz-1")


def test_freeze_visible_cross_connection(engine, ledger):
    other = Ledger(engine)
    ledger.freeze("t-fz-2")
    try:
        box = []
        act = _wrapped_call(other, "t-fz-2", box)
        with pytest.raises(FrozenError):
            act()
        assert box == []
    finally:
        ledger.unfreeze("t-fz-2")


def test_unfreeze_restores_execution(ledger):
    ledger.freeze("t-fz-3")
    ledger.unfreeze("t-fz-3")

    box = []
    act = _wrapped_call(ledger, "t-fz-3", box)
    assert act() == "ok"
    assert box == [True]
    with Session(ledger.engine) as session:
        rows = session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == "t-fz-3")
        ).all()
    assert len(rows) == 1
    assert rows[0].status == "applied"


def test_concurrent_second_freeze_raises_try_lock(ledger, engine):
    ledger.freeze("t-fz-4")
    try:
        other = Ledger(engine)
        with pytest.raises(FrozenError, match="already frozen"):
            other.freeze("t-fz-4")
        # re-freezing from the holder is a clean no-op
        ledger.freeze("t-fz-4")
    finally:
        ledger.unfreeze("t-fz-4")
    # after unfreeze someone else can freeze
    other = Ledger(engine)
    other.freeze("t-fz-4")
    other.unfreeze("t-fz-4")
