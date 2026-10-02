"""Postgres adapter tests: row-level FOR UPDATE snapshots and precise restore.

Acceptance:
* capture_before locks and snapshots exactly the tool's target rows, in the
  same transaction as the tool write;
* rollback restores the captured before-values exactly (UPDATE back, DELETE
  inserted rows, re-INSERT deleted rows), row-precise — never a table swap;
* under concurrent writers, writes to OTHER rows are never clobbered and a
  concurrent writer to the SAME row serializes on the row lock, after which
  restore still produces the exact captured before-values.
"""
import threading
import time
from uuid import uuid4

from undolog_core.ledger import Ledger

from undolog_adapters import PostgresAdapter, PostgresRestoreExecutor

from conftest import connect


def _read_row(conn, row_id):
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = %s", (row_id,)
    ).fetchone()
    conn.commit()
    return dict(row) if row is not None else None


def _make_executor(schema):
    return PostgresRestoreExecutor(connect=lambda: connect(schema))


# --------------------------------------------------------------------------
# Capture semantics
# --------------------------------------------------------------------------

def test_capture_before_locks_and_snapshots_target_rows(pg_conn, schema):
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[1, 2])
    before = adapter.capture_before()
    assert before["table"] == "accounts"
    assert before["pk"] == "id"
    assert before["before"]["1"]["balance"] == 1000
    assert before["before"]["2"]["owner"] == "user-2"
    # Other rows are not part of the proof.
    assert set(before["before"]) == {"1", "2"}

    # The row lock is actually held: NOWAIT from another session fails.
    outcome = []

    def try_lock():
        other = connect(schema)
        try:
            other.execute("SELECT 1 FROM accounts WHERE id = 1 FOR UPDATE NOWAIT")
            outcome.append("acquired")
        except Exception:
            outcome.append("blocked")
        finally:
            other.close()

    t = threading.Thread(target=try_lock)
    t.start()
    t.join(timeout=10)
    assert outcome == ["blocked"], "row 1 should be locked by capture_before"
    pg_conn.commit()


def test_capture_before_marks_absent_rows_for_inserts(pg_conn):
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[51])
    before = adapter.capture_before()
    assert before["before"] == {"51": None}, "absent row -> None (an insert)"
    pg_conn.commit()


def test_rows_may_be_a_callable_evaluated_per_capture(pg_conn):
    target = {"ids": [1]}

    def spec():
        return list(target["ids"])

    adapter = PostgresAdapter(pg_conn, table="accounts", rows=spec)
    before = adapter.capture_before()
    target["ids"] = [1, 2]
    after = adapter.capture_after(None)
    assert set(before["before"]) == {"1"}
    assert set(after["after"]) == {"1", "2"}
    pg_conn.commit()


# --------------------------------------------------------------------------
# Reversible restore: UPDATE / DELETE / INSERT
# --------------------------------------------------------------------------

def test_update_is_restored_exactly(engine, pg_conn, schema):
    ledger = Ledger(engine)
    tl = ledger.for_thread(f"pg-update-{uuid4().hex[:8]}")
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[1])

    @tl.wrap(adapter=adapter, tool_name="database.execute")
    def debit(conn):
        conn.execute(
            "UPDATE accounts SET balance = balance - 250 WHERE id = 1"
        )

    debit(pg_conn)
    adapter.commit()
    assert _read_row(pg_conn, 1)["balance"] == 750

    report = ledger.rollback(
        tl.thread_id, 0, {"database.execute": _make_executor(schema)}
    )
    assert report.complete
    assert _read_row(pg_conn, 1)["balance"] == 1000, "exact before-value"
    assert _read_row(pg_conn, 2)["balance"] == 1000, "other rows untouched"


def test_deleted_row_is_reinserted_with_before_values(engine, pg_conn, schema):
    ledger = Ledger(engine)
    tl = ledger.for_thread(f"pg-delete-{uuid4().hex[:8]}")
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[7])

    @tl.wrap(adapter=adapter, tool_name="database.execute")
    def remove(conn):
        conn.execute("DELETE FROM accounts WHERE id = 7")

    before = adapter.capture_before()  # capture outside wrap for comparison
    remove(pg_conn)
    adapter.commit()
    assert _read_row(pg_conn, 7) is None

    ledger.rollback(tl.thread_id, 0,
                    {"database.execute": _make_executor(schema)})
    restored = _read_row(pg_conn, 7)
    assert restored == before["before"]["7"], "re-inserted exactly"


def test_inserted_row_is_deleted_by_restore(engine, pg_conn, schema):
    ledger = Ledger(engine)
    tl = ledger.for_thread(f"pg-insert-{uuid4().hex[:8]}")
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[51])

    @tl.wrap(adapter=adapter, tool_name="database.execute")
    def create(conn):
        conn.execute(
            "INSERT INTO accounts (id, owner, balance) "
            "VALUES (51, 'user-51', 5000)"
        )

    create(pg_conn)
    adapter.commit()
    assert _read_row(pg_conn, 51)["balance"] == 5000

    ledger.rollback(tl.thread_id, 0,
                    {"database.execute": _make_executor(schema)})
    assert _read_row(pg_conn, 51) is None, "inserted row removed"


def test_restore_survives_row_deleted_after_the_tool_write(engine, pg_conn, schema):
    """A legitimate DELETE of the row after the tool write must not break
    restore: the before-proof re-INSERTs it."""
    ledger = Ledger(engine)
    tl = ledger.for_thread(f"pg-del2-{uuid4().hex[:8]}")
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[3])

    @tl.wrap(adapter=adapter, tool_name="database.execute")
    def touch(conn):
        conn.execute("UPDATE accounts SET version = version + 1 WHERE id = 3")

    touch(pg_conn)
    adapter.commit()
    # Concurrent legitimate effect: someone deletes row 3 afterwards.
    rogue = connect(schema)
    rogue.execute("DELETE FROM accounts WHERE id = 3")
    rogue.commit()
    rogue.close()

    ledger.rollback(tl.thread_id, 0,
                    {"database.execute": _make_executor(schema)})
    row = _read_row(pg_conn, 3)
    assert row is not None and row["version"] == 0, "before-proof wins"


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------

def _hammer(schema, row_ids, stop, barrier, counts):
    conn = connect(schema)
    try:
        barrier.wait(timeout=10)
        n = 0
        while not stop.is_set():
            for rid in row_ids:
                conn.execute(
                    "UPDATE accounts SET balance = balance + 1, "
                    "version = version + 1 WHERE id = %s",
                    (rid,),
                )
                conn.commit()
                n += 1
        counts.append(n)
    finally:
        conn.close()


def test_concurrent_hammers_do_not_break_exact_restore(engine, pg_conn, schema):
    """Threads hammer other rows while capture+write+rollback run; after
    rollback the tool row matches the captured before-values exactly and
    every hammered row kept exactly the writes it committed."""
    hammer_rows = list(range(100, 120))
    setup = connect(schema)
    setup.execute(
        "INSERT INTO accounts (id, owner, balance) "
        "SELECT i, 'hammer-' || i, 0 FROM generate_series(100, 119) AS g(i)"
    )
    setup.commit()
    setup.close()

    stop = threading.Event()
    barrier = threading.Barrier(5)
    counts: list[int] = []
    threads = [
        threading.Thread(target=_hammer, args=(schema, hammer_rows, stop, barrier, counts))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    barrier.wait(timeout=10)

    try:
        ledger = Ledger(engine)
        tl = ledger.for_thread(f"pg-hammer-{uuid4().hex[:8]}")
        adapter = PostgresAdapter(pg_conn, table="accounts", rows=[1, 2])

        @tl.wrap(adapter=adapter, tool_name="database.execute")
        def transfer(conn):
            conn.execute(
                "UPDATE accounts SET balance = balance - 100 WHERE id = 1"
            )
            conn.execute(
                "UPDATE accounts SET balance = balance + 100 WHERE id = 2"
            )

        before = adapter.capture_before()
        transfer(pg_conn)
        adapter.commit()
        report = ledger.rollback(
            tl.thread_id, 0, {"database.execute": _make_executor(schema)}
        )
        assert report.complete
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=30)
        assert all(not t.is_alive() for t in threads), "hammers must finish"

    for rid in (1, 2):
        assert _read_row(pg_conn, rid) == before["before"][str(rid)], (
            f"row {rid} restored exactly despite concurrent writers"
        )
    # Every hammer increment landed exactly once per committed write and
    # restore never touched hammer rows.
    conn = connect(schema)
    for rid in hammer_rows:
        row = conn.execute(
            "SELECT balance, version FROM accounts WHERE id = %s", (rid,)
        ).fetchone()
        assert row["balance"] == row["version"], (
            f"row {rid}: balance {row['balance']} vs version {row['version']} "
            "— restore clobbered a legitimate concurrent write"
        )
    conn.close()


def test_concurrent_same_row_writer_serializes_and_restore_is_exact(
    engine, pg_conn, schema
):
    """While the tool transaction holds the FOR UPDATE lock, a concurrent
    writer to the same row blocks; whichever order the writes land in,
    rollback's restore produces the exact captured before-values."""
    ledger = Ledger(engine)
    tl = ledger.for_thread(f"pg-lock-{uuid4().hex[:8]}")
    adapter = PostgresAdapter(pg_conn, table="accounts", rows=[5])

    entered = threading.Event()
    done = threading.Event()

    @tl.wrap(adapter=adapter, tool_name="database.execute")
    def debit(conn):
        entered.set()
        time.sleep(0.4)  # hold the row lock past capture_before
        conn.execute("UPDATE accounts SET balance = 555 WHERE id = 5")

    writer_result = []

    def rogue_writer():
        assert entered.wait(timeout=5)  # capture_before already holds the lock
        rogue = connect(schema)
        try:
            rogue.execute("UPDATE accounts SET balance = 9999 WHERE id = 5")
            rogue.commit()
            writer_result.append("written")
        finally:
            rogue.close()
            done.set()

    t = threading.Thread(target=rogue_writer)
    t.start()
    debit(pg_conn)
    assert entered.wait(timeout=5)
    # The lock is held: the rogue writer cannot commit yet.
    time.sleep(0.2)
    assert not done.is_set(), "row lock was not held during the tool txn"
    adapter.commit()  # releases the lock; rogue writer unblocks
    t.join(timeout=10)
    assert done.is_set()
    assert writer_result == ["written"]

    report = ledger.rollback(
        tl.thread_id, 0, {"database.execute": _make_executor(schema)}
    )
    assert report.complete
    row = _read_row(pg_conn, 5)
    assert row["balance"] == 1000, (
        "restored to the exact before-value even though a concurrent "
        "writer wrote the row after capture"
    )
