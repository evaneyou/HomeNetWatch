import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { CHART_COLORS } from "../chartTheme";
import { chartWindow, formatChartTick } from "../chartTime";
import { TimeAxis } from "./TimeAxis";

export type LimitMetric = {
  key: string;
  label: string;
  /** The line this metric is judged against. */
  limit: number;
  unit: string;
  /** A speed fails when it falls below the limit. Other metrics fail when they rise above it. */
  floor?: boolean;
};

export type LimitSample = {
  ts: number;
  values: Record<string, number | null>;
};

const BUCKET_MS: Record<string, number> = {
  "1h": 60 * 1000,
  "6h": 5 * 60 * 1000,
  "12h": 15 * 60 * 1000,
  "24h": 30 * 60 * 1000,
  "7d": 3 * 60 * 60 * 1000,
  "30d": 12 * 60 * 60 * 1000,
};

function shareOf(metric: LimitMetric, value: number | null): number | null {
  if (value == null || !Number.isFinite(value) || !(metric.limit > 0)) return null;
  if (metric.floor) {
    if (value <= 0) return null;
    return (metric.limit / value) * 100;
  }
  return (value / metric.limit) * 100;
}

function sharesFor(sample: LimitSample, metrics: LimitMetric[]): Record<string, number | null> {
  const shares: Record<string, number | null> = {};
  for (const metric of metrics) shares[metric.key] = shareOf(metric, sample.values[metric.key] ?? null);
  return shares;
}

/**
 * One row per moment, on the sample's own timestamp. A metric is filled in only when
 * that metric crossed its limit, so a round-trip failure does not appear as packet loss.
 */
export function limitMoments(samples: LimitSample[], metrics: LimitMetric[], range: string, now = Date.now()): LimitSample[] {
  const usable = metrics.filter((metric) => metric.limit > 0);
  if (usable.length === 0) return [];
  const span = chartWindow(range, now);
  let size = BUCKET_MS[range] ?? 60 * 1000;
  let rows: LimitSample[] = [];
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const byTime = new Map<number, LimitSample>();
    for (const metric of usable) {
      const groups = new Map<number, { sample: LimitSample; share: number }>();
      for (const sample of samples) {
        if (!Number.isFinite(sample.ts)) continue;
        const share = shareOf(metric, sample.values[metric.key] ?? null);
        if (share == null || share <= 100) continue;
        const start = Math.floor(sample.ts / size) * size;
        const current = groups.get(start);
        if (!current || share > current.share) groups.set(start, { sample, share });
      }
      for (const { sample } of groups.values()) {
        const existing = byTime.get(sample.ts);
        if (existing) {
          existing.values[metric.key] = sample.values[metric.key] ?? null;
          continue;
        }
        const values: Record<string, number | null> = {};
        for (const item of usable) values[item.key] = null;
        values[metric.key] = sample.values[metric.key] ?? null;
        byTime.set(sample.ts, { ts: sample.ts, values });
      }
    }
    rows = [...byTime.values()].sort((left, right) => left.ts - right.ts);
    if (rows.length <= 16 || size * 2 > span[1] - span[0]) break;
    size *= 2;
  }
  return rows;
}

function formatRaw(value: number, unit: string): string {
  if (unit === "%") return `${value.toFixed(1)}%`;
  if (unit === " Mbps") return `${value.toFixed(1)} Mbps`;
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ms`;
}

export default function LimitShareChart({
  note,
  samples,
  metrics,
  range,
  domain,
}: {
  note: string;
  samples: LimitSample[];
  metrics: LimitMetric[];
  range: string;
  /** Same window the line charts above use, so the time labels sit on the same marks. */
  domain?: [number, number];
}) {
  const span = domain ?? chartWindow(range);
  const moments = limitMoments(samples, metrics, range, span[1]);
  const rows = moments.map((sample) => {
    const shares = sharesFor(sample, metrics);
    const row: Record<string, number | string | null | Record<string, number | null>> = {
      ts: sample.ts,
      raw: sample.values,
    };
    for (const metric of metrics) row[metric.key] = shares[metric.key];
    return row;
  });
  const tallest = rows.reduce((max, row) => {
    const here = metrics.reduce((best, metric) => {
      const value = row[metric.key];
      return typeof value === "number" ? Math.max(best, value) : best;
    }, 0);
    return Math.max(max, here);
  }, 100);
  return (
    <div>
      <h3 className="wifi-heading">Share of each limit</h3>
      <p className="muted">{note}</p>
      {rows.length === 0 ? (
        <div className="muted">Nothing crossed a limit in this range.</div>
      ) : (
        <div className="chart-zoom" style={{ width: "100%", height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2} barSize={12}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <TimeAxis domain={span} />
              <YAxis
                domain={[0, Math.ceil(tallest * 1.08)]}
                tick={{ fontSize: 11 }}
                width={64}
              />
              <Tooltip
                labelFormatter={(value) => formatChartTick(value, span[1] - span[0])}
                formatter={(value, name, item) => {
                  const key = String(item?.dataKey ?? "");
                  const metric = metrics.find((entry) => entry.key === key);
                  const raw = (item?.payload?.raw ?? {}) as Record<string, number | null>;
                  const reading = metric && raw[key] != null ? formatRaw(Number(raw[key]), metric.unit) : "—";
                  const percent = typeof value === "number" ? `${Math.round(value)}% of limit` : "—";
                  return [`${reading} · ${percent}`, name];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine y={100} stroke="var(--degraded)" strokeDasharray="5 4" />
              {metrics.map((metric, index) => (
                <Bar
                  key={metric.key}
                  name={metric.label}
                  dataKey={metric.key}
                  fill={CHART_COLORS[index % CHART_COLORS.length]}
                  radius={[3, 3, 0, 0]}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
