import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { isAnonymized, stableId } from "../../anonymized";
import type { RfChannelSnapshot } from "../../types";
import { useElementWidth, useNarrow } from "../../useNarrow";
import { type AirContext } from "./shared";

const COLORS = ["#5eead4", "#7cb7ff", "#b79cff", "#ff8f85", "#f3dcb0", "#ffc457"];
const HEIGHT = 390;

/** The 2.4 and 5 GHz buttons. They sit at the top of the page, in every state. */
function BandPills({ band }: { band: "2.4" | "5" }) {
  const [params, setParams] = useSearchParams();
  function choose(next: "2.4" | "5") {
    const query = new URLSearchParams(params);
    query.set("band", next);
    setParams(query);
  }
  return (
    <div className="aw-pills" role="group" aria-label="Band">
      {(["2.4", "5"] as const).map((item) => (
        <button key={item} className={band === item ? "aw-pill active" : "aw-pill"} type="button" aria-pressed={band === item} onClick={() => choose(item)}>
          {item} GHz
        </button>
      ))}
    </div>
  );
}

export default function AirwavesChannels() {
  const ctx = useOutletContext<AirContext>();
  const [params] = useSearchParams();
  const band = params.get("band") === "5" ? "5" : "2.4";
  const snapshotQ = useQuery({
    queryKey: ["rf-channels", ctx.agent.id, band],
    queryFn: () => api.rfChannels(ctx.agent.id, band),
    refetchInterval: ctx.refresh ? 15_000 : false,
  });

  const bare = (title: string, body: string) => (
    <div className="awc">
      <div className="awc-top">
        <BandPills band={band} />
      </div>
      <Empty title={title} body={body} />
    </div>
  );
  if (!ctx.overviewLoading && !ctx.overview?.last_scan_at) return bare("No scans yet", `${ctx.agent.name} has not uploaded a scan.`);
  if (snapshotQ.isLoading) return bare("Channel graph", "Loading the latest scan…");
  if (snapshotQ.isError) return bare("Channel graph", "Could not load the channel graph.");
  if (!snapshotQ.data || (snapshotQ.data.series.length === 0 && snapshotQ.data.channels.length === 0)) {
    return bare(`${band} GHz`, `No ${band} GHz networks on the latest scan.`);
  }
  return <ChannelView snapshot={snapshotQ.data} ctx={ctx} band={band} />;
}

function ChannelView({ snapshot, ctx, band }: { snapshot: RfChannelSnapshot; ctx: AirContext; band: "2.4" | "5" }) {
  const [hover, setHover] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [plotRef, plotWidth] = useElementWidth<HTMLDivElement>();
  const narrow = useNarrow("(max-width: 640px)");
  const shown = snapshot.series.slice(0, 12);
  const advice = ctx.overview?.bands.find((item) => item.band === snapshot.band);
  const recommended = advice?.recommended_channel ?? snapshot.recommended_channel;
  const reason = advice?.reason ?? snapshot.reason;
  // "Your 2.4 GHz band is crowded. Channel 1 would be quieter than channel 6." restates the title and the
  // Busiest tile, so the line is only shown when it says something else.
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

  // The graph is drawn at the width it is given, so text stays the size it is written at, on a phone too.
  const width = Math.max(plotWidth, 300);
  const small = width < 560;
  const left = small ? 44 : 62;
  const right = width - 14;
  const xOf = (channel: number) => {
    if (snapshot.band === "2.4") return left + ((channel + 1) / 16) * (right - left);
    const span = Math.max(axis.hi - axis.lo, 1);
    return left + ((channel - axis.lo) / span) * (right - left);
  };
  const shadeLeft = Math.max(left, xOf(recommended - 2));
  const shadeRight = Math.min(right, xOf(recommended + 2));
  const hills = shown.map((series, index) => ({
    ...series,
    color: COLORS[index % COLORS.length],
    d: hillPath(series.channel, series.channel_width, series.rssi_dbm, xOf),
  }));
  // Channel numbers closer together than this would run into each other.
  const labels: number[] = [];
  let lastX = -999;
  for (const channel of axis.labels) {
    const x = xOf(channel);
    if (x - lastX < (small ? 26 : 34) && channel !== recommended) continue;
    labels.push(channel);
    lastX = x;
  }
  const pillWidth = Math.max(Math.min(shadeRight - shadeLeft - 8, 150), 96);
  const pillX = Math.min(Math.max((shadeLeft + shadeRight) / 2 - pillWidth / 2, left), right - pillWidth);
  const visible = showAll ? hills : hills.slice(0, narrow ? 3 : 6);

  return (
    <div className="awc">
      <div className="awc-top">
        <BandPills band={band} />
        <section className="awc-rec" aria-label={`Recommended channel for ${snapshot.band} GHz`}>
          <span className="awc-rec-icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0d1220" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12.55a11 11 0 0 1 14.08 0" />
              <path d="M1.42 9a16 16 0 0 1 21.16 0" />
              <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
              <path d="M12 20h.01" />
            </svg>
          </span>
          <div className="awc-rec-main">
            <small>Recommended for {snapshot.band} GHz</small>
            <b>Try channel {recommended}</b>
            {extra ? <span>{extra}</span> : null}
          </div>
          <dl className="awc-rec-stats">
            <div>
              <dt>Networks</dt>
              <dd>{advice?.ap_count ?? snapshot.series.length}</dd>
            </div>
            <div>
              <dt>Busiest</dt>
              <dd>{busiest == null ? "—" : `Ch ${busiest}`}</dd>
              {busiest != null ? <small>{onBusiest} networks</small> : null}
            </div>
            <div>
              <dt>Strongest</dt>
              <dd>{strongest ? dbmText(strongest.rssi_dbm) : "—"}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="aw-card awc-graph">
        <div className="awc-head">
          <h2>Channel graph</h2>
          <p>
            {snapshot.series.length > shown.length ? "The 12 strongest networks · " : ""}
            Tap or hover a network below to spotlight it
          </p>
        </div>
        <div className="awc-plot" ref={plotRef}>
          <svg
            viewBox={`0 0 ${width} ${HEIGHT}`}
            width="100%"
            style={{ height: "auto", display: "block" }}
            role="img"
            aria-label={`Signal strength by channel for the ${shown.length} strongest ${snapshot.band} GHz networks. The quietest zone is shaded around channel ${recommended}.`}
          >
            <rect x={shadeLeft} y={20} width={Math.max(shadeRight - shadeLeft, 8)} height={290} fill="rgba(94,234,212,0.12)" />
            {[20, 92.5, 165, 237.5].map((y) => (
              <line key={y} x1={left} y1={y} x2={right} y2={y} stroke="rgba(255,255,255,0.08)" />
            ))}
            <line x1={left} y1={310} x2={right} y2={310} stroke="rgba(255,255,255,0.18)" />
            <AxisLabel x={left - 8} y={25} text="−20" />
            <AxisLabel x={left - 8} y={97} text="−40" />
            <AxisLabel x={left - 8} y={170} text="−60" />
            <AxisLabel x={left - 8} y={242} text="−80" />
            <AxisLabel x={left - 8} y={315} text="−100" />
            <text x={left - 8} y={9} fill="#9aa6c0" fontSize={12} fontWeight={800} textAnchor="end">
              dBm
            </text>
            <rect x={pillX} y={30} width={pillWidth} height={28} rx={14} fill="rgba(94,234,212,0.22)" />
            <text x={pillX + pillWidth / 2} y={49} fill="#5eead4" fontSize={13} fontWeight={800} textAnchor="middle">
              Quietest zone
            </text>
            {labels.map((channel) => {
              const load = small ? null : channelLoad(snapshot, channel);
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
            <text x={(left + right) / 2} y={380} fill="#9aa6c0" fontSize={12} fontWeight={800} textAnchor="middle">
              Wi-Fi channel
            </text>
            <clipPath id="awc-plotclip">
              <rect x={left} y={14} width={Math.max(right - left, 1)} height={297} />
            </clipPath>
            {hills.map((hill, index) => {
              const on = hover === index;
              const dim = hover !== null && !on;
              return (
                <path
                  key={stableId(hill, hill.bssid)}
                  d={hill.d}
                  clipPath="url(#awc-plotclip)"
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

      <section className="aw-card awc-nets">
        <div className="awc-head">
          <h2>Networks in range</h2>
          <p>{hills.length} on {snapshot.band} GHz</p>
        </div>
        <div className="awc-list">
          {visible.map((hill, index) => {
            const on = hover === index;
            const dim = hover !== null && !on;
            return (
              <button
                type="button"
                key={stableId(hill, `${hill.bssid}-${index}`)}
                className={on ? "awc-net on" : dim ? "awc-net dim" : "awc-net"}
                style={{ ["--net" as string]: hill.color }}
                aria-pressed={on}
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
                onClick={() => setHover(on ? null : index)}
              >
                <span className="awc-net-row">
                  <i style={{ background: hill.color }} aria-hidden="true" />
                  <span className={isAnonymized(hill.ssid) ? "awc-net-name anon-mark" : "awc-net-name"}>{hill.ssid}</span>
                  <b>{dbmText(hill.rssi_dbm)}</b>
                </span>
                <span className="awc-meter">
                  <span style={{ width: meter(hill.rssi_dbm), background: hill.color }} />
                </span>
                <span className="awc-net-meta">
                  Channel {hill.channel} · {hill.channel_width} MHz
                  {busyText(snapshot, hill.channel)}
                  {hill.trusted ? " · Trusted" : ""}
                </span>
              </button>
            );
          })}
        </div>
        {hills.length > visible.length || showAll ? (
          <button className="air-text-btn aw-see-all" type="button" onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show fewer" : `See all ${hills.length}`}
          </button>
        ) : null}
      </section>
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
