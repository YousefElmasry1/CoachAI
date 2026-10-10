"""Add database-coordinated rate-limit buckets.

Revision ID: 20261009_0002
Revises: 20261008_0001
Create Date: 2026-10-09

The rows contain only short-lived counters.  They coordinate paid/high-cost
operations across processes and survive a process restart; they contain no
credentials or request payloads.
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261009_0002"
down_revision: Optional[str] = "20261008_0001"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("window_key", sa.Integer(), primary_key=True),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "request_count >= 0", name="ck_rate_limit_request_count"
        ),
    )
    op.create_index(
        "idx_rate_limit_expiry", "rate_limit_buckets", ["expires_at"]
    )


def downgrade() -> None:
    # Buckets are disposable operational counters, not user data.  Dropping
    # them is therefore a safe, fully reversible rollback for this revision.
    op.drop_index("idx_rate_limit_expiry", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
