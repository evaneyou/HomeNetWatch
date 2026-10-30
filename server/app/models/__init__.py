from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class UserRole(str, enum.Enum):
    admin = "admin"
    viewer = "viewer"
    guest = "guest"


class MonitorStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"


class NetworkKind(str, enum.Enum):
    wired = "wired"
    wifi = "wifi"


class TestKind(str, enum.Enum):
    __test__ = False
    http = "http"
    dns = "dns"
    ping = "ping"
    wifi = "wifi"
    mtr = "mtr"
    librespeed = "librespeed"


class ResultStatus(str, enum.Enum):
    ok = "ok"
    degraded = "degraded"
    fail = "fail"


class IssueSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class IssueStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    monitors: Mapped[list[Monitor]] = relationship(back_populates="group")
    networks: Mapped[list[Network]] = relationship(back_populates="group")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "pi" runs the full set of checks. "android" (a TV or phone) runs only the speed test and synthetic traffic.
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="pi", server_default="pi")
    # "sha256:<hex>" for tokens issued by enrollment, bcrypt for older keys.
    # Empty until the remote agent's first poll after approval.
    token_hash: Mapped[Optional[str]] = mapped_column(String(255))
    identity_public_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    last_handshake_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[MonitorStatus] = mapped_column(
        Enum(MonitorStatus, name="monitor_status"), default=MonitorStatus.unknown, nullable=False
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    location_label: Mapped[Optional[str]] = mapped_column(String(128))
    region: Mapped[Optional[str]] = mapped_column(String(64))
    country: Mapped[Optional[str]] = mapped_column(String(64))
    wg_public_key: Mapped[Optional[str]] = mapped_column(String(64))
    wg_address: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    # Second tunnel, pinned to Wi-Fi. The wired tunnel stays in wg_address.
    wg_wireless_public_key: Mapped[Optional[str]] = mapped_column(String(64))
    wg_wireless_address: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    last_wireless_handshake_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lan_ip: Mapped[Optional[str]] = mapped_column(String(64))
    public_ip: Mapped[Optional[str]] = mapped_column(String(64))
    isp: Mapped[Optional[str]] = mapped_column(String(128))
    agent_version: Mapped[Optional[str]] = mapped_column(String(32))
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    applied_config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    health_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    # SSID and BSSID the link adapter is joined to.
    link_ssid: Mapped[Optional[str]] = mapped_column(String(64))
    link_bssid: Mapped[Optional[str]] = mapped_column(String(17))
    # Set by an admin. The probe clears it once, then replaces its own process.
    restart_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scan_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Set by an admin. The probe clears it when it posts LAN scan results.
    lan_scan_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped[Group] = relationship(back_populates="monitors")
    tests: Mapped[list[Test]] = relationship(back_populates="monitor")
    health_samples: Mapped[list["MonitorHealth"]] = relationship(back_populates="monitor")

    __table_args__ = (UniqueConstraint("group_id", "slug", name="uq_monitor_group_slug"),)


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[NetworkKind] = mapped_column(Enum(NetworkKind, name="network_kind"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped[Group] = relationship(back_populates="networks")
    tests: Mapped[list[Test]] = relationship(back_populates="network")

    __table_args__ = (UniqueConstraint("group_id", "slug", name="uq_network_group_slug"),)


class Test(Base):
    __test__ = False  # not a pytest class
    __tablename__ = "tests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("networks.id", ondelete="CASCADE"), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[TestKind] = mapped_column(Enum(TestKind, name="test_kind"), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stable_status: Mapped[ResultStatus] = mapped_column(
        Enum(ResultStatus, name="result_status"), nullable=False, default=ResultStatus.ok, server_default="ok"
    )
    state_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    monitor: Mapped[Monitor] = relationship(back_populates="tests")
    network: Mapped[Network] = relationship(back_populates="tests")
    results: Mapped[list[TestResult]] = relationship(back_populates="test")

    __table_args__ = (
        UniqueConstraint("monitor_id", "network_id", "slug", name="uq_test_monitor_network_slug"),
    )


class TestResult(Base):
    __test__ = False
    __tablename__ = "test_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Client-supplied idempotency key from the remote agent
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ResultStatus] = mapped_column(Enum(ResultStatus, name="result_status"), nullable=False)
    # Stage waterfall: [{name, duration_ms, status?}, ...]
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    message: Mapped[Optional[str]] = mapped_column(Text)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    test: Mapped[Test] = relationship(back_populates="results")

    __table_args__ = (
        UniqueConstraint("external_id", name="uq_test_result_external_id"),
        Index("ix_test_results_test_started", "test_id", "started_at"),
    )


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    monitor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("monitors.id", ondelete="SET NULL"))
    network_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("networks.id", ondelete="SET NULL"))
    test_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(IssueSeverity, name="issue_severity"), nullable=False
    )
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus, name="issue_status"), default=IssueStatus.open, nullable=False
    )
    root_cause_stage: Mapped[Optional[str]] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_issues_status_opened", "status", "opened_at"),)


class WifiObservation(Base):
    __tablename__ = "wifi_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ssid: Mapped[str] = mapped_column(String(64), nullable=False)
    bssid: Mapped[str] = mapped_column(String(17), nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False)
    channel: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_width: Mapped[int] = mapped_column(Integer, nullable=False)
    rssi_dbm: Mapped[float] = mapped_column(Float, nullable=False)
    noise_dbm: Mapped[Optional[float]] = mapped_column(Float)
    security: Mapped[str] = mapped_column(String(32), nullable=False)
    phy: Mapped[str] = mapped_column(String(8), nullable=False)
    max_link_rate_mbps: Mapped[int] = mapped_column(Integer, nullable=False)
    client_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("ix_wifi_observations_monitor_scanned", "monitor_id", "scanned_at"),)


class RfChannelStat(Base):
    """Noise and airtime for one channel at one moment, from an iw survey or later capture."""

    __tablename__ = "rf_channel_stats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False)
    channel: Mapped[int] = mapped_column(Integer, nullable=False)
    noise_dbm: Mapped[Optional[float]] = mapped_column(Float)
    busy_pct: Mapped[Optional[float]] = mapped_column(Float)
    airtime_pct: Mapped[Optional[float]] = mapped_column(Float)
    packets: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (Index("ix_rf_channel_stats_monitor_measured", "monitor_id", "measured_at"),)


class RfClient(Base):
    """A client of a trusted access point, sent by Kismet."""

    __tablename__ = "rf_clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    mac: Mapped[str] = mapped_column(String(17), nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bssid: Mapped[str] = mapped_column(String(17), nullable=False)
    signal: Mapped[Optional[float]] = mapped_column(Float)
    signal_min: Mapped[Optional[float]] = mapped_column(Float)
    signal_max: Mapped[Optional[float]] = mapped_column(Float)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    randomized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False, default="", server_default="")
    channel: Mapped[Optional[int]] = mapped_column(Integer)
    packets: Mapped[Optional[int]] = mapped_column(Integer)
    data_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    retries: Mapped[Optional[int]] = mapped_column(Integer)
    disconnects: Mapped[Optional[int]] = mapped_column(Integer)
    probed_ssids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")

    __table_args__ = (
        UniqueConstraint("monitor_id", "observed_at", "dedupe_key", name="uq_rf_clients_capture"),
        Index("ix_rf_clients_monitor_observed", "monitor_id", "observed_at"),
    )


class RfAlert(Base):
    """One Kismet alert from a remote agent."""

    __tablename__ = "rf_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    bssid: Mapped[str] = mapped_column(String(17), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("monitor_id", "observed_at", "dedupe_key", name="uq_rf_alerts_capture"),
        Index("ix_rf_alerts_monitor_observed", "monitor_id", "observed_at"),
    )


class LanScanRun(Base):
    """One on-demand LAN scan requested from Airwaves Security."""

    __tablename__ = "lan_scan_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    subnet: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    hosts: Mapped[list["LanScanHost"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_lan_scan_runs_monitor_requested", "monitor_id", "requested_at"),)


class LanScanHost(Base):
    """One live host found during a LAN scan."""

    __tablename__ = "lan_scan_hosts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lan_scan_runs.id", ondelete="CASCADE"), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    mac: Mapped[str] = mapped_column(String(17), nullable=False, default="")
    vendor: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, default="")
    ports: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    run: Mapped[LanScanRun] = relationship(back_populates="hosts")

    __table_args__ = (Index("ix_lan_scan_hosts_run", "run_id"),)


class ApInventory(Base):
    """Trusted flag and first/last seen for one access point at one agent."""

    __tablename__ = "ap_inventory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    bssid: Mapped[str] = mapped_column(String(17), nullable=False)
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trust_source: Mapped[Optional[str]] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("monitor_id", "bssid", name="uq_ap_inventory_monitor_bssid"),)


class MonitorHealth(Base):
    __tablename__ = "monitor_health"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False)
    load_1: Mapped[float] = mapped_column(Float, nullable=False)
    mem_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    temp_c: Mapped[Optional[float]] = mapped_column(Float)
    lan_ip: Mapped[Optional[str]] = mapped_column(String(64))
    public_ip: Mapped[Optional[str]] = mapped_column(String(64))
    isp: Mapped[Optional[str]] = mapped_column(String(128))
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    airwave_mode: Mapped[Optional[str]] = mapped_column(String(16))
    wired_iface: Mapped[Optional[str]] = mapped_column(String(32))
    wifi_link_iface: Mapped[Optional[str]] = mapped_column(String(32))
    wifi_radio_iface: Mapped[Optional[str]] = mapped_column(String(32))
    last_error: Mapped[Optional[str]] = mapped_column(String(600))

    monitor: Mapped[Monitor] = relationship(back_populates="health_samples")

    __table_args__ = (Index("ix_monitor_health_monitor_reported", "monitor_id", "reported_at"),)


class InstallerCode(Base):
    """An installer code an admin registered from a Pi's LAN page."""

    __tablename__ = "installer_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    device_name: Mapped[str] = mapped_column(String(128), nullable=False)
    location_label: Mapped[Optional[str]] = mapped_column(String(128))
    registered_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set on approval. Deleting the monitor releases the code.
    monitor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))

    checkins: Mapped[list["EnrollmentCheckin"]] = relationship(
        back_populates="code", cascade="all, delete-orphan", passive_deletes=True
    )


class EnrollmentCheckin(Base):
    """A remote agent waiting for approval under a registered installer code."""

    __tablename__ = "enrollment_checkins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("installer_codes.id", ondelete="CASCADE"), nullable=False)
    identity_public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    wg_public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    wg_wireless_public_key: Mapped[Optional[str]] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(16), nullable=False, default="pi", server_default="pi")
    device_name: Mapped[Optional[str]] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    lan_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_ip: Mapped[Optional[str]] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    code: Mapped[InstallerCode] = relationship(back_populates="checkins")

    __table_args__ = (UniqueConstraint("code_id", "identity_public_key", name="uq_checkin_code_identity"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # No foreign key, so the record outlives a deleted monitor.
    monitor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")


class GuestVisit(Base):
    """One guest sign-in. The place is looked up when it happens, so it stays correct if the map file changes.

    Kept for a year (see services.retention). The Pi's own address is never stored here.
    """

    __tablename__ = "guest_visits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    country_code: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    __table_args__ = (
        Index("ix_guest_visits_visited_at", "visited_at"),
        Index("ix_guest_visits_country_visited", "country_code", "visited_at"),
    )


class TelegramSetting(Base):
    """One row: where alerts are sent. The bot token is stored encrypted and is never returned by the API."""

    __tablename__ = "telegram_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    bot_token_enc: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="", server_default="")
    bot_username: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    notify_network: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_pi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_server: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notify_recovery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_ok_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeminiSetting(Base):
    """One row: Gemini API key and model for on-demand KPI root cause analysis. Key is stored encrypted."""

    __tablename__ = "gemini_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="", server_default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="gemini-3.8-flash", server_default="gemini-2.5-flash")
    last_ok_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertEvent(Base):
    """A condition that is, or was, firing. `key` names the condition so it is announced once."""

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # pending: seen but not long enough to announce. open: announced. resolved: over.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        Index("ix_alert_events_status", "status", "first_seen_at"),
        Index("uq_alert_events_active_key", "key", unique=True, postgresql_where=text("status in ('pending','open')")),
    )


class KpiHourly(Base):
    """Exact counts for one hour, so reports stay correct after raw measurements are averaged.

    `kind` is internet (the ISP path ping), voice, video, apps (other application checks) or speed.
    Sums and counts are kept, not averages, so any period can be added up without bias.
    """

    __tablename__ = "kpi_hourly"

    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), primary_key=True)
    link: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_max: Mapped[Optional[float]] = mapped_column(Float)
    rtt_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rtt_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jitter_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    jitter_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    down_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    down_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    up_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    up_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Minutes without internet: failed checks scaled to the hour. Kept so a day can be added up exactly.
    outage_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    # Minutes the monitoring had no result, and how many separate stretches. Not the same as being offline.
    gap_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("ix_kpi_hourly_monitor_bucket", "monitor_id", "bucket_start"),)


class KpiDaily(Base):
    """One day (UTC) of KPI counts, kept for good. Weeks, months and years are added up from these."""

    __tablename__ = "kpi_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), primary_key=True)
    link: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_max: Mapped[Optional[float]] = mapped_column(Float)
    rtt_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rtt_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jitter_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    jitter_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    down_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    down_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    up_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    up_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outage_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    # Minutes the monitoring had no result, and how many separate stretches. Not the same as being offline.
    gap_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("ix_kpi_daily_monitor_day", "monitor_id", "day"),)


class SpeedTest(Base):
    """A live speed test the Pi runs against the LibreSpeed server: ping, then download, then upload."""

    __tablename__ = "speed_tests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    link: Mapped[str] = mapped_column(String(16), nullable=False)
    # pending: waiting for the Pi. running: the Pi took it. finished, failed or cancelled: over.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    down_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    up_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    down_mbps: Mapped[Optional[float]] = mapped_column(Float)
    up_mbps: Mapped[Optional[float]] = mapped_column(Float)
    ping_ms: Mapped[Optional[float]] = mapped_column(Float)
    jitter_ms: Mapped[Optional[float]] = mapped_column(Float)
    error: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    __table_args__ = (Index("ix_speed_tests_monitor_requested", "monitor_id", "requested_at"),)


class SpeedSample(Base):
    """One reading during a speed test, about every half second."""

    __tablename__ = "speed_samples"

    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("speed_tests.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    t_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    mbps: Mapped[float] = mapped_column(Float, nullable=False)


class WebServiceSetting(Base):
    __tablename__ = "web_service_settings"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    http_url: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    http_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    icmp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mtr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    jitter_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    http_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    icmp_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    jitter_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)


class InternetServiceSetting(Base):
    """Singleton path, Bandwidth Test, and video-call schedule for the Internet Service card."""

    __tablename__ = "internet_service_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    http_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    icmp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mtr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    http_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    icmp_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    mtr_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    librespeed_variance_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    librespeed_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    librespeed_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)


class WebStabilitySetting(Base):
    """One row. These numbers apply to every web service."""

    __tablename__ = "web_stability_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    window_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    degraded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    recover_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    pct_floor: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    abs_floor_http_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    abs_floor_icmp_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    abs_floor_jitter_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    quorum_degraded_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    quorum_fail_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=40)


class RtnpSecret(Base):
    """One shared key for the public RTNP receiver. Every monitor is given this key."""

    __tablename__ = "rtnp_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)


class SyntheticProfile(Base):
    __tablename__ = "synthetic_profiles"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SyntheticRun(Base):
    __tablename__ = "synthetic_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    profile_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    links: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    finished_links: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stress_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("stress_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )

    samples: Mapped[list["SyntheticSample"]] = relationship(back_populates="run")


class StressRun(Base):
    """One stress test: a group of sessions that start, change and stop together."""

    __tablename__ = "stress_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    links: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SyntheticSample(Base):
    __tablename__ = "synthetic_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("synthetic_runs.id", ondelete="CASCADE"), nullable=False)
    link: Mapped[str] = mapped_column(String(16), nullable=False)
    sample_key: Mapped[str] = mapped_column(String(64), nullable=False)
    interval_index: Mapped[Optional[int]] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rtt_ms: Mapped[Optional[float]] = mapped_column(Float)
    jitter_ms: Mapped[Optional[float]] = mapped_column(Float)
    loss_pct: Mapped[Optional[float]] = mapped_column(Float)
    verdict: Mapped[Optional[str]] = mapped_column(String(16))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[SyntheticRun] = relationship(back_populates="samples")

    __table_args__ = (
        UniqueConstraint("run_id", "link", "sample_key", name="uq_synthetic_sample"),
        Index("ix_synthetic_sample_recorded", "recorded_at"),
        Index("ix_synthetic_samples_run_link_recorded", "run_id", "link", "recorded_at"),
    )


class AdhocTrace(Base):
    """A trace someone asked a remote agent to run on demand, to any host they typed in."""

    __tablename__ = "adhoc_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False, index=True)
    link: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending: waiting for the agent. running: handed to the agent. done or failed: finished.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    hops: Mapped[Optional[list[Any]]] = mapped_column(JSONB)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SystemLog(Base):
    """Consolidated server and remote agent operational log entry."""

    __tablename__ = "system_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # server | agent
    monitor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), nullable=True
    )
    subsystem: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_system_logs_recorded_at", "recorded_at"),
        Index("ix_system_logs_monitor_recorded", "monitor_id", "recorded_at"),
    )


class ServerSample(Base):
    """One minute of the HomeNetWatch server: CPU, memory, disk, database, the app NIC, and the machine NIC."""

    __tablename__ = "server_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float)
    mem_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    db_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    connections: Mapped[int] = mapped_column(Integer, nullable=False)
    iface: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    rx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    host_iface: Mapped[Optional[str]] = mapped_column(String(32))
    host_rx_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    host_tx_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_server_samples_recorded", "recorded_at"),)


class ServerDbDaily(Base):
    """One database size per UTC day, kept for the disk-full forecast."""

    __tablename__ = "server_db_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    db_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    free_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disk_used_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    disk_total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class ServerStorage(Base):
    """The cached breakdown of what uses the disk. One row."""

    __tablename__ = "server_storage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ServerAdvisory(Base):
    """The cached weekly package and vulnerability check. One row."""

    __tablename__ = "server_advisories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ServerStuck(Base):
    """A host process that currently looks stuck."""

    __tablename__ = "server_stuck"

    pid: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(16))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    d_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hot_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpu_ticks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
