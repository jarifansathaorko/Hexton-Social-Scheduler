"""Phase 5: Multi-channel video analytics and 1K+ view monitoring.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_job",
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scheduled_job",
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scheduled_job",
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scheduled_job",
        sa.Column("last_stats_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_view_count", "scheduled_job", ["view_count"])


def downgrade() -> None:
    op.drop_index("ix_job_view_count", table_name="scheduled_job")
    op.drop_column("scheduled_job", "last_stats_synced_at")
    op.drop_column("scheduled_job", "comment_count")
    op.drop_column("scheduled_job", "like_count")
    op.drop_column("scheduled_job", "view_count")
