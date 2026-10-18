"""Count the time the monitoring had no results, so a down server or Pi is not a blank in the report."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_kpi_gaps"
down_revision: Union[str, None] = "0040_speed_tests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("kpi_hourly", "kpi_daily"):
        op.add_column(table, sa.Column("gap_min", sa.Float(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("gaps", sa.Integer(), nullable=False, server_default="0"))
    # A gap is more than 3 minutes between two ping results. Its length is the wait minus the usual minute.
    op.execute(
        """
        UPDATE kpi_hourly h SET gap_min = x.gap_min, gaps = x.gaps
        FROM (
          SELECT monitor_id, link, bucket, SUM(gap) AS gap_min, COUNT(*) AS gaps
          FROM (
            SELECT t.monitor_id AS monitor_id,
                   CASE n.kind::text WHEN 'wifi' THEN 'wireless' ELSE 'wired' END AS link,
                   (date_trunc('hour', r.started_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS bucket,
                   EXTRACT(EPOCH FROM (r.started_at - lag(r.started_at) OVER (PARTITION BY r.test_id ORDER BY r.started_at))) / 60.0 - 1 AS gap
            FROM test_results r
            JOIN tests t ON t.id = r.test_id
            JOIN networks n ON n.id = t.network_id
            WHERE t.slug = 'icmp-path' AND r.external_id NOT LIKE 'rollup-%'
          ) g
          WHERE gap > 2
          GROUP BY 1, 2, 3
        ) x
        WHERE h.kind = 'internet' AND h.monitor_id = x.monitor_id AND h.link = x.link AND h.bucket_start = x.bucket
        """
    )
    op.execute(
        """
        UPDATE kpi_daily d SET gap_min = x.gap_min, gaps = x.gaps
        FROM (
          SELECT (bucket_start AT TIME ZONE 'UTC')::date AS day, monitor_id, link, kind, SUM(gap_min) AS gap_min, SUM(gaps) AS gaps
          FROM kpi_hourly GROUP BY 1, 2, 3, 4
        ) x
        WHERE d.day = x.day AND d.monitor_id = x.monitor_id AND d.link = x.link AND d.kind = x.kind
        """
    )


def downgrade() -> None:
    for table in ("kpi_daily", "kpi_hourly"):
        op.drop_column(table, "gaps")
        op.drop_column(table, "gap_min")
