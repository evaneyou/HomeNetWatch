import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { api } from "../api";
import { CHART_COLORS } from "../chartTheme";
import { chartWindow, formatChartTick, zoomedDomain } from "../chartTime";
import { useChartZoom } from "../useChartZoom";
import LimitShareChart from "./LimitShareChart";
import { TimeAxis } from "./InternetDetail";

const RANGES = ["1h", "6h", "12h", "24h", "7d", "30d"] as const;
type Range = (typeof RANGES)[number];

type Point = {
  t: string;
  rtt_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number | null;
  verdict: string | null;
};

type Limits = { max_rtt_ms: number; max_jitter_ms: number; max_loss_pct: number };

type TrafficType = {
  slug: string;
  name: string;
  limits?: Limits;
  latest: { rtt_ms: number | null; jitter_ms: number | null; loss_pct: number | null; verdict: string | null };
  points: Point[];
};

const DEFAULT_LIMITS: Limits = { max_rtt_ms: 300, max_jitter_ms: 30, max_loss_pct: 1 };

function fmt(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(1);
}

function statusOf(verdict: string | null): string {
  if (verdict === "pass") return "ok";
  if (verdict === "fail") return "fail";
  if (verdict === "software") return "software";
  return "unknown";
}

type Diagnosis = { cause: string; title: string; summary: string };

export default function RealtimeHealth({
  monitorId,
  link,
  range,
  slug,
  title,
  onOpen,
}: {
  monitorId: string;
  link: "wired" | "wireless";
  range: Range;
  slug?: string | null;
  title?: string;
  onOpen?: (slug: string) => void;
}) {
  const healthQ = useQuery({
    queryKey: ["synthetic-health", monitorId, link, range],
    queryFn: () =>
      api.syntheticHealth(monitorId, link, range) as Promise<{
        types: TrafficType[];
        diagnosis?: Diagnosis | null;
      }>,
    refetchInterval: 15000,
  });
  const types = healthQ.data?.types ?? [];
  const diagnosis = healthQ.data?.diagnosis ?? null;
  const selected = slug ? types.find((item) => item.slug === slug) : null;

  if (slug) {
    return (
      <div className="panel">
        <h2 style={{ marginTop: 0, fontSize: "1.4rem" }}>
          {link === "wireless" ? "Wi-Fi" : "Wired"} · {selected?.name ?? "Real-Time Application Health"}
        </h2>
        {diagnosis ? (
          <div
            style={{
              padding: "10px 14px",
              marginBottom: 14,
              borderRadius: 8,
              border: "1px solid var(--degraded)",
              background: "color-mix(in srgb, var(--degraded) 10%, var(--card))",
              fontSize: "0.9rem",
            }}
          >
            <strong>Root cause · {diagnosis.title}:</strong> {diagnosis.summary}
          </div>
        ) : null}
        {selected?.latest.verdict === "software" ? (
          <p className="muted">Software problem in the monitor. These results are left out of the KPI, and a restart of this test was requested.</p>
        ) : null}
        <ChartBody item={selected} range={range} loading={healthQ.isLoading} failed={healthQ.isError} />
      </div>
    );
  }

  return (
    <div className="health-stack">
      <h2>{title ?? "Real-Time Application Health"}</h2>
      {diagnosis ? (
        <div
          style={{
            padding: "10px 14px",
            marginBottom: 6,
            borderRadius: 8,
            border: "1px solid var(--degraded)",
            background: "color-mix(in srgb, var(--degraded) 10%, var(--card))",
            fontSize: "0.9rem",
          }}
        >
          <strong>Root cause · {diagnosis.title}:</strong> {diagnosis.summary}
        </div>
      ) : null}
      {healthQ.isLoading ? <div className="muted">Loading real-time application health…</div> : null}
      {healthQ.isError ? <div className="muted">Could not load real-time application health.</div> : null}
      {!healthQ.isLoading && types.length === 0 ? (
        <div className="muted">No continuous application check is running on this link.</div>
      ) : null}
      {types.map((item) => (
        <div className="health-row is-link" key={item.slug} onClick={() => onOpen?.(item.slug)}>
          <div className="health-row-head">
            <button className="health-row-title" type="button">
              {item.name}
              <svg className="row-chevron" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            </button>
            <span className="mono">
              {fmt(item.latest.rtt_ms)} ms · jitter {fmt(item.latest.jitter_ms)} ms · loss {fmt(item.latest.loss_pct)}%
              {item.latest.verdict === "software" ? " · software" : ""}
            </span>
          </div>
          <span
            className="isp-strip health-bar"
            title={item.name}
            style={{ background: strip(item.points) }}
          />
        </div>
      ))}
    </div>
  );
}

function strip(points: Point[]): string {
  if (points.length === 0) return "var(--line)";
  const statuses = points.map((point) => statusOf(point.verdict));
  const stops: string[] = [];
  let index = 0;
  while (index < statuses.length) {
    const status = statuses[index];
    let end = index + 1;
    while (end < statuses.length && statuses[end] === status) end += 1;
    const color = status === "ok" ? "var(--ok)" : status === "fail" ? "var(--fail)" : status === "software" ? "var(--degraded)" : "var(--line)";
    stops.push(`${color} ${(index / statuses.length) * 100}% ${(end / statuses.length) * 100}%`);
    index = end;
  }
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

function ChartBody({
  item,
  range,
  loading,
  failed,
}: {
  item: TrafficType | null | undefined;
  range: Range;
  loading: boolean;
  failed: boolean;
}) {
  const data = useMemo(
    () =>
      (item?.points ?? []).map((point) => ({
        t: point.t,
        ts: new Date(point.t).getTime(),
        rtt: point.rtt_ms,
        jitter: point.jitter_ms,
        loss: point.loss_pct,
      })),
    [item],
  );
  const span = chartWindow(range);
  const limits = item?.limits ?? DEFAULT_LIMITS;
  if (loading) return <div className="muted">Loading real-time application health…</div>;
  if (failed) return <div className="muted">Could not load real-time application health.</div>;
  if (data.length === 0) return <div className="muted">No samples in this range.</div>;
  return (
    <>
      <MiniChart title="Round-trip" data={data} span={span} dataKey="rtt" unit=" ms" />
      <MiniChart title="Jitter" data={data} span={span} dataKey="jitter" unit=" ms" />
      <MiniChart title="Packet loss" data={data} span={span} dataKey="loss" unit=" %" />
      <LimitShareChart
        note={`Limits are ${fmt(limits.max_rtt_ms)} ms round-trip, ${fmt(limits.max_jitter_ms)} ms jitter, and ${fmt(limits.max_loss_pct)}% loss. 100% is that limit. A bar past it failed. Only moments that crossed a limit are shown.`}
        range={range}
        metrics={[
          { key: "rtt", label: "Round-trip", limit: limits.max_rtt_ms, unit: " ms" },
          { key: "jitter", label: "Jitter", limit: limits.max_jitter_ms, unit: " ms" },
          { key: "loss", label: "Packet loss", limit: limits.max_loss_pct, unit: "%" },
        ]}
        samples={data.map((point) => ({
          ts: point.ts,
          values: { rtt: point.rtt, jitter: point.jitter, loss: point.loss },
        }))}
      />
    </>
  );
}

function MiniChart({
  title,
  data,
  span,
  dataKey,
  unit,
}: {
  title: string;
  data: { ts: number; rtt: number | null; jitter: number | null; loss: number | null }[];
  span: [number, number];
  dataKey: "rtt" | "jitter" | "loss";
  unit: string;
}) {
  const zoom = useChartZoom(data);
  const domain = zoomedDomain(span, data.length, zoom.visible as { ts?: unknown }[]);
  return (
    <div>
      <h3 className="wifi-heading">{title}</h3>
      <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: 200 }} onDoubleClick={zoom.onDoubleClick}>
        <ResponsiveContainer>
          <LineChart
            data={zoom.visible}
            margin={{ top: 8, right: 8, left: 0, bottom: 4 }}
            onMouseDown={zoom.onMouseDown}
            onMouseMove={zoom.onMouseMove}
          >
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <TimeAxis domain={domain} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip
              labelFormatter={(value) => formatChartTick(Number(value), domain[1] - domain[0])}
              formatter={(value) => `${value ?? ""}${unit}`}
            />
            {zoom.selection}
            <Line type="monotone" dataKey={dataKey} stroke={CHART_COLORS[0]} dot={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
