"""On-demand LAN scans for Airwaves Security, plus a request flag on monitors."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_lan_scan"
down_revision: Union[str, None] = "0047_server_host_iface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column("lan_scan_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "lan_scan_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("monitor_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("subnet", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lan_scan_runs_monitor_requested", "lan_scan_runs", ["monitor_id", "requested_at"])
    op.create_table(
        "lan_scan_hosts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("mac", sa.String(length=17), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("ports", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.ForeignKeyConstraint(["run_id"], ["lan_scan_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lan_scan_hosts_run", "lan_scan_hosts", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_lan_scan_hosts_run", table_name="lan_scan_hosts")
    op.drop_table("lan_scan_hosts")
    op.drop_index("ix_lan_scan_runs_monitor_requested", table_name="lan_scan_runs")
    op.drop_table("lan_scan_runs")
    op.drop_column("monitors", "lan_scan_requested_at")
