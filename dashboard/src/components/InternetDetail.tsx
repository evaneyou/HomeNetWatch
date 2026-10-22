import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { InternetServiceSetting } from "../types";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { isAnonymized } from "../anonymized";
import { chartTicks, chartWindow, formatChartTick, zoomedDomain } from "../chartTime";
import { useChartZoom } from "../useChartZoom";
import LimitShareChart, { type LimitMetric } from "./LimitShareChart";
import RealtimeHealth from "./RealtimeHealth";
import {
  APPS,
  COLORS,
  type ChartRange,
  type CheckKey,
  type DetailKey,
  ISP_SLUGS,
  RANGES,
  type TestRow,
  destLatency,
  fmt,
  httpStages,
  httpTime,
  isCheck,
  probeKinds,
  speed,
  statusStrip,
  stripGradient,
  useSeries,
  worstHopLoss,
} from "./pathSeries";

export function InternetDetail({
  tests,
  detail,
  isp,
  linkLabel,
  monitorId,
  range: rangeProp,
  onRangeChange,
}: {
  tests: TestRow[];
  detail: DetailKey;
  isp: string | null;
  linkLabel?: string | null;
  monitorId?: string;
  range?: ChartRange;
  onRangeChange?: (range: ChartRange) => void;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [localRange, setLocalRange] = useState<ChartRange>("1h");
  const settingsQ = useQuery({
    queryKey: ["internet-service"],
    queryFn: () => api.internetService() as Promise<InternetServiceSetting>,
    enabled: detail === "isp",
  });
  const range = rangeProp ?? localRange;
  const setRange = onRangeChange ?? setLocalRange;
  const bySlug = useMemo(() => new Map(tests.map((test) => [test.slug, test])), [tests]);
  const app = APPS.find((item) => item.key === detail);
  const slugs =
    detail === "isp"
      ? [...ISP_SLUGS, "librespeed"]
      : probeKinds(detail).map((kind) => `${kind}-${detail}`);
  const watched = slugs.flatMap((slug) => {
    const test = bySlug.get(slug);
    return test ? [test] : [];
  });
  const { points, loading, failed } = useSeries(watched, range);

  const httpSlug = detail === "isp" ? "http-path" : `http-${detail}`;
  const icmpSlug = detail === "isp" ? "icmp-path" : `icmp-${detail}`;
  // Only the ISP path is traced; web services are HTTP and ICMP alone.
  const mtrSlug = "mtr-path";

  const httpChart = useMemo(() => {
    const names = new Set<string>();
    for (const point of points.get(httpSlug) ?? []) {
      for (const stage of httpStages(point)) names.add(stage.name);
    }
    const stageNames = [...names];
    const rows = (points.get(httpSlug) ?? []).map((point) => {
      const row: Record<string, string | number> = { t: point.t, ts: new Date(point.t).getTime() };
      const stages = httpStages(point);
      for (const name of stageNames) {
        row[name] = stages.find((stage) => stage.name === name)?.duration_ms ?? 0;
      }
      return row;
    });
    return { stageNames, rows };
  }, [points, httpSlug]);

  const httpLine = useMemo(
    () =>
      (points.get(httpSlug) ?? []).map((point) => ({
        t: point.t,
        ts: new Date(point.t).getTime(),
        ms: httpTime(point),
      })),
    [points, httpSlug],
  );

  const icmpChart = useMemo(
    () =>
      (points.get(icmpSlug) ?? []).map((point) => ({
        t: point.t,
        ts: new Date(point.t).getTime(),
        rtt: point.rtt_ms,
        loss: point.loss_pct,
        jitter: point.jitter_ms,
      })),
    [points, icmpSlug],
  );

  const mtrChart = useMemo(
    () =>
      (points.get(mtrSlug) ?? []).map((point) => ({
        t: point.t,
        ts: new Date(point.t).getTime(),
        latency: destLatency(point),
        loss: worstHopLoss(point),
      })),
    [points, mtrSlug],
  );

  const transferChart = useMemo(
    () =>
      (points.get("librespeed") ?? []).map((point) => {
        const sample = speed(point);
        return {
          t: point.t,
          ts: new Date(point.t).getTime(),
          down: sample.down,
          up: sample.up,
          ping: point.rtt_ms,
          jitter: point.jitter_ms,
          status: point.status,
        };
      }),
    [points],
  );

  const latestHops = (points.get(mtrSlug) ?? []).at(-1)?.hops ?? [];
  const latestLibre = (points.get("librespeed") ?? []).at(-1);
  const transferStrip = useMemo(
    () => statusStrip(points.get("librespeed") ?? [], range),
    [points, range],
  );
  const span = chartWindow(range);
  const linkHealth = detail === "isp" && (linkLabel === "Wired" || linkLabel === "Wi-Fi");
  const healthLink = linkLabel === "Wi-Fi" ? "wireless" : "wired";
  const requestedCheck = searchParams.get("check");
  const rtnpSlug = requestedCheck?.startsWith("rtnp:") ? requestedCheck.slice(5) : null;
  const check = linkHealth && isCheck(requestedCheck) ? requestedCheck : null;

  function openCheck(key: CheckKey) {
    const next = new URLSearchParams(searchParams);
    next.set("check", key);
    setSearchParams(next);
  }

  function openConnection() {
    const next = new URLSearchParams(searchParams);
    next.set("detail", "connection");
    next.delete("check");
    next.delete("view");
    setSearchParams(next);
  }

  if (linkHealth && rtnpSlug && monitorId) {
    return (
      <>
        {onRangeChange ? null : (
          <div className="panel">
            <RangeTabs range={range} onChange={setRange} />
          </div>
        )}
        <RealtimeHealth
          monitorId={monitorId}
          link={healthLink}
          range={range}
          slug={rtnpSlug}
        />
      </>
    );
  }

  if (linkHealth && !check) {
    return (
      <>
        <section className="panel network-health">
          <h2>{linkLabel} Network Health</h2>
          {onRangeChange ? null : <RangeTabs range={range} onChange={setRange} />}
          {loading ? (
            <div className="muted">Loading internet checks…</div>
          ) : failed ? (
            <div className="muted">Could not load internet checks.</div>
          ) : (
            <div className="health-stack">
              <HealthBar
                title="Internet Connection"
                detail={`${fmt(speed(latestLibre).down)} Mbps down · ${fmt(speed(latestLibre).up)} Mbps up · ping ${fmt(latestLibre?.rtt_ms)} ms · jitter ${fmt(latestLibre?.jitter_ms)} ms`}
                strip={transferStrip}
                empty={transferChart.length === 0}
                onOpen={openConnection}
              />
            </div>
          )}
        </section>
        {monitorId && !loading && !failed ? (
          <section className="panel network-health">
            <RealtimeHealth
              monitorId={monitorId}
              link={healthLink}
              range={range}
              onOpen={(slug) => openCheck(`rtnp:${slug}`)}
            />
          </section>
        ) : null}
      </>
    );
  }

  if (check === "librespeed") {
    return (
      <div className="panel">
        <h2 style={{ marginTop: 0, fontSize: "1.4rem" }}>
          {linkLabel} · Internet Connection
        </h2>
        {onRangeChange ? null : <RangeTabs range={range} onChange={setRange} />}
        {loading ? (
          <div className="muted">Loading internet checks…</div>
        ) : failed ? (
          <div className="muted">Could not load internet checks.</div>
        ) : (
          <>
            <PathChart
              title="Download and upload"
              data={transferChart}
              span={span}
              series={[
                { key: "down", label: "Download" },
                { key: "up", label: "Upload" },
              ]}
              unit=" Mbps"
            />
            <PathChart title="Ping" data={transferChart} span={span} dataKey="ping" label="Ping" unit=" ms" />
            <PathChart title="Jitter" data={transferChart} span={span} dataKey="jitter" label="Jitter" unit=" ms" />
            <ConnectionLimitChart points={transferChart} range={range} variancePct={settingsQ.data?.librespeed_variance_pct ?? 25} />
          </>
        )}
      </div>
    );
  }

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0, fontSize: "1.4rem" }}>
        {linkLabel ? `${linkLabel} · ` : ""}
        {detail === "isp" ? isp || "ISP" : app?.label}
      </h2>
      {onRangeChange ? null : <RangeTabs range={range} onChange={setRange} />}
      {loading ? (
        <div className="muted">Loading internet checks…</div>
      ) : failed ? (
        <div className="muted">Could not load internet checks.</div>
      ) : detail === "isp" ? (
        <>
          <div>
            <h3 className="wifi-heading">HTTP</h3>
            {httpChart.rows.length === 0 ? (
              <div className="muted">No samples in this range.</div>
            ) : (
              <HttpAreaChart rows={httpChart.rows} stageNames={httpChart.stageNames} span={span} />
            )}
          </div>
          <div className="chart-pair">
            <PathChart title="ICMP round-trip" data={icmpChart} span={span} dataKey="rtt" label="Round-trip" unit=" ms" />
            <PathChart title="ICMP packet loss" data={icmpChart} span={span} dataKey="loss" label="Loss" unit=" %" />
          </div>
          <div className="chart-pair">
            <PathChart title="MTR destination latency" data={mtrChart} span={span} dataKey="latency" label="Destination" unit=" ms" />
            <PathChart title="MTR worst hop loss" data={mtrChart} span={span} dataKey="loss" label="Worst hop" unit=" %" />
          </div>
          <h3 className="wifi-heading">Latest MTR hops</h3>
          {latestHops.length === 0 ? (
            <div className="muted">No hop sample in this range.</div>
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Hop</th>
                    <th>Host</th>
                    <th>Loss</th>
                    <th>Best</th>
                    <th>Avg</th>
                    <th>Worst</th>
                  </tr>
                </thead>
                <tbody>
                  {latestHops.map((hop) => (
                    <tr key={hop.hop}>
                      <td className="mono">{hop.hop}</td>
                      <td className={isAnonymized(hop.host) ? "anon-mark" : "mono"}>{hop.host}</td>
                      <td className="mono">{fmt(hop.loss_pct)}%</td>
                      <td className="mono">{fmt(hop.best_ms)} ms</td>
                      <td className="mono">{fmt(hop.avg_ms)} ms</td>
                      <td className="mono">{fmt(hop.worst_ms)} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="chart-pair">
            <PathChart title="HTTP response time" data={httpLine} span={span} dataKey="ms" label="HTTP" unit=" ms" />
            <PathChart title="ICMP round-trip" data={icmpChart} span={span} dataKey="rtt" label="Round-trip" unit=" ms" />
          </div>
          <div className="chart-pair">
            <PathChart title="ICMP packet loss" data={icmpChart} span={span} dataKey="loss" label="Loss" unit=" %" />
            <PathChart title="ICMP jitter" data={icmpChart} span={span} dataKey="jitter" label="Jitter" unit=" ms" />
          </div>
        </>
      )}
    </div>
  );
}

export function RangeTabs({
  range,
  onChange,
}: {
  range: (typeof RANGES)[number];
  onChange: (range: (typeof RANGES)[number]) => void;
}) {
  return (
    <div className="range-row">
      {RANGES.map((item) => (
        <button
          key={item}
          type="button"
          className={`tab ${range === item ? "active" : ""}`}
          onClick={() => onChange(item)}
        >
          {item}
        </button>
      ))}
    </div>
  );
}

export function HealthBar({
  title,
  detail,
  strip,
  empty,
  onOpen,
}: {
  title: string;
  detail: string;
  strip: string[];
  empty: boolean;
  onOpen?: () => void;
}) {
  return (
    <div className={onOpen ? "health-row is-link" : "health-row"} onClick={onOpen}>
      <div className="health-row-head">
        {onOpen ? (
          <button className="health-row-title" type="button">
            {title}
            <svg className="row-chevron" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        ) : (
          <strong>{title}</strong>
        )}
        <span className="mono">{detail}</span>
      </div>
      <span className="isp-strip health-bar" title={title} style={{ background: stripGradient(strip) }} />
      {empty ? <div className="muted">No samples in this range.</div> : null}
    </div>
  );
}

function HttpAreaChart({
  rows,
  stageNames,
  span,
}: {
  rows: Record<string, string | number>[];
  stageNames: string[];
  span: [number, number];
}) {
  const zoom = useChartZoom(rows);
  const domain = zoomedDomain(span, rows.length, zoom.visible as { ts?: unknown }[]);
  return (
    <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: 240 }} onDoubleClick={zoom.onDoubleClick}>
      <ResponsiveContainer>
        <AreaChart
          data={zoom.visible}
          margin={{ top: 8, right: 8, left: 0, bottom: 4 }}
          onMouseDown={zoom.onMouseDown}
          onMouseMove={zoom.onMouseMove}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <TimeAxis domain={domain} />
          <YAxis tick={{ fontSize: 11 }} unit=" ms" width={64} />
          <Tooltip labelFormatter={(value) => formatChartTick(value, domain[1] - domain[0])} />
          {zoom.selection}
          {stageNames.map((name, index) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stackId="http"
              stroke={COLORS[index % COLORS.length]}
              fill={COLORS[index % COLORS.length]}
              fillOpacity={0.65}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TimeAxis({ domain }: { domain: [number, number] }) {
  const span = domain[1] - domain[0];
  return (
    <XAxis
      dataKey="ts"
      type="number"
      scale="time"
      domain={domain}
      allowDataOverflow
      ticks={chartTicks(domain)}
      tickFormatter={(value) => formatChartTick(value, span)}
      tick={{ fontSize: 10, fill: "var(--muted)" }}
      minTickGap={56}
      angle={-28}
      textAnchor="end"
      height={40}
    />
  );
}

function median(values: Array<number | null>, allowZero = false): number | null {
  const real = values.filter((value): value is number => (
    value != null && Number.isFinite(value) && (allowZero ? value >= 0 : value > 0)
  ));
  if (real.length < 3) return null;
  const ordered = [...real].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[mid] : (ordered[mid - 1] + ordered[mid]) / 2;
}

function ConnectionLimitChart({
  points,
  range,
  variancePct,
}: {
  points: { ts: number; down: number | null; up: number | null; ping: number | null; jitter: number | null; status: string }[];
  range: ChartRange;
  variancePct: number;
}) {
  const steady = points.filter((point) => point.status === "ok");
  const room = 1 + variancePct / 100;
  const floorRatio = Math.max(0, 1 - variancePct / 100);
  const down = median(steady.map((point) => point.down));
  const up = median(steady.map((point) => point.up));
  const ping = median(steady.map((point) => point.ping));
  const jitter = median(steady.map((point) => point.jitter), true);
  const metrics: LimitMetric[] = [];
  if (down != null && floorRatio > 0) metrics.push({ key: "down", label: "Download", limit: down * floorRatio, unit: " Mbps", floor: true });
  if (up != null && floorRatio > 0) metrics.push({ key: "up", label: "Upload", limit: up * floorRatio, unit: " Mbps", floor: true });
  if (ping != null) metrics.push({ key: "ping", label: "Ping", limit: ping * room, unit: " ms" });
  if (jitter != null && jitter > 0) metrics.push({ key: "jitter", label: "Jitter", limit: jitter * room, unit: " ms" });
  if (metrics.length === 0) {
    return (
      <div>
        <h3 className="wifi-heading">Share of each limit</h3>
        <p className="muted">Not enough steady results yet to compare against the limits.</p>
      </div>
    );
  }
  return (
    <LimitShareChart
      note={`100% is the limit from the recent normal and the ${variancePct}% tolerance. A bar past it failed: download or upload fell below the floor, or ping or jitter rose above the tolerance. Only those moments are shown.`}
      range={range}
      metrics={metrics}
      samples={points.map((point) => ({
        ts: point.ts,
        values: { down: point.down, up: point.up, ping: point.ping, jitter: point.jitter },
      }))}
    />
  );
}

function PathChart({
  title,
  data,
  span,
  dataKey,
  label,
  series,
  unit,
}: {
  title: string;
  data: Record<string, string | number | null>[];
  span: [number, number];
  dataKey?: string;
  label?: string;
  series?: { key: string; label: string }[];
  unit: string;
}) {
  const lines = series ?? (dataKey ? [{ key: dataKey, label: label ?? dataKey }] : []);
  const zoom = useChartZoom(data);
  const domain = zoomedDomain(span, data.length, zoom.visible as { ts?: unknown }[]);
  return (
    <div>
      <h3 className="wifi-heading">{title}</h3>
      {data.length === 0 ? (
        <div className="muted">No samples in this range.</div>
      ) : (
        <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: 240 }} onDoubleClick={zoom.onDoubleClick}>
          <ResponsiveContainer>
            <LineChart
              data={zoom.visible}
              margin={{ top: 8, right: 8, left: 0, bottom: 4 }}
              onMouseDown={zoom.onMouseDown}
              onMouseMove={zoom.onMouseMove}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <TimeAxis domain={domain} />
              <YAxis tick={{ fontSize: 11 }} unit={unit} width={64} />
              <Tooltip labelFormatter={(value) => formatChartTick(value, domain[1] - domain[0])} />
              {zoom.selection}
              {lines.map((line, index) => (
                <Line
                  key={line.key}
                  type="monotone"
                  dataKey={line.key}
                  name={line.label}
                  stroke={COLORS[index % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
