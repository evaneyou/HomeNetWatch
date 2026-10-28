import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api } from "../../api";
import { isAnonymized } from "../../anonymized";
import { useGuestGate } from "../../guestGate";
import type { LanScanRun, RfAlert, SecurityAttention, User } from "../../types";
import type { AirContext } from "./shared";

const RANGES = ["1h", "6h", "24h", "7d"] as const;
const SEVERITIES = ["critical", "warning", "info"] as const;
const SECTIONS = [
  { key: "alerts", label: "Alerts" },
  { key: "trust", label: "Trust" },
  { key: "scans", label: "Scans" },
] as const;

type Section = (typeof SECTIONS)[number]["key"];

const ALERT_LABELS: Record<string, string> = {
  DEAUTHFLOOD: "Deauth flood",
  DISCONFLOOD: "Disconnect flood",
  AUTHFLOOD: "Auth flood",
  APSPOOF: "AP spoof",
  EVILTWIN: "Evil twin",
  BSSIDSPOOF: "BSSID spoof",
  BSSTIMESTAMP: "BSS timestamp anomaly",
  CRYPTODROP: "Crypto drop",
  KARMA: "Karma attack",
  AIRJACK: "AirJack",
  WPSBRUTE: "WPS brute force",
  WPA3DOWNGRADE: "WPA3 downgrade",
  DHCPCONFLICT: "DHCP conflict",
  DHCPROGUE: "Rogue DHCP",
  ARPSPOOF: "ARP spoof",
};

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function widsLabel(mode: string): string {
  if (mode === "kismet") return "listening (Kismet)";
  if (mode === "scan") return "scan only";
  return "off";
}

function attentionLabel(item: SecurityAttention): string {
  if (item.reason === "evil_twin") return item.issue_title || `Possible evil twin · ${item.ssid || item.bssid}`;
  if (item.reason === "untrusted_same_ssid") return `Untrusted same SSID · ${item.ssid || "hidden"} · ${item.bssid}`;
  return item.issue_title || item.bssid || "Airwave alert";
}

export default function AirwavesSecurity() {
  const ctx = useOutletContext<AirContext>();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const run = useGuestGate();
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("alerts");
  const [range, setRange] = useState<(typeof RANGES)[number]>("24h");
  const [severity, setSeverity] = useState<Record<(typeof SEVERITIES)[number], boolean>>({
    critical: true,
    warning: true,
    info: false,
  });
  const [trustedOnly, setTrustedOnly] = useState(false);
  const [search, setSearch] = useState("");

  const security = useQuery({
    queryKey: ["rf-security", ctx.agent.id],
    queryFn: () => api.rfSecurity(ctx.agent.id),
    refetchInterval: ctx.refresh ? 15_000 : false,
  });
  const alerts = useQuery({
    queryKey: ["rf-alerts", ctx.agent.id, range, severity, trustedOnly, search],
    queryFn: () => {
      const params: Record<string, string> = { range, limit: "200" };
      const active = SEVERITIES.filter((key) => severity[key]);
      if (active.length === 1) params.severity = active[0];
      if (trustedOnly) params.trusted = "true";
      if (search.trim()) params.q = search.trim();
      return api.rfAlerts(ctx.agent.id, params);
    },
    refetchInterval: ctx.refresh ? 15_000 : false,
    enabled: section === "alerts",
  });
  const scans = useQuery({
    queryKey: ["rf-lan-scans", ctx.agent.id],
    queryFn: () => api.rfLanScans(ctx.agent.id),
    refetchInterval: ctx.refresh ? 15_000 : false,
    enabled: section === "scans" || section === "alerts",
  });

  const filteredAlerts = useMemo(() => {
    const rows = alerts.data ?? [];
    const active = new Set(SEVERITIES.filter((key) => severity[key]));
    if (active.size === 0 || active.size === SEVERITIES.length) return rows;
    return rows.filter((row) => active.has((row.severity ?? "info") as (typeof SEVERITIES)[number]));
  }, [alerts.data, severity]);

  const trust = useMutation({
    mutationFn: async (bssid: string) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.rfTrust(ctx.agent.id, bssid, true, csrf);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["rf-security", ctx.agent.id] }),
        queryClient.invalidateQueries({ queryKey: ["rf-aps", ctx.agent.id] }),
      ]);
    },
  });

  const lanScan = useMutation({
    mutationFn: async () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.rfLanScanNow(ctx.agent.id, csrf);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["rf-security", ctx.agent.id] }),
        queryClient.invalidateQueries({ queryKey: ["rf-lan-scans", ctx.agent.id] }),
      ]);
    },
  });

  const summary = security.data;
  const attention = summary?.attention ?? [];
  const lastScan = summary?.last_lan_scan ?? scans.data?.[0] ?? null;
  const scanning = lastScan?.status === "pending" || lastScan?.status === "running" || lanScan.isPending;

  return (
    <div className="aw-security">
      <div className="aw-sec-strip" role="status">
        <span>
          WIDS <strong>{widsLabel(summary?.airwave_mode || ctx.overview?.airwave_mode || "off")}</strong>
        </span>
        <span>
          Open issues <strong>{summary?.open_issues ?? "—"}</strong>
        </span>
        <span>
          Last LAN scan <strong>{lastScan ? when(lastScan.finished_at || lastScan.requested_at) : "never"}</strong>
        </span>
      </div>

      <div className="aw-sec-seg" role="tablist" aria-label="Security sections">
        {SECTIONS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            className={section === item.key ? "aw-sec-seg-btn on" : "aw-sec-seg-btn"}
            aria-selected={section === item.key}
            onClick={() => setSection(item.key)}
          >
            {item.label}
            {item.key === "trust" && attention.length > 0 ? <span className="aw-sec-count">{attention.length}</span> : null}
          </button>
        ))}
      </div>

      {section === "alerts" ? (
        <>
          <div className="aw-sec-filters" role="group" aria-label="Alert filters">
            {RANGES.map((item) => (
              <button key={item} type="button" className={range === item ? "aw-chip on" : "aw-chip"} onClick={() => setRange(item)}>
                {item}
              </button>
            ))}
            {SEVERITIES.map((item) => (
              <button
                key={item}
                type="button"
                className={severity[item] ? "aw-chip on" : "aw-chip"}
                aria-pressed={severity[item]}
                onClick={() => setSeverity((prev) => ({ ...prev, [item]: !prev[item] }))}
              >
                {item[0].toUpperCase() + item.slice(1)}
              </button>
            ))}
            <button
              type="button"
              className={trustedOnly ? "aw-chip on" : "aw-chip"}
              aria-pressed={trustedOnly}
              onClick={() => setTrustedOnly((value) => !value)}
            >
              Trusted only
            </button>
            <input
              className="aw-sec-search"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search alerts"
              aria-label="Search alerts"
            />
          </div>

          <section className="aw-card aw-alerts" aria-label="WIDS alerts">
            <header>
              <h2>WIDS alerts</h2>
              <span className={`aw-badge${filteredAlerts.some((a) => a.severity === "critical" || a.severity === "warning") ? "" : " info"}`}>
                {filteredAlerts.length}
              </span>
            </header>
            <div className="aw-alerts-list aw-sec-alerts" tabIndex={0} role="region" aria-label="WIDS alerts list">
              {alerts.isLoading ? <p className="aw-empty">Loading alerts…</p> : null}
              {alerts.isError ? <p className="aw-empty">Could not load alerts.</p> : null}
              {!alerts.isLoading && !alerts.isError && filteredAlerts.length === 0 ? <p className="aw-empty">No alerts in this range.</p> : null}
              {filteredAlerts.map((alert, index) => (
                <AlertRow key={`${alert.observed_at}-${alert.alert_type}-${index}`} alert={alert} />
              ))}
            </div>
          </section>
        </>
      ) : null}

      {section === "trust" ? (
        <section className="aw-card" aria-label="Trust">
          <header className="aw-sec-head">
            <h2>Attention</h2>
          </header>
          <p className="aw-sec-note">Evil twins and untrusted access points that share your joined SSID.</p>
          {attention.length === 0 ? <p className="aw-empty">Nothing needs attention right now.</p> : null}
          <ul className="aw-sec-attention">
            {attention.map((item) => (
              <li key={`${item.reason}-${item.bssid}-${item.issue_title}`}>
                <span className={isAnonymized(item.bssid) || isAnonymized(item.ssid) ? "anon-mark" : undefined}>{attentionLabel(item)}</span>
                {item.bssid && ctx.operate ? (
                  <button
                    type="button"
                    className="air-text-btn"
                    disabled={trust.isPending}
                    onClick={() => run(() => trust.mutateAsync(item.bssid))}
                  >
                    Trust
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          {!ctx.operate ? <p className="air-caption">An admin can mark a network as trusted.</p> : null}
        </section>
      ) : null}

      {section === "scans" ? (
        <section className="aw-card" aria-label="LAN scans">
          <header className="aw-sec-head">
            <h2>LAN scan</h2>
            {ctx.operate ? (
              <button type="button" className="aw-scan" disabled={scanning} onClick={() => run(() => lanScan.mutateAsync())}>
                {scanning ? "Scanning…" : "Run scan"}
              </button>
            ) : null}
          </header>
          <p className="aw-sec-note">Wired LAN only. Scapy ARP discovery, then Nmap on common ports. The WireGuard tunnel is never scanned.</p>
          {lanScan.isError ? <p className="error">{(lanScan.error as Error).message}</p> : null}
          {!ctx.operate ? <p className="air-caption">An admin can run a LAN scan.</p> : null}
          <ScanResult run={lastScan} />
          {(scans.data ?? []).length > 0 ? (
            <div className="aw-sec-history">
              <h3 className="aw-sec-subhead">Scan history</h3>
              <ul>
                {(scans.data ?? []).map((item) => (
                  <li key={item.id}>
                    {when(item.finished_at || item.requested_at)} · {item.status}
                    {item.hosts.length ? ` · ${item.hosts.length} hosts` : ""}
                    {item.subnet ? ` · ${item.subnet}` : ""}
                    {item.error ? ` · ${item.error}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function AlertRow({ alert }: { alert: RfAlert }) {
  const sev = alert.severity ?? "info";
  const label = ALERT_LABELS[alert.alert_type.toUpperCase()] || alert.alert_type;
  return (
    <div className={`aw-alert sev-${sev}`}>
      <i aria-hidden="true" />
      <div>
        <b>
          {alert.alert_type}
          {label !== alert.alert_type ? <span className="aw-sec-label"> · {label}</span> : null}
        </b>
        <small>
          {alert.bssid ? (
            <>
              <span className={isAnonymized(alert.bssid) ? "anon-mark" : undefined}>{alert.bssid}</span>
              {" · "}
            </>
          ) : null}
          <span className={isAnonymized(alert.text) ? "anon-mark" : undefined}>{alert.text}</span>
        </small>
      </div>
      <time dateTime={alert.observed_at}>{clock(alert.observed_at)}</time>
    </div>
  );
}

function ScanResult({ run }: { run: LanScanRun | null }) {
  if (!run) return <p className="aw-empty">No LAN scan yet.</p>;
  if (run.status === "pending" || run.status === "running") {
    return <p className="aw-empty">Scan in progress…</p>;
  }
  if (run.status === "failed") {
    return <p className="aw-empty">Scan failed{run.error ? `: ${run.error}` : "."}</p>;
  }
  if (run.hosts.length === 0) {
    return <p className="aw-empty">No hosts answered on {run.subnet || "the LAN"}.</p>;
  }
  return (
    <ul className="aw-sec-hosts">
      {run.hosts.map((host) => (
        <li key={`${host.ip}-${host.mac}`}>
          <b>{host.ip}</b>
          {host.vendor ? ` · ${host.vendor}` : host.mac ? ` · ${host.mac}` : null}
          <span className="aw-sec-ports">{host.ports.length ? host.ports.join(" ") : "no open ports in the set"}</span>
        </li>
      ))}
    </ul>
  );
}
