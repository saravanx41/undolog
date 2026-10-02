"""Shared Postgres fixtures for undolog-torture tests.

Same pattern as packages/core/tests/conftest.py: every test module gets its
own schema (test_<module>_<pid>_<rand>), migrated to head with alembic and
dropped after the module finishes. The heavy lifting lives in
undolog_torture.dbutil (shared with the chaos CLI and the TS-06 child).
"""
import os
import uuid
from pathlib import Path

import pytest

from undolog_torture.dbutil import (
    DEFAULT_DATABASE_URL,
    drop_schema,
    engine_for_schema,
    setup_schema,
)


@pytest.fixture(scope="module")
def schema(request):
    name = (
        f"test_{Path(request.module.__file__).stem}_"
        f"{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    setup_schema(DEFAULT_DATABASE_URL, name)
    yield name
    drop_schema(DEFAULT_DATABASE_URL, name)


@pytest.fixture(scope="module")
def engine(schema):
    eng = engine_for_schema(DEFAULT_DATABASE_URL, schema)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def make_engine(request):
    """Factory: each call returns a fresh engine on its own fresh schema.

    Used by determinism tests that need two fully independent runs with
    the same thread_id (the ledger idempotency key embeds thread_id, and
    idempotency keys are globally unique — independent runs need fresh
    schemas to both record all 50 rows).
    """
    created = []

    def _make():
        name = (
            f"test_{Path(request.module.__file__).stem}_"
            f"{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        setup_schema(DEFAULT_DATABASE_URL, name)
        eng = engine_for_schema(DEFAULT_DATABASE_URL, name)
        created.append((name, eng))
        return eng

    yield _make

    for name, eng in created:
        eng.dispose()
        drop_schema(DEFAULT_DATABASE_URL, name)
