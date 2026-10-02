"""Task 1.1 (part 2): ledger_entries is append-only for the app role.

The migration creates a dedicated login role `undolog_app` with
SELECT + INSERT only. UPDATE/DELETE/TRUNCATE attempts must fail.
"""
import psycopg
import psycopg.errors
import pytest

APP_DSN = "postgresql://undolog_app:undolog_app@localhost:5432/undolog"

INSERT_SQL = (
    "INSERT INTO ledger_entries (thread_id, seq, tool_name, args_hash, "
    "idempotency_key, class, status) VALUES ('t', %s, 'x.y', 'h', %s, 'unknown', 'applied')"
)


def _app_conn(schema):
    conn = psycopg.connect(APP_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    return conn


def test_app_role_can_select_and_insert(schema):
    with _app_conn(schema) as conn:
        conn.execute(INSERT_SQL, (1, "ao-key-1"))
        keys = [r[0] for r in conn.execute("SELECT idempotency_key FROM ledger_entries")]
        assert keys == ["ao-key-1"]


def test_app_role_cannot_update(schema):
    with _app_conn(schema) as conn:
        conn.execute(INSERT_SQL, (2, "ao-key-2"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE ledger_entries SET status = 'compensated' WHERE idempotency_key = 'ao-key-2'")


def test_app_role_cannot_delete(schema):
    with _app_conn(schema) as conn:
        conn.execute(INSERT_SQL, (3, "ao-key-3"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM ledger_entries WHERE idempotency_key = 'ao-key-3'")


def test_app_role_cannot_truncate(schema):
    with _app_conn(schema) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("TRUNCATE ledger_entries")
