import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
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
type PackageRow = { name: string; installed: string; newest: string; behind: boolean; advisories: Advisory[]; risk: string };
type Report = {
  latest: Point | null;
  points: Point[];
  tables: { name: string; bytes: number }[];
  forecast: { ready: boolean; growing: boolean; full_on: string | null; bytes_per_day: number | null; far: boolean };
  retention: { kept: string; items: string[] }[];
  advisories: { checked_at: string; os: string; kernel: string; error?: string; packages: PackageRow[] } | null;
  stuck: { pid: number; name: string; state: string; reason: string; since: string }[];
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
  const behind = report.advisories?.packages.length ?? 0;
  const worst = report.advisories?.packages[0]?.risk;
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

  const query = useQuery({
    queryKey: ["server-health", range],
    queryFn: () => api.serverHealth(range) as Promise<Report>,
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
        return <Chart key={key} title="Storage" data={data} span={span} lines={[{ key: "disk", name: "Used" }]} unit="" />;
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
              <>
                <p className="muted server-health-note">
                  Last checked {when(current.advisories.checked_at)}. The next check is Sunday at 3:00 AM Eastern.
                </p>
                <p>
                  {current.advisories.os}
                  {current.advisories.kernel ? ` · kernel ${current.advisories.kernel}` : ""}
                </p>
                {current.advisories.error ? <p className="muted">{current.advisories.error}</p> : null}
                {current.advisories.packages.length === 0 ? (
                  <div className="state-box">
                    {current.advisories.error
                      ? "Packages could not be compared on this check."
                      : "No packages are behind, and none has a known vulnerability."}
                  </div>
                ) : (
                  <table className="server-health-table">
                    <thead>
                      <tr>
                        <th>Package</th>
                        <th>Installed</th>
                        <th>Newest</th>
                        <th>Advisory</th>
                        <th>Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.advisories.packages.map((row) => (
                        <tr key={row.name}>
                          <td>{row.name}</td>
                          <td>{row.installed}</td>
                          <td>{row.newest}</td>
                          <td>{row.advisories.map((item) => item.id).filter(Boolean).join(", ") || "—"}</td>
                          <td className={`risk-${row.risk}`}>{row.risk}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
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

  const graphTab = active !== "software" && active !== "stuck";

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