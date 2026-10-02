"""ledger schema + append-only grants

Revision ID: 0001
Revises:
Create Date: 2026-10-02

Creates ledger_entries (append-only), thread_counters (gapless seq),
compensations, tool_registry, and the dedicated app role `undolog_app`
with SELECT + INSERT only on ledger_entries (UPDATE/DELETE/TRUNCATE
revoked). Grants are applied to current_schema() so the same migration
works for every test schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ENTRY_CLASSES = ("reversible", "compensatable", "irreversible", "unknown")
ENTRY_STATUSES = ("applied", "compensated", "failed")


def _enum(name, values):
    # Types are created explicitly in upgrade(); tables reference by name.
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in (
        ("entry_class", ENTRY_CLASSES),
        ("entry_status", ENTRY_STATUSES),
    ):
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "thread_counters",
        sa.Column("thread_id", sa.String(), primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("args_hash", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("before_jsonb", postgresql.JSONB(), nullable=True),
        sa.Column("after_jsonb", postgresql.JSONB(), nullable=True),
        sa.Column("class", _enum("entry_class", ENTRY_CLASSES), nullable=False),
        sa.Column("compensation_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", _enum("entry_status", ENTRY_STATUSES), nullable=False),
        sa.Column("prompt_context_ref", sa.String(), nullable=True),
        sa.Column("log_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("thread_id", "seq", name="uq_ledger_thread_seq"),
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_idempotency_key"),
    )
    op.create_index("ix_ledger_entries_thread_id", "ledger_entries", ["thread_id"])

    op.create_table(
        "compensations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_compensations_entry_id", "compensations", ["entry_id"])

    op.create_table(
        "tool_registry",
        sa.Column("tool_name", sa.String(), primary_key=True),
        sa.Column("class", _enum("entry_class", ENTRY_CLASSES), nullable=False),
        sa.Column("compensation", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_capable", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    # Dedicated app role: append-only access. Login is required so tests
    # (and the app) can connect as this role and be denied mutations.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'undolog_app') THEN
                CREATE ROLE undolog_app LOGIN PASSWORD 'undolog_app';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            sch text := current_schema();
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO undolog_app', current_database());
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO undolog_app', sch);
            EXECUTE format('GRANT SELECT, INSERT ON %I.ledger_entries TO undolog_app', sch);
            EXECUTE format('GRANT SELECT ON %I.tool_registry TO undolog_app', sch);
            EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON %I.ledger_entries FROM undolog_app', sch);
            EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON %I.ledger_entries FROM PUBLIC', sch);
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tool_registry")
    op.execute("DROP TABLE IF EXISTS compensations")
    op.execute("DROP INDEX IF EXISTS ix_ledger_entries_thread_id")
    op.execute("DROP TABLE IF EXISTS ledger_entries")
    op.execute("DROP TABLE IF EXISTS thread_counters")
    for name in ("entry_class", "entry_status"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
