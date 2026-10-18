"""Add system_logs table and v_unified_events view for consolidated AI analysis."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_unified_logs"
down_revision: Union[str, None] = "0041_kpi_gaps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VIEW_SQL = """
CREATE OR REPLACE VIEW v_unified_events AS
SELECT
    r.started_at AS recorded_at,
    r.id AS event_id,
    t.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'test'::text AS category,
    t.slug::text AS subsystem,
    n.slug::text AS network,
    CASE r.status::text
        WHEN 'fail' THEN 'critical'
        WHEN 'degraded' THEN 'warning'
        ELSE 'ok'
    END AS severity,
    t.name::text AS title,
    COALESCE(r.status_reason, r.message, '')::text AS message,
    (r.metrics ->> 'rtt_ms')::double precision AS rtt_ms,
    (r.metrics ->> 'jitter_ms')::double precision AS jitter_ms,
    (r.metrics ->> 'loss_pct')::double precision AS loss_pct,
    COALESCE((r.metrics -> 'throughput_mbps' ->> 'download')::double precision, (r.metrics ->> 'down_mbps')::double precision) AS down_mbps,
    COALESCE((r.metrics -> 'throughput_mbps' ->> 'upload')::double precision, (r.metrics ->> 'up_mbps')::double precision) AS up_mbps,
    jsonb_build_object(
        'test_id', t.id,
        'kind', t.kind::text,
        'target', t.target,
        'status', r.status::text,
        'stages', r.stages,
        'metrics', r.metrics
    ) AS metadata
FROM test_results r
JOIN tests t ON t.id = r.test_id
JOIN monitors m ON m.id = t.monitor_id
JOIN networks n ON n.id = t.network_id

UNION ALL

SELECT
    s.recorded_at AS recorded_at,
    s.id AS event_id,
    sr.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'synthetic'::text AS category,
    sr.profile_slug::text AS subsystem,
    CASE s.link WHEN 'wireless' THEN 'wifi' ELSE s.link END::text AS network,
    CASE s.verdict
        WHEN 'fail' THEN 'critical'
        WHEN 'pass' THEN 'ok'
        ELSE 'info'
    END AS severity,
    COALESCE(sp.name, sr.profile_slug)::text AS title,
    CASE s.verdict
        WHEN 'fail' THEN 'Outside the round-trip, jitter, or loss limit.'
        WHEN 'pass' THEN 'Within the round-trip, jitter, and loss limits.'
        ELSE ''
    END::text AS message,
    s.rtt_ms AS rtt_ms,
    s.jitter_ms AS jitter_ms,
    s.loss_pct AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'run_id', sr.id,
        'mode', sr.mode,
        'verdict', s.verdict,
        'sample_key', s.sample_key,
        'metrics', s.metrics
    ) AS metadata
FROM synthetic_samples s
JOIN synthetic_runs sr ON sr.id = s.run_id
JOIN monitors m ON m.id = sr.monitor_id
LEFT JOIN synthetic_profiles sp ON sp.slug = sr.profile_slug

UNION ALL

SELECT
    a.observed_at AS recorded_at,
    a.id AS event_id,
    a.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'airwave'::text AS category,
    a.alert_type::text AS subsystem,
    'wifi'::text AS network,
    CASE
        WHEN upper(a.alert_type) IN ('DEAUTHFLOOD','DISCONFLOOD','AUTHFLOOD','APSPOOF','EVILTWIN','BSSIDSPOOF','BSSTIMESTAMP','CRYPTODROP','KARMA','AIRJACK','WPSBRUTE') THEN 'critical'
        WHEN upper(a.alert_type) IN ('WPA3DOWNGRADE','DHCPCONFLICT','DHCPROGUE','ARPSPOOF') THEN 'warning'
        ELSE 'info'
    END AS severity,
    ('Airwave ' || a.alert_type || CASE WHEN a.bssid <> '' THEN ' (' || a.bssid || ')' ELSE '' END)::text AS title,
    a.text::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'alert_type', a.alert_type,
        'bssid', a.bssid,
        'dedupe_key', a.dedupe_key
    ) AS metadata
FROM rf_alerts a
JOIN monitors m ON m.id = a.monitor_id

UNION ALL

SELECT
    COALESCE(st.finished_at, st.started_at, st.requested_at) AS recorded_at,
    st.id AS event_id,
    st.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'speedtest'::text AS category,
    'speedtest'::text AS subsystem,
    CASE st.link WHEN 'wireless' THEN 'wifi' ELSE st.link END::text AS network,
    CASE st.status
        WHEN 'failed' THEN 'warning'
        WHEN 'done' THEN 'ok'
        ELSE 'info'
    END AS severity,
    ('Speed test (' || st.link || ')')::text AS title,
    st.error::text AS message,
    st.ping_ms AS rtt_ms,
    st.jitter_ms AS jitter_ms,
    NULL::double precision AS loss_pct,
    st.down_mbps AS down_mbps,
    st.up_mbps AS up_mbps,
    jsonb_build_object(
        'status', st.status,
        'requested_by', st.requested_by,
        'down_seconds', st.down_seconds,
        'up_seconds', st.up_seconds
    ) AS metadata
FROM speed_tests st
JOIN monitors m ON m.id = st.monitor_id

UNION ALL

SELECT
    i.opened_at AS recorded_at,
    i.id AS event_id,
    i.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'issue'::text AS category,
    COALESCE(i.root_cause_stage, 'issue')::text AS subsystem,
    COALESCE(n.slug, '')::text AS network,
    i.severity::text AS severity,
    i.title::text AS title,
    i.summary::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'status', i.status::text,
        'resolved_at', i.resolved_at,
        'test_id', i.test_id
    ) AS metadata
FROM issues i
LEFT JOIN monitors m ON m.id = i.monitor_id
LEFT JOIN networks n ON n.id = i.network_id

UNION ALL

SELECT
    COALESCE(ae.opened_at, ae.first_seen_at) AS recorded_at,
    ae.id AS event_id,
    NULL::uuid AS monitor_id,
    NULL::text AS monitor_slug,
    NULL::text AS monitor_name,
    'alert'::text AS category,
    ae.category::text AS subsystem,
    ''::text AS network,
    ae.severity::text AS severity,
    ae.title::text AS title,
    ae.detail::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'key', ae.key,
        'status', ae.status,
        'first_seen_at', ae.first_seen_at,
        'resolved_at', ae.resolved_at,
        'notified_at', ae.notified_at
    ) AS metadata
FROM alert_events ae

UNION ALL

SELECT
    mh.reported_at AS recorded_at,
    mh.id AS event_id,
    mh.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'health'::text AS category,
    'device'::text AS subsystem,
    ''::text AS network,
    CASE
        WHEN mh.last_error IS NOT NULL AND mh.last_error <> '' THEN 'warning'
        ELSE 'ok'
    END AS severity,
    (m.name || ' health check-in')::text AS title,
    COALESCE(mh.last_error, '')::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'cpu_percent', mh.cpu_percent,
        'load_1', mh.load_1,
        'mem_used_mb', mh.mem_used_mb,
        'mem_total_mb', mh.mem_total_mb,
        'disk_used_mb', mh.disk_used_mb,
        'disk_total_mb', mh.disk_total_mb,
        'temp_c', mh.temp_c,
        'uptime_seconds', mh.uptime_seconds,
        'airwave_mode', mh.airwave_mode,
        'agent_version', m.agent_version
    ) AS metadata
FROM monitor_health mh
JOIN monitors m ON m.id = mh.monitor_id

UNION ALL

SELECT
    au.created_at AS recorded_at,
    au.id AS event_id,
    au.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'audit'::text AS category,
    au.action::text AS subsystem,
    ''::text AS network,
    'info'::text AS severity,
    ('Audit: ' || au.action || ' by ' || au.username)::text AS title,
    au.detail::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    jsonb_build_object(
        'username', au.username,
        'action', au.action
    ) AS metadata
FROM audit_events au
LEFT JOIN monitors m ON m.id = au.monitor_id

UNION ALL

SELECT
    sl.recorded_at AS recorded_at,
    sl.id AS event_id,
    sl.monitor_id AS monitor_id,
    m.slug::text AS monitor_slug,
    m.name::text AS monitor_name,
    'system'::text AS category,
    sl.subsystem::text AS subsystem,
    ''::text AS network,
    sl.severity::text AS severity,
    (initcap(sl.source) || ' ' || sl.subsystem)::text AS title,
    sl.message::text AS message,
    NULL::double precision AS rtt_ms,
    NULL::double precision AS jitter_ms,
    NULL::double precision AS loss_pct,
    NULL::double precision AS down_mbps,
    NULL::double precision AS up_mbps,
    sl.detail || jsonb_build_object('source', sl.source) AS metadata
FROM system_logs sl
LEFT JOIN monitors m ON m.id = sl.monitor_id;
"""


def upgrade() -> None:
    op.create_table(
        "system_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("monitor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=True),
        sa.Column("subsystem", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_system_logs_recorded_at", "system_logs", ["recorded_at"])
    op.create_index("ix_system_logs_monitor_recorded", "system_logs", ["monitor_id", "recorded_at"])
    op.execute(VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_unified_events")
    op.drop_index("ix_system_logs_monitor_recorded", table_name="system_logs")
    op.drop_index("ix_system_logs_recorded_at", table_name="system_logs")
    op.drop_table("system_logs")
