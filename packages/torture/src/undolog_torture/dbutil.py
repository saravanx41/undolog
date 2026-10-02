"""Postgres schema helpers shared by the test conftest, the chaos CLI, and
the TS-06 subprocess child (all live in packages/torture)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://undolog:undolog@localhost:5432/undolog",
)
ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "core" / "alembic"


def setup_schema(url: str, schema: str) -> None:
    """Create a fresh schema and migrate it to head."""
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["UNDOLOG_SCHEMA"] = schema
    try:
        command.upgrade(cfg, "head")
    finally:
        del os.environ["UNDOLOG_SCHEMA"]


def drop_schema(url: str, schema: str) -> None:
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


def engine_for_schema(url: str, schema: str):
    """Engine whose connections search_path into the given schema."""
    eng = create_engine(url, poolclass=NullPool)

    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute(f'SET search_path TO "{schema}"')
        cur.close()

    return eng
