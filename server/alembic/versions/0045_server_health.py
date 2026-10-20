"""Server Health samples, the daily database size, and the weekly advisory cache."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_server_health"
down_revision: Union[str, None] = "0044_software_impact"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("mem_used_mb", sa.Integer(), nullable=False),
        sa.Column("mem_total_mb", sa.Integer(), nullable=False),
        sa.Column("disk_used_mb", sa.Integer(), nullable=False),
        sa.Column("disk_total_mb", sa.Integer(), nullable=False),
        sa.Column("db_bytes", sa.BigInteger(), nullable=False),
        sa.Column("connections", sa.Integer(), nullable=False),
        sa.Column("iface", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("rx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_server_samples_recorded", "server_samples", ["recorded_at"])
    op.create_table(
        "server_db_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("db_bytes", sa.BigInteger(), nullable=False),
        sa.Column("free_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "server_advisories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "server_stuck",
        sa.Column("pid", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("d_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hot_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cpu_ticks", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("server_stuck")
    op.drop_table("server_advisories")
    op.drop_table("server_db_daily")
    op.drop_index("ix_server_samples_recorded", table_name="server_samples")
    op.drop_table("server_samples")
