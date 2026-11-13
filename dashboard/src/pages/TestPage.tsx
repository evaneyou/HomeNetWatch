import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { CHART_COLORS } from "../chartTheme";
import { chartTicks, chartWindow, formatChartTick, formatChartTime, zoomedDomain } from "../chartTime";
import { useChartZoom } from "../useChartZoom";
import { checkLabel, networkLabel } from "../labels";
import type { MetricsResponse, TestDetail } from "../types";

const RANGES = ["1h", "6h", "24h", "7d", "30d"] as const;

const STAGE_COLORS = CHART_COLORS;

export default function TestPage() {
  const { id = "" } = useParams();
  const [range, setRange] = useState<(typeof RANGES)[number]>("1h");

  const testQ = useQuery({
    queryKey: ["test", id],
    queryFn: () => api.test(id) as Promise<TestDetail>,
    enabled: !!id,
  });
  const metricsQ = useQuery({
    queryKey: ["metrics", id, range],
    queryFn: () => api.metrics(id, range) as Promise<MetricsResponse>,
    enabled: !!id,
  });

  const stageNames = useMemo(() => {
    const names = new Set<string>();
    for (const p of metricsQ.data?.points ?? []) {
      for (const s of p.stages) {
        if (s.name !== "ttfb") names.add(s.name);
      }
    }
    return [...names];
  }, [metricsQ.data]);

  const waterfallData = useMemo(() => {
    return (metricsQ.data?.points ?? []).map((p) => {
      const row: Record<string, string | number> = {
        t: p.t,
        ts: new Date(p.t).getTime(),
        status: p.status,
      };
      for (const name of stageNames) {
        const stage = p.stages.find((s) => s.name === name);
        row[name] = stage?.duration_ms ?? 0;
      }
      return row;
    });
  }, [metricsQ.data, stageNames]);

  const metricData = useMemo(
    () =>
      (metricsQ.data?.points ?? []).map((p) => ({
        t: p.t,
        ts: new Date(p.t).getTime(),
        rtt_ms: p.rtt_ms,
        loss_pct: p.loss_pct,
      })),
    [metricsQ.data],
  );

  if (testQ.isLoading) return <div className="state-box">Loading test…</div>;
  if (testQ.isError || !testQ.data) return <div className="state-box">Test not found.</div>;

  const test = testQ.data;
  const points = metricsQ.data?.points ?? [];
  const span = chartWindow(range);

  return (
    <div>
      <h1 className="page-title">{checkLabel(test)}</h1>
      <p className="page-sub">
        <Link to={`/monitors/${test.monitor_id}`}>{test.monitor_name}</Link>
        {" · "}
        {networkLabel({ slug: test.network_slug, name: test.network_name })}
        {" · "}
        <span className="mono">
          {checkLabel({ slug: test.kind, name: test.kind })} → {test.target}
        </span>
      </p>

      <div className="range-row">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            className={`tab ${range === r ? "active" : ""}`}
            onClick={() => setRange(r)}
          >
            {r}
          </button>
        ))}
      </div>

      {metricsQ.isLoading ? (
        <div className="state-box">Loading metrics…</div>
      ) : points.length === 0 ? (
        <div className="state-box">No results in this range.</div>
      ) : (
        <>
          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: "1rem" }}>Pass / fail strip</h2>
            <div className="passfail" title="Chronological pass/fail">
              {points.map((p, i) => (
                <span
                  key={i}
                  className={`status-${p.status}`}
                  style={{
                    background:
                      p.status === "ok"
                        ? "var(--ok)"
                        : p.status === "degraded"
                          ? "var(--degraded)"
                          : "var(--fail)",
                  }}
                />
              ))}
            </div>
          </div>

          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: "1rem" }}>Stage waterfall</h2>
            <div style={{ width: "100%", height: 280 }}>
              <ZoomArea data={waterfallData} stageNames={stageNames} span={span} />
            </div>
          </div>

          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: "1rem" }}>RTT</h2>
            <div style={{ width: "100%", height: 220 }}>
              <ZoomLine data={metricData} dataKey="rtt_ms" stroke="var(--accent)" unit="ms" span={span} />
            </div>
          </div>

          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: "1rem" }}>Packet loss</h2>
            <div style={{ width: "100%", height: 180 }}>
              <ZoomBars data={metricData} span={span} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ZoomArea({
  data,
  stageNames,
  span,
}: {
  data: Record<string, string | number>[];
  stageNames: string[];
  span: [number, number];
}) {
  const zoom = useChartZoom(data);
  const domain = zoomedDomain(span, data.length, zoom.visible as { ts?: unknown }[]);
  return (
    <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: "100%" }} onDoubleClick={zoom.onDoubleClick}>
      <ResponsiveContainer>
        <AreaChart
          data={zoom.visible}
          onMouseDown={zoom.onMouseDown}
          onMouseMove={zoom.onMouseMove}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={domain}
            allowDataOverflow
            ticks={chartTicks(domain)}
            tickFormatter={(value) => formatChartTick(value, domain[1] - domain[0])}
            hide={zoom.visible.length > 48}
            tick={{ fontSize: 10 }}
          />
          <YAxis tick={{ fontSize: 11 }} unit="ms" />
          <Tooltip labelFormatter={(value) => formatChartTime(value)} />
          {zoom.selection}
          {stageNames.map((name, i) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stackId="1"
              stroke={STAGE_COLORS[i % STAGE_COLORS.length]}
              fill={STAGE_COLORS[i % STAGE_COLORS.length]}
              fillOpacity={0.7}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ZoomLine({
  data,
  dataKey,
  stroke,
  unit,
  span,
}: {
  data: { t: string; ts: number; rtt_ms: number | null; loss_pct: number | null }[];
  dataKey: string;
  stroke: string;
  unit: string;
  span: [number, number];
}) {
  const zoom = useChartZoom(data);
  const domain = zoomedDomain(span, data.length, zoom.visible);
  return (
    <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: "100%" }} onDoubleClick={zoom.onDoubleClick}>
      <ResponsiveContainer>
        <LineChart data={zoom.visible} onMouseDown={zoom.onMouseDown} onMouseMove={zoom.onMouseMove}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={domain}
            allowDataOverflow
            ticks={chartTicks(domain)}
            tickFormatter={(value) => formatChartTick(value, domain[1] - domain[0])}
            hide={zoom.visible.length > 48}
            tick={{ fontSize: 10 }}
          />
          <YAxis tick={{ fontSize: 11 }} unit={unit} />
          <Tooltip labelFormatter={(value) => formatChartTime(value)} />
          {zoom.selection}
          <Line type="monotone" dataKey={dataKey} stroke={stroke} dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ZoomBars({
  data,
  span,
}: {
  data: { t: string; ts: number; rtt_ms: number | null; loss_pct: number | null }[];
  span: [number, number];
}) {
  const zoom = useChartZoom(data);
  const domain = zoomedDomain(span, data.length, zoom.visible);
  return (
    <div className="chart-zoom" ref={zoom.boxRef} style={{ width: "100%", height: "100%" }} onDoubleClick={zoom.onDoubleClick}>
      <ResponsiveContainer>
        <BarChart data={zoom.visible} onMouseDown={zoom.onMouseDown} onMouseMove={zoom.onMouseMove}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={domain}
            allowDataOverflow
            ticks={chartTicks(domain)}
            tickFormatter={(value) => formatChartTick(value, domain[1] - domain[0])}
            hide={zoom.visible.length > 48}
            tick={{ fontSize: 10 }}
          />
          <YAxis tick={{ fontSize: 11 }} unit="%" />
          <Tooltip labelFormatter={(value) => formatChartTime(value)} />
          {zoom.selection}
          <Bar dataKey="loss_pct" fill="var(--degraded)" radius={[12, 12, 4, 4]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
