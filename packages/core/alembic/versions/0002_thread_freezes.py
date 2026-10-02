"""thread_freezes table

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02

Adds the flag row checked by the wrap interceptor at entry when a thread
is frozen.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "thread_freezes",
        sa.Column("thread_id", sa.String(), primary_key=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT SELECT ON %I.thread_freezes TO undolog_app', current_schema()
            );
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS thread_freezes")
