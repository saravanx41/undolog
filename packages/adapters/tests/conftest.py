"""Shared fixtures for undolog-adapters tests.

Postgres-backed tests reuse the core pattern: every test module gets its own
schema (test_<module>_<pid>_<rand>) created, migrated with alembic, and
dropped afterwards. A raw-psycopg fixture gives the adapters a connection on
that schema plus a scratch ``accounts`` table for row-snapshot tests.
"""
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://undolog:undolog@localhost:5432/undolog",
)
ALEMBIC_DIR = (
    Path(__file__).resolve().parents[2] / "core" / "alembic"
)


def _migrate(schema: str) -> None:
    admin = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    os.environ["UNDOLOG_SCHEMA"] = schema
    try:
        command.upgrade(cfg, "head")
    finally:
        del os.environ["UNDOLOG_SCHEMA"]


def _drop(schema: str) -> None:
    admin = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture(scope="module")
def schema(request):
    name = (
        f"test_{Path(request.module.__file__).stem}_"
        f"{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    _migrate(name)
    yield name
    _drop(name)


@pytest.fixture(scope="module")
def engine(schema):
    eng = create_engine(DATABASE_URL, poolclass=NullPool)

    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute(f'SET search_path TO "{schema}"')
        cur.close()

    yield eng
    eng.dispose()


def connect(schema: str) -> psycopg.Connection:
    """Fresh psycopg connection bound to the module schema."""
    url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(
        url,
        options=f'-c search_path="{schema}"',
        row_factory=psycopg.rows.dict_row,
    )


@pytest.fixture()
def pg_conn(schema):
    """Schema-bound psycopg connection plus a fresh scratch accounts table."""
    conn = connect(schema)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id       int PRIMARY KEY,
            owner    text NOT NULL,
            balance  int  NOT NULL,
            version  int  NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("TRUNCATE accounts")
    conn.execute(
        "INSERT INTO accounts (id, owner, balance) "
        "SELECT i, 'user-' || i, 1000 FROM generate_series(1, 50) AS g(i)"
    )
    conn.commit()
    yield conn
    conn.close()
