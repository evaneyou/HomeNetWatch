export type User = {
  id: string;
  username: string;
  role: "admin" | "viewer" | "guest";
  csrf_token?: string | null;
};

export type HealthCell = {
  monitor_id: string;
  monitor_slug: string;
  monitor_name: string;
  network_id: string;
  network_slug: string;
  network_name: string;
  column: string;
  status: "ok" | "degraded" | "fail" | "unknown";
  last_test_at: string | null;
  failing_tests: number;
  quorum?: { bad: number; total: number } | null;
  cause?: "wifi" | "wired" | "wan" | "bandwidth" | "tunnel" | "server" | "agent" | "service" | null;
};

export type IssueSummary = {
  id: string;
  title: string;
  severity: "info" | "warning" | "critical";
  status: string;
  opened_at: string;
  root_cause_stage: string | null;
  monitor_slug: string | null;
  monitor_name: string | null;
  network_slug: string | null;
  network_name: string | null;
  test_name: string | null;
};

export type Summary = {
  generated_at: string;
  matrix: HealthCell[];
  open_issues: IssueSummary[];
  diagnoses?: Record<string, { cause: string; title: string; summary: string }>;
  monitors: {
    id: string;
    slug: string;
    name: string;
    status: string;
    location_label: string | null;
    region: string | null;
    last_seen_at: string | null;
  }[];
  networks: { id: string; slug: string; name: string; kind: string }[];
};

export type WifiObservation = {
  scanned_at: string;
  ssid: string;
  bssid: string;
  anon_key?: string;
  vendor: string;
  band: string;
  channel: number;
  channel_width: number;
  rssi_dbm: number;
  noise_dbm: number;
  security: string;
  phy: string;
  max_link_rate_mbps: number;
};

export type RfBandAdvice = {
  band: string;
  ap_count: number;
  busiest_channel: number | null;
  recommended_channel: number;
  reason: string;
};

export type RfAlert = {
  alert_type: string;
  bssid: string;
  text: string;
  observed_at: string;
  severity?: "info" | "warning" | "critical";
};

export type RfOverview = {
  agent_id: string;
  agent_name: string;
  location_label: string | null;
  last_scan_at: string | null;
  scan_requested: boolean;
  bands: RfBandAdvice[];
  headline: string;
  sparkline: { t: string; ap_count: number; count_24: number; count_5: number }[];
  airwave_mode: string;
  alerts: RfAlert[];
};

export type LanScanHost = { ip: string; mac: string; vendor: string; hostname: string; ports: number[] };

export type LanScanRun = {
  id: string;
  status: string;
  subnet: string;
  error: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  hosts: LanScanHost[];
};

export type SecurityAttention = { bssid: string; ssid: string; reason: string; issue_title: string };

export type SecuritySummary = {
  airwave_mode: string;
  open_issues: number;
  attention: SecurityAttention[];
  last_lan_scan: LanScanRun | null;
};

export type RfAp = {
  bssid: string;
  anon_key?: string;
  ssid: string;
  hidden: boolean;
  band: string;
  channel: number;
  channel_width: number;
  rssi_dbm: number;
  security: string;
  vendor: string;
  phy: string;
  first_seen: string;
  last_seen: string;
  trusted: boolean;
  label: string;
};

export type RfHistoryPoint = { t: string; rssi_dbm: number; channel: number };

export type RfClient = {
  mac: string;
  anon_key?: string;
  bssid: string;
  ssid: string;
  vendor: string;
  randomized: boolean;
  signal: number | null;
  signal_min: number | null;
  signal_max: number | null;
  last_seen: string;
  first_seen: string | null;
  band: string;
  channel: number | null;
  packets: number | null;
  data_bytes: number | null;
  retries: number | null;
  disconnects: number | null;
  probed_ssids: string[];
  points: { t: string; rssi_dbm: number; bssid: string; roam: boolean }[];
};

export type RfChannelSnapshot = {
  band: string;
  recommended_channel: number;
  reason: string;
  channels: {
    channel: number;
    noise_dbm: number | null;
    busy_pct: number | null;
    airtime_pct: number | null;
    packets: number | null;
  }[];
  series: {
    bssid: string;
    anon_key?: string;
    ssid: string;
    hidden: boolean;
    trusted: boolean;
    channel: number;
    channel_width: number;
    rssi_dbm: number;
    security: string;
    points: { channel: number; rssi: number }[];
  }[];
};

export type Result = {
  id: string;
  external_id: string;
  test_id: string;
  test_slug: string;
  test_name: string;
  test_kind: string;
  monitor_id: string;
  monitor_slug: string;
  network_id: string;
  network_slug: string;
  network_name: string;
  started_at: string;
  finished_at: string;
  status: string;
  stages: { name: string; duration_ms: number; status?: string }[];
  metrics: Record<string, number>;
  message: string | null;
  status_reason: string | null;
};

export type ResultPage = {
  total: number;
  offset: number;
  limit: number;
  items: Result[];
};

export type HopMetric = {
  hop: number;
  host: string;
  loss_pct: number;
  best_ms: number;
  avg_ms: number;
  worst_ms: number;
};

export type ThroughputMbps = {
  upload: number | null;
  download: number | null;
};

export type MetricsResponse = {
  test_id: string;
  test_slug: string;
  points: {
    t: string;
    status: string;
    rtt_ms: number | null;
    loss_pct: number | null;
    jitter_ms: number | null;
    up_mbps: number | null;
    down_mbps: number | null;
    hops: HopMetric[];
    throughput_mbps: ThroughputMbps | null;
    stages: { name: string; duration_ms: number; status?: string }[];
  }[];
};

export type Issue = {
  id: string;
  title: string;
  summary: string;
  severity: string;
  status: string;
  root_cause_stage: string | null;
  opened_at: string;
  resolved_at: string | null;
  monitor_id: string | null;
  monitor_slug: string | null;
  network_id: string | null;
  network_slug: string | null;
  test_id: string | null;
  test_slug: string | null;
  recent_failures: Result[];
};

export type DeviceSample = {
  reported_at: string;
  hostname: string;
  uptime_seconds: number;
  cpu_percent: number;
  load_1: number;
  mem_used_mb: number;
  mem_total_mb: number;
  disk_used_mb: number;
  disk_total_mb: number;
  temp_c: number | null;
  lan_ip: string | null;
  last_error: string | null;
};

export type DeviceReport = {
  monitor_id: string;
  name: string;
  connected: boolean;
  last_seen_at: string | null;
  lan_ip: string | null;
  public_ip: string | null;
  isp: string | null;
  agent_version: string | null;
  hostname: string | null;
  config_version: number;
  applied_config_version: number;
  health_interval_seconds: number;
  restart_requested_at: string | null;
  location_label: string | null;
  region: string | null;
  country: string | null;
  latitude: number | string | null;
  longitude: number | string | null;
  latest: DeviceSample | null;
  points: {
    t: string;
    cpu_percent: number;
    temp_c: number | null;
    mem_used_mb: number;
    disk_used_mb: number;
  }[];
};

export type MonitorAdmin = {
  id: string;
  slug: string;
  name: string;
  status: string;
  last_seen_at: string | null;
  lan_ip: string | null;
  agent_version: string | null;
  location_label: string | null;
  region: string | null;
  country: string | null;
  tunnel_address: string | null;
  wireless_tunnel_address: string | null;
  // null for agents added before installer-code enrollment
  tunnel_state: "never_connected" | "connected" | "disconnected" | null;
  wireless_tunnel_state: "never_connected" | "connected" | "disconnected" | null;
  last_handshake_at: string | null;
  health_interval_seconds: number;
  config_version: number;
  applied_config_version: number;
  restart_requested_at: string | null;
};

export type EnrollmentCheckin = {
  id: string;
  hostname: string;
  lan_ip: string;
  observed_ip: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

export type EnrollmentRegistration = {
  id: string;
  device_name: string;
  location: string | null;
  registered_by: string;
  created_at: string | null;
  expires_at: string;
  conflict: boolean;
  setup_key?: string | null;
  checkins: EnrollmentCheckin[];
};

export type MonitorKey = {
  id: string;
  name: string;
  key: string;
  tunnel_address?: string | null;
  tunnel_config?: string | null;
  wireless_tunnel_address?: string | null;
  wireless_tunnel_config?: string | null;
};

export type TunnelIssue = {
  id: string;
  name: string;
  address: string;
  config: string;
  wireless_address: string;
  wireless_config: string;
};

export type MonitorDetail = {
  id: string;
  slug: string;
  name: string;
  status: string;
  last_seen_at: string | null;
  latitude: number | string | null;
  longitude: number | string | null;
  location_label: string | null;
  lan_ip?: string | null;
  tunnel_address?: string | null;
  wireless_tunnel_address?: string | null;
  public_ip: string | null;
  isp: string | null;
  wired_iface?: string | null;
  wifi_link_iface?: string | null;
  wifi_radio_iface?: string | null;
  airwave_mode?: string | null;
  probes?: { slug: string; name: string }[];
  tests: {
    id: string;
    slug: string;
    name: string;
    kind: string;
    target: string;
    network_id: string;
    network_slug: string;
    network_name: string;
    latest_status: string;
    latest_at: string | null;
    latest_reason: string | null;
    latest_rtt_ms: number | null;
    latest_loss_pct: number | null;
    enabled: boolean;
  }[];
};

export type WebStability = {
  poll_interval_seconds: number;
  window_size: number;
  degraded_count: number;
  fail_count: number;
  recover_count: number;
  pct_floor: number;
  abs_floor_http_ms: number;
  abs_floor_icmp_ms: number;
  abs_floor_jitter_ms: number;
  quorum_degraded_pct: number;
  quorum_fail_pct: number;
  preset: string;
};

export type WebServiceSetting = {
  key: string;
  label: string;
  interval_seconds: number;
  http_enabled: boolean;
  icmp_enabled: boolean;
  jitter_enabled: boolean;
  http_variance_pct: number;
  icmp_variance_pct: number;
  jitter_variance_pct: number;
};

export type InternetServiceSetting = {
  interval_seconds: number;
  http_enabled: boolean;
  icmp_enabled: boolean;
  mtr_enabled: boolean;
  http_variance_pct: number;
  icmp_variance_pct: number;
  mtr_variance_pct: number;
  librespeed_enabled: boolean;
  librespeed_interval_seconds: number;
  librespeed_variance_pct: number;
};

export type TestDetail = {
  id: string;
  slug: string;
  name: string;
  kind: string;
  target: string;
  interval_seconds: number;
  monitor_id: string;
  monitor_slug: string;
  monitor_name: string;
  network_id: string;
  network_slug: string;
  network_name: string;
};
