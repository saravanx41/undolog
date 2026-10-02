"""Task 1.1 (part 1): alembic upgrade head runs clean from a fresh schema."""
import sqlalchemy as sa
from sqlmodel import Session, select

from undolog_core.models import LedgerEntry


def test_upgrade_head_creates_all_tables(schema, engine):
    insp = sa.inspect(engine)
    for table in ("ledger_entries", "thread_counters", "compensations", "tool_registry"):
        assert insp.has_table(table, schema=schema), f"missing table {table}"


def test_ledger_entries_columns(schema, engine):
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("ledger_entries", schema=schema)}
    expected = {
        "id", "ts", "thread_id", "seq", "tool_name", "args_hash",
        "idempotency_key", "before_jsonb", "after_jsonb", "class",
        "compensation_ref", "status", "prompt_context_ref",
    }
    assert expected <= cols, expected - cols


def test_idempotency_key_and_thread_seq_are_unique(schema, engine):
    insp = sa.inspect(engine)
    unique_cols = {tuple(c["column_names"]) for c in insp.get_unique_constraints("ledger_entries", schema=schema)}
    assert ("idempotency_key",) in unique_cols
    assert ("thread_id", "seq") in unique_cols


def test_insert_and_select_roundtrip(engine):
    with Session(engine) as s:
        row = LedgerEntry(
            thread_id="t-mig", seq=1, tool_name="x.y", args_hash="h",
            idempotency_key="mig-key-1", class_="unknown", status="applied",
        )
        s.add(row)
        s.commit()
        found = s.exec(select(LedgerEntry).where(LedgerEntry.idempotency_key == "mig-key-1")).one()
        assert found.thread_id == "t-mig"
        assert found.class_ == "unknown"
        assert found.before_jsonb is None
