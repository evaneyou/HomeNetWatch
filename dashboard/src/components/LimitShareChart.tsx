import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS } from "../chartTheme";
import { chartWindow, formatChartTick } from "../chartTime";

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

function peak(shares: Record<string, number | null>): number {
  const values = Object.values(shares).filter((value): value is number => value != null);
  return values.length === 0 ? 0 : Math.max(...values);
}

/** Worst moment in each time bucket, kept only when some metric crossed its limit. */
export function limitMoments(samples: LimitSample[], metrics: LimitMetric[], range: string): LimitSample[] {
  const usable = metrics.filter((metric) => metric.limit > 0);
  if (usable.length === 0) return [];
  const span = chartWindow(range);
  let size = BUCKET_MS[range] ?? 60 * 1000;
  let picked: LimitSample[] = [];
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const groups = new Map<number, LimitSample>();
    for (const sample of samples) {
      if (!Number.isFinite(sample.ts)) continue;
      const start = Math.floor(sample.ts / size) * size;
      const current = groups.get(start);
      if (!current || peak(sharesFor(sample, usable)) > peak(sharesFor(current, usable))) {
        groups.set(start, sample);
      }
    }
    picked = [...groups.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([, sample]) => sample)
      .filter((sample) => peak(sharesFor(sample, usable)) > 100);
    if (picked.length <= 16 || size * 2 > span[1] - span[0]) break;
    size *= 2;
  }
  return picked;
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
}: {
  note: string;
  samples: LimitSample[];
  metrics: LimitMetric[];
  range: string;
}) {
  const moments = limitMoments(samples, metrics, range);
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
  const span = chartWindow(range);
  const tilt = rows.length > 6;
  return (
    <div>
      <h3 className="wifi-heading">Share of each limit</h3>
      <p className="muted">{note}</p>
      {rows.length === 0 ? (
        <div className="muted">Nothing crossed a limit in this range.</div>
      ) : (
        <div className="chart-zoom" style={{ width: "100%", height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2} barCategoryGap="22%">
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis
                dataKey="ts"
                tickFormatter={(value) => formatChartTick(value, span[1] - span[0])}
                tick={{ fontSize: 10, fill: "var(--muted)" }}
                interval="preserveStartEnd"
                angle={tilt ? -28 : 0}
                textAnchor={tilt ? "end" : "middle"}
                height={tilt ? 48 : 30}
              />
              <YAxis
                domain={[0, Math.ceil(tallest * 1.08)]}
                tick={{ fontSize: 11 }}
                unit="%"
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
