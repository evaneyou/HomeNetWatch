from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class MonitorLocationIn(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_label: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    name: Optional[str] = None
    health_interval_seconds: Optional[int] = None


_KEY = Field(min_length=44, max_length=44)
_SIGNATURE = Field(min_length=1, max_length=128)
_NONCE = Field(min_length=1, max_length=64)


class NonceIn(BaseModel):
    identity_public_key: str = _KEY


class NonceOut(BaseModel):
    nonce: str
    expires_in: int
    server_time: datetime


class CheckInIn(BaseModel):
    identity_public_key: str = _KEY
    wg_public_key: str = _KEY
    wg_wireless_public_key: Optional[str] = None
    installer_code: str = Field(min_length=1, max_length=32)
    hostname: str = Field(min_length=1, max_length=253)
    lan_ip: str = Field(min_length=1, max_length=64)
    nonce: str = _NONCE
    signature: str = _SIGNATURE


class CheckInOut(BaseModel):
    state: str  # waiting_for_approval | approved
    conflict: bool = False
    server_time: datetime


class PollIn(BaseModel):
    identity_public_key: str = _KEY
    have_token: bool
    nonce: str = _NONCE
    signature: str = _SIGNATURE


class TunnelConfigOut(BaseModel):
    link: str = "wired"
    address: str
    server_public_key: str
    endpoint: str
    allowed_ips: str
    persistent_keepalive: int
    preshared_key: Optional[str] = None


class PollOut(BaseModel):
    state: str  # waiting_for_approval | approved
    server_time: datetime
    monitor_id: Optional[UUID] = None
    name: Optional[str] = None
    token: Optional[str] = None
    tunnel: Optional[TunnelConfigOut] = None
    tunnels: list[TunnelConfigOut] = Field(default_factory=list)


class InstallerCodeIn(BaseModel):
    installer_code: str = Field(min_length=1, max_length=32)
    device_name: str = Field(min_length=1, max_length=128)
    location: Optional[str] = Field(default=None, max_length=128)


class ApproveIn(BaseModel):
    replace_monitor_id: Optional[UUID] = None


class CheckinOut(BaseModel):
    id: UUID
    hostname: str
    lan_ip: str
    observed_ip: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime


class RegistrationOut(BaseModel):
    id: UUID
    device_name: str
    location: Optional[str] = None
    registered_by: str
    created_at: Optional[datetime] = None
    expires_at: datetime
    conflict: bool
    checkins: list[CheckinOut] = Field(default_factory=list)


class MonitorAdminOut(BaseModel):
    id: UUID
    slug: str
    name: str
    status: str
    last_seen_at: Optional[datetime] = None
    lan_ip: Optional[str] = None
    public_ip: Optional[str] = None
    isp: Optional[str] = None
    agent_version: Optional[str] = None
    location_label: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    tunnel_address: Optional[str] = None
    wireless_tunnel_address: Optional[str] = None
    # never_connected | connected | disconnected, or None for agents added before installer codes
    tunnel_state: Optional[str] = None
    wireless_tunnel_state: Optional[str] = None
    last_handshake_at: Optional[datetime] = None
    health_interval_seconds: int
    config_version: int
    applied_config_version: int
    restart_requested_at: Optional[datetime] = None


class AgentLogIn(BaseModel):
    subsystem: str = Field(default="agent", max_length=32)
    severity: str = Field(default="info", max_length=16)
    message: str = Field(min_length=1, max_length=600)
    recorded_at: Optional[datetime] = None


class DeviceHealthIn(BaseModel):
    hostname: str = Field(min_length=1, max_length=128)
    agent_version: str = Field(min_length=1, max_length=32)
    uptime_seconds: int = Field(ge=0)
    cpu_percent: float = Field(ge=0, le=100)
    load_1: float = Field(ge=0)
    mem_used_mb: int = Field(ge=0)
    mem_total_mb: int = Field(ge=0)
    disk_used_mb: int = Field(ge=0)
    disk_total_mb: int = Field(ge=0)
    temp_c: Optional[float] = None
    lan_ip: Optional[str] = Field(default=None, max_length=64)
    public_ip: Optional[str] = Field(default=None, max_length=64)
    isp: Optional[str] = Field(default=None, max_length=128)
    config_version: int = Field(default=0, ge=0)
    airwave_mode: Optional[str] = None
    wired_iface: Optional[str] = None
    wifi_link_iface: Optional[str] = None
    wifi_radio_iface: Optional[str] = None
    link_ssid: Optional[str] = Field(default=None, max_length=64)
    link_bssid: Optional[str] = Field(default=None, max_length=17)
    last_error: Optional[str] = Field(default=None, max_length=600)
    logs: list[AgentLogIn] = Field(default_factory=list)

    @field_validator("public_ip")
    @classmethod
    def _public_address(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            return None
        if not address.is_global:
            return None
        return str(address)

    @field_validator("isp")
    @classmethod
    def _provider(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        cleaned = " ".join(value.split())
        return cleaned[:128] or None

    @field_validator("wired_iface", "wifi_link_iface", "wifi_radio_iface")
    @classmethod
    def _iface_name(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,31}", cleaned):
            return None
        return cleaned


class ScheduledTestOut(BaseModel):
    slug: str
    name: str
    kind: str
    target: str
    network_slug: str
    interval_seconds: int
    # wired, wireless, or wan. The agent pins wired and wireless probes to that NIC.
    link: str = "wan"


class TunnelIssueOut(BaseModel):
    id: UUID
    name: str
    address: str
    config: str
    wireless_address: str
    wireless_config: str


class DeviceConfigOut(BaseModel):
    name: str
    health_interval_seconds: int
    scan_interval_seconds: int = 300
    config_version: int
    tests: list[ScheduledTestOut] = Field(default_factory=list)
    restart: bool = False
    scan: bool = False
    lan_scan: bool = False
    trusted_bssids: list[str] = Field(default_factory=list)


class TraceRequestIn(BaseModel):
    link: str = Field(pattern="^(wired|wireless)$")
    target: str = Field(min_length=1, max_length=300)


class TraceResultIn(BaseModel):
    hops: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    error: str = Field(default="", max_length=300)


class ProbeCommandOut(BaseModel):
    restart: bool
    scan: bool = False
    lan_scan: bool = False
    traces: list[dict[str, str]] = Field(default_factory=list)
    trusted_bssids: list[str] = Field(default_factory=list)


class GuestVisitOut(BaseModel):
    created_at: datetime
    address: str
    country: str = ""
    country_code: str = ""
    region: str = ""
    city: str = ""


class GuestPeriodOut(BaseModel):
    date: str
    count: int


class GuestCountryOut(BaseModel):
    country: str
    country_code: str
    count: int
    unique: int


class GuestCityOut(BaseModel):
    city: str
    region: str
    country: str
    count: int


class GuestSummaryOut(BaseModel):
    range: str
    days: int
    since: datetime
    until: datetime
    bucket: str
    total: int
    previous_total: int
    unique_addresses: int
    previous_unique: int
    countries: int
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None
    busiest: Optional[GuestPeriodOut] = None
    by_period: list[GuestPeriodOut]
    by_country: list[GuestCountryOut]
    by_city: list[GuestCityOut]
    retention_days: int
    located: bool


class UserOut(BaseModel):
    id: UUID
    username: str
    role: str
    csrf_token: Optional[str] = None

    model_config = {"from_attributes": True}


class StageIn(BaseModel):
    name: str
    duration_ms: float
    status: Optional[str] = None


class IngestResult(BaseModel):
    id: str = Field(..., description="Client-side unique result id for idempotency")
    test_slug: str
    network_slug: str
    started_at: datetime
    finished_at: datetime
    status: str
    stages: list[StageIn] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class IngestRequest(BaseModel):
    results: list[IngestResult]


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    ids: list[str]


class HealthCell(BaseModel):
    monitor_id: UUID
    monitor_slug: str
    monitor_name: str
    network_id: UUID
    network_slug: str
    network_name: str
    status: str  # ok | degraded | fail | unknown
    column: str = ""
    last_test_at: Optional[datetime] = None
    failing_tests: int = 0
    quorum: Optional[dict[str, int]] = None
    cause: Optional[str] = None


class IssueSummary(BaseModel):
    id: UUID
    title: str
    severity: str
    status: str
    opened_at: datetime
    root_cause_stage: Optional[str] = None
    monitor_slug: Optional[str] = None
    monitor_name: Optional[str] = None
    network_slug: Optional[str] = None
    network_name: Optional[str] = None
    test_name: Optional[str] = None


class SummaryResponse(BaseModel):
    generated_at: datetime
    matrix: list[HealthCell]
    open_issues: list[IssueSummary]
    monitors: list[dict[str, Any]]
    networks: list[dict[str, Any]]
    diagnoses: dict[str, dict[str, str]] = Field(default_factory=dict)


class ResultOut(BaseModel):
    id: UUID
    external_id: str
    test_id: UUID
    test_slug: str
    test_name: str
    test_kind: str
    monitor_id: UUID
    monitor_slug: str
    network_id: UUID
    network_slug: str
    network_name: str
    started_at: datetime
    finished_at: datetime
    status: str
    stages: list[dict[str, Any]]
    metrics: dict[str, Any]
    message: Optional[str] = None
    status_reason: Optional[str] = None


class ResultPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ResultOut]


class UnifiedEventOut(BaseModel):
    recorded_at: datetime
    event_id: UUID
    monitor_id: Optional[UUID] = None
    monitor_slug: Optional[str] = None
    monitor_name: Optional[str] = None
    category: str
    subsystem: str
    network: str = ""
    severity: str
    title: str
    message: str = ""
    rtt_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnifiedEventPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[UnifiedEventOut]


class HopMetric(BaseModel):
    hop: int
    host: str
    loss_pct: float
    best_ms: float
    avg_ms: float
    worst_ms: float


class ThroughputMbps(BaseModel):
    upload: Optional[float] = None
    download: Optional[float] = None


class MetricsPoint(BaseModel):
    t: datetime
    status: str
    rtt_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    jitter_ms: Optional[float] = None
    up_mbps: Optional[float] = None
    down_mbps: Optional[float] = None
    hops: list[HopMetric] = Field(default_factory=list)
    throughput_mbps: Optional[ThroughputMbps] = None
    stages: list[dict[str, Any]] = Field(default_factory=list)


class MetricsResponse(BaseModel):
    test_id: UUID
    test_slug: str
    points: list[MetricsPoint]


class WifiObservationOut(BaseModel):
    scanned_at: datetime
    ssid: str
    bssid: str
    vendor: str
    band: str
    channel: int
    channel_width: int
    rssi_dbm: float
    noise_dbm: Optional[float] = None
    security: str
    phy: str
    max_link_rate_mbps: int


class WifiScanIn(BaseModel):
    scanned_at: datetime
    raw: str = ""
    survey: str = ""
    scan_iface_role: Optional[str] = None


class RfBandAdvice(BaseModel):
    band: str
    ap_count: int
    busiest_channel: Optional[int] = None
    recommended_channel: int
    reason: str


class RfSparkPoint(BaseModel):
    t: datetime
    ap_count: int
    count_24: int = 0
    count_5: int = 0


class RfOverviewOut(BaseModel):
    agent_id: UUID
    agent_name: str
    location_label: Optional[str] = None
    last_scan_at: Optional[datetime] = None
    scan_requested: bool
    bands: list[RfBandAdvice]
    headline: str
    sparkline: list[RfSparkPoint]
    airwave_mode: str = "off"
    alerts: list["RfAlertOut"] = Field(default_factory=list)


class RfApOut(BaseModel):
    bssid: str
    ssid: str
    hidden: bool
    band: str
    channel: int
    channel_width: int
    rssi_dbm: float
    security: str
    vendor: str
    phy: str
    first_seen: datetime
    last_seen: datetime
    trusted: bool
    label: str


class RfHistoryPoint(BaseModel):
    t: datetime
    rssi_dbm: float
    channel: int


class RfHillPoint(BaseModel):
    channel: int
    rssi: float


class RfHillSeries(BaseModel):
    bssid: str
    ssid: str
    hidden: bool
    trusted: bool
    channel: int
    channel_width: int
    rssi_dbm: float
    security: str
    points: list[RfHillPoint]


class RfChannelBusy(BaseModel):
    channel: int
    noise_dbm: Optional[float] = None
    busy_pct: Optional[float] = None
    airtime_pct: Optional[float] = None
    packets: Optional[int] = None


class RfChannelSnapshotOut(BaseModel):
    band: str
    recommended_channel: int
    reason: str
    series: list[RfHillSeries]
    channels: list[RfChannelBusy] = Field(default_factory=list)


class RfAlertOut(BaseModel):
    alert_type: str
    bssid: str
    text: str
    observed_at: datetime
    severity: str = "info"


class RfClientPoint(BaseModel):
    t: datetime
    rssi_dbm: float
    bssid: str
    roam: bool = False


class RfClientOut(BaseModel):
    mac: str
    bssid: str
    ssid: str = ""
    vendor: str = ""
    randomized: bool
    signal: Optional[float] = None
    signal_min: Optional[float] = None
    signal_max: Optional[float] = None
    last_seen: datetime
    first_seen: Optional[datetime] = None
    band: str = ""
    channel: Optional[int] = None
    packets: Optional[int] = None
    data_bytes: Optional[int] = None
    retries: Optional[int] = None
    disconnects: Optional[int] = None
    probed_ssids: list[str] = Field(default_factory=list)
    points: list[RfClientPoint]


class RfCaptureAp(BaseModel):
    bssid: str
    ssid: str = ""
    band: str
    channel: int
    signal: float = -100
    client_count: int = 0


class RfCaptureClient(BaseModel):
    mac: str
    bssid: str
    signal: Optional[float] = None
    last_seen: int = 0
    randomized: bool = False
    vendor: str = ""
    signal_min: Optional[float] = None
    signal_max: Optional[float] = None
    first_seen: int = 0
    band: str = ""
    channel: Optional[int] = None
    packets: Optional[int] = None
    data_bytes: Optional[int] = None
    retries: Optional[int] = None
    disconnects: Optional[int] = None
    probed_ssids: list[str] = Field(default_factory=list)


class RfCaptureChannel(BaseModel):
    band: str
    channel: int
    airtime_pct: Optional[float] = None
    packets: Optional[int] = None
    busy_pct: Optional[float] = None
    devices: Optional[int] = None


class RfCaptureAlert(BaseModel):
    type: str
    bssid: str = ""
    text: str = ""
    ts: int = 0
    hash: str = ""


class RfCaptureIn(BaseModel):
    captured_at: datetime
    aps: list[RfCaptureAp] = Field(default_factory=list)
    clients: list[RfCaptureClient] = Field(default_factory=list)
    channels: list[RfCaptureChannel] = Field(default_factory=list)
    alerts: list[RfCaptureAlert] = Field(default_factory=list)


class RfScanRequestOut(BaseModel):
    agent_id: UUID
    agent_name: str
    scan_requested_at: datetime


class LanScanHostIn(BaseModel):
    ip: str = Field(min_length=1, max_length=64)
    mac: str = Field(default="", max_length=17)
    vendor: str = Field(default="", max_length=128)
    ports: list[int] = Field(default_factory=list, max_length=64)


class LanScanIn(BaseModel):
    started_at: datetime
    finished_at: datetime
    status: str = Field(pattern="^(finished|failed)$")
    error: str = Field(default="", max_length=400)
    subnet: str = Field(default="", max_length=64)
    hosts: list[LanScanHostIn] = Field(default_factory=list, max_length=512)


class LanScanHostOut(BaseModel):
    ip: str
    mac: str = ""
    vendor: str = ""
    ports: list[int] = Field(default_factory=list)


class LanScanRunOut(BaseModel):
    id: UUID
    status: str
    subnet: str = ""
    error: str = ""
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    hosts: list[LanScanHostOut] = Field(default_factory=list)


class LanScanRequestOut(BaseModel):
    agent_id: UUID
    agent_name: str
    run_id: UUID
    lan_scan_requested_at: datetime


class SecurityAttentionOut(BaseModel):
    bssid: str
    ssid: str = ""
    reason: str
    issue_title: str = ""


class SecuritySummaryOut(BaseModel):
    airwave_mode: str = "off"
    open_issues: int = 0
    attention: list[SecurityAttentionOut] = Field(default_factory=list)
    last_lan_scan: Optional[LanScanRunOut] = None


class WebServiceSettingOut(BaseModel):
    key: str
    label: str
    interval_seconds: int
    http_enabled: bool
    icmp_enabled: bool
    jitter_enabled: bool
    http_variance_pct: int
    icmp_variance_pct: int
    jitter_variance_pct: int


class WebServiceSettingIn(BaseModel):
    key: str = Field(min_length=1, max_length=32)
    interval_seconds: int = Field(ge=60, le=86400)
    http_enabled: bool
    icmp_enabled: bool
    jitter_enabled: bool
    http_variance_pct: int = Field(ge=0, le=500)
    icmp_variance_pct: int = Field(ge=0, le=500)
    jitter_variance_pct: int = Field(ge=0, le=500)


class WebServiceSettingsIn(BaseModel):
    services: list[WebServiceSettingIn]


class WebStabilityOut(BaseModel):
    poll_interval_seconds: int
    window_size: int
    degraded_count: int
    fail_count: int
    recover_count: int
    pct_floor: int
    abs_floor_http_ms: int
    abs_floor_icmp_ms: int
    abs_floor_jitter_ms: int
    quorum_degraded_pct: int
    quorum_fail_pct: int
    preset: str


class WebStabilityIn(BaseModel):
    poll_interval_seconds: int = Field(ge=60, le=86400)
    window_size: int = Field(ge=3, le=30)
    degraded_count: int = Field(ge=1, le=30)
    fail_count: int = Field(ge=1, le=30)
    recover_count: int = Field(ge=1, le=30)
    pct_floor: int = Field(ge=0, le=500)
    abs_floor_http_ms: int = Field(ge=0, le=5000)
    abs_floor_icmp_ms: int = Field(ge=0, le=5000)
    abs_floor_jitter_ms: int = Field(ge=0, le=5000)
    quorum_degraded_pct: int = Field(ge=1, le=100)
    quorum_fail_pct: int = Field(ge=1, le=100)


class InternetServiceSettingOut(BaseModel):
    interval_seconds: int
    http_enabled: bool
    icmp_enabled: bool
    mtr_enabled: bool
    http_variance_pct: int
    icmp_variance_pct: int
    mtr_variance_pct: int
    librespeed_enabled: bool
    librespeed_interval_seconds: int
    librespeed_variance_pct: int


class InternetServiceSettingIn(BaseModel):
    interval_seconds: int = Field(ge=60, le=86400)
    http_enabled: bool
    icmp_enabled: bool
    mtr_enabled: bool
    http_variance_pct: int = Field(ge=0, le=500)
    icmp_variance_pct: int = Field(ge=0, le=500)
    mtr_variance_pct: int = Field(ge=0, le=500)
    librespeed_enabled: bool
    librespeed_interval_seconds: int = Field(ge=300, le=86400)
    librespeed_variance_pct: int = Field(ge=0, le=500)


class IssueOut(BaseModel):
    id: UUID
    title: str
    summary: str
    severity: str
    status: str
    root_cause_stage: Optional[str] = None
    opened_at: datetime
    resolved_at: Optional[datetime] = None
    monitor_id: Optional[UUID] = None
    monitor_slug: Optional[str] = None
    network_id: Optional[UUID] = None
    network_slug: Optional[str] = None
    test_id: Optional[UUID] = None
    test_slug: Optional[str] = None
    recent_failures: list[ResultOut] = Field(default_factory=list)
