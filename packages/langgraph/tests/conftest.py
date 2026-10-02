"""Shared fixtures for the undolog-langgraph adapter tests.

Postgres: every test module gets its own schema (migrated with the core
alembic migration). LangGraph checkpoints live in the SAME schema via
PostgresSaver on a psycopg connection with search_path set; the schema is
dropped after the module finishes.
"""
import copy
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from undolog_core import Ledger
from undolog_core.registry import Registry

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://undolog:undolog@localhost:5432/undolog",
)
PSYCOPG_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "core" / "alembic"

# Test taxonomy: the fake "charge" tool uses the seeded stripe name so the
# default-style mapping (compensatable -> refund) applies; the dict-state
# tools are reversible restore_before.
TEST_REGISTRY = Registry.from_mapping(
    {
        "tools": {
            "cart.add_item": {
                "class": "reversible",
                "compensation": {"type": "restore_before"},
                "snapshot_capable": True,
            },
            "notes.set": {
                "class": "reversible",
                "compensation": {"type": "restore_before"},
                "snapshot_capable": True,
            },
            "cart.purge": {
                "class": "reversible",
                "compensation": {"type": "restore_before"},
                "snapshot_capable": True,
            },
            "stripe.create_charge": {
                "class": "compensatable",
                "compensation": {"type": "refund", "target": "stripe"},
            },
        }
    }
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


@pytest.fixture(scope="module")
def ledger(engine):
    return Ledger(engine, registry=TEST_REGISTRY)


@pytest.fixture(scope="module")
def checkpointer(schema):
    """PostgresSaver against the module's schema; torn down with it."""
    conn = psycopg.connect(PSYCOPG_URL, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    saver = PostgresSaver(conn)
    saver.setup()
    yield saver
    conn.close()


@pytest.fixture()
def world():
    """In-memory 'external systems' the fake tools act on."""
    return {"cart": {}, "notes": {}, "charges": []}


def make_world_snapshot(world):
    return lambda: copy.deepcopy(
        {"cart": world["cart"], "notes": world["notes"], "charges": world["charges"]}
    )
