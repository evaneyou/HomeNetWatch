import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { isAnonymized, stableId } from "../../anonymized";
import type { RfChannelSnapshot } from "../../types";
import { type AirContext } from "./shared";

const COLORS = ["#5eead4", "#7cb7ff", "#b79cff", "#ff8f85", "#f3dcb0", "#ffc457"];
const PLOT_LEFT = 62;
const PLOT_RIGHT = 880;

export default function AirwavesChannels() {
  const ctx = useOutletContext<AirContext>();
  const [params] = useSearchParams();
  const band = params.get("band") === "5" ? "5" : "2.4";
  const snapshotQ = useQuery({
    queryKey: ["rf-channels", ctx.agent.id, band],
    queryFn: () => api.rfChannels(ctx.agent.id, band),
    refetchInterval: ctx.refresh ? 15_000 : false,
  });

  if (!ctx.overviewLoading && !ctx.overview?.last_scan_at) {
    return <Empty title="No scans yet" body={`${ctx.agent.name} has not uploaded a scan.`} />;
  }
  if (snapshotQ.isLoading) return <Empty title="Channel graph" body="Loading the latest scan…" />;
  if (snapshotQ.isError) return <Empty title="Channel graph" body="Could not load the channel graph." />;
  if (!snapshotQ.data || (snapshotQ.data.series.length === 0 && snapshotQ.data.channels.length === 0)) {
    return <Empty title={`${band} GHz`} body={`No ${band} GHz networks on the latest scan.`} />;
  }
  return <ChannelView snapshot={snapshotQ.data} ctx={ctx} />;
}

function ChannelView({ snapshot, ctx }: { snapshot: RfChannelSnapshot; ctx: AirContext }) {
  const [hover, setHover] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const shown = snapshot.series.slice(0, 12);
  const advice = ctx.overview?.bands.find((item) => item.band === snapshot.band);
  const recommended = advice?.recommended_channel ?? snapshot.recommended_channel;
  const reason = advice?.reason ?? snapshot.reason;
  // "Your 2.4 GHz band is crowded. Channel 1 would be quieter than channel 6." restates the title and the
  // Busiest channel tile below it, so the line is only shown when it says something else.
  const extra = /crowded/i.test(reason)
    ? ""
    : /is clear on/i.test(reason)
      ? "No other network overlaps it."
      : /quietest/i.test(reason)
        ? "The quietest choice here, though nearby networks still reach it."
        : reason;
  const strongest = shown[0];
  const busiest = advice?.busiest_channel;
  const onBusiest = busiest == null ? 0 : snapshot.series.filter((series) => series.channel === busiest).length;
  const axis = axisFor(
    snapshot.band,
    [...shown.map((series) => series.channel), ...snapshot.channels.map((row) => row.channel)],
    recommended,
  );
  const xOf = (channel: number) => {
    if (snapshot.band === "2.4") return PLOT_LEFT + (channel + 1) * 51.125;
    const span = Math.max(axis.hi - axis.lo, 1);
    return PLOT_LEFT + ((channel - axis.lo) / span) * (PLOT_RIGHT - PLOT_LEFT);
  };
  const shadeLeft = Math.max(PLOT_LEFT, xOf(recommended - 2));
  const shadeRight = Math.min(PLOT_RIGHT, xOf(recommended + 2));
  const hills = shown.map((series, index) => ({
    ...series,
    color: COLORS[index % COLORS.length],
    d: hillPath(series.channel, series.channel_width, series.rssi_dbm, xOf),
  }));

  return (
    <div className="aw-channels">
      <section className="aw-card air-rec aw-rec">
        <div className="air-rec-copy">
          <div className="air-rec-icon" aria-hidden="true">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#0d1220" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12.55a11 11 0 0 1 14.08 0" />
              <path d="M1.42 9a16 16 0 0 1 21.16 0" />
              <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
              <path d="M12 20h.01" />
            </svg>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="air-kicker">Recommended for {snapshot.band} GHz</div>
            <h2>Try channel {recommended}</h2>
            {extra ? <p className="air-reason">{extra}</p> : null}
          </div>
        </div>
        <div className="npv-stats">
          <Stat label="Networks nearby" value={String(advice?.ap_count ?? snapshot.series.length)} note={`on ${snapshot.band} GHz`} />
          <Stat label="Busiest channel" value={busiest == null ? "—" : `Ch ${busiest}`} note={busiest == null ? "No neighbors" : `${onBusiest} networks`} />
          <Stat label="Strongest signal" value={strongest ? dbmText(strongest.rssi_dbm) : "—"} note={strongest ? strongest.ssid : "No networks"} />
        </div>
      </section>

      <section className="aw-card aw-graph">
        <div className="npv-card-head">
          <h2>Channel graph</h2>
          <p className="npv-hint">
            {snapshot.series.length > shown.length ? "Showing the 12 strongest networks · " : ""}
            hover a network below to spotlight it
          </p>
        </div>
        <div className="air-plot">
          <svg viewBox="0 0 900 390" role="img" aria-label={`Signal strength by channel for the ${shown.length} strongest ${snapshot.band} GHz networks. A recommended zone is shaded around channel ${recommended}.`}>
            <rect x={shadeLeft} y={20} width={Math.max(shadeRight - shadeLeft, 8)} height={290} fill="rgba(94,234,212,0.12)" />
            {[20, 92.5, 165, 237.5].map((y) => (
              <line key={y} x1={PLOT_LEFT} y1={y} x2={PLOT_RIGHT} y2={y} stroke="rgba(255,255,255,0.08)" />
            ))}
            <line x1={PLOT_LEFT} y1={310} x2={PLOT_RIGHT} y2={310} stroke="rgba(255,255,255,0.18)" />
            <AxisLabel x={54} y={25} text="−20" />
            <AxisLabel x={54} y={97} text="−40" />
            <AxisLabel x={54} y={170} text="−60" />
            <AxisLabel x={54} y={242} text="−80" />
            <AxisLabel x={54} y={315} text="−100" />
            <text x={54} y={9} fill="#9aa6c0" fontSize={12} fontWeight={800} textAnchor="end">
              dBm
            </text>
            <rect x={shadeLeft + 12} y={30} width={Math.max(shadeRight - shadeLeft - 24, 120)} height={28} rx={14} fill="rgba(94,234,212,0.22)" />
            <text x={(shadeLeft + shadeRight) / 2} y={49} fill="#5eead4" fontSize={14} fontWeight={800} textAnchor="middle">
              Quietest zone
            </text>
            {axis.labels.map((channel) => {
              const load = channelLoad(snapshot, channel);
              return (
                <g key={channel}>
                  <text x={xOf(channel)} y={336} fill="#c5cee3" fontSize={14} fontWeight={800} textAnchor="middle">
                    {channel}
                  </text>
                  {load ? (
                    <text x={xOf(channel)} y={354} fill="#9aa6c0" fontSize={11} fontWeight={700} textAnchor="middle">
                      {load.short}
                    </text>
                  ) : null}
                </g>
              );
            })}
            <text x={471} y={376} fill="#9aa6c0" fontSize={12} fontWeight={800} textAnchor="middle">
              Wi-Fi channel
            </text>
            <clipPath id="air-plotclip">
              <rect x={PLOT_LEFT} y={14} width={PLOT_RIGHT - PLOT_LEFT} height={297} />
            </clipPath>
            {hills.map((hill, index) => {
              const on = hover === index;
              const dim = hover !== null && !on;
              return (
                <path
                  key={stableId(hill, hill.bssid)}
                  d={hill.d}
                  clipPath="url(#air-plotclip)"
                  fill={hill.color}
                  fillOpacity={on ? 0.55 : dim ? 0.05 : 0.2}
                  stroke={hill.color}
                  strokeOpacity={dim ? 0.25 : 1}
                  strokeWidth={on ? 3 : hill.trusted ? 3 : 2}
                  strokeLinejoin="round"
                />
              );
            })}
          </svg>
        </div>
      </section>

      <section className="aw-card aw-nets-card">
        <h2>Networks in range</h2>
        <div className="air-nets">
          {(showAll ? hills : hills.slice(0, 4)).map((hill, index) => {
            const on = hover === index;
            const dim = hover !== null && !on;
            return (
              <div
                key={stableId(hill, `${hill.bssid}-${index}`)}
                className={on ? "air-net on" : dim ? "air-net dim" : "air-net"}
                style={{ ["--net" as string]: hill.color }}
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              >
                <div className="air-net-row">
                  <span style={{ width: 14, height: 14, borderRadius: "50%", background: hill.color, flex: "none" }} />
                  <span className={isAnonymized(hill.ssid) ? "air-net-name anon-mark" : "air-net-name"}>{hill.ssid}</span>
                  <span className="air-net-dbm">{dbmText(hill.rssi_dbm)}</span>
                </div>
                <div className="air-meter">
                  <span style={{ width: meter(hill.rssi_dbm), background: hill.color }} />
                </div>
                <div className="air-net-meta">
                  Channel {hill.channel} · {hill.channel_width} MHz
                  {busyText(snapshot, hill.channel)}
                  {hill.trusted ? " · Trusted" : ""}
                </div>
              </div>
            );
          })}
        </div>
        {hills.length > 4 ? (
          <button className="air-text-btn aw-see-all" type="button" onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show fewer" : `See all ${hills.length}`}
          </button>
        ) : null}
      </section>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="npv-stat">
      <div className="k">{label}</div>
      <div className="v">{value}</div>
      <div className="s">{note}</div>
    </div>
  );
}

function AxisLabel({ x, y, text }: { x: number; y: number; text: string }) {
  return (
    <text x={x} y={y} fill="#9aa6c0" fontSize={12} fontWeight={700} textAnchor="end">
      {text}
    </text>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <section className="aw-card air-empty">
      <div className="air-mark">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#5eead4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 12c2-5 4-5 6 0s4 5 6 0 4-5 6 0" />
        </svg>
      </div>
      <h2>{title}</h2>
      <p>{body}</p>
    </section>
  );
}

function channelLoad(snapshot: RfChannelSnapshot, channel: number): { text: string; short: string } | null {
  const row = snapshot.channels.find((item) => item.channel === channel);
  if (!row) return null;
  if (row.airtime_pct != null) {
    const pct = Math.round(row.airtime_pct);
    return { text: `${pct}% airtime`, short: `${pct}% airtime` };
  }
  if (row.busy_pct != null) {
    const pct = Math.round(row.busy_pct);
    return { text: `${pct}% busy`, short: `${pct}% busy` };
  }
  return null;
}

function busyText(snapshot: RfChannelSnapshot, channel: number): string {
  const load = channelLoad(snapshot, channel);
  return load ? ` · ${load.text}` : "";
}

function dbmText(rssi: number): string {
  return `−${Math.abs(Math.round(rssi))} dBm`;
}

function meter(rssi: number): string {
  return `${Math.max(0, Math.min(100, Math.round(((rssi + 100) / 80) * 100)))}%`;
}

function hillPath(channel: number, mhz: number, dbm: number, xOf: (channel: number) => number): string {
  const half = Math.max(mhz, 20) / 10;
  const yOf = (level: number) => 20 + ((-20 - level) / 80) * 290;
  let path = `M${xOf(channel - half).toFixed(1)} ${yOf(-100).toFixed(1)}`;
  const steps = 28;
  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps;
    const shape = 0.5 - 0.5 * Math.cos(2 * Math.PI * t);
    const level = -100 + (dbm + 100) * Math.pow(shape, 0.8);
    path += ` L${xOf(channel - half + 2 * half * t).toFixed(1)} ${yOf(level).toFixed(1)}`;
  }
  return `${path} Z`;
}

function axisFor(band: string, channels: number[], recommended: number): { lo: number; hi: number; labels: number[] } {
  if (band === "2.4") {
    return { lo: -1, hi: 14, labels: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14] };
  }
  const present = [...channels, recommended].filter((channel) => channel > 0);
  const lo = Math.min(...present) - 4;
  const hi = Math.max(...present) + 4;
  const step = hi - lo > 48 ? 8 : 4;
  const labels = new Set<number>([recommended]);
  for (const channel of channels) labels.add(channel);
  const start = Math.ceil(Math.min(...present) / step) * step;
  for (let channel = start; channel <= Math.max(...present); channel += step) labels.add(channel);
  const sorted = [...labels].sort((a, b) => a - b);
  const spaced: number[] = [];
  for (const channel of sorted) {
    const previous = spaced[spaced.length - 1];
    if (previous != null && channel - previous < step && channel !== recommended) continue;
    spaced.push(channel);
  }
  if (!spaced.includes(recommended)) spaced.push(recommended);
  return { lo, hi, labels: spaced.sort((a, b) => a - b) };
}
