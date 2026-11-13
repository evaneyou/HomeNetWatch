import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Line, LineChart, YAxis } from "recharts";
import { api } from "../api";
import { isAnonymized, shownAnon } from "../anonymized";
import { isAndroid } from "../deviceType";
import type { HealthCell, Issue, MonitorAdmin, Summary, User } from "../types";
import "../overview.css";

type CheckState = "ok" | "slow" | "down" | "unknown";
type Mood = "happy" | "curious" | "slow" | "down";

const CHECKS: { key: "wired" | "wifi" | "web" | "apps"; label: string; icon: string; column: string }[] = [
  { key: "wifi", label: "Wi-Fi", icon: "wifi", column: "wifi-main" },
  { key: "wired", label: "Wired", icon: "plug", column: "lan" },
  { key: "web", label: "Web", icon: "cloud", column: "web" },
  { key: "apps", label: "Apps", icon: "pulse", column: "apps" },
];

const DEFAULT_DETAIL: Record<string, Record<CheckState, string>> = {
  wifi: { ok: "Strong signal", slow: "Weak signal", down: "Not connected", unknown: "Not tested yet" },
  wired: { ok: "Rock solid", slow: "Running slow", down: "Not connected", unknown: "Not tested yet" },
  web: { ok: "Loading quickly", slow: "Loading slowly", down: "Not loading", unknown: "Not tested yet" },
  apps: { ok: "Calls clear", slow: "Jitter / loss", down: "Failing", unknown: "Not running" },
};

const MOOD_LABEL: Record<Mood, string> = {
  happy: "Happy",
  curious: "Mostly happy, some checks not tested yet",
  slow: "A little slow",
  down: "Something is down",
};

const MOOD_COLOR_NAME: Record<Mood, string> = {
  happy: "Mint Green · Happy",
  curious: "Lavender & Mint · Waiting on Tests",
  slow: "Amber · Running Slow",
  down: "Coral Red · Attention Needed",
};

const CHECK_WHAT: Record<string, string> = {
  wifi: "ISP & tunnel path over Wi-Fi",
  wired: "ISP & tunnel path over Ethernet",
  web: "DNS & 8 major websites quorum",
  apps: "Live voice & video UDP flows",
};
// An Android agent (an NVIDIA Shield) is on Wi-Fi only and runs the voice stream and the Speed Test, so its card
// shows Wi-Fi and Apps, and its Wi-Fi tile is the voice stream over Wi-Fi rather than the Pi's path checks.
const ANDROID_CHECKS = new Set(["wifi", "apps"]);
const ANDROID_WIFI_DETAIL: Record<CheckState, string> = {
  ok: "Voice clear",
  slow: "Jitter / loss",
  down: "Failing",
  unknown: "Not running",
};
const ANDROID_WIFI_WHAT = "Voice stream and Speed Test over Wi-Fi";

const MOOD_RANK: Record<Mood, number> = { down: 0, slow: 1, curious: 2, happy: 3 };
const STATE_RANK: Record<CheckState, number> = { unknown: -1, ok: 0, slow: 1, down: 2 };

function mapState(status: string | undefined): CheckState {
  if (status === "ok") return "ok";
  if (status === "degraded") return "slow";
  if (status === "fail") return "down";
  return "unknown";
}

function join(labels: string[]): string {
  if (labels.length <= 2) return labels.join(" and ");
  return `${labels.slice(0, -1).join(", ")}, and ${labels[labels.length - 1]}`;
}

function hasResult(cell: HealthCell): boolean {
  return cell.status !== "unknown" || cell.last_test_at !== null;
}

function checkCells(cells: HealthCell[], monitorId: string, key: string, column: string): HealthCell[] {
  const mine = cells.filter((cell) => cell.monitor_id === monitorId);
  // Link chips follow the tunnel checks. The older lan and wifi-main networks have no tests.
  if (key === "wired") {
    return mine.filter((cell) => (cell.network_slug === "wired" && cell.column === "internet") || cell.network_slug === "lan");
  }
  if (key === "wifi") {
    return mine.filter((cell) => (cell.network_slug === "wifi" && cell.column === "internet") || cell.network_slug === "wifi-main");
  }
  if (key === "apps") {
    return mine.filter((cell) => cell.column === "apps");
  }
  const matches = mine.filter((cell) => cell.column === column);
  // Once a wired or wireless link is reporting, the old Internet-network copy is no longer refreshed.
  const linkLive = matches.some((cell) => (cell.network_slug === "wired" || cell.network_slug === "wifi") && hasResult(cell));
  return linkLive ? matches.filter((cell) => cell.network_slug !== "wan") : matches;
}

function columnState(cells: HealthCell[], monitorId: string, key: string, column: string): CheckState {
  const matches = checkCells(cells, monitorId, key, column);
  if (key === "web" || key === "apps") {
    // When degradation on one link is attributed to that physical link (Wi-Fi or Wired) while the sibling link is healthy,
    // do not blame the Web or Apps service chip.
    const serviceCells = matches.filter((cell) => cell.cause !== "wifi" && cell.cause !== "wired");
    const pool = serviceCells.length > 0 ? serviceCells : matches;
    const statuses = pool.map((cell) => (cell.cause === "wifi" || cell.cause === "wired" ? ("ok" as CheckState) : mapState(cell.status)));
    return statuses.reduce<CheckState>((worst, state) => (STATE_RANK[state] > STATE_RANK[worst] ? state : worst), "unknown");
  }
  const statuses = matches.map((cell) => mapState(cell.status));
  return statuses.reduce<CheckState>((worst, state) => (STATE_RANK[state] > STATE_RANK[worst] ? state : worst), "unknown");
}

function columnDetail(cells: HealthCell[], monitorId: string, key: string, column: string, state: CheckState): string {
  if ((key === "wifi" || key === "wired") && (state === "slow" || state === "down")) {
    const matches = checkCells(cells, monitorId, key, column);
    if (matches.some((cell) => cell.cause === "bandwidth")) return "Bandwidth drop";
    if (matches.some((cell) => cell.cause === "tunnel")) return "Tunnel down";
    if (matches.some((cell) => cell.cause === "wan")) return state === "down" ? "ISP / WAN outage" : "ISP path / bandwidth";
  }
  if (key === "wifi" && (state === "slow" || state === "down")) {
    const wifiCell = checkCells(cells, monitorId, key, column).find((cell) => cell.cause === "wifi");
    if (wifiCell) return state === "down" ? "Failing on Wi-Fi" : "Packet loss / slow";
  }
  if (key === "wired" && (state === "slow" || state === "down")) {
    const wiredCell = checkCells(cells, monitorId, key, column).find((cell) => cell.cause === "wired");
    if (wiredCell) return state === "down" ? "Failing on Wired" : "Packet loss / slow";
  }
  return DEFAULT_DETAIL[key][state];
}

function checkNetworkId(cells: HealthCell[], monitorId: string, key: string, column: string): string | null {
  const matches = checkCells(cells, monitorId, key, column);
  const tested = matches.find((cell) => hasResult(cell));
  return (tested ?? matches[0])?.network_id ?? null;
}

function slugNetworkId(cells: HealthCell[], monitorId: string, slug: string): string | null {
  const matches = cells.filter((cell) => cell.monitor_id === monitorId && cell.network_slug === slug);
  const tested = matches.find((cell) => hasResult(cell));
  return (tested ?? matches[0])?.network_id ?? null;
}

function checkHref(monitorId: string, key: string, networkId: string | null): string {
  if (!networkId) return `/monitors/${monitorId}`;
  if (key === "wired" || key === "wifi") {
    const params = new URLSearchParams({
      network: networkId,
      detail: "isp",
      link: key === "wired" ? "wired" : "wireless",
    });
    return `/monitors/${monitorId}?${params}`;
  }
  if (key === "apps") {
    const params = new URLSearchParams({
      detail: "apps",
    });
    return `/monitors/${monitorId}?${params}`;
  }
  return `/monitors/${monitorId}?network=${networkId}`;
}

function Icon({ name }: { name: string }) {
  if (name === "plug") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="5" y="3" width="14" height="10" rx="2" />
        <path d="M9 6v3M12 6v3M15 6v3M9 13v3h6v-3M12 16v5" />
      </svg>
    );
  }
  if (name === "wifi") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0" />
        <circle className="solid" cx="12" cy="19.5" r="1" />
      </svg>
    );
  }
  if (name === "pulse") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 12h4l2.5-6 5 12 2.5-6H21" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 18a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.7 1.2A3.5 3.5 0 0 1 17 18z" />
    </svg>
  );
}

/** A small drawing of the agent's hardware: the Shield's black wedge, or a green Pi board. */
function DeviceArt({ android }: { android: boolean }) {
  if (android) {
    return (
      <svg className="device-art" viewBox="0 0 24 24" aria-hidden="true">
        <path className="device-shield" d="M3 8.5 21 6v11.5L3 19.5z" />
        <path className="device-shield-light" d="M6 17.2 18 15.6" />
      </svg>
    );
  }
  return (
    <svg className="device-art" viewBox="0 0 24 24" aria-hidden="true">
      <rect className="device-pi" x="2.5" y="5.5" width="19" height="13" rx="2" />
      <rect className="device-pi-chip" x="6" y="9" width="5.5" height="5.5" rx="0.8" />
      <rect className="device-pi-port" x="14" y="8.5" width="5.5" height="2.5" rx="0.6" />
      <rect className="device-pi-port" x="14" y="12.5" width="5.5" height="3.5" rx="0.6" />
    </svg>
  );
}

function Face({ mood }: { mood: Mood }) {
  return (
    <svg viewBox="0 0 64 44" aria-hidden="true">
      <circle className="eye" cx="18" cy="12" r="3" />
      <circle className="eye" cx="46" cy="12" r="3" />
      {mood === "happy" ? <path d="M20 28q12 12 24 0" /> : null}
      {mood === "down" ? <path d="M20 36q12-10 24 0" /> : null}
      {mood === "curious" || mood === "slow" ? <path d="M24 32h16" /> : null}
    </svg>
  );
}

/** The worst voice result on this agent's Wi-Fi link. */
function androidWifiState(cells: HealthCell[], monitorId: string): CheckState {
  return cells
    .filter((cell) => cell.monitor_id === monitorId && cell.column === "apps" && cell.network_slug === "wifi")
    .map((cell) => mapState(cell.status))
    .reduce<CheckState>((worst, state) => (STATE_RANK[state] > STATE_RANK[worst] ? state : worst), "unknown");
}

function viewOf(data: Summary, regionById: Map<string, string | null>) {
  const locations = data.monitors.map((monitor) => {
    const diag = data.diagnoses?.[monitor.id];
    const android = isAndroid(monitor.platform);
    const checks = CHECKS.filter((check) => !android || ANDROID_CHECKS.has(check.key)).map((check) => {
      if (android && check.key === "wifi") {
        const state = androidWifiState(data.matrix, monitor.id);
        return { ...check, state, detail: ANDROID_WIFI_DETAIL[state], what: ANDROID_WIFI_WHAT, href: `/monitors/${monitor.id}` };
      }
      const state = columnState(data.matrix, monitor.id, check.key, check.column);
      const wifiId = slugNetworkId(data.matrix, monitor.id, "wifi");
      const networkId =
        check.key === "wifi" || check.key === "apps"
          ? (wifiId ?? checkNetworkId(data.matrix, monitor.id, check.key, check.column))
          : checkNetworkId(data.matrix, monitor.id, check.key, check.column);
      return {
        ...check,
        state,
        detail: columnDetail(data.matrix, monitor.id, check.key, check.column, state),
        what: CHECK_WHAT[check.key],
        href: checkHref(monitor.id, check.key, networkId),
      };
    });
    const labels = (state: CheckState) => checks.filter((check) => check.state === state).map((check) => check.label);
    const down = labels("down");
    const slow = labels("slow");
    const unknown = checks
      .filter((check) => check.key !== "apps" && check.state === "unknown")
      .map((check) => check.label);
    let mood: Mood = "happy";
    let blurb = "Everything here is happy and humming.";
    let reason = `The remote probe is online and all active checks (${join(checks.map((check) => check.label))}) are passing within their latency, jitter, and packet-loss thresholds.`;
    if (monitor.status === "offline") {
      mood = "down";
      blurb = "Remote probe is offline and has stopped checking in.";
      reason =
        `The face is coral red with a frown because the remote ${android ? "Android" : "Raspberry Pi"} probe has stopped checking in for over 5 minutes, so live network health cannot be verified.`;
    } else if (diag?.cause === "bandwidth" && (down.length > 0 || slow.length > 0)) {
      mood = down.length > 0 ? "down" : "slow";
      blurb = "ISP bandwidth drop is degrading Wired, Wi-Fi, and Apps.";
      reason = diag.summary;
    } else if (diag?.cause === "wan" && (down.length > 0 || slow.length > 0)) {
      mood = down.length > 0 ? "down" : "slow";
      blurb = "Shared ISP / WAN degradation across Wired and Wi-Fi.";
      reason = diag.summary;
    } else if (diag?.cause === "tunnel" && (down.length > 0 || slow.length > 0)) {
      mood = down.length > 0 ? "down" : "slow";
      blurb = "Wi-Fi measurement tunnel to the server is down.";
      reason = diag.summary;
    } else if (diag?.cause === "server" && (down.length > 0 || slow.length > 0)) {
      mood = down.length > 0 ? "down" : "slow";
      blurb = "Server path or synthetic receiver issue (external Web is up).";
      reason = diag.summary;
    } else if (down.length > 0) {
      mood = "down";
      blurb = `${join(down)} ${down.length === 1 ? "is" : "are"} down.`;
      reason = diag?.summary ?? `The face is coral red with a frown because ${join(down)} ${down.length === 1 ? "has" : "have"} sustained failures across recent test windows.`;
    } else if (slow.length > 0) {
      mood = "slow";
      blurb = `${join(slow)} ${slow.length === 1 ? "is" : "are"} running slow.`;
      reason = diag?.summary ?? `The face is amber yellow with a flat expression because ${join(slow)} ${slow.length === 1 ? "is" : "are"} experiencing elevated latency, jitter, or packet loss.`;
    } else if (unknown.length > 0) {
      const ok = labels("ok");
      mood = "curious";
      blurb =
        (ok.length > 0 ? `${join(ok)} ${ok.length === 1 ? "is" : "are"} fine. ` : "") +
        `${join(unknown)} ${unknown.length === 1 ? "hasn't" : "haven't"} been tested yet.`;
      reason = `The face is lavender and mint because ${join(unknown)} ${unknown.length === 1 ? "has" : "have"} not completed a full test window yet.`;
    }
    const city = (monitor.location_label?.trim() || "").trim();
    const state = (monitor.region ?? regionById.get(monitor.id) ?? "").trim();
    const cityShown = city && city !== monitor.name ? city : "";
    const stateShown = state && state !== monitor.name && state !== cityShown ? state : "";
    return {
      id: monitor.id,
      name: monitor.name,
      android,
      city: cityShown || null,
      state: stateShown || null,
      checks,
      mood,
      moodLabel: MOOD_LABEL[mood],
      colorTitle: MOOD_COLOR_NAME[mood],
      blurb,
      reason,
      probeOnline: monitor.status !== "offline",
    };
  });
  locations.sort((a, b) => MOOD_RANK[a.mood] - MOOD_RANK[b.mood] || a.name.localeCompare(b.name));
  return { locations };
}

/** One traffic type of the continuous voice and video streams on a link, as /synthetic/health returns it. */
type StreamType = {
  slug: string;
  latest: { rtt_ms: number | null; jitter_ms: number | null; loss_pct: number | null };
  points: { rtt_ms: number | null; jitter_ms: number | null }[];
};
type TileMetric = { value: string; label: string; series: number[] };

const HERO_WORD: Record<Mood, string> = {
  happy: "happy",
  curious: "warming up",
  slow: "a little slow",
  down: "having trouble",
};

/** At most `max` values, evenly spaced, so a sparkline stays light whatever the sample count. */
function thin(values: number[], max = 40): number[] {
  if (values.length <= max) return values;
  const step = values.length / max;
  return Array.from({ length: max }, (_, index) => values[Math.floor(index * step)]);
}

function numbers(values: (number | null)[]): number[] {
  return values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
}

/** Round-trip time of the first stream on a link that has reported one. */
function rttMetric(types: StreamType[]): TileMetric | null {
  const type = types.find((item) => item.latest.rtt_ms !== null);
  if (!type || type.latest.rtt_ms === null) return null;
  return {
    value: `${Math.round(type.latest.rtt_ms)} ms`,
    label: "round trip",
    series: thin(numbers(type.points.map((point) => point.rtt_ms))),
  };
}

/** The worst current jitter across every stream: the number a call or a video would feel first. */
function jitterMetric(types: StreamType[]): TileMetric | null {
  let worst: StreamType | null = null;
  for (const type of types) {
    if (type.latest.jitter_ms === null) continue;
    if (!worst || type.latest.jitter_ms > (worst.latest.jitter_ms ?? 0)) worst = type;
  }
  if (!worst || worst.latest.jitter_ms === null) return null;
  return {
    value: `${worst.latest.jitter_ms < 10 ? worst.latest.jitter_ms.toFixed(1) : Math.round(worst.latest.jitter_ms)} ms`,
    label: "jitter",
    series: thin(numbers(worst.points.map((point) => point.jitter_ms))),
  };
}

function webMetric(cells: HealthCell[], monitorId: string): TileMetric | null {
  const cell = cells.find((item) => item.monitor_id === monitorId && item.column === "web" && item.quorum && item.quorum.total > 0);
  if (!cell?.quorum) return null;
  return { value: `${cell.quorum.total - cell.quorum.bad}/${cell.quorum.total}`, label: "sites up", series: [] };
}

/** The last hour of a number as a small line. Decoration: the value beside it carries the meaning. */
function Spark({ series }: { series: number[] }) {
  if (series.length < 2) return null;
  return (
    <span className="home-spark" aria-hidden="true">
      <LineChart width={120} height={30} data={series.map((v) => ({ v }))} margin={{ top: 4, right: 3, bottom: 4, left: 3 }}>
        <YAxis hide domain={["dataMin", "dataMax"]} />
        <Line type="monotone" dataKey="v" dot={false} strokeWidth={2.4} isAnimationActive animationDuration={1600} />
      </LineChart>
    </span>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : date.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

export default function OverviewPage() {
  const q = useQuery({
    queryKey: ["summary"],
    queryFn: () => api.summary() as Promise<Summary>,
    refetchInterval: 30_000,
  });
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
    refetchInterval: 30_000,
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const issuesQ = useQuery({
    queryKey: ["issues", "all"],
    queryFn: () => api.issues() as Promise<Issue[]>,
    refetchInterval: 60_000,
  });
  const role = me.data?.role;
  const hideCity = role === "guest" || !me.data;

  // The numbers and sparklines on the tiles come from the continuous streams: Wi-Fi and Wired on a Pi, Wi-Fi on Android.
  const streamLinks = (q.data?.monitors ?? []).flatMap((monitor) =>
    (isAndroid(monitor.platform) ? (["wireless"] as const) : (["wireless", "wired"] as const)).map((link) => ({ id: monitor.id, link })),
  );
  const streamQs = useQueries({
    queries: streamLinks.map(({ id, link }) => ({
      queryKey: ["synthetic-health", id, link, "1h"],
      queryFn: () => api.syntheticHealth(id, link, "1h") as Promise<{ types: StreamType[] }>,
      refetchInterval: 60_000,
    })),
  });

  if (q.isLoading) {
    return (
      <div className="dash">
        <p className="probe-text">Loading locations…</p>
      </div>
    );
  }
  if (q.isError || !q.data) {
    return (
      <div className="dash">
        <p className="probe-text">Could not load locations.</p>
      </div>
    );
  }

  const data = q.data;
  const regionById = new Map((monitorsQ.data ?? []).map((monitor) => [monitor.id, monitor.region]));
  const view = viewOf(data, regionById);

  const streams = new Map<string, StreamType[]>();
  streamLinks.forEach(({ id, link }, index) => streams.set(`${id}:${link}`, streamQs[index]?.data?.types ?? []));
  const metricOf = (monitorId: string, key: string): TileMetric | null => {
    const wifi = streams.get(`${monitorId}:wireless`) ?? [];
    const wired = streams.get(`${monitorId}:wired`) ?? [];
    if (key === "wifi") return rttMetric(wifi);
    if (key === "wired") return rttMetric(wired);
    if (key === "apps") return jitterMetric([...wifi, ...wired]);
    return webMetric(data.matrix, monitorId);
  };

  const overall: Mood | null = view.locations.length > 0 ? view.locations[0].mood : null;
  const online = view.locations.filter((location) => location.probeOnline).length;
  const openCount = data.open_issues.length;
  // The same problem can open several times in a row: show it once, newest first, with how often it happened.
  const lately: { issue: Issue; times: number }[] = [];
  for (const issue of [...(issuesQ.data ?? [])].sort((a, b) => b.opened_at.localeCompare(a.opened_at))) {
    const same = lately.find((entry) => entry.issue.title === issue.title && entry.issue.status === issue.status);
    if (same) same.times += 1;
    else if (lately.length < 3) lately.push({ issue, times: 1 });
  }
  const shortcuts = [
    { to: "/path", label: "Path View", note: "Hop by hop to the server" },
    { to: "/airwaves", label: "Airwaves", note: "Wi-Fi around the house" },
    { to: "/issues", label: "Issues", note: openCount === 0 ? "None open" : `${openCount} open` },
    { to: "/kpi", label: "KPI Dashboard", note: "Scores over time" },
    ...(role === "admin" || role === "guest" ? [{ to: "/server-health", label: "Server Health", note: "CPU, memory, disk" }] : []),
  ];

  return (
    <div className="dash">
      <div className="home-sky" aria-hidden="true">
        <span className="home-glow g1" />
        <span className="home-glow g2" />
        <span className="home-glow g3" />
        <span className="home-star s1" />
        <span className="home-star s2" />
        <span className="home-star s3" />
        <span className="home-star s4" />
      </div>

      <header className={`home-hero mood-${overall ?? "curious"}`}>
        <div className="home-hero-orb" aria-hidden="true">
          <span className="home-ripple" />
          <span className="home-ripple r2" />
          <span className="home-ripple r3" />
          <div className="home-bob">
            <div className={`orb mood-${overall ?? "curious"}`}>
              <Face mood={overall ?? "curious"} />
            </div>
          </div>
        </div>
        <div className="home-hero-text">
          {overall ? (
            <h1>
              Your home network is{" "}
              <span className="home-word">
                {HERO_WORD[overall]}
                <svg viewBox="0 0 120 12" preserveAspectRatio="none" aria-hidden="true">
                  <path pathLength={1} d="M2 7c10-8 18 8 29 0s19 8 29 0 19 8 29 0 19 7 29 0" />
                </svg>
              </span>
              .
            </h1>
          ) : (
            <h1>Waiting for your first remote agent.</h1>
          )}
          <ul className="home-pills">
            <li className="live">
              <span className="home-live-dot" />
              {online} of {view.locations.length} {view.locations.length === 1 ? "agent" : "agents"} reporting
            </li>
            <li>
              <Link to="/issues">{openCount === 0 ? "No open issues" : `${openCount} open ${openCount === 1 ? "issue" : "issues"}`}</Link>
            </li>
            <li>Checked {when(data.generated_at)}</li>
          </ul>
        </div>
        <ul className="legend" aria-label="What the colors mean">
          <li><span className="dot ok" />Happy</li>
          <li><span className="dot slow" />Slow</li>
          <li><span className="dot down" />Down</li>
          <li><span className="dot unknown" />Not tested yet</li>
        </ul>
        <div className="home-wave w1" aria-hidden="true">
          <svg viewBox="0 0 1200 34" preserveAspectRatio="none">
            <path d="M0 18c50-18 100 18 150 0s100 18 150 0 100 18 150 0 100 18 150 0 100 18 150 0 100 18 150 0 100 18 150 0 100 18 150 0v16H0z" />
          </svg>
        </div>
        <div className="home-wave w2" aria-hidden="true">
          <svg viewBox="0 0 1200 24" preserveAspectRatio="none">
            <path d="M0 12c50-12 100 12 150 0s100 12 150 0 100 12 150 0 100 12 150 0 100 12 150 0 100 12 150 0 100 12 150 0 100 12 150 0v12H0z" />
          </svg>
        </div>
      </header>

      <main className="probe-grid">
        {view.locations.length === 0 ? <p className="probe-text">No locations have checked in yet.</p> : null}
        {view.locations.map((location, index) => (
          <section className="probe" key={location.id} aria-labelledby={`loc-${index + 1}`}>
            <Link className="kpi-icon kpi-icon-next" to="/airwaves" aria-label="Airwaves">
              <svg viewBox="4 5 16 16" aria-hidden="true">
                <path d="M4.5 16a7.5 7.5 0 0 1 15 0" />
                <path d="M8 16a4 4 0 0 1 8 0" />
                <circle cx="12" cy="16" r="1.15" fill="currentColor" stroke="none" />
              </svg>
              <span className="kpi-icon-tip" role="tooltip">Airwaves</span>
            </Link>
            <Link className="kpi-icon" to="/kpi" aria-label="KPI Dashboard">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="3.5" y="13" width="4.5" height="7.5" rx="1.2" fill="currentColor" stroke="none" />
                <rect x="9.75" y="4" width="4.5" height="16.5" rx="1.2" fill="currentColor" stroke="none" />
                <rect x="16" y="9" width="4.5" height="11.5" rx="1.2" fill="currentColor" stroke="none" />
              </svg>
              <span className="kpi-icon-tip" role="tooltip">KPI Dashboard</span>
            </Link>
            <div className="probe-top">
              <div className="orb-wrap" tabIndex={0} aria-label={`${location.colorTitle}. ${location.reason}`}>
                <div className="home-bob">
                  <div className={`orb mood-${location.mood}`} role="img" aria-hidden="true">
                    <Face mood={location.mood} />
                  </div>
                </div>
                <div className="orb-popover" role="tooltip">
                  <div className="orb-popover-head">
                    <span className={`dot ${location.mood === "happy" ? "ok" : location.mood === "curious" ? "unknown" : location.mood}`} />
                    <strong>{location.colorTitle}</strong>
                  </div>
                  <p className="orb-popover-why">{location.reason}</p>
                  <ul className="orb-popover-list">
                    <li>
                      <span className={`dot ${location.probeOnline ? "ok" : "down"}`} />
                      <span>
                        <strong>Remote Probe:</strong> {location.probeOnline ? "Online & checking in" : "Offline (>5 min)"}
                      </span>
                    </li>
                    {location.checks.map((check) => (
                      <li key={check.key}>
                        <span className={`dot ${check.state}`} />
                        <span>
                          <strong>{check.label}:</strong> {check.detail}{" "}
                          <em className="orb-popover-meta">({check.what})</em>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="probe-text">
                <h2 id={`loc-${index + 1}`}>
                  <Link to={`/monitors/${location.id}`}>{location.name}</Link>
                </h2>
                <span className="device-chip">
                  <DeviceArt android={location.android} />
                  {location.android ? "NVIDIA Shield · Android" : "Raspberry Pi · Linux"}
                </span>
                <p>{location.blurb}</p>
                {location.city || location.state ? (
                  <p className="probe-id">
                    {location.city ? (
                      <span
                        className={hideCity || isAnonymized(location.city) ? "place-city" : undefined}
                        aria-hidden={hideCity || isAnonymized(location.city) || undefined}
                      >
                        {shownAnon(location.city)}
                      </span>
                    ) : null}
                    {location.city && location.state ? <span aria-hidden={hideCity || undefined}>, </span> : null}
                    {location.state ? <span>{location.state}</span> : null}
                  </p>
                ) : null}
              </div>
            </div>
            <ul className="checks">
              {location.checks.map((check) => {
                const metric = check.state === "unknown" ? null : metricOf(location.id, check.key);
                return (
                  <li key={check.key}>
                    <Link className={`chip ${check.state}`} to={check.href}>
                      <span className="chip-icon">
                        <Icon name={check.icon} />
                      </span>
                      <span className="chip-text">
                        <span className="chip-label">{check.label}</span>
                        <span className="chip-detail">{check.detail}</span>
                      </span>
                      {metric ? <Spark series={metric.series} /> : null}
                      {metric ? (
                        <span className="chip-metric">
                          <span className="chip-value">{metric.value}</span>
                          <span className="chip-unit">{metric.label}</span>
                        </span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </main>

      <section className="home-lately" aria-labelledby="home-lately-title">
        <div className="home-lately-head">
          <h2 id="home-lately-title">Lately</h2>
          <Link to="/issues">See all issues</Link>
        </div>
        {issuesQ.isLoading ? <p className="home-lately-empty">Loading…</p> : null}
        {issuesQ.isError ? <p className="home-lately-empty">Could not load recent issues.</p> : null}
        {!issuesQ.isLoading && !issuesQ.isError && lately.length === 0 ? (
          <p className="home-lately-empty">Nothing to report. No issues on record.</p>
        ) : null}
        {lately.length > 0 ? (
          <ul>
            {lately.map(({ issue, times }) => {
              const open = issue.status === "open";
              return (
                <li key={issue.id}>
                  <span className={`dot ${open ? (issue.severity === "critical" ? "down" : "slow") : "ok"}`} />
                  <Link to={`/issues/${issue.id}`}>{issue.title}</Link>
                  <span className="home-lately-meta">
                    {open ? "Open" : "Resolved"} · {when(issue.opened_at)}
                    {times > 1 ? ` · ${times} times` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      <nav className="home-shortcuts" aria-label="Other views">
        {shortcuts.map((item) => (
          <Link key={item.to} to={item.to}>
            <strong>{item.label}</strong>
            <span>{item.note}</span>
          </Link>
        ))}
      </nav>
    </div>
  );
}
