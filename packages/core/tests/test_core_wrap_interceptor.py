"""Task 1.2: the ledger.wrap interceptor."""
import pytest
from sqlmodel import Session, select

from undolog_core.ledger import Ledger
from undolog_core.models import LedgerEntry


@pytest.fixture(scope="module")
def ledger(engine):
    return Ledger(engine)


def _rows(engine, thread_id):
    with Session(engine) as session:
        return session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()


class DictAdapter:
    def capture_before(self):
        return {"state": "before"}

    def capture_after(self, result):
        return {"result": result}


class BadBeforeAdapter(DictAdapter):
    def capture_before(self):
        raise RuntimeError("capture_before exploded")


class BadAfterAdapter(DictAdapter):
    def capture_after(self, result):
        raise RuntimeError("capture_after exploded")


def test_wrap_plain_function_records_row(engine, ledger):
    bound = ledger.for_thread("t-wrap-plain")

    @bound.wrap
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    rows = _rows(engine, "t-wrap-plain")
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name.endswith("add")
    assert len(row.args_hash) == 64
    assert row.idempotency_key
    assert row.status == "applied"
    assert row.seq == 1
    assert row.before_jsonb is None
    assert row.after_jsonb is None


def test_wrap_as_decorator_factory_and_direct_call(engine, ledger):
    bound = ledger.for_thread("t-wrap-forms")
    adapter = DictAdapter()

    @bound.wrap(adapter=adapter)
    def double(x):
        return x * 2

    assert double(21) == 42

    def triple(x):
        return x * 3

    wrapped = bound.wrap(triple, adapter=adapter)
    assert wrapped(5) == 15

    assert len(_rows(engine, "t-wrap-forms")) == 2


def test_adapter_captures_are_stored(engine, ledger):
    bound = ledger.for_thread("t-wrap-adapter")
    adapter = DictAdapter()

    @bound.wrap(adapter=adapter, tool_name="notion.append_block")
    def append(text):
        return f"appended:{text}"

    assert append("hello") == "appended:hello"
    row = _rows(engine, "t-wrap-adapter")[0]
    assert row.before_jsonb == {"state": "before"}
    assert row.after_jsonb == {"result": "appended:hello"}
    assert row.status == "applied"
    assert row.log_only is False


def test_fn_exception_writes_failed_row_and_reraises(engine, ledger):
    bound = ledger.for_thread("t-wrap-fail")

    @bound.wrap
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        boom()

    rows = _rows(engine, "t-wrap-fail")
    assert len(rows) == 1
    assert rows[0].status == "failed"
    # no row may claim applied for a call that raised
    assert all(r.status != "applied" for r in rows)


def test_capture_before_failure_falls_back_to_log_only(engine, ledger):
    bound = ledger.for_thread("t-wrap-badbefore")

    @bound.wrap(adapter=BadBeforeAdapter())
    def ok():
        return 7

    assert ok() == 7  # fn still executes
    row = _rows(engine, "t-wrap-badbefore")[0]
    assert row.status == "applied"
    assert row.class_ == "unknown"
    assert row.log_only is True
    # before-proof was lost; after-proof was genuinely captured, so it is kept
    assert row.before_jsonb is None
    assert row.after_jsonb == {"result": 7}


def test_capture_before_failure_never_claims_unproven_after(engine, ledger):
    bound = ledger.for_thread("t-wrap-badafter")

    @bound.wrap(adapter=BadAfterAdapter())
    def ok():
        return 9

    assert ok() == 9
    rows = _rows(engine, "t-wrap-badafter")
    assert len(rows) == 1
    row = rows[0]
    # Invariant: an applied row must never assert after-proof that was never captured.
    assert row.status == "applied"
    assert row.after_jsonb is None
    assert row.log_only is True
    assert row.class_ == "unknown"
    unproven = [
        r for r in rows
        if r.status == "applied" and r.after_jsonb is not None
    ]
    assert unproven == []


def test_capture_before_failure_escalates_known_tool_class_to_unknown(engine, ledger):
    """Regression: registry class must be escalated to 'unknown' on capture failure."""
    bound = ledger.for_thread("t-wrap-esc-before")

    @bound.wrap(adapter=BadBeforeAdapter(), tool_name="notion.append_block")
    def act():
        return 1

    assert act() == 1
    row = _rows(engine, "t-wrap-esc-before")[0]
    # notion.append_block is 'reversible' in the registry; the capture
    # failure must escalate the row to 'unknown'.
    assert row.class_ == "unknown"
    assert row.log_only is True
    assert row.status == "applied"
    assert row.before_jsonb is None


def test_capture_after_failure_escalates_known_tool_class_to_unknown(engine, ledger):
    bound = ledger.for_thread("t-wrap-esc-after")

    @bound.wrap(adapter=BadAfterAdapter(), tool_name="stripe.create_charge")
    def act():
        return 2

    assert act() == 2
    row = _rows(engine, "t-wrap-esc-after")[0]
    # stripe.create_charge is 'compensatable' in the registry; the failed
    # capture_after must escalate the row to 'unknown' with NULL proof.
    assert row.class_ == "unknown"
    assert row.log_only is True
    assert row.status == "applied"
    assert row.before_jsonb == {"state": "before"}
    assert row.after_jsonb is None


def test_no_capture_failure_keeps_registry_class(engine, ledger):
    bound = ledger.for_thread("t-wrap-noesc")

    @bound.wrap(adapter=DictAdapter(), tool_name="notion.append_block")
    def act():
        return 3

    assert act() == 3
    row = _rows(engine, "t-wrap-noesc")[0]
    assert row.class_ == "reversible"
    assert row.log_only is False
