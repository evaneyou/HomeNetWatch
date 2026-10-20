import type { EnrollmentRegistration, InternetServiceSetting, RfAp, RfChannelSnapshot, RfClient, RfHistoryPoint, RfOverview, WebServiceSetting, WebStability } from "./types";

// The session cookie is stored for the API host only. A page opened at
// localhost cannot use a cookie set by 127.0.0.1, so keep the API on the
// same hostname as the page.
function apiBase(): string {
  const configured = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8847";
  if (typeof window === "undefined") return configured;
  try {
    const url = new URL(configured);
    if (window.location.hostname && url.hostname !== window.location.hostname) {
      url.hostname = window.location.hostname;
    }
    return url.origin;
  } catch {
    return configured;
  }
}

const API_BASE = apiBase();

type JsonInit = Omit<RequestInit, "headers"> & { headers?: Record<string, string> };

async function request<T>(path: string, init?: JsonInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function csrfHeaders(token: string): Record<string, string> {
  return { "X-CSRF-Token": token };
}

export const api = {
  base: API_BASE,
  login: (username: string, password: string) =>
    request("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),
  me: () => request("/api/v1/auth/me"),
  summary: () => request("/api/v1/summary"),
  results: (params: Record<string, string>) => {
    const q = new URLSearchParams(params).toString();
    return request(`/api/v1/results?${q}`);
  },
  metrics: (testId: string, range: string) =>
    request(`/api/v1/metrics?test_id=${testId}&range=${range}`),
  issues: (status?: string) =>
    request(`/api/v1/issues${status ? `?status=${status}` : ""}`),
  issue: (id: string) => request(`/api/v1/issues/${id}`),
  monitors: () => request("/api/v1/monitors"),
  enrollments: () => request<EnrollmentRegistration[]>("/api/v1/enrollment"),
  registerEnrollment: (
    body: { installer_code: string; device_name: string; location?: string | null },
    csrf: string,
  ) =>
    request<EnrollmentRegistration>("/api/v1/enrollment/codes", {
      method: "POST",
      headers: csrfHeaders(csrf),
      body: JSON.stringify(body),
    }),
  approveEnrollment: (id: string, replaceMonitorId: string | null, csrf: string) =>
    request(`/api/v1/enrollment/codes/${id}/approve`, {
      method: "POST",
      headers: csrfHeaders(csrf),
      body: JSON.stringify(replaceMonitorId ? { replace_monitor_id: replaceMonitorId } : {}),
    }),
  startOverEnrollment: (id: string, csrf: string) =>
    request<EnrollmentRegistration>(`/api/v1/enrollment/codes/${id}/start-over`, {
      method: "POST",
      headers: csrfHeaders(csrf),
    }),
  removeEnrollment: (id: string, csrf: string) =>
    request<void>(`/api/v1/enrollment/codes/${id}`, {
      method: "DELETE",
      headers: csrfHeaders(csrf),
    }),
  deleteMonitor: (id: string, csrf: string) =>
    request<void>(`/api/v1/monitors/${id}`, {
      method: "DELETE",
      headers: csrfHeaders(csrf),
    }),
  issueMonitorTunnel: (id: string, csrf: string) =>
    request(`/api/v1/monitors/${id}/tunnel`, { method: "POST", headers: csrfHeaders(csrf) }),
  restartMonitor: (id: string, csrf: string) =>
    request(`/api/v1/monitors/${id}/restart`, { method: "POST", headers: csrfHeaders(csrf) }),
  monitor: (id: string) => request(`/api/v1/monitors/${id}`),
  monitorPath: (id: string, link: "wired" | "wireless", destination = "server") =>
    request(`/api/v1/monitors/${id}/path?link=${link}&destination=${encodeURIComponent(destination)}`),
  requestTrace: (id: string, link: "wired" | "wireless", target: string, csrf: string) =>
    request(`/api/v1/monitors/${id}/traces`, { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify({ link, target }) }),
  monitorTrace: (id: string, traceId: string) => request(`/api/v1/monitors/${id}/traces/${traceId}`),
  monitorLog: (id: string, offset: number, limit: number, probe = "all") =>
    request(`/api/v1/monitors/${id}/log?offset=${offset}&limit=${limit}&probe=${encodeURIComponent(probe)}`),
  updateMonitorLocation: (
    id: string,
    body: {
      latitude?: number | null;
      longitude?: number | null;
      location_label?: string | null;
      region?: string | null;
      country?: string | null;
      name?: string;
      health_interval_seconds?: number;
    },
    csrf: string,
  ) =>
    request(`/api/v1/monitors/${id}`, {
      method: "PATCH",
      headers: csrfHeaders(csrf),
      body: JSON.stringify(body),
    }),
  monitorDevice: (id: string) => request(`/api/v1/monitors/${id}/device`),
  monitorHealth: (id: string, range: string) =>
    request(`/api/v1/monitors/${id}/health?range=${range}`),
  wifiScans: (monitorId: string, range: string) =>
    request(`/api/v1/wifi-scans?monitor_id=${monitorId}&range=${range}`),
  test: (id: string) => request(`/api/v1/tests/${id}`),
  webServices: () => request("/api/v1/settings/web-services"),
  saveWebServices: (services: WebServiceSetting[], csrf: string) =>
    request("/api/v1/settings/web-services", {
      method: "PUT",
      headers: csrfHeaders(csrf),
      body: JSON.stringify({ services }),
    }),
  webStability: () => request<WebStability>("/api/v1/settings/web-stability"),
  saveWebStability: (setting: WebStability, csrf: string) =>
    request<WebStability>("/api/v1/settings/web-stability", {
      method: "PUT",
      headers: csrfHeaders(csrf),
      body: JSON.stringify(setting),
    }),
  guestVisits: (query: { limit: number; before?: string; range: string }) => {
    const q = new URLSearchParams({ limit: String(query.limit), range: query.range });
    if (query.before) q.set("before", query.before);
    return request(`/api/v1/settings/guest-visits?${q.toString()}`);
  },
  guestVisitSummary: (range: string) => request(`/api/v1/settings/guest-visits/summary?range=${encodeURIComponent(range)}`),
  internetService: () => request("/api/v1/settings/internet-service"),
  saveInternetService: (setting: InternetServiceSetting, csrf: string) =>
    request("/api/v1/settings/internet-service", {
      method: "PUT",
      headers: csrfHeaders(csrf),
      body: JSON.stringify(setting),
    }),
  syntheticProfiles: (monitorId: string) =>
    request(`/api/v1/synthetic/profiles?monitor_id=${monitorId}`),
  saveSyntheticContinuous: (body: unknown, csrf: string) =>
    request("/api/v1/synthetic/continuous", { method: "PUT", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  startSyntheticManual: (body: unknown, csrf: string) =>
    request("/api/v1/synthetic/manual", { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  syntheticRuns: (monitorId: string) => request(`/api/v1/synthetic/runs?monitor_id=${monitorId}`),
  syntheticReport: (runId: string) => request(`/api/v1/synthetic/runs/${runId}/report`),
  syntheticHealth: (monitorId: string, link: string, range: string) =>
    request(`/api/v1/synthetic/health?monitor_id=${monitorId}&link=${link}&range=${range}`),
  rfOverview: (agentId: string) =>
    request<RfOverview>(`/api/v1/rf/overview?agent_id=${agentId}`),
  rfAps: (agentId: string, params: Record<string, string>) => {
    const query = new URLSearchParams({ agent_id: agentId, ...params });
    return request<RfAp[]>(`/api/v1/rf/aps?${query}`);
  },
  rfHistory: (agentId: string, bssid: string, range: string) =>
    request<RfHistoryPoint[]>(
      `/api/v1/rf/aps/${encodeURIComponent(bssid)}/history?agent_id=${agentId}&range=${range}`,
    ),
  rfClients: (agentId: string) =>
    request<RfClient[]>(`/api/v1/rf/clients?agent_id=${agentId}`),
  rfChannels: (agentId: string, band: string) =>
    request<RfChannelSnapshot>(
      `/api/v1/rf/channel-snapshot?agent_id=${agentId}&band=${encodeURIComponent(band)}`,
    ),
  stressCatalog: () => request("/api/v1/stress/catalog"),
  stressActive: (monitorId: string) => request(`/api/v1/stress/active?monitor_id=${monitorId}`),
  stressStart: (
    body: { monitor_id: string; links: string[]; duration_seconds: number; sessions: { kind: string; params: Record<string, unknown>; count: number }[] },
    csrf: string,
  ) => request("/api/v1/stress", { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  stressAdd: (id: string, body: { kind: string; params: Record<string, unknown>; count: number }, csrf: string) =>
    request(`/api/v1/stress/${id}/sessions`, { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  stressRemove: (id: string, sessionId: string, csrf: string) =>
    request(`/api/v1/stress/${id}/sessions/${sessionId}`, { method: "DELETE", headers: csrfHeaders(csrf) }),
  stressStop: (id: string, csrf: string) => request(`/api/v1/stress/${id}/stop`, { method: "POST", headers: csrfHeaders(csrf) }),
  stressLive: (id: string, since?: string) =>
    request(`/api/v1/stress/${id}/live${since ? `?since=${encodeURIComponent(since)}` : ""}`),
  telegram: () => request("/api/v1/settings/telegram"),
  telegramEvents: () => request("/api/v1/settings/telegram/events"),
  telegramSave: (
    body: {
      token?: string;
      chat_id: string;
      enabled: boolean;
      notify_network: boolean;
      notify_pi: boolean;
      notify_server: boolean;
      notify_app: boolean;
      notify_recovery: boolean;
    },
    csrf: string,
  ) => request("/api/v1/settings/telegram", { method: "PUT", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  telegramTest: (csrf: string) => request("/api/v1/settings/telegram/test", { method: "POST", headers: csrfHeaders(csrf) }),
  telegramDetect: (token: string | undefined, csrf: string) =>
    request("/api/v1/settings/telegram/detect", { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify({ token }) }),
  telegramDisconnect: (csrf: string) => request("/api/v1/settings/telegram", { method: "DELETE", headers: csrfHeaders(csrf) }),
  serverHealth: (range: string) => request(`/api/v1/server-health?range=${encodeURIComponent(range)}`),
  serverHealthPackage: (source: string, name: string) =>
    request(`/api/v1/server-health/software?source=${encodeURIComponent(source)}&name=${encodeURIComponent(name)}`),
  kpi: (period: string, day?: string, monitorId?: string) =>
    request(`/api/v1/kpi?period=${encodeURIComponent(period)}${day ? `&day=${encodeURIComponent(day)}` : ""}${monitorId ? `&monitor_id=${encodeURIComponent(monitorId)}` : ""}`),
  kpiDaily: (first: string, last: string, monitorId?: string) =>
    request(`/api/v1/kpi/daily?from=${encodeURIComponent(first)}&to=${encodeURIComponent(last)}&limit=1000${monitorId ? `&monitor_id=${encodeURIComponent(monitorId)}` : ""}`),
  speedtestStart: (body: { monitor_id: string; link: string }, csrf: string) =>
    request("/api/v1/speedtest", { method: "POST", headers: csrfHeaders(csrf), body: JSON.stringify(body) }),
  speedtestRead: (id: string, since: number) => request(`/api/v1/speedtest/${id}?since=${since}`),
  speedtestActive: (monitorId: string) => request(`/api/v1/speedtest/active?monitor_id=${monitorId}`),
  speedtestHistory: (monitorId: string) => request(`/api/v1/speedtest/history?monitor_id=${monitorId}&limit=8`),
  speedtestCancel: (id: string, csrf: string) => request(`/api/v1/speedtest/${id}/cancel`, { method: "POST", headers: csrfHeaders(csrf) }),
  rfScanNow: (agentId: string, csrf: string) =>
    request(`/api/v1/rf/scan-now?agent_id=${agentId}`, { method: "POST", headers: csrfHeaders(csrf) }),
  rfTrust: (agentId: string, bssid: string, trusted: boolean, csrf: string) =>
    request<RfAp>(`/api/v1/rf/aps/${encodeURIComponent(bssid)}/trust?agent_id=${agentId}`, {
      method: trusted ? "POST" : "DELETE",
      headers: csrfHeaders(csrf),
    }),
};
