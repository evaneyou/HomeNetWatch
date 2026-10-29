import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import SelectMenu from "../components/SelectMenu";
import type { MonitorAdmin, User } from "../types";
import "../kpi.css";

type Period = "day" | "week" | "month" | "year";
type Kind = { samples: number; failed: number; availability: number | null; loss_avg: number | null; loss_max: number | null; rtt_ms: number | null; jitter_ms: number | null; down_mbps: number | null; up_mbps: number | null; incidents: number; outage_minutes: number; gap_minutes: number; gaps: number; mttr_minutes?: number | null };
type Point = {
  t: string;
  internet: number | null;
  internet_wired: number | null;
  internet_wireless: number | null;
  voice: number | null;
  voice_wired?: number | null;
  voice_wireless?: number | null;
  video: number | null;
  video_wired?: number | null;
  video_wireless?: number | null;
  web: number | null;
  web_wired?: number | null;
  web_wireless?: number | null;
  loss_internet: number | null;
  loss_voice: number | null;
  loss_video: number | null;
  rtt_ms: number | null;
  jitter_ms: number | null;
  down_mbps: number | null;
  up_mbps: number | null;
  outage_minutes: number;
  gap_minutes: number;
};
type Report = {
  period: Period;
  bucket: "hour" | "day" | "month";
  from: string;
  to: string;
  generated_at: string;
  data_since: string | null;
  targets: { internet: number; voice: number; video: number; web?: number; loss_pct: number };
  summary: Record<"internet" | "voice" | "video" | "web" | "apps" | "speed", Kind>;
  previous: Record<"internet" | "voice" | "video" | "web" | "apps" | "speed", Kind>;
  app_loss_avg: number | null;
  links: Record<"internet" | "voice" | "video" | "web", Record<string, Kind>>;
  diagnosis?: { cause: string; title: string; summary: string } | null;
  series: Point[];
  incidents: { start: string; link: string; minutes: number; checks: number }[];
  gaps: { start: string; minutes: number; links: "wired" | "wireless" | "both"; ongoing: boolean }[];
};

type RootCauseReport = {
  generated_at: string;
  model: string;
  started_at: string;
  root_cause: string;
  category: string;
  confidence: "high" | "medium" | "low" | string;
  summary: string;
  timeline: { time: string; event: string; impact: string }[];
  evidence: string[];
  recommendations: string[];
};

const KPI_RCA_DEMO: RootCauseReport = {
  generated_at: "",
  model: "demo",
  started_at: "Oct 16, 2026 at 08:00 ET",
  root_cause: "Upstream ISP upload bandwidth collapse across both Wired and Wi-Fi",
  category: "bandwidth",
  confidence: "high",
  summary:
    "LibreSpeed upload on both links fell from about 31 Mbps to under 1 Mbps while download stayed relatively high. Real-time voice and video over Wired and Wi-Fi then showed rising loss and jitter because upstream queues were starved. This is a shared WAN bandwidth problem, not a single-link Wi-Fi radio fault.",
  timeline: [
    {
      time: "2026-10-16 08:00 ET",
      event: "LibreSpeed upload fell to ~0.6 Mbps on Wired and Wi-Fi",
      impact: "Egress capacity collapsed for real-time UDP streams",
    },
    {
      time: "2026-10-16 08:05 ET",
      event: "Voice and video synthetic samples began failing on both links",
      impact: "Application availability dropped while light HTTP path checks still often passed",
    },
    {
      time: "2026-10-16 08:10 ET",
      event: "Telegram net-bandwidth alert for the WAN",
      impact: "Per-app alerts stayed suppressed under the bandwidth hierarchy",
    },
  ],
  evidence: [
    "Wired and Wi-Fi LibreSpeed upload ~0.6 Mbps vs ~31 Mbps baseline",
    "Both links show correlated voice/video loss after the bandwidth drop",
    "Public web checks did not show a full ISP outage",
  ],
  recommendations: [
    "Check the modem/ONT upstream channel power and reboot the gateway",
    "Confirm with the ISP whether upstream capacity or congestion is reported",
    "Compare a second LibreSpeed run after recovery before blaming individual apps",
  ],
};

function parseApiError(err: unknown): string {
  const text = err instanceof Error ? err.message : "Could not determine root cause.";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // plain text
  }
  return text;
}

const PERIODS: { key: Period; label: string; text: string }[] = [
  { key: "day", label: "Day", text: "the last 24 hours, by hour" },
  { key: "week", label: "Week", text: "the last 7 days, by day" },
  { key: "month", label: "Month", text: "the last 30 days, by day" },
  { key: "year", label: "Year", text: "the last 12 months, by month" },
];

const C = { good: "#4fd1b5", warn: "#ffc24d", bad: "#ff8a7a", blue: "#6aa8ff", violet: "#a78bfa", grid: "#232d49", axis: "#9aa6c4" };
/** Eastern Time, so the clock follows EST and EDT. */
const EASTERN = "America/New_York";
const LINKS = { wired: "Wired", wireless: "Wi-Fi" } as const;
/** An availability chart never starts lower than this, so the small differences stay readable. */
const AXIS_MIN = 95;

/** The real figure behind a plotted point, for the tooltip. */
function rawOf(item: unknown): number | null {
  const entry = item as { dataKey?: unknown; payload?: Record<string, number | null> } | undefined;
  const key = String(entry?.dataKey ?? "").replace("_plot", "");
  return entry?.payload?.[key] ?? null;
}

/** Two decimals, never rounded up to 100: 99.996 reads 99.99%. A whole 100 or 0 has no decimals. */
function pct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value >= 100) return "100%";
  if (value === 0) return "0%";
  const scale = 10 ** digits;
  return `${(Math.floor(value * scale + 1e-9) / scale).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(digits);
}

function minutes(value: number): string {
  if (value < 1) return "0 min";
  if (value < 90) return `${Math.round(value)} min`;
  const hours = value / 60;
  return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} days`;
}

type Tone = "good" | "warn" | "bad" | "none";

function availabilityTone(value: number | null, target: number): Tone {
  if (value == null) return "none";
  if (value >= target) return "good";
  return value >= target - 1 ? "warn" : "bad";
}

function lossTone(value: number | null, target: number): Tone {
  if (value == null) return "none";
  if (value <= target) return "good";
  return value <= target * 3 ? "warn" : "bad";
}

function easternParts(date: Date): { monthDay: string; hm: string; year: string } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN,
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  const hour = part("hour") === "24" ? "00" : part("hour");
  return { monthDay: `${part("month")} ${part("day")}`, hm: `${hour}:${part("minute")}`, year: part("year") };
}

function easternWhen(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: EASTERN,
  });
}

function stamp(iso: string, bucket: Report["bucket"], full = false): string {
  const date = new Date(iso);
  if (bucket === "hour") {
    const eastern = easternParts(date);
    return full ? `${eastern.monthDay} ${eastern.hm}` : eastern.hm;
  }
  if (bucket === "month") return date.toLocaleDateString(undefined, { month: "short", year: full ? "numeric" : undefined, timeZone: "UTC" });
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

function Delta({ now, before, better, unit = "pts" }: { now: number | null; before: number | null; better: "up" | "down"; unit?: string }) {
  if (now == null || before == null) return <span className="kpi-delta none">no earlier data</span>;
  const diff = now - before;
  if (Math.abs(diff) < 0.005) return <span className="kpi-delta none">unchanged vs previous</span>;
  const good = better === "up" ? diff > 0 : diff < 0;
  return (
    <span className={good ? "kpi-delta good" : "kpi-delta bad"}>
      {diff > 0 ? "▲" : "▼"} {Math.abs(diff).toFixed(2)} {unit} vs previous
    </span>
  );
}

function Card({ label, value, tone, target, children }: { label: string; value: string; tone: Tone; target?: string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <article
      className={`kpi-card tone-${tone}${open ? " is-open" : ""}`}
      tabIndex={0}
      onClick={() => {
        if (window.matchMedia("(hover: none)").matches) setOpen((value) => !value);
      }}
    >
      <span className="kpi-label">{label}</span>
      <div className="kpi-value">{value}</div>
      <div className="kpi-card-more">
        {target ? <span className="kpi-target">{target}</span> : null}
        <div className="kpi-card-sub">{children}</div>
      </div>
    </article>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="kpi-panel">
      <header>
        <h3>{title}</h3>
        {note ? <span>{note}</span> : null}
      </header>
      {children}
    </section>
  );
}

const tooltipStyle = { background: "#10172a", border: "1px solid #232d49", borderRadius: 10, fontSize: 12 };

type DailyRow = {
  day: string;
  internet: number | null;
  internet_wired: number | null;
  internet_wireless: number | null;
  voice: number | null;
  voice_wired?: number | null;
  voice_wireless?: number | null;
  video: number | null;
  video_wired?: number | null;
  video_wireless?: number | null;
  web: number | null;
  web_wired?: number | null;
  web_wireless?: number | null;
  loss: number | null;
  offline_minutes: number;
  gap_minutes: number;
  score: number | null;
  tone: Tone;
  score_parts: Record<string, number>;
  root_cause?: string | null;
  root_cause_title?: string | null;
  outages: number;
  rtt_ms: number | null;
};

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function shiftDay(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

function niceDay(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

const DAY_TONES = { good: "Healthy, 95 or more", warn: "Fair, 80 to 94", bad: "Poor, under 80", none: "No data" } as const;

/** A day's colour comes from its health score: internet 60%, voice 20%, video 10%, packet drop 10%. */
function dayTone(row: DailyRow | null): Tone {
  return row ? row.tone : "none";
}

function missed(row: DailyRow, targets: Report["targets"]): string[] {
  const out: string[] = [];
  if (availabilityTone(row.internet, targets.internet) !== "good" && row.internet != null) out.push("Internet");
  if (availabilityTone(row.voice, targets.voice) !== "good" && row.voice != null) out.push("Voice");
  if (availabilityTone(row.video, targets.video) !== "good" && row.video != null) out.push("Video");
  if (availabilityTone(row.web, targets.web ?? 99.9) !== "good" && row.web != null) out.push("Web");
  if (lossTone(row.loss, targets.loss_pct) !== "good" && row.loss != null) out.push("Packet drop");
  return out;
}

type Cell = { day: string; row: DailyRow | null; pad?: boolean };

/** One coloured square per day. Hover (or focus) a square for that day's numbers, click it to open the day. */
function DailyHistory({ selected, onPick, targets, since, monitorId }: { monitorId: string; selected: string | null; onPick: (day: string) => void; targets: Report["targets"]; since: string | null }) {
  // Start no earlier than the first day with data, so the grid is not mostly empty.
  const clamp = (day: string) => (since && day < since ? since : day);
  const [last, setLast] = useState(isoToday());
  const [first, setFirst] = useState(() => clamp(shiftDay(isoToday(), -89)));
  const [tip, setTip] = useState<{ cell: Cell; x: number; y: number; place: "above" | "below" } | null>(null);
  const valid = ISO_DAY.test(first) && ISO_DAY.test(last) && first <= last && first >= shiftDay(last, -400);
  const rows = useQuery({
    queryKey: ["kpi-daily", first, last, monitorId],
    queryFn: () => api.kpiDaily(first, last, monitorId || undefined) as Promise<DailyRow[]>,
    enabled: valid,
    refetchInterval: 60000,
    placeholderData: (previous) => previous,
  });
  const list = valid ? rows.data ?? [] : [];

  // Every day in the range, oldest first, so the calendar has no holes. Weeks run top to bottom, Monday first.
  const cells = useMemo(() => {
    if (!valid) return [] as Cell[];
    const byDay = new Map(list.map((row) => [row.day, row]));
    const days: Cell[] = [];
    for (let day = first; day <= last; day = shiftDay(day, 1)) days.push({ day, row: byDay.get(day) ?? null });
    const [year, month, date] = first.split("-").map(Number);
    const lead = (new Date(Date.UTC(year, month - 1, date)).getUTCDay() + 6) % 7;
    return [...Array.from({ length: lead }, (_unused, index) => ({ day: `pad-${index}`, row: null, pad: true }) as Cell), ...days];
  }, [list, first, last, valid]);
  const weeks = Math.ceil(cells.length / 7);
  const monthLabels = useMemo(() => {
    const labels: string[] = [];
    let previous = "";
    for (let week = 0; week < weeks; week += 1) {
      const firstReal = cells.slice(week * 7, week * 7 + 7).find((cell) => !cell.pad);
      const name = firstReal ? new Date(`${firstReal.day}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" }) : "";
      labels.push(name && name !== previous ? name : "");
      if (name) previous = name;
    }
    return labels;
  }, [cells, weeks]);

  function download() {
    const head = ["Date", "Internet %", "Wired %", "Wi-Fi %", "Voice %", "Video %", "Web %", "Packet drop %", "Offline min", "Outages", "Reporting gap min", "Health score", "Root cause"];
    const cell = (value: number | null | undefined, digits = 2) => (value == null ? "" : value.toFixed(digits));
    const lines = list.map((row) => [row.day, cell(row.internet), cell(row.internet_wired), cell(row.internet_wireless), cell(row.voice), cell(row.video), cell(row.web), cell(row.loss), cell(row.offline_minutes, 1), String(row.outages), cell(row.gap_minutes, 1), cell(row.score, 1), row.root_cause_title ?? ""].join(","));
    const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `network-kpi-${first}-to-${last}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function show(cell: Cell, element: HTMLElement) {
    const box = element.getBoundingClientRect();
    const frame = element.closest(".kpi-heat")?.getBoundingClientRect();
    if (!frame) return;
    const centre = box.left - frame.left + box.width / 2;
    // Tip is tall once diagnosis is included; open below when the viewport lacks room above.
    const tipHeight = 280;
    const spaceAbove = box.top;
    const spaceBelow = window.innerHeight - box.bottom;
    const place: "above" | "below" = spaceAbove < tipHeight && spaceBelow >= Math.min(tipHeight, spaceAbove + 40) ? "below" : "above";
    setTip({
      cell,
      x: Math.min(Math.max(centre, 130), Math.max(130, frame.width - 130)),
      y: place === "above" ? box.top - frame.top : box.bottom - frame.top,
      place,
    });
  }

  return (
    <section className="kpi-panel kpi-history">
      <header>
        <h3>Daily KPI history</h3>
        <span>One square per day, coloured by a health score: internet 60%, voice 20%, video 10%, packet drop 10%. Hover for the numbers, click to open the day.</span>
      </header>
      <div className="kpi-filter">
        <label>
          <span>From</span>
          <input type="date" value={first} max={last} onChange={(event) => setFirst(event.target.value)} />
        </label>
        <label>
          <span>To</span>
          <input type="date" value={last} min={first} max={isoToday()} onChange={(event) => setLast(event.target.value)} />
        </label>
        <div className="kpi-quick" role="group" aria-label="Quick ranges">
          {[
            ["30 days", 29],
            ["90 days", 89],
            ["1 year", 364],
          ].map(([label, days]) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                setLast(isoToday());
                setFirst(clamp(shiftDay(isoToday(), -Number(days))));
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" className="kpi-print" disabled={list.length === 0} onClick={download}>
          Download CSV
        </button>
      </div>
      {!valid ? <p className="kpi-clear muted">Choose a start date that is on or before the end date, within about a year.</p> : null}

      <div className="kpi-heat" onMouseLeave={() => setTip(null)}>
        <div className="kpi-heat-scroll">
          <div className="kpi-heat-body">
            <div className="kpi-heat-months" style={{ gridTemplateColumns: `repeat(${weeks}, var(--cell))` }} aria-hidden="true">
              {monthLabels.map((name, index) => (
                <span key={index}>{name}</span>
              ))}
            </div>
            <div className="kpi-heat-main">
              <div className="kpi-heat-days" aria-hidden="true">
                {["M", "T", "W", "T", "F", "S", "S"].map((letter, index) => (
                  <span key={index}>{letter}</span>
                ))}
              </div>
              <div className="kpi-heat-grid" role="list" aria-label="Daily results">
                {cells.map((cell) =>
                  cell.pad ? (
                    <i key={cell.day} className="kpi-cell pad" aria-hidden="true" />
                  ) : (
                    <button
                      key={cell.day}
                      type="button"
                      role="listitem"
                      className={`kpi-cell tone-${dayTone(cell.row)}${cell.day === selected ? " is-selected" : ""}`}
                      aria-label={`${niceDay(cell.day)}: ${cell.row && cell.row.score != null ? `health score ${Math.round(cell.row.score)}` : "no data"}`}
                      onMouseEnter={(event) => show(cell, event.currentTarget)}
                      onFocus={(event) => show(cell, event.currentTarget)}
                      onBlur={() => setTip(null)}
                      onClick={() => onPick(cell.day)}
                    />
                  ),
                )}
              </div>
            </div>
          </div>
        </div>
        {tip ? (
          <div className={`kpi-tip${tip.place === "below" ? " is-below" : ""}`} role="tooltip" style={{ left: tip.x, top: tip.y }}>
            <b>{niceDay(tip.cell.day)}</b>
            {tip.cell.row ? (
              <>
                <span className={`kpi-tip-status tone-${dayTone(tip.cell.row)}`}>
                  {tip.cell.row.score == null ? "No health score" : `Health score ${Math.round(tip.cell.row.score)} of 100`}
                  {missed(tip.cell.row, targets).length ? ` · below target: ${missed(tip.cell.row, targets).join(", ")}` : ""}
                </span>
                <dl>
                  <dt>Internet</dt>
                  <dd>
                    {pct(tip.cell.row.internet)} <small>wired {pct(tip.cell.row.internet_wired)} · Wi-Fi {pct(tip.cell.row.internet_wireless)}</small>
                  </dd>
                  <dt>Voice</dt>
                  <dd>
                    {pct(tip.cell.row.voice)}
                    {tip.cell.row.voice_wired != null || tip.cell.row.voice_wireless != null ? (
                      <>
                        {" "}
                        <small>wired {pct(tip.cell.row.voice_wired)} · Wi-Fi {pct(tip.cell.row.voice_wireless)}</small>
                      </>
                    ) : null}
                  </dd>
                  <dt>Video</dt>
                  <dd>
                    {pct(tip.cell.row.video)}
                    {tip.cell.row.video_wired != null || tip.cell.row.video_wireless != null ? (
                      <>
                        {" "}
                        <small>wired {pct(tip.cell.row.video_wired)} · Wi-Fi {pct(tip.cell.row.video_wireless)}</small>
                      </>
                    ) : null}
                  </dd>
                  <dt>Web</dt>
                  <dd>
                    {pct(tip.cell.row.web)}
                    {tip.cell.row.web_wired != null || tip.cell.row.web_wireless != null ? (
                      <>
                        {" "}
                        <small>wired {pct(tip.cell.row.web_wired)} · Wi-Fi {pct(tip.cell.row.web_wireless)}</small>
                      </>
                    ) : null}
                  </dd>
                  <dt>Packet drop</dt>
                  <dd>{pct(tip.cell.row.loss)}</dd>
                  {tip.cell.row.root_cause_title ? (
                    <>
                      <dt>Diagnosis</dt>
                      <dd className="kpi-tip-diagnosis">{tip.cell.row.root_cause_title}</dd>
                    </>
                  ) : null}
                  <dt>Offline</dt>
                  <dd>
                    {tip.cell.row.offline_minutes > 0 ? minutes(tip.cell.row.offline_minutes) : "none"}
                    {tip.cell.row.outages ? ` · ${tip.cell.row.outages} outage${tip.cell.row.outages === 1 ? "" : "s"}` : ""}
                  </dd>
                  <dt>Reporting gaps</dt>
                  <dd>{tip.cell.row.gap_minutes > 0 ? minutes(tip.cell.row.gap_minutes) : "none"}</dd>
                  <dt>Latency</dt>
                  <dd>{tip.cell.row.rtt_ms == null ? "—" : `${num(tip.cell.row.rtt_ms)} ms`}</dd>
                </dl>
              </>
            ) : (
              <span className="kpi-tip-status tone-none">No data for this day</span>
            )}
          </div>
        ) : null}
        <ul className="kpi-heat-legend" aria-label="Colour key">
          {(["good", "warn", "bad", "none"] as const).map((tone) => (
            <li key={tone}>
              <i className={`kpi-cell tone-${tone}`} aria-hidden="true" />
              {DAY_TONES[tone]}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export default function KpiPage() {
  const [params, setParams] = useSearchParams();
  const period = (PERIODS.find((item) => item.key === params.get("period"))?.key ?? "day") as Period;
  const picked = params.get("date") ?? "";
  const day = ISO_DAY.test(picked) && picked < isoToday() ? picked : null;
  const monitorsQ = useQuery({ queryKey: ["monitors"], queryFn: () => api.monitors() as Promise<MonitorAdmin[]> });
  const monitors = monitorsQ.data ?? [];
  // "" means every sensor together. A sensor id that no longer exists falls back to that.
  const picked_monitor = params.get("sensor") ?? "";
  const monitorId = monitors.some((item) => item.id === picked_monitor) ? picked_monitor : "";
  // The picker holds a choice until Display is pressed. It starts on the sensor being shown.
  const shown = monitorId || (monitors.length === 1 ? monitors[0].id : "");
  const [draft, setDraft] = useState<string | null>(null);
  const sensorValue = draft ?? shown;
  function showSensor() {
    const next: Record<string, string> = { period };
    if (day) next.date = day;
    if (sensorValue && monitors.length > 1) next.sensor = sensorValue;
    setParams(next, { replace: true });
    setDraft(null);
  }
  const query = useQuery({
    queryKey: ["kpi", period, day, monitorId],
    queryFn: () => api.kpi(period, day ?? undefined, monitorId || undefined) as Promise<Report>,
    refetchInterval: day ? false : 30000,
    placeholderData: (previous) => previous,
  });

  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const admin = me.data?.role === "admin";
  const [rcaOpen, setRcaOpen] = useState(false);
  const [demoRca, setDemoRca] = useState<RootCauseReport | null>(null);
  const rcaMutation = useMutation({
    mutationFn: () =>
      api.kpiRootCause({
        period,
        day: day ?? undefined,
        monitor_id: monitorId || undefined,
      }) as Promise<RootCauseReport>,
  });
  const rcaReport = admin ? rcaMutation.data : demoRca;
  const rcaPending = admin && rcaMutation.isPending;
  const rcaError = admin && rcaMutation.isError ? parseApiError(rcaMutation.error) : null;

  function runRootCause() {
    setRcaOpen(true);
    if (!admin) {
      setDemoRca({ ...KPI_RCA_DEMO, generated_at: new Date().toISOString() });
      return;
    }
    setDemoRca(null);
    rcaMutation.mutate();
  }

  function rerunRootCause() {
    if (!admin) {
      setDemoRca({ ...KPI_RCA_DEMO, generated_at: new Date().toISOString() });
      return;
    }
    rcaMutation.mutate();
  }

  function go(next: { period?: Period; date?: string | null }) {
    const chosen = next.date === undefined ? day : next.date;
    const query: Record<string, string> = { period: next.period ?? period };
    if (monitorId) query.sensor = monitorId;
    if (chosen && chosen < isoToday()) query.date = chosen;
    setParams(query, { replace: true });
  }

  if (query.isLoading) return <div className="state-box">Loading the KPI report…</div>;
  if (query.isError || !query.data) return <div className="state-box">Could not load the KPI report.</div>;

  const report = query.data;
  const { summary, previous, targets, series, bucket } = report;
  const plot = (value: number | null) => (value == null ? null : Math.max(value, AXIS_MIN));
  const data = series.map((point) => ({
    ...point,
    internet_wired_plot: plot(point.internet_wired),
    internet_wireless_plot: plot(point.internet_wireless),
    voice_plot: plot(point.voice),
    video_plot: plot(point.video),
    web_plot: plot(point.web),
    label: stamp(point.t, bucket),
    full: stamp(point.t, bucket, true),
  }));
  const under = (...values: (number | null)[]) => values.some((value) => value != null && value < AXIS_MIN);
  const internetClipped = series.some((point) => under(point.internet_wired, point.internet_wireless));
  const appClipped = series.some((point) => under(point.voice, point.video, point.web));
  const internet = summary.internet;
  const voice = summary.voice;
  const video = summary.video;
  const web = summary.web;
  const webTarget = targets.web ?? 99.9;
  const wired = report.links.internet.wired;
  const wifi = report.links.internet.wireless;
  const voiceWired = report.links.voice?.wired;
  const voiceWifi = report.links.voice?.wireless;
  const videoWired = report.links.video?.wired;
  const videoWifi = report.links.video?.wireless;
  const webWired = report.links.web?.wired;
  const webWifi = report.links.web?.wireless;
  const appLossPeak = Math.max(voice.loss_max ?? 0, video.loss_max ?? 0);
  const speed = summary.speed;

  const checks: { name: string; tone: Tone }[] = [
    { name: "Internet availability", tone: availabilityTone(internet.availability, targets.internet) },
    { name: "Voice availability", tone: availabilityTone(voice.availability, targets.voice) },
    { name: "Video availability", tone: availabilityTone(video.availability, targets.video) },
    { name: "Web availability", tone: availabilityTone(web?.availability ?? null, webTarget) },
    { name: "Packet drop", tone: lossTone(report.app_loss_avg, targets.loss_pct) },
  ];
  const measured = checks.filter((item) => item.tone !== "none");
  const missed = measured.filter((item) => item.tone !== "good");
  const verdict: Tone = measured.length === 0 ? "none" : missed.some((item) => item.tone === "bad") ? "bad" : missed.length > 0 ? "warn" : "good";
  const headline =
    verdict === "none"
      ? "Gathering the first measurements"
      : verdict === "good"
        ? report.diagnosis?.cause === "wifi" || report.diagnosis?.cause === "wired"
          ? `All service levels met · ${report.diagnosis.title}`
          : "All service levels are being met"
        : `${missed.length} of ${measured.length} service levels below target${report.diagnosis ? ` · ${report.diagnosis.title}` : ""}`;

  const minFloor = (values: (number | null)[], target: number) => {
    const real = values.filter((value): value is number => value != null);
    const low = real.length ? Math.min(...real, target) : target;
    return Math.max(AXIS_MIN, Math.floor(low - 0.3));
  };
  const availFloor = minFloor(data.flatMap((point) => [point.internet_wired_plot, point.internet_wireless_plot]), targets.internet);
  const appFloor = minFloor(data.flatMap((point) => [point.voice_plot, point.video_plot, point.web_plot]), Math.min(targets.voice, targets.video, webTarget));
  const axis = { stroke: C.axis, tick: { fontSize: 11 }, tickLine: false } as const;
  // `to` is the end of the last day, so step back a moment to name the last day itself.
  const lastMoment = new Date(new Date(report.to).getTime() - 1).toISOString();
  const fromEastern = easternParts(new Date(report.from));
  const toEastern = easternParts(new Date(lastMoment));
  const first = report.bucket === "hour" ? fromEastern.monthDay : stamp(report.from, "day", true);
  const last = report.bucket === "hour" ? toEastern.monthDay : stamp(lastMoment, "day", true);
  const rangeYear = report.bucket === "hour" ? toEastern.year : String(new Date(lastMoment).getUTCFullYear());
  const range = report.bucket === "month" || first === last ? first : `${first} – ${last}`;
  const periodText = day
    ? period === "day"
      ? "24 hours, by hour"
      : `${PERIODS.find((item) => item.key === period)?.text.replace("the last", "the")} ending ${stamp(day, "day", true)}`
    : (PERIODS.find((item) => item.key === period)?.text ?? "");
  const latest = isoToday();

  return (
    <div className="kpi">
      <header className="kpi-head">
        <div>
          <p className="kpi-eyebrow">Home network · Service report</p>
          <h1>Network health report</h1>
          <p className="kpi-range">
            {range}, {rangeYear} · {periodText} · times in ET
          </p>
        </div>
        {monitors.length > 0 ? (
          <div className="kpi-sensor">
            <SelectMenu className="kpi-select" aria-label="Remote sensor" value={sensorValue} onChange={(event) => setDraft(event.target.value)}>
              {monitors.length > 1 ? <option value="">All sensors</option> : null}
              {monitors.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </SelectMenu>
            <button type="button" className="kpi-display" disabled={sensorValue === shown} onClick={showSensor}>
              Display
            </button>
          </div>
        ) : null}
        <div className="kpi-tools">
          <div className="kpi-seg" role="group" aria-label="Reporting period">
            {PERIODS.map((item) => (
              <button key={item.key} type="button" className={item.key === period ? "active" : ""} aria-pressed={item.key === period} onClick={() => go({ period: item.key })}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="kpi-date" role="group" aria-label="Go to a day">
            <button type="button" aria-label="Previous day" onClick={() => go({ date: shiftDay(day ?? latest, -1) })}>
              ‹
            </button>
            <input
              type="date"
              aria-label="Report day"
              value={day ?? latest}
              max={latest}
              onChange={(event) => {
                const value = event.target.value;
                if (ISO_DAY.test(value)) go({ date: value >= latest ? null : value });
              }}
            />
            <button type="button" aria-label="Next day" disabled={!day} onClick={() => go({ date: shiftDay(day ?? latest, 1) })}>
              ›
            </button>
            {day ? (
              <button type="button" className="kpi-latest" onClick={() => go({ date: null })}>
                Today
              </button>
            ) : null}
          </div>
          <button
            type="button"
            className="kpi-rca-btn"
            disabled={rcaPending}
            onClick={runRootCause}
          >
            {rcaPending
              ? "Analyzing Event Log…"
              : admin
                ? "Determine Root Cause"
                : "Run root-cause demo"}
          </button>
        </div>
      </header>

      {rcaOpen ? (
        <section className="kpi-panel kpi-rca-panel" aria-live="polite">
          <header className="kpi-rca-head">
            <div>
              <span className="kpi-rca-eyebrow">
                {admin ? "Gemini AI · Event & Metric Log Analysis" : "Demo sample · Event & Metric Log Analysis"}
              </span>
              <h3>Root Cause Report</h3>
            </div>
            <div className="kpi-rca-actions">
              <button
                type="button"
                className="kpi-print"
                disabled={rcaPending}
                onClick={rerunRootCause}
              >
                {rcaPending ? "Running…" : admin ? "Re-run Analysis" : "Re-run demo"}
              </button>
              <button
                type="button"
                className="kpi-print"
                onClick={() => setRcaOpen(false)}
              >
                Close
              </button>
            </div>
          </header>
          {rcaPending ? (
            <p className="kpi-rca-loading">
              Examining bandwidth tests, path latency, application health transitions, and alert logs…
            </p>
          ) : rcaError ? (
            <div className="kpi-rca-error">
              <p>{rcaError}</p>
              {rcaError.toLowerCase().includes("gemini api key") ? (
                <p>
                  <Link to="/settings/gemini" className="kpi-rca-link">
                    Open Settings → Configure Gemini AI
                  </Link>{" "}
                  to save your API key.
                </p>
              ) : null}
            </div>
          ) : rcaReport ? (
            <div className="kpi-rca-body">
              {!admin ? (
                <p className="kpi-rca-summary muted">
                  Guests see this canned bandwidth-collapse example. Live Gemini analysis is admin-only.
                </p>
              ) : null}
              <div className="kpi-rca-meta">
                <div className="kpi-rca-stat">
                  <span>When it started</span>
                  <strong>{rcaReport.started_at}</strong>
                </div>
                <div className="kpi-rca-stat">
                  <span>Primary culprit</span>
                  <strong>{rcaReport.root_cause}</strong>
                </div>
                <div className="kpi-rca-stat">
                  <span>Confidence</span>
                  <strong className={`kpi-rca-conf conf-${rcaReport.confidence}`}>
                    {rcaReport.confidence.toUpperCase()}
                  </strong>
                </div>
              </div>
              <p className="kpi-rca-summary">{rcaReport.summary}</p>

              {rcaReport.timeline.length > 0 ? (
                <div className="kpi-rca-section">
                  <h4>Chronological Event Timeline</h4>
                  <ul className="kpi-rca-timeline">
                    {rcaReport.timeline.map((item, idx) => (
                      <li key={idx}>
                        <time>{item.time}</time>
                        <div>
                          <b>{item.event}</b>
                          {item.impact ? <span>{item.impact}</span> : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="kpi-rca-cols">
                {rcaReport.evidence.length > 0 ? (
                  <div className="kpi-rca-section">
                    <h4>Supporting Evidence</h4>
                    <ul className="kpi-rca-list">
                      {rcaReport.evidence.map((ev, idx) => (
                        <li key={idx}>{ev}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {rcaReport.recommendations.length > 0 ? (
                  <div className="kpi-rca-section">
                    <h4>Recommended Next Steps</h4>
                    <ul className="kpi-rca-list">
                      {rcaReport.recommendations.map((rec, idx) => (
                        <li key={idx}>{rec}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      <section className={`kpi-verdict tone-${verdict}`} role="status">
        <span className="kpi-verdict-mark" aria-hidden="true">
          {verdict === "good" ? "✓" : verdict === "none" ? "…" : "!"}
        </span>
        <div>
          <b>{headline}</b>
          <span>
            {measured.length === 0
              ? "The report fills in as measurements arrive."
              : report.diagnosis
                ? `${report.diagnosis.summary} Targets: internet ${targets.internet}%, voice, video and web ${targets.voice}%, packet drop ≤ ${targets.loss_pct}%.`
                : `Targets: internet ${targets.internet}%, voice, video and web ${targets.voice}%, packet drop at or under ${targets.loss_pct}%.`}
          </span>
        </div>
        <ul>
          {checks.map((item) => (
            <li key={item.name} className={`tone-${item.tone}`}>
              {item.name}
            </li>
          ))}
        </ul>
      </section>

      <section className="kpi-cards" aria-label="Key indicators">
        <Card
          label="Internet"
          value={pct(internet.availability)}
          tone={availabilityTone(internet.availability, targets.internet)}
          target={`Target ${targets.internet}%`}
        >
          <Delta now={internet.availability} before={previous.internet.availability} better="up" />
          <span className="kpi-split">
            Wired {pct(wired?.availability)} · Wi-Fi {pct(wifi?.availability)}
          </span>
        </Card>
        <Card
          label="Voice"
          value={pct(voice.availability)}
          tone={availabilityTone(voice.availability, targets.voice)}
          target={`Target ${targets.voice}%`}
        >
          <Delta now={voice.availability} before={previous.voice.availability} better="up" />
          <span className="kpi-split">
            {voiceWired || voiceWifi
              ? `Wired ${pct(voiceWired?.availability)} · Wi-Fi ${pct(voiceWifi?.availability)}`
              : `${voice.samples.toLocaleString()} quality checks`}
          </span>
        </Card>
        <Card
          label="Video"
          value={pct(video.availability)}
          tone={availabilityTone(video.availability, targets.video)}
          target={`Target ${targets.video}%`}
        >
          <Delta now={video.availability} before={previous.video.availability} better="up" />
          <span className="kpi-split">
            {videoWired || videoWifi
              ? `Wired ${pct(videoWired?.availability)} · Wi-Fi ${pct(videoWifi?.availability)}`
              : `${video.samples.toLocaleString()} quality checks`}
          </span>
        </Card>
        <Card
          label="Web"
          value={pct(web?.availability)}
          tone={availabilityTone(web?.availability ?? null, webTarget)}
          target={`Target ${webTarget}%`}
        >
          <Delta now={web?.availability ?? null} before={previous.web?.availability ?? null} better="up" />
          <span className="kpi-split">
            {webWired || webWifi
              ? `Wired ${pct(webWired?.availability)} · Wi-Fi ${pct(webWifi?.availability)}`
              : `${(web?.samples ?? 0).toLocaleString()} web checks`}
          </span>
        </Card>
        <Card
          label="Packet drop"
          value={pct(report.app_loss_avg)}
          tone={lossTone(report.app_loss_avg, targets.loss_pct)}
          target={`Target ≤ ${targets.loss_pct}%`}
        >
          <span className="kpi-split">
            Voice {pct(voice.loss_avg)} · Video {pct(video.loss_avg)}
          </span>
          <span className="kpi-split">Worst interval {appLossPeak ? pct(appLossPeak, 1) : "—"}</span>
        </Card>
        <Card label="Time offline" value={internet.samples ? minutes(internet.outage_minutes) : "—"} tone={internet.samples === 0 ? "none" : internet.incidents === 0 ? "good" : "warn"}>
          <span className="kpi-split">
            {internet.samples ? `${internet.incidents} outage${internet.incidents === 1 ? "" : "s"}` : "No data yet"}
            {internet.mttr_minutes ? ` · about ${minutes(internet.mttr_minutes)} each` : ""}
          </span>
          <span className="kpi-split">Counted from failed internet checks</span>
        </Card>
        <Card label="Reporting gaps" value={internet.samples ? (internet.gap_minutes > 0 ? minutes(internet.gap_minutes) : "None") : "—"} tone={internet.samples === 0 ? "none" : internet.gaps === 0 ? "good" : "warn"}>
          <span className="kpi-split">
            {internet.samples ? (internet.gaps === 0 ? "Monitoring ran without a break" : `${internet.gaps} stretch${internet.gaps === 1 ? "" : "es"} with no data`) : "No data yet"}
          </span>
          <span className="kpi-split">The Pi or the server was down. Not counted as internet downtime.</span>
        </Card>
        <Card label="Latency" value={internet.rtt_ms == null ? "—" : `${num(internet.rtt_ms)} ms`} tone="none">
          <span className="kpi-split">To the server</span>
          <span className="kpi-split">Jitter {internet.jitter_ms == null ? "—" : `${num(internet.jitter_ms)} ms`}</span>
        </Card>
        <Card label="Speed" value={speed.down_mbps == null && speed.up_mbps == null ? "—" : `${speed.down_mbps == null ? "—" : num(speed.down_mbps, 0)} / ${speed.up_mbps == null ? "—" : num(speed.up_mbps, 0)}`} tone="none">
          <span className="kpi-split">Download / upload, Mbit/s</span>
        </Card>
      </section>

      <div className="kpi-grid">
        <Panel title="Internet availability" note={`Dashed line is the ${targets.internet}% target${internetClipped ? `. Anything under ${AXIS_MIN}% sits on the bottom edge.` : ""}`}>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis {...axis} domain={[availFloor, 100]} width={58} tickFormatter={(value: number) => `${value}%`} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(_value, name, item) => [pct(rawOf(item)), name]} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine y={targets.internet} stroke={C.warn} strokeDasharray="5 4" />
              <Line name="Wired" type="monotone" dataKey="internet_wired_plot" stroke={C.good} strokeWidth={2.2} dot={false} isAnimationActive={false} />
              <Line name="Wi-Fi" type="monotone" dataKey="internet_wireless_plot" stroke={C.blue} strokeWidth={2.2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Voice, video and web availability" note={`Dashed line is the ${targets.voice}% target${appClipped ? `. Anything under ${AXIS_MIN}% sits on the bottom edge.` : ""}`}>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis {...axis} domain={[appFloor, 100]} width={58} tickFormatter={(value: number) => `${value}%`} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(_value, name, item) => [pct(rawOf(item)), name]} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine y={targets.voice} stroke={C.warn} strokeDasharray="5 4" />
              <Line name="Voice" type="monotone" dataKey="voice_plot" stroke={C.blue} strokeWidth={2.2} dot={false} isAnimationActive={false} />
              <Line name="Video" type="monotone" dataKey="video_plot" stroke={C.violet} strokeWidth={2.2} dot={false} isAnimationActive={false} />
              <Line name="Web" type="monotone" dataKey="web_plot" stroke={C.good} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Packet drop" note={`Average loss; ${targets.loss_pct}% is the limit`}>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis {...axis} domain={[0, "auto"]} width={58} tickFormatter={(value: number) => `${value}%`} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(value) => [pct(typeof value === "number" ? value : null), ""]} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine y={targets.loss_pct} stroke={C.warn} strokeDasharray="5 4" />
              <Area name="Voice" type="monotone" dataKey="loss_voice" stroke={C.blue} fill={C.blue} fillOpacity={0.15} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Area name="Video" type="monotone" dataKey="loss_video" stroke={C.violet} fill={C.violet} fillOpacity={0.15} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Area name="Internet path" type="monotone" dataKey="loss_internet" stroke={C.good} fill={C.good} fillOpacity={0.1} strokeWidth={2} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Time offline" note="Minutes without internet">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis {...axis} allowDecimals={false} width={44} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(255,138,122,0.1)" }} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(value) => [`${num(typeof value === "number" ? value : null)} min`, "Offline"]} />
              <Bar dataKey="outage_minutes" fill={C.bad} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Latency and jitter" note="Round trip to the server, in milliseconds">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis yAxisId="rtt" {...axis} width={44} axisLine={false} />
              <YAxis yAxisId="jit" orientation="right" {...axis} width={36} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(value) => [`${num(typeof value === "number" ? value : null)} ms`, ""]} />
              <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
              <Line yAxisId="rtt" name="Latency" type="monotone" dataKey="rtt_ms" stroke={C.good} strokeWidth={2.2} dot={false} isAnimationActive={false} />
              <Line yAxisId="jit" name="Jitter" type="monotone" dataKey="jitter_ms" stroke={C.warn} strokeWidth={2} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Internet speed" note="Average from the bandwidth test, Mbit/s">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="label" {...axis} minTickGap={20} />
              <YAxis {...axis} width={44} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(79,209,181,0.08)" }} labelFormatter={(_label, rows) => String(rows?.[0]?.payload?.full ?? "")} formatter={(value) => [`${num(typeof value === "number" ? value : null, 0)} Mbit/s`, ""]} />
              <Legend iconType="square" wrapperStyle={{ fontSize: 12 }} />
              <Bar name="Download" dataKey="down_mbps" fill={C.good} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              <Bar name="Upload" dataKey="up_mbps" fill={C.blue} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      <div className="kpi-lower">
        <Panel title="Wired and Wi-Fi compared">
          <div className="kpi-table-wrap">
            <table className="kpi-table">
              <thead>
                <tr>
                  <th>Link</th>
                  <th>Internet</th>
                  <th>Web</th>
                  <th>Voice</th>
                  <th>Voice drop</th>
                  <th>Video</th>
                  <th>Video drop</th>
                </tr>
              </thead>
              <tbody>
                {(["wired", "wireless"] as const).map((link) => (
                  <tr key={link}>
                    <th scope="row">{LINKS[link]}</th>
                    <td className={`tone-${availabilityTone(report.links.internet[link]?.availability ?? null, targets.internet)}`}>{pct(report.links.internet[link]?.availability)}</td>
                    <td className={`tone-${availabilityTone(report.links.web?.[link]?.availability ?? null, webTarget)}`}>{pct(report.links.web?.[link]?.availability)}</td>
                    <td className={`tone-${availabilityTone(report.links.voice[link]?.availability ?? null, targets.voice)}`}>{pct(report.links.voice[link]?.availability)}</td>
                    <td className={`tone-${lossTone(report.links.voice[link]?.loss_avg ?? null, targets.loss_pct)}`}>{pct(report.links.voice[link]?.loss_avg)}</td>
                    <td className={`tone-${availabilityTone(report.links.video[link]?.availability ?? null, targets.video)}`}>{pct(report.links.video[link]?.availability)}</td>
                    <td className={`tone-${lossTone(report.links.video[link]?.loss_avg ?? null, targets.loss_pct)}`}>{pct(report.links.video[link]?.loss_avg)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Outages and gaps" note={report.period === "year" ? "Most recent, from detailed records" : "Most recent first"}>
          <h4 className="kpi-subhead">Internet outages</h4>
          {report.incidents.length === 0 ? (
            <p className="kpi-clear">No internet outages in this period.</p>
          ) : (
            <ul className="kpi-incidents">
              {report.incidents.map((item) => (
                <li key={`${item.start}-${item.link}`}>
                  <i aria-hidden="true" />
                  <span>
                    <b>{LINKS[item.link as keyof typeof LINKS] ?? item.link}</b> offline for {minutes(item.minutes)}
                  </span>
                  <time>{easternWhen(item.start)}</time>
                </li>
              ))}
            </ul>
          )}
          <h4 className="kpi-subhead">Reporting gaps</h4>
          {report.gaps.length === 0 ? (
            <p className="kpi-clear">The monitoring ran without a break.</p>
          ) : (
            <ul className="kpi-incidents kpi-gaps">
              {report.gaps.map((item) => (
                <li key={`${item.start}-${item.links}`}>
                  <i aria-hidden="true" />
                  <span>
                    {"No results for "}
                    <b>{minutes(item.minutes)}</b>
                    {item.ongoing ? " so far" : ""}
                    {item.links === "both" ? "" : ` on ${LINKS[item.links]}`}
                  </span>
                  <time>{easternWhen(item.start)}</time>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <DailyHistory
        monitorId={monitorId}
        targets={targets}
        since={report.data_since ? report.data_since.slice(0, 10) : null}
        selected={day}
        onPick={(picked) => {
          go({ period: "day", date: picked });
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />

      <footer className="kpi-foot">
        <p>
          <b>How these are measured.</b> Internet availability is the share of one-minute checks to the HomeNetWatch server, on each link, that
          succeeded. Voice and video availability is the share of their quality checks (every few seconds) that stayed within their limits. Packet
          drop is the average loss those checks reported. Time offline is counted from failed internet checks. A reporting gap is time with no results at all, because the Pi or the server was down; it is shown
          separately and is not counted as internet downtime. A period with no measurements shows a gap, never 100%. Each day in the history gets a health score from 0 to 100 (internet 60%, voice 20%, video 10%, packet drop 10%): green is 95 or more, amber 80 to 94, red below 80. Figures are cut off, not rounded up, at two decimals. Each day is stored as a daily record, and weeks, months and years are added up from those records.
        </p>
        <p>{report.data_since ? `Measurements since ${new Date(report.data_since).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}. ` : ""}Home network, report generated {new Date(report.generated_at).toLocaleString("en-US", { timeZone: EASTERN })} ET.</p>
      </footer>
    </div>
  );
}
