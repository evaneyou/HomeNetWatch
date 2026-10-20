import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { TimeAxis } from "../components/InternetDetail";
import { CHART_COLORS } from "../chartTheme";
import { chartWindow, formatChartTick } from "../chartTime";

type Range = "1h" | "6h" | "24h" | "7d";
type Point = {
  t: string;
  cpu: number | null;
  mem_used_mb: number;
  mem_total_mb: number;
  disk_used_mb: number;
  disk_total_mb: number;
  db_bytes: number;
  forecast_bytes: number | null;
  rx_bps: number | null;
  tx_bps: number | null;
  iface: string;
  connections: number;
};
type Advisory = { id: string; risk: string; summary: string };
type PackageRow = {
  name: string;
  installed: string;
  newest: string;
  behind: boolean;
  advisories: Advisory[];
  advisory_count?: number;
  risk: string;
};
type StorageReport = {
  projection: {
    ready: boolean;
    growing: boolean;
    bytes_per_day: number | null;
    full_on: string | null;
    far: boolean;
    line: { t: string; bytes: number }[];
  };
  breakdown: {
    checked_at: string;
    used_bytes: number;
    total_bytes: number;
    groups: { key: string; title: string; bytes: number; children: { name: string; bytes: number; note?: string }[] }[];
  } | null;
};
type SoftwareGroup = { key?: string; title: string; note?: string; error?: string; packages: PackageRow[] };
type PackageDetail = {
  source: string;
  source_title: string;
  os: string;
  kernel: string;
  checked_at: string;
  package: PackageRow;
};
type Report = {
  latest: Point | null;
  points: Point[];
  tables: { name: string; bytes: number }[];
  forecast: { ready: boolean; growing: boolean; full_on: string | null; bytes_per_day: number | null; far: boolean };
  retention: { kept: string; items: string[] }[];
  advisories: {
    checked_at: string;
    os: string;
    kernel: string;
    error?: string;
    packages: PackageRow[];
    groups?: SoftwareGroup[];
  } | null;
  stuck: { pid: number; name: string; state: string; reason: string; since: string }[];
  storage: StorageReport;
};

const RANGES: Range[] = ["1h", "6h", "24h", "7d"];
const REASONS: Record<string, string> = {
  zombie: "Zombie",
  sleep: "Uninterruptible sleep",
  cpu: "Using almost a full core",
};

function amount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.abs(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || size >= 10 ? 0 : 1;
  return `${value < 0 ? "-" : ""}${size.toFixed(digits)} ${units[unit]}`;
}

function perSecond(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${amount(value)}/s`;
}

function when(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function ago(value: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d`;
}

function forecastText(cast: Report["forecast"]): string {
  if (!cast.ready) return "There is not enough history yet. The forecast needs 7 daily sizes.";
  if (!cast.growing) return "The database is not growing toward a full disk.";
  const rate = cast.bytes_per_day == null ? "" : `, about ${amount(cast.bytes_per_day)} a day`;
  if (!cast.full_on) return `The database is growing${rate}. The disk would not fill within the next 200 years.`;
  return `The database would fill the disk on ${when(cast.full_on)}${rate}.`;
}

function Chart({
  title,
  note,
  data,
  span,
  lines,
  unit,
  domain,
}: {
  title: string;
  note?: string;
  data: Record<string, string | number | null>[];
  span: [number, number];
  lines: { key: string; name: string; dashed?: boolean }[];
  unit: string;
  domain?: [number, number];
}) {
  const drawn = data.some((row) => lines.some((line) => row[line.key] != null));
  const body = !drawn ? (
    <div className="state-box">No samples in this range yet.</div>
  ) : (
    <div style={{ width: "100%", height: 220 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
          <TimeAxis domain={span} />
              <YAxis
                domain={domain}
                tick={{ fontSize: 11, fill: "var(--muted)" }}
                width={72}
                tickFormatter={(value) => {
                  const number = Number(value);
                  if (unit === " %") return `${Math.round(number)}%`;
                  if (unit === "/s") return perSecond(number).replace("/s", "");
                  return amount(number);
                }}
              />
              {lines.length > 1 ? <Legend /> : null}
              <Tooltip
            labelFormatter={(value) => formatChartTick(Number(value), span[1] - span[0])}
            formatter={(value, name) => {
              const shown =
                typeof value === "number" ? (unit === " %" ? `${value.toFixed(1)}%` : unit === "/s" ? perSecond(value) : amount(value)) : "—";
              return [shown, String(name)];
            }}
          />
          {lines.map((line, index) => (
            <Line
              key={line.key}
              type="monotone"
              dataKey={line.key}
              name={line.name}
              stroke={CHART_COLORS[index % CHART_COLORS.length]}
              strokeDasharray={line.dashed ? "6 4" : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
  if (!title) return body;
  return (
    <div className="panel">
      <h2 className="server-health-heading">{title}</h2>
      {note ? <p className="muted server-health-note">{note}</p> : null}
      {body}
    </div>
  );
}

function dayText(value: string): string {
  const date = new Date(`${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function projectionText(projection: StorageReport["projection"]): string {
  if (!projection.ready) return "There is not enough history yet. The projection needs 7 daily readings.";
  if (!projection.growing) return "Used space is not growing.";
  const rate = projection.bytes_per_day == null ? "" : ` about ${amount(projection.bytes_per_day)} a day`;
  if (projection.full_on && !projection.far) return `At${rate}, the disk would fill on ${dayText(projection.full_on)}.`;
  return `Used space is growing${rate ? `,${rate}` : ""}. The disk would not fill within the next year.`;
}

function StorageChart({ history, projection, total }: { history: { ts: number; used: number }[]; projection: StorageReport["projection"]; total: number }) {
  const line = projection.line.map((point) => ({ ts: new Date(point.t).getTime(), projected: point.bytes }));
  const rows: { ts: number; used?: number; projected?: number }[] = [...history, ...line].sort((a, b) => a.ts - b.ts);
  if (history.length === 0) return <div className="state-box">No samples in this range yet.</div>;
  const end = rows[rows.length - 1].ts;
  const fills = projection.growing && projection.full_on && !projection.far && line.length === 2;
  return (
    <div style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={[rows[0].ts, end]}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            tickFormatter={(value) => new Date(Number(value)).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          />
          <YAxis
            domain={[0, total]}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            width={72}
            tickFormatter={(value) => amount(Number(value))}
          />
          <Legend />
          <Tooltip
            labelFormatter={(value) => new Date(Number(value)).toLocaleString()}
            formatter={(value, name) => [typeof value === "number" ? amount(value) : "—", String(name)]}
          />
          <ReferenceLine y={total} stroke="var(--fail)" strokeDasharray="6 4" label={{ value: `Disk size ${amount(total)}`, fill: "var(--fail)", fontSize: 11, position: "insideTopLeft" }} />
          <Line type="monotone" dataKey="used" name="Used" stroke={CHART_COLORS[0]} dot={false} isAnimationActive={false} connectNulls={false} />
          {line.length > 0 ? (
            <Line
              type="linear"
              dataKey="projected"
              name="Projection"
              stroke={CHART_COLORS[1]}
              strokeDasharray="6 4"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ) : null}
          {fills ? <ReferenceDot x={end} y={total} r={4} fill={CHART_COLORS[1]} stroke="none" /> : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function StorageBreakdown({ breakdown }: { breakdown: NonNullable<StorageReport["breakdown"]> }) {
  const used = breakdown.used_bytes || 1;
  const share = (bytes: number) => {
    const percent = (100 * bytes) / used;
    return percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`;
  };
  return (
    <>
      <h3 className="server-health-heading">What uses the space</h3>
      <p className="muted server-health-note">Measured {when(breakdown.checked_at)}. It is measured every 6 hours.</p>
      <div
        className="storage-stack"
        role="img"
        aria-label={breakdown.groups.map((group) => `${group.title} ${amount(group.bytes)}`).join(", ")}
      >
        {breakdown.groups.map((group) => (
          <i key={group.key} className={`storage-${group.key}`} style={{ flex: group.bytes }} />
        ))}
      </div>
      {breakdown.groups.map((group, index) => (
        <details key={group.key} className={`storage-group storage-${group.key}`} open={index === 0 || undefined}>
          <summary>
            <span className="storage-title">
              <span className="storage-swatch" />
              {group.title}
            </span>
            <span className="storage-num">{amount(group.bytes)}</span>
            <span className="storage-num muted">{share(group.bytes)}</span>
          </summary>
          {group.children.map((child) => (
            <div className="storage-kid" key={child.name}>
              <span>
                {child.name}
                {child.note ? <small>{child.note}</small> : null}
              </span>
              <span className="storage-num">{amount(child.bytes)}</span>
              <span className="storage-num muted">{share(child.bytes)}</span>
            </div>
          ))}
        </details>
      ))}
    </>
  );
}

const RISK_ORDER = ["critical", "high", "medium", "low"];

function riskRank(risk: string): number {
  const index = RISK_ORDER.indexOf(risk);
  return index < 0 ? RISK_ORDER.length : index;
}

function allPackages(advisories: Report["advisories"]): PackageRow[] {
  if (!advisories) return [];
  return [...advisories.packages, ...(advisories.groups ?? []).flatMap((group) => group.packages)];
}

function riskLabel(risk: string): string {
  return risk.charAt(0).toUpperCase() + risk.slice(1);
}

type Source = { key: string; title: string; heading: string; note?: string; error?: string; packages: PackageRow[] };

function sourcesOf(advisories: NonNullable<Report["advisories"]>): Source[] {
  const host = [advisories.os, advisories.kernel ? `kernel ${advisories.kernel}` : ""].filter(Boolean).join(" · ");
  return [
    { key: "host", title: "Host", heading: host ? `Host · ${host}` : "Host", error: advisories.error, packages: advisories.packages },
    ...(advisories.groups ?? []).map((group) => ({
      key: group.key ?? group.title,
      title: group.title,
      heading: group.title,
      note: group.note,
      error: group.error,
      packages: group.packages,
    })),
  ];
}

function riskCounts(rows: PackageRow[]): Record<string, number> {
  const counts: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const row of rows) counts[row.risk in counts ? row.risk : "low"] += 1;
  return counts;
}

/** Where an advisory id leads. Ids without a public page (such as "End of life") have none. */
function advisoryUrl(id: string): string | null {
  const cve = id.match(/CVE-\d{4}-\d+/);
  if (id.startsWith("UBUNTU-") && cve) return `https://ubuntu.com/security/${cve[0]}`;
  if (/^[A-Za-z]+-[\w.-]+$/.test(id)) return `https://osv.dev/vulnerability/${encodeURIComponent(id)}`;
  return null;
}

function packageLink(source: string, name: string): string {
  return `/server-health/software?${new URLSearchParams({ source, name }).toString()}`;
}

function fixText(row: PackageRow): string {
  return row.behind ? `Update available: ${row.installed} to ${row.newest}.` : "Installed is the newest available. No fix yet.";
}

function PackageRows({ source, rows }: { source: string; rows: PackageRow[] }) {
  return (
    <div className="software-rows">
      <div className="software-row software-head" aria-hidden="true">
        <span>Package</span>
        <span className="software-ver">Installed</span>
        <span className="software-end">Advisories</span>
        <span className="software-end">Risk</span>
      </div>
      {rows.map((row) => {
        const total = row.advisory_count ?? row.advisories.length;
        const more = total - row.advisories.length;
        return (
          <Link key={row.name} to={packageLink(source, row.name)} className="software-row software-link">
            <span className="software-name">
              {row.name}
              {row.behind ? <em className="software-update">Update available</em> : null}
            </span>
            <span className="software-ver" title={row.behind ? `${row.installed} to ${row.newest}` : row.installed}>
              {row.installed}
            </span>
            <span className="software-end">{total || "—"}</span>
            <span className="software-end">
              <span className={`software-pill software-${row.risk}`}>{riskLabel(row.risk)}</span>
            </span>
            <span className="software-tip" role="tooltip">
              <strong>{row.name}</strong>
              <small>{fixText(row)}</small>
              {row.advisories.map((item) => (
                <span className="software-tip-line" key={item.id}>
                  <span className={`software-pill software-${item.risk}`}>{riskLabel(item.risk)}</span>
                  <span>
                    {item.id}
                    {item.summary ? <small>{item.summary}</small> : null}
                  </span>
                </span>
              ))}
              <span className="software-tip-foot">{more > 0 ? `${more.toLocaleString()} more. ` : ""}Click for details.</span>
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function SourceGroup({ source, filter }: { source: Source; filter: string | null }) {
  const counts = riskCounts(source.packages);
  const worst = source.packages.length ? riskRank(source.packages[0].risk) : RISK_ORDER.length;
  const [showLow, setShowLow] = useState(false);
  const sorted = [...source.packages].sort((a, b) => riskRank(a.risk) - riskRank(b.risk) || a.name.localeCompare(b.name));
  const visible = filter
    ? sorted.filter((row) => row.risk === filter)
    : showLow
      ? sorted
      : sorted.filter((row) => row.risk !== "low");
  const hiddenLow = filter ? 0 : sorted.length - sorted.filter((row) => row.risk !== "low").length;
  if (filter && visible.length === 0) return null;
  return (
    <details className="software-source" open={worst <= 2 || undefined}>
      <summary>
        <span className="software-source-title">{source.heading}</span>
        <span className="software-mini">
          {RISK_ORDER.filter((risk) => counts[risk] > 0).map((risk) => (
            <span key={risk} className={`software-${risk}`}>
              {counts[risk]} {risk}
            </span>
          ))}
        </span>
        <span className="muted">
          {source.packages.length} {source.packages.length === 1 ? "package" : "packages"}
        </span>
      </summary>
      {source.note ? <p className="muted server-health-note">{source.note}</p> : null}
      {source.error ? <p className="muted">{source.error}</p> : null}
      {source.packages.length === 0 ? (
        <div className="state-box">Nothing to review.</div>
      ) : (
        <>
          {visible.length > 0 ? <PackageRows source={source.key} rows={visible} /> : <p className="muted">Only low-risk packages here.</p>}
          {hiddenLow > 0 ? (
            <button type="button" className="software-more" onClick={() => setShowLow((value) => !value)}>
              {showLow ? "Hide low risk" : `Show ${hiddenLow} low risk`}
            </button>
          ) : null}
        </>
      )}
    </details>
  );
}

function SoftwarePanel({ advisories }: { advisories: NonNullable<Report["advisories"]> }) {
  const [filter, setFilter] = useState<string | null>(null);
  const sources = sourcesOf(advisories);
  const all = sources.flatMap((source) => source.packages);
  const counts = riskCounts(all);
  const widest = Math.max(1, ...sources.map((source) => source.packages.length));
  return (
    <>
      <p className="muted server-health-note">
        Last checked {when(advisories.checked_at)}. The next check is Sunday at 3:00 AM Eastern.
      </p>
      <div className="software-tiles" role="group" aria-label="Packages by risk">
        {RISK_ORDER.map((risk) => (
          <button
            key={risk}
            type="button"
            className={`software-tile software-${risk}`}
            aria-pressed={filter === risk}
            onClick={() => setFilter(filter === risk ? null : risk)}
          >
            <strong>{counts[risk]}</strong>
            <span>{riskLabel(risk)}</span>
          </button>
        ))}
      </div>
      <h3 className="server-health-heading">Risk by source</h3>
      <div className="software-bars">
        {sources.map((source) => {
          const sourceCounts = riskCounts(source.packages);
          const summary = RISK_ORDER.filter((risk) => sourceCounts[risk] > 0)
            .map((risk) => `${sourceCounts[risk]} ${risk}`)
            .join(", ");
          return (
            <div className="software-bar-row" key={source.key}>
              <span>{source.title}</span>
              {source.packages.length === 0 ? (
                <span className="software-pill software-ok">Nothing to review</span>
              ) : (
                <div
                  className="software-bar"
                  style={{ width: `${Math.max(6, (100 * source.packages.length) / widest)}%` }}
                  role="img"
                  aria-label={`${source.title}: ${summary}`}
                >
                  {RISK_ORDER.filter((risk) => sourceCounts[risk] > 0).map((risk) => (
                    <i key={risk} className={`software-fill-${risk}`} style={{ flex: sourceCounts[risk] }} />
                  ))}
                </div>
              )}
              <span className="muted">{source.packages.length}</span>
            </div>
          );
        })}
      </div>
      <h3 className="server-health-heading">Details by source</h3>
      {filter ? (
        <p className="muted server-health-note">
          Showing {filter} only.{" "}
          <button type="button" className="software-more" onClick={() => setFilter(null)}>
            Show all
          </button>
        </p>
      ) : null}
      <div className="software-sources">
        {sources.map((source) => (
          <SourceGroup key={`${source.key}-${filter ?? "all"}`} source={source} filter={filter} />
        ))}
      </div>
    </>
  );
}

function PackageDetailBody({ detail }: { detail: PackageDetail }) {
  const row = detail.package;
  const total = row.advisory_count ?? row.advisories.length;
  const [shown, setShown] = useState(25);
  return (
    <>
      <div className="software-facts">
        <div>
          <small>Source</small>
          <span>{detail.source === "host" ? `Host · ${detail.os}` : detail.source_title}</span>
        </div>
        <div>
          <small>Installed</small>
          <span>{row.installed}</span>
        </div>
        <div>
          <small>Newest available</small>
          <span>{row.newest}</span>
        </div>
        <div>
          <small>Risk</small>
          <span className={`software-pill software-${row.risk}`}>{riskLabel(row.risk)}</span>
        </div>
      </div>
      <div className="software-callout">
        {row.behind ? `An update is available: ${row.installed} to ${row.newest}.` : "No fix yet. This is the newest version available, so there is nothing to install."}{" "}
        <span className="muted">Last checked {when(detail.checked_at)}.</span>
      </div>
      <h2 className="server-health-heading">Advisories · {total.toLocaleString()}</h2>
      {row.advisories.length === 0 ? (
        <div className="state-box">No advisories are listed for this package.</div>
      ) : (
        <div className="software-rows">
          {row.advisories.slice(0, shown).map((item) => {
            const url = advisoryUrl(item.id);
            return (
              <div className="software-advisory" key={item.id}>
                <span className={`software-pill software-${item.risk}`}>{riskLabel(item.risk)}</span>
                {url ? (
                  <a href={url} target="_blank" rel="noreferrer">
                    {item.id}
                  </a>
                ) : (
                  <span>{item.id}</span>
                )}
                <span>{item.summary || "No summary."}</span>
              </div>
            );
          })}
        </div>
      )}
      {row.advisories.length > shown ? (
        <button type="button" className="software-more" onClick={() => setShown(shown + 100)}>
          Show {Math.min(100, row.advisories.length - shown)} more
        </button>
      ) : null}
    </>
  );
}

export function ServerHealthPackagePage() {
  const [params] = useSearchParams();
  const source = params.get("source") ?? "";
  const name = params.get("name") ?? "";
  const query = useQuery({
    queryKey: ["server-health-package", source, name],
    queryFn: () => api.serverHealthPackage(source, name) as Promise<PackageDetail>,
    enabled: Boolean(source && name),
  });
  return (
    <div className="server-health">
      <p className="page-sub">
        <Link to="/server-health?tab=software">Server Health</Link> / Software / {name}
      </p>
      <h1 className="page-title">{name || "Package"}</h1>
      {query.isLoading ? <div className="state-box">Loading…</div> : null}
      {query.isError ? <div className="state-box">That package is not in the latest check.</div> : null}
      {query.data ? (
        <div className="panel">
          <PackageDetailBody detail={query.data} />
        </div>
      ) : null}
    </div>
  );
}

const SECTIONS = ["cpu", "memory", "interface", "storage", "database", "software", "stuck"] as const;
type SectionKey = (typeof SECTIONS)[number];

/** The figures are the buttons. Software and Processes get tiles of their own. */
function CardTabs({
  latest,
  diskPercent,
  report,
  active,
  onPick,
}: {
  latest: Point | null;
  diskPercent: number | null;
  report: Report;
  active: string;
  onPick: (key: string) => void;
}) {
  const reviewRows = allPackages(report.advisories);
  const behind = reviewRows.length;
  const worst = [...reviewRows].sort((a, b) => riskRank(a.risk) - riskRank(b.risk))[0]?.risk;
  const tiles: { key: string; label: string; value: string; note: string; warn?: boolean }[] = [
    { key: "cpu", label: "CPU", value: latest?.cpu == null ? "—" : `${latest.cpu.toFixed(0)}%`, note: "busy" },
    {
      key: "memory",
      label: "Memory",
      value: latest ? amount(latest.mem_used_mb * 1024 * 1024) : "—",
      note: latest ? `of ${amount(latest.mem_total_mb * 1024 * 1024)}` : "",
    },
    { key: "interface", label: latest?.iface ? `Interface ${latest.iface}` : "Interface", value: perSecond(latest?.rx_bps), note: `out ${perSecond(latest?.tx_bps)}` },
    {
      key: "storage",
      label: "Storage",
      value: diskPercent == null ? "—" : `${diskPercent}% full`,
      note: latest ? `${amount(latest.disk_used_mb * 1024 * 1024)} of ${amount(latest.disk_total_mb * 1024 * 1024)}` : "",
    },
    { key: "database", label: "Database", value: amount(latest?.db_bytes), note: `${latest?.connections ?? 0} connections` },
    {
      key: "software",
      label: "Software",
      value: report.advisories ? `${behind} to review` : "Not checked",
      note: worst ? `highest risk ${worst}` : "weekly check",
      warn: worst === "critical" || worst === "high",
    },
    {
      key: "stuck",
      label: "Processes",
      value: report.stuck.length === 0 ? "None stuck" : `${report.stuck.length} stuck`,
      note: "look stuck",
      warn: report.stuck.length > 0,
    },
  ];
  return (
    <div className="server-health-tiles" role="tablist" aria-label="Server Health sections">
      {tiles.map((tile) => (
        <button
          key={tile.key}
          type="button"
          role="tab"
          aria-selected={active === tile.key}
          className={`server-health-tile${active === tile.key ? " active" : ""}${tile.warn ? " warn" : ""}`}
          onClick={() => onPick(tile.key)}
        >
          <span>{tile.label}</span>
          <strong>{tile.value}</strong>
          <small>{tile.note}</small>
        </button>
      ))}
    </div>
  );
}

export default function ServerHealthPage() {
  const [range, setRange] = useState<Range>("1h");
  const [params, setParams] = useSearchParams();
  const active: SectionKey = SECTIONS.find((key) => key === params.get("tab")) ?? "cpu";

  function pick(key: string) {
    const next = new URLSearchParams(params);
    next.set("tab", key);
    setParams(next, { replace: true });
  }

  // The storage chart always shows the last 7 days, so the line has history to project from.
  const fetchRange: Range = active === "storage" ? "7d" : range;
  const query = useQuery({
    queryKey: ["server-health", fetchRange],
    queryFn: () => api.serverHealth(fetchRange) as Promise<Report>,
    refetchInterval: 60_000,
  });
  const report = query.data;
  const latest = report?.latest ?? null;
  const data = useMemo(
    () =>
      (report?.points ?? []).map((point) => ({
        ts: new Date(point.t).getTime(),
        cpu: point.cpu,
        mem: point.mem_used_mb * 1024 * 1024,
        rx: point.rx_bps,
        tx: point.tx_bps,
        disk: point.disk_used_mb * 1024 * 1024,
        db: point.db_bytes,
        forecast: point.forecast_bytes,
      })),
    [report],
  );
  const span = chartWindow(range);
  const diskPercent =
    latest && latest.disk_total_mb > 0 ? Math.round((100 * latest.disk_used_mb) / latest.disk_total_mb) : null;

  function section(key: SectionKey, current: Report) {
    switch (key) {
      case "cpu":
        return <Chart key={key} title="CPU" data={data} span={span} lines={[{ key: "cpu", name: "Busy" }]} unit=" %" domain={[0, 100]} />;
      case "memory":
        return (
          <Chart
            key={key}
            title="Memory"
            note={latest ? `Total ${amount(latest.mem_total_mb * 1024 * 1024)}` : undefined}
            data={data}
            span={span}
            lines={[{ key: "mem", name: "Used" }]}
            unit=""
          />
        );
      case "interface":
        return (
          <Chart
            key={key}
            title={latest?.iface ? `Interface ${latest.iface}` : "Interface"}
            data={data}
            span={span}
            lines={[
              { key: "rx", name: "In" },
              { key: "tx", name: "Out" },
            ]}
            unit="/s"
          />
        );
      case "storage":
        return (
          <div className="panel" key={key}>
            <h2 className="server-health-heading">Storage</h2>
            <p className="muted server-health-note">
              {latest ? `${amount(latest.disk_used_mb * 1024 * 1024)} of ${amount(latest.disk_total_mb * 1024 * 1024)} used. ` : ""}
              {projectionText(current.storage.projection)}
            </p>
            {latest ? (
              <StorageChart
                history={data.map((row) => ({ ts: row.ts, used: row.disk }))}
                projection={current.storage.projection}
                total={latest.disk_total_mb * 1024 * 1024}
              />
            ) : null}
            {current.storage.breakdown ? (
              <StorageBreakdown breakdown={current.storage.breakdown} />
            ) : (
              <p className="muted server-health-note">What uses the space is being measured. Check back in a few minutes.</p>
            )}
          </div>
        );
      case "database":
        return (
          <div className="panel" key={key}>
            <h2 className="server-health-heading">Database</h2>
            <p className="muted server-health-note">{forecastText(current.forecast)}</p>
            <Chart
              title=""
              data={data}
              span={span}
              lines={[
                { key: "db", name: "Size" },
                { key: "forecast", name: "Forecast", dashed: true },
              ]}
              unit=""
            />
            {current.tables.length > 0 ? (
              <p className="muted server-health-note">
                Largest tables: {current.tables.map((table) => `${table.name} ${amount(table.bytes)}`).join(", ")}
              </p>
            ) : null}
            <h3 className="server-health-heading">How long rows are kept</h3>
            <div className="server-health-kept">
              {current.retention.map((group) => (
                <div className="server-health-kept-row" key={group.kept}>
                  <span className="server-health-kept-time">{group.kept}</span>
                  <div className="server-health-chips">
                    {group.items.map((item) => (
                      <span className="server-health-chip" key={item}>
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      case "software":
        return (
          <div className="panel" key={key}>
            <h2 className="server-health-heading">Software and vulnerabilities</h2>
            {current.advisories == null ? (
              <div className="state-box">The weekly check has not finished.</div>
            ) : (
              <SoftwarePanel advisories={current.advisories} />
            )}
          </div>
        );
      case "stuck":
        return (
          <div className="panel" key={key}>
            <h2 className="server-health-heading">Processes that look stuck</h2>
            {current.stuck.length === 0 ? (
              <div className="state-box">No processes look stuck.</div>
            ) : (
              <table className="server-health-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>PID</th>
                    <th>State</th>
                    <th>How long</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {current.stuck.map((row) => (
                    <tr key={row.pid}>
                      <td>{row.name}</td>
                      <td>{row.pid}</td>
                      <td>{row.state}</td>
                      <td>{ago(row.since)}</td>
                      <td>{REASONS[row.reason] ?? row.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
    }
  }

  const graphTab = active !== "software" && active !== "stuck" && active !== "storage";

  return (
    <div className="server-health">
      <h1 className="page-title">Server Health</h1>
      <p className="page-sub">The machine running HomeNetWatch. Samples are taken once a minute.</p>
      {query.isLoading ? <div className="state-box">Loading server health…</div> : null}
      {query.isError ? <div className="state-box">Could not load server health.</div> : null}
      {report && !latest ? <div className="state-box">The first sample has not been recorded yet.</div> : null}
      {report ? <CardTabs latest={latest} diskPercent={diskPercent} report={report} active={active} onPick={pick} /> : null}
      {report && graphTab ? (
        <div className="range-row">
          {RANGES.map((item) => (
            <button key={item} type="button" className={`tab ${range === item ? "active" : ""}`} onClick={() => setRange(item)}>
              {item}
            </button>
          ))}
        </div>
      ) : null}
      {report ? section(active, report) : null}
    </div>
  );
}