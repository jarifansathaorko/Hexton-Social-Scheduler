"""Phase 4: Optimization indexes for worker sweep and channel identity.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_index("ix_job_sweep", "scheduled_job", ["state", "next_poll_at"])
    op.create_index("ix_job_channel_identity", "scheduled_job", ["channel_identity_id"])


def downgrade() -> None:
    op.drop_index("ix_job_channel_identity", table_name="scheduled_job")
    op.drop_index("ix_job_sweep", table_name="scheduled_job")
