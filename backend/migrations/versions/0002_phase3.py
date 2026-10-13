"""Phase 3: Sheets reporting configuration, daily summary outbox, privacy-policy gate.

Adds three tables and changes nothing that exists. ``sheet_report_record`` is untouched:
the daily summary gets its own table precisely so that ``job_id`` stays required and
unique, which is what keeps "one job, one upload row" true.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from hexton.db.types import UTCDateTime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_connection",
        sa.Column("disabled_at", UTCDateTime(), nullable=True),
    )

    op.create_table(
        "sheets_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("platform_connection.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("spreadsheet_id", sa.String(length=128), nullable=True),
        sa.Column("worksheet", sa.String(length=128), nullable=True),
        sa.Column("report_timezone", sa.String(length=64), nullable=False),
        sa.Column("cutoff_time", sa.String(length=5), nullable=False),
        sa.Column("include_statuses", sa.JSON(), nullable=False),
        sa.Column("include_optional_columns", sa.JSON(), nullable=False),
        sa.Column("include_daily_summary", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("verified_at", UTCDateTime(), nullable=True),
        sa.Column("last_test_write_at", UTCDateTime(), nullable=True),
        sa.Column("last_sync_at", UTCDateTime(), nullable=True),
        sa.Column("last_sync_status", sa.String(length=32), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_sync_foreign_keys", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )

    op.create_table(
        "sheet_summary_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.String(length=10), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("sync_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_synced_at", UTCDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("sync_key", name=op.f("uq_sheet_summary_record_sync_key")),
        sa.Index("ix_sheet_summary_record_date_channel", "report_date", "channel_id"),
    )

    op.create_table(
        "policy_acceptance_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("accepted_by", sa.String(length=128), nullable=False),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.Column("superseded_at", UTCDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("policy_acceptance_record")
    op.drop_table("sheet_summary_record")
    op.drop_table("sheets_config")
