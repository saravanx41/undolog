"""Task 1.1 (part 3): seq is gapless per thread under 100 concurrent inserts."""
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from undolog_core.ledger import next_seq
from undolog_core.models import LedgerEntry


def _insert_one(engine, thread_id, i):
    with Session(engine) as session:
        seq = next_seq(session, thread_id)
        session.add(
            LedgerEntry(
                thread_id=thread_id, seq=seq, tool_name="load.test",
                args_hash=f"h{i}", idempotency_key=f"gs-{thread_id}-{i}",
                class_="unknown", status="applied",
            )
        )
        session.commit()


def test_seq_gapless_under_100_concurrent_inserts(engine):
    with ThreadPoolExecutor(max_workers=100) as pool:
        list(pool.map(lambda i: _insert_one(engine, "thread-hot", i), range(100)))

    with Session(engine) as session:
        rows = session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == "thread-hot")
        ).all()

    assert len(rows) == 100
    seqs = sorted(r.seq for r in rows)
    assert seqs == list(range(1, 101)), "seqs must be exactly 1..100, no gaps or duplicates"


def test_seq_is_per_thread(engine):
    with Session(engine) as session:
        assert next_seq(session, "thread-a") == 1
        assert next_seq(session, "thread-b") == 1
        assert next_seq(session, "thread-a") == 2
        session.rollback()
