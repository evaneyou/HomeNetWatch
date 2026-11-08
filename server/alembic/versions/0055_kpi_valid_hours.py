"""Restrict KPI hourly and daily records to valid working hours (6:00 AM to 12:00 AM ET, 7 days a week)."""

from typing import Sequence, Union

from alembic import op

revision: str = "0055_kpi_valid_hours"
down_revision: Union[str, None] = "0054_trace_address"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUM_FIELDS = (
    "n",
    "failed",
    "degraded",
    "incidents",
    "loss_sum",
    "loss_n",
    "rtt_sum",
    "rtt_n",
    "jitter_sum",
    "jitter_n",
    "down_sum",
    "down_n",
    "up_sum",
    "up_n",
    "gap_min",
    "gaps",
    "outage_min",
)


def upgrade() -> None:
    op.execute(
        "DELETE FROM kpi_hourly WHERE EXTRACT(HOUR FROM (bucket_start AT TIME ZONE 'America/New_York')) < 6"
    )
    op.execute("DELETE FROM kpi_daily")
    names = ", ".join(_SUM_FIELDS)
    sums = ", ".join(f"SUM({name})" for name in _SUM_FIELDS)
    op.execute(
        f"INSERT INTO kpi_daily (day, monitor_id, link, kind, {names}, loss_max) "
        f"SELECT (bucket_start AT TIME ZONE 'America/New_York')::date, monitor_id, link, kind, {sums}, MAX(loss_max) "
        f"FROM kpi_hourly "
        f"WHERE EXTRACT(HOUR FROM (bucket_start AT TIME ZONE 'America/New_York')) >= 6 "
        f"GROUP BY 1, monitor_id, link, kind"
    )


def downgrade() -> None:
    pass
