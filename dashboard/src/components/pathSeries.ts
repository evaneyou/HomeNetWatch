import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api";
import { CHART_COLORS } from "../chartTheme";
import type { MetricsResponse, MonitorDetail } from "../types";
import type { LinkLive, LinkStatus } from "./ConnectionMini";
import { statusLabel } from "./StatusMark";

export const RANGES = ["1h", "6h", "12h", "24h", "7d", "30d"] as const;
export const RANGE_HOURS: Record<(typeof RANGES)[number], number> = {
  "1h": 1,
  "6h": 6,
  "12h": 12,
  "24h": 24,
  "7d": 24 * 7,
  "30d": 24 * 30,
};
export type ChartRange = (typeof RANGES)[number];
export const STRIP_RANGES = [
  { id: "1h", label: "1h", hours: 1 },
  { id: "6h", label: "6h", hours: 6 },
  { id: "12h", label: "12h", hours: 12 },
  { id: "24h", label: "24h", hours: 24 },
  { id: "7d", label: "7d", hours: 24 * 7 },
  { id: "30d", label: "30d", hours: 24 * 30 },
] as const;

export type StripRange = (typeof STRIP_RANGES)[number]["id"];
export const COLORS = CHART_COLORS;
export const ISP_SLUGS = ["http-path", "icmp-path", "mtr-path"] as const;

export const APPS = [
  { key: "youtube", label: "YouTube" },
  { key: "google", label: "Google" },
  { key: "microsoft", label: "Microsoft" },
  { key: "hulu", label: "Hulu" },
  { key: "zoom", label: "Zoom" },
  { key: "netflix", label: "Netflix" },
  { key: "teams", label: "Microsoft Teams" },
  { key: "facebook", label: "Facebook" },
] as const;

export type AppKey = (typeof APPS)[number]["key"];
export type DetailKey = "isp" | AppKey;
/** A built-in check, or one application probe by slug. */
export type CheckKey = "librespeed" | `rtnp:${string}`;

export function probeKinds(_key: AppKey): ("http" | "icmp")[] {
  return ["http", "icmp"];
}

export function isSaasProbe(slug: string): boolean {
  return APPS.some(
    (app) => slug === `http-${app.key}` || slug === `icmp-${app.key}`,
  );
}

export type Point = MetricsResponse["points"][number];
export type TestRow = MonitorDetail["tests"][number];

export function stripGradient(statuses: string[]): string {
  if (statuses.length === 0) return "var(--line)";
  const stops: string[] = [];
  let index = 0;
  while (index < statuses.length) {
    const status = statuses[index];
    let end = index + 1;
    while (end < statuses.length && statuses[end] === status) end += 1;
    const color = statusColor(status);
    const from = (index / statuses.length) * 100;
    const to = (end / statuses.length) * 100;
    stops.push(`${color} ${from}%`, `${color} ${to}%`);
    index = end;
  }
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

export function statusColor(status: string): string {
  if (status === "ok") return "var(--ok)";
  if (status === "degraded") return "var(--degraded)";
  if (status === "fail") return "var(--fail)";
  if (status === "empty") return "var(--line)";
  return "var(--unknown)";
}

export function ispStrip(series: Map<string, Point[]>, hours: number): string[] {
  const buckets = 96;
  const end = Date.now();
  const start = end - hours * 60 * 60 * 1000;
  const width = (end - start) / buckets;
  const tracks = [...series.values()].map((points) => trackStatuses(points, start, end, width, buckets));
  return Array.from({ length: buckets }, (_, index) => ragStatus(tracks.map((track) => track[index])));
}

export function trackStatuses(
  points: Point[],
  start: number,
  end: number,
  width: number,
  buckets: number,
): (string | null)[] {
  const slots: (string | null)[] = Array.from({ length: buckets }, () => null);
  for (const point of points) {
    const time = new Date(point.t).getTime();
    if (time < start || time > end) continue;
    const index = Math.min(buckets - 1, Math.floor((time - start) / width));
    slots[index] = point.status || "unknown";
  }
  const first = slots.findIndex((status) => status);
  if (first < 0) return slots;
  // A check that reports every N minutes cannot be "missing" for less than N minutes, so the time
  // before its first sample is only sampling phase. Cover it by one typical spacing and no more:
  // a real gap, such as an agent that was offline, is longer than that and stays empty.
  const reach = Math.min(first, Math.ceil(typicalSpacing(points, start, end) / width));
  let last = slots[first];
  return slots.map((status, index) => {
    if (index < first) return index >= first - reach ? last : null;
    if (status) last = status;
    return last;
  });
}

/** The median time between consecutive samples inside the window, or 0 when there are too few to say. */
export function typicalSpacing(points: Point[], start: number, end: number): number {
  const times = points
    .map((point) => new Date(point.t).getTime())
    .filter((time) => time >= start && time <= end)
    .sort((a, b) => a - b);
  if (times.length < 3) return 0;
  const gaps = times.slice(1).map((time, index) => time - times[index]).sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

export function ragStatus(statuses: (string | null | undefined)[]): string {
  const present = statuses.filter((status) => status != null);
  if (present.length === 0) return "empty";
  if (statuses.every((status) => status === "fail")) return "fail";
  if (present.every((status) => status === "ok")) return "ok";
  if (present.some((status) => status === "fail" || status === "degraded")) return "degraded";
  if (present.some((status) => status === "ok")) return "ok";
  return "unknown";
}

export function httpStages(point: Point) {
  return point.stages.filter((stage) => stage.name !== "ttfb");
}

export function httpTime(point: Point | undefined): number | null {
  if (!point) return null;
  const stages = httpStages(point);
  if (stages.length > 0) {
    return Math.round(stages.reduce((sum, stage) => sum + stage.duration_ms, 0) * 10) / 10;
  }
  return point.rtt_ms;
}

export function destLatency(point: Point | undefined): number | null {
  if (!point) return null;
  const lastHop = point.hops[point.hops.length - 1];
  return lastHop?.avg_ms ?? point.rtt_ms;
}

export function worstHopLoss(point: Point | undefined): number | null {
  if (!point) return null;
  if (point.hops.length === 0) return point.loss_pct;
  return Math.max(...point.hops.map((hop) => hop.loss_pct));
}

export function speed(point: Point | undefined): { down: number | null; up: number | null } {
  return {
    down: point?.throughput_mbps?.download ?? point?.down_mbps ?? null,
    up: point?.throughput_mbps?.upload ?? point?.up_mbps ?? null,
  };
}

export const FRESH_MS = 30 * 60 * 1000;

export function liveOf(series: Point[]): LinkLive {
  const latest = series.at(-1);
  const fresh = latest != null && Date.now() - new Date(latest.t).getTime() < FRESH_MS;
  const status: LinkStatus =
    fresh && (latest.status === "ok" || latest.status === "degraded" || latest.status === "fail") ? latest.status : "unknown";
  return { status, down: speed(latest).down, up: speed(latest).up, ping: latest?.rtt_ms ?? null };
}

export function currentRag(statuses: (string | null | undefined)[]): string {
  const status = ragStatus(statuses);
  return status === "empty" ? "unknown" : status;
}

export function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function isDetail(value: string | null): value is DetailKey {
  return value === "isp" || APPS.some((app) => app.key === value);
}

export function isCheck(value: string | null): value is CheckKey {
  return value === "librespeed" || Boolean(value?.startsWith("rtnp:"));
}

export function logProbeForDetail(detail: string | null, check: string | null = null): string | null {
  if (check?.startsWith("rtnp:")) return check;
  if (detail === "isp" && isCheck(check)) return `slug:${check}`;
  if (detail === "isp") return "internet";
  if (APPS.some((app) => app.key === detail)) return `svc:${detail}`;
  return null;
}

export function useSeries(tests: TestRow[], range: string) {
  const queries = useQueries({
    queries: tests.map((test) => ({
      queryKey: ["metrics", test.id, range],
      queryFn: () => api.metrics(test.id, range) as Promise<MetricsResponse>,
    })),
  });
  const points = useMemo(() => {
    const map = new Map<string, Point[]>();
    queries.forEach((query, index) => {
      const slug = tests[index]?.slug;
      if (slug && query.data) map.set(slug, query.data.points);
    });
    return map;
  }, [queries, tests]);
  return {
    points,
    loading: queries.some((query) => query.isLoading),
    failed: queries.some((query) => query.isError),
  };
}

export function useAppStrips(tests: TestRow[], stripRange: StripRange) {
  const bySlug = useMemo(() => new Map(tests.map((test) => [test.slug, test])), [tests]);
  const stripTests = useMemo(() => {
    const slugs: string[] = [];
    for (const app of APPS) {
      for (const kind of probeKinds(app.key)) slugs.push(`${kind}-${app.key}`);
    }
    return slugs.flatMap((slug) => {
      const test = bySlug.get(slug);
      if (!test || test.enabled === false) return [];
      return [test];
    });
  }, [bySlug]);
  const { points: stripPoints } = useSeries(stripTests, stripRange);
  const stripHours = STRIP_RANGES.find((item) => item.id === stripRange)?.hours ?? 1;
  const appStrips = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const app of APPS) {
      const series = new Map<string, Point[]>();
      for (const kind of probeKinds(app.key)) {
        const slug = `${kind}-${app.key}`;
        const test = bySlug.get(slug);
        if (!test || test.enabled === false) continue;
        series.set(slug, stripPoints.get(slug) ?? []);
      }
      map.set(app.key, ispStrip(series, stripHours));
    }
    return map;
  }, [bySlug, stripPoints, stripHours]);

  function probesFor(key: AppKey): TestRow[] {
    return probeKinds(key).flatMap((kind) => {
      const test = bySlug.get(`${kind}-${key}`);
      return test && test.enabled !== false ? [test] : [];
    });
  }

  function statusFor(key: AppKey): string {
    return currentRag(probesFor(key).map((test) => test.latest_status));
  }

  function detailFor(key: AppKey): string | null {
    const blocks = probesFor(key).map(probeEvent);
    return blocks.length ? blocks.join("\n\n") : null;
  }

  const stripLabel = STRIP_RANGES.find((item) => item.id === stripRange)?.label ?? "1 hour";
  return { appStrips, statusFor, detailFor, stripLabel };
}

export function probeEvent(test: TestRow): string {
  const name = test.kind === "http" ? "HTTP" : test.kind === "ping" ? "ICMP" : test.name;
  const when = test.latest_at ? new Date(test.latest_at).toLocaleString() : "No sample yet";
  const lines = [`${name} · ${when}`, test.latest_reason?.trim() || statusLabel(test.latest_status)];
  const metrics: string[] = [];
  if (test.latest_rtt_ms != null) metrics.push(`RTT ${fmt(test.latest_rtt_ms)} ms`);
  if (test.latest_loss_pct != null) metrics.push(`Loss ${fmt(test.latest_loss_pct)}%`);
  if (metrics.length) lines.push(metrics.join(" · "));
  return lines.join("\n");
}

export function statusStrip(samples: Point[], range: (typeof RANGES)[number]): string[] {
  const series = new Map<string, Point[]>();
  series.set("sample", samples);
  return ispStrip(series, RANGE_HOURS[range]);
}
