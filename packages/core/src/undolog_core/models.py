"""SQLModel tables for the undolog ledger.

All tables are created by the alembic migration in packages/core/alembic;
these models are the runtime view used by wrap() and the tests.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EntryClass(str, Enum):
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class EntryStatus(str, Enum):
    APPLIED = "applied"
    COMPENSATED = "compensated"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_type(enum_cls):
    # Persist enum VALUES ("unknown"), not member names ("UNKNOWN").
    return sa.Enum(
        enum_cls, values_callable=lambda e: [m.value for m in e]
    )


class LedgerEntry(SQLModel, table=True):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        sa.UniqueConstraint("thread_id", "seq", name="uq_ledger_thread_seq"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow)
    thread_id: str = Field(index=True)
    seq: int
    tool_name: str
    args_hash: str
    idempotency_key: str = Field(unique=True)
    before_jsonb: Optional[dict] = Field(
        default=None, sa_column=Column("before_jsonb", JSONB, nullable=True)
    )
    after_jsonb: Optional[dict] = Field(
        default=None, sa_column=Column("after_jsonb", JSONB, nullable=True)
    )
    # Column is named "class" (reserved word) in Postgres; attribute is class_.
    class_: EntryClass = Field(sa_column=Column("class", _enum_type(EntryClass), nullable=False))
    compensation_ref: Optional[UUID] = None
    status: EntryStatus = Field(sa_column=Column("status", _enum_type(EntryStatus), nullable=False))
    prompt_context_ref: Optional[str] = None
    # True when adapter proof capture failed and the row is log-only
    # (before/after NULL, class escalated to "unknown").
    log_only: bool = Field(default=False)


class ThreadCounter(SQLModel, table=True):
    """Per-thread monotonic counter backing gapless seq allocation."""

    __tablename__ = "thread_counters"

    thread_id: str = Field(primary_key=True)
    seq: int = 0


class ThreadFreeze(SQLModel, table=True):
    """Flag row marking a thread as frozen.

    The row is what wrapped calls check at entry (cheap SELECT); the
    advisory lock held by the freezer (see Ledger.freeze) is what makes the
    freeze exclusive across connections/processes and serializes rollback
    against new freezers. A stale row from a crashed freezer is overwritten
    by the next successful freeze.
    """

    __tablename__ = "thread_freezes"

    thread_id: str = Field(primary_key=True)
    frozen_at: datetime = Field(default_factory=_utcnow)


class Compensation(SQLModel, table=True):
    """Registry of compensation executions against ledger entries (Week 2)."""

    __tablename__ = "compensations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    entry_id: Optional[UUID] = Field(default=None, index=True)
    tool_name: str
    type: str
    target: Optional[str] = None
    payload: Optional[dict] = Field(
        default=None, sa_column=Column("payload", JSONB, nullable=True)
    )
    status: str = "planned"
    created_at: datetime = Field(default_factory=_utcnow)


class ToolRegistryRow(SQLModel, table=True):
    """DB mirror of the YAML Tool Safety Registry (taxonomy)."""

    __tablename__ = "tool_registry"

    tool_name: str = Field(primary_key=True)
    class_: EntryClass = Field(sa_column=Column("class", _enum_type(EntryClass), nullable=False))
    compensation: Optional[dict] = Field(
        default=None, sa_column=Column("compensation", JSONB, nullable=True)
    )
    snapshot_capable: bool = False
