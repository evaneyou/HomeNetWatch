import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { joinAnon } from "../anonymized";
import type { HealthCell, MonitorAdmin, Summary } from "../types";
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
  const statuses = checkCells(cells, monitorId, key, column).map((cell) => mapState(cell.status));
  return statuses.reduce<CheckState>((worst, state) => (STATE_RANK[state] > STATE_RANK[worst] ? state : worst), "unknown");
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

function viewOf(data: Summary, regionById: Map<string, string | null>) {
  const locations = data.monitors.map((monitor) => {
    const checks = CHECKS.map((check) => {
      const state = columnState(data.matrix, monitor.id, check.key, check.column);
      const wifiId = slugNetworkId(data.matrix, monitor.id, "wifi");
      const networkId =
        check.key === "wifi" || check.key === "apps"
          ? (wifiId ?? checkNetworkId(data.matrix, monitor.id, check.key, check.column))
          : checkNetworkId(data.matrix, monitor.id, check.key, check.column);
      return {
        ...check,
        state,
        detail: DEFAULT_DETAIL[check.key][state],
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
    let reason =
      "The remote probe is online and all active checks (Wi-Fi, Wired, Web, and Apps) are passing within their latency, jitter, and packet-loss thresholds.";
    if (monitor.status === "offline") {
      mood = "down";
      blurb = "Remote probe is offline and has stopped checking in.";
      reason =
        "The face is coral red with a frown because the remote Raspberry Pi probe has stopped checking in for over 5 minutes, so live network health cannot be verified.";
    } else if (down.length > 0) {
      mood = "down";
      blurb = `${join(down)} ${down.length === 1 ? "is" : "are"} down.`;
      reason = `The face is coral red with a frown because ${join(down)} ${down.length === 1 ? "has" : "have"} sustained failures across recent test windows.`;
    } else if (slow.length > 0) {
      mood = "slow";
      blurb = `${join(slow)} ${slow.length === 1 ? "is" : "are"} running slow.`;
      reason = `The face is amber yellow with a flat expression because ${join(slow)} ${slow.length === 1 ? "is" : "are"} experiencing elevated latency, jitter, or packet loss.`;
    } else if (unknown.length > 0) {
      const ok = labels("ok");
      mood = "curious";
      blurb =
        (ok.length > 0 ? `${join(ok)} ${ok.length === 1 ? "is" : "are"} fine. ` : "") +
        `${join(unknown)} ${unknown.length === 1 ? "hasn't" : "haven't"} been tested yet.`;
      reason = `The face is lavender and mint because ${join(unknown)} ${unknown.length === 1 ? "has" : "have"} not completed a full test window yet.`;
    }
    const place = joinAnon(
      [monitor.location_label, monitor.region ?? regionById.get(monitor.id)]
        .map((part) => part?.trim() || "")
        .filter((part, index, all) => part !== "" && part !== monitor.name && all.indexOf(part) === index),
    );
    return {
      id: monitor.id,
      name: monitor.name,
      place: place || null,
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

  const regionById = new Map((monitorsQ.data ?? []).map((monitor) => [monitor.id, monitor.region]));
  const view = viewOf(q.data, regionById);

  return (
    <div className="dash">
      <header className="dash-head">
        <ul className="legend" aria-label="What the colors mean">
          <li><span className="dot ok" />Happy</li>
          <li><span className="dot slow" />Slow</li>
          <li><span className="dot down" />Down</li>
          <li><span className="dot unknown" />Not tested yet</li>
        </ul>
      </header>
      <main className="probe-grid">
        {view.locations.length === 0 ? <p className="probe-text">No locations have checked in yet.</p> : null}
        {view.locations.map((location, index) => (
          <section className="probe" key={location.id} aria-labelledby={`loc-${index + 1}`}>
            <Link className="kpi-icon" to="/kpi" aria-label="KPI Dashboard">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 19V9M12 19V5M19 19v-7" />
                <path d="M3.5 19.5h17" />
              </svg>
              <span className="kpi-icon-tip" role="tooltip">KPI Dashboard</span>
            </Link>
            <div className="probe-top">
              <div className="orb-wrap" tabIndex={0} aria-label={`${location.colorTitle}. ${location.reason}`}>
                <div className={`orb mood-${location.mood}`} role="img" aria-hidden="true">
                  <Face mood={location.mood} />
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
                          <em className="orb-popover-meta">({CHECK_WHAT[check.key]})</em>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="probe-text">
                <h2 id={`loc-${index + 1}`}>{location.name}</h2>
                <p>{location.blurb}</p>
                {location.place ? <p className="probe-id">{location.place}</p> : null}
              </div>
            </div>
            <ul className="checks">
              {location.checks.map((check) => (
                <li key={check.key}>
                  <Link className={`chip ${check.state}`} to={check.href}>
                    <span className="chip-icon">
                      <Icon name={check.icon} />
                    </span>
                    <span className="chip-text">
                      <span className="chip-label">{check.label}</span>
                      <span className="chip-detail">{check.detail}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </main>
    </div>
  );
}
