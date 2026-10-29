"""Gemini settings for on-demand KPI root cause analysis."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_gemini_settings"
down_revision: Union[str, None] = "0049_lan_scan_hostname"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gemini_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_key_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_hint", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="gemini-2.5-flash"),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("gemini_settings")
