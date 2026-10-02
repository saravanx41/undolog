"""Shared Postgres fixtures for undolog-core tests.

Every test module gets its own schema (test_<module>_<pid>_<rand>) so the
suite is repeatable against the shared local Postgres. The schema is created,
migrated to head with alembic, and dropped after the module finishes.
"""
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://undolog:undolog@localhost:5432/undolog",
)
ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"


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
