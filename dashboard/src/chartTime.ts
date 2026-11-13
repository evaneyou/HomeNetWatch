const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Tick spacings a time axis may use, smallest first. */
const STEPS = [
  MINUTE,
  2 * MINUTE,
  5 * MINUTE,
  10 * MINUTE,
  15 * MINUTE,
  30 * MINUTE,
  HOUR,
  2 * HOUR,
  3 * HOUR,
  6 * HOUR,
  12 * HOUR,
  DAY,
  2 * DAY,
  7 * DAY,
  14 * DAY,
  30 * DAY,
];

/** At most this many ticks on an axis, so the labels never crowd each other. */
const MAX_TICKS = 8;

/** The smallest tick spacing that keeps a window of `spanMs` to MAX_TICKS ticks or fewer. */
export function tickStep(spanMs: number): number {
  if (!Number.isFinite(spanMs) || spanMs <= 0) return HOUR;
  return STEPS.find((step) => spanMs / step <= MAX_TICKS) ?? STEPS[STEPS.length - 1];
}

/**
 * Evenly spaced ticks inside the window, on whole local minutes, hours or days: 2:00, 2:30, 3:00, or 6 AM, noon,
 * 6 PM, or one per midnight. Every chart passes these to its time axis, so the axis never picks its own ticks
 * at a spacing finer than its labels can tell apart (which used to print each hour twice on a 6-hour chart).
 */
export function chartTicks(domain: [number, number]): number[] | undefined {
  const [from, to] = domain;
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return undefined;
  const step = tickStep(to - from);
  const ticks: number[] = [];
  if (step < HOUR) {
    // Minute steps divide an hour, and local time is offset from UTC by whole quarter hours at worst.
    for (let time = Math.ceil(from / step) * step; time <= to; time += step) ticks.push(time);
    return ticks.length > 0 ? ticks : undefined;
  }
  // Hour and day steps start from local midnight and are counted on the local calendar, so they stay on
  // 6 AM, noon and so on through a daylight-saving change.
  const cursor = new Date(from);
  cursor.setHours(0, 0, 0, 0);
  if (step < DAY) {
    const hours = step / HOUR;
    while (cursor.getTime() < from) cursor.setHours(cursor.getHours() + hours);
    for (; cursor.getTime() <= to; cursor.setHours(cursor.getHours() + hours)) ticks.push(cursor.getTime());
  } else {
    const days = step / DAY;
    while (cursor.getTime() < from) cursor.setDate(cursor.getDate() + 1);
    for (; cursor.getTime() <= to; cursor.setDate(cursor.getDate() + days)) ticks.push(cursor.getTime());
  }
  return ticks.length > 0 ? ticks : undefined;
}

function toDate(value: unknown): Date | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Axis label for a tick on a window of `spanMs`. The label carries exactly what the tick spacing needs: the
 * time with minutes for minute spacing, the date and hour for hour spacing, the date alone for day spacing.
 */
export function formatChartTick(value: unknown, spanMs = Number.POSITIVE_INFINITY): string {
  const date = toDate(value);
  if (!date) return value == null ? "" : String(value);
  const step = tickStep(spanMs);
  if (step < HOUR) return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (step < DAY) return date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "numeric" });
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The exact moment of one sample, for a tooltip: date and time to the minute. */
export function formatChartTime(value: unknown): string {
  const date = toDate(value);
  if (!date) return value == null ? "" : String(value);
  return date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** Rows with an ISO time in `t` get the same moment as epoch milliseconds in `ts`, for a numeric time axis. */
export function withTs<T extends { t: string }>(rows: T[]): (T & { ts: number })[] {
  return rows.map((row) => ({ ...row, ts: new Date(row.t).getTime() }));
}

/** First and last `ts` of the rows, the window a chart without a fixed range shows. */
export function dataWindow(rows: { ts: number }[]): [number, number] {
  if (rows.length === 0) return [0, 0];
  return [rows[0].ts, rows[rows.length - 1].ts];
}

const RANGE_HOURS: Record<string, number> = {
  "1h": 1,
  "3h": 3,
  "6h": 6,
  "8h": 8,
  "12h": 12,
  "24h": 24,
  "7d": 24 * 7,
  "30d": 24 * 30,
};

/** Start and end of the selected chart window, in epoch milliseconds. */
export function chartWindow(range: string, now = Date.now()): [number, number] {
  const hours = RANGE_HOURS[range] ?? 1;
  return [now - hours * 60 * 60 * 1000, now];
}

/** Keep the full window until the user zooms into a subset of samples. */
export function zoomedDomain(
  full: [number, number],
  dataLength: number,
  visible: { ts?: unknown }[],
): [number, number] {
  if (visible.length > 1 && visible.length < dataLength) {
    const start = Number(visible[0]?.ts);
    const end = Number(visible[visible.length - 1]?.ts);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) return [start, end];
  }
  return full;
}
