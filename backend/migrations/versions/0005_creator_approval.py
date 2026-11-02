"""Add creator_approval column to scheduled_job for note taking.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # Check if column already added
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("scheduled_job")]
    if "creator_approval" not in cols:
        op.add_column(
            "scheduled_job",
            sa.Column("creator_approval", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("scheduled_job", "creator_approval")
