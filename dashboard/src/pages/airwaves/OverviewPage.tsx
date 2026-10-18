import { useOutletContext } from "react-router-dom";
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";
import { isAnonymized } from "../../anonymized";
import { CHART_COLORS } from "../../chartTheme";
import type { RfBandAdvice, RfOverview } from "../../types";
import type { AirContext } from "./shared";

export default function AirwavesOverview() {
  const ctx = useOutletContext<AirContext>();
  const overview = ctx.overview;

  if (ctx.overviewLoading) return <div className="state-box">Loading the latest scan…</div>;
  if (ctx.overviewError) return <div className="state-box">Could not load airwave data.</div>;
  if (!overview?.last_scan_at) {
    return <div className="state-box">No data yet. {ctx.agent.name} uploads scans on its own.</div>;
  }

  return (
    <div className="aw-overview">
      {overview.bands.map((band) => (
        <BandCard key={band.band} band={band} overview={overview} />
      ))}
      <Alerts alerts={overview.alerts} />
    </div>
  );
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function Alerts({ alerts }: { alerts: RfOverview["alerts"] }) {
  const activeCount = alerts.filter((a) => a.severity === "critical" || a.severity === "warning").length;
  return (
    <section className="aw-card aw-alerts" aria-label="Recent alerts">
      <header>
        <h2>Recent alerts</h2>
        <span className={`aw-badge${activeCount === 0 ? " info" : ""}`}>{alerts.length}</span>
      </header>
      <div className="aw-alerts-list" tabIndex={0} role="region" aria-label="Recent alerts list, scrollable">
        {alerts.length === 0 ? <p className="aw-empty">No alerts in this range.</p> : null}
        {alerts.map((alert, index) => {
          const sev = alert.severity ?? "info";
          return (
            <div className={`aw-alert sev-${sev}`} key={`${alert.observed_at}-${index}`}>
              <i aria-hidden="true" />
              <div>
                <b>{alert.alert_type}</b>
                <small>
                  {alert.bssid ? (
                    <>
                      <span className={isAnonymized(alert.bssid) ? "anon-mark" : undefined}>{alert.bssid}</span>
                      {" · "}
                    </>
                  ) : null}
                  <span className={isAnonymized(alert.text) ? "anon-mark" : undefined}>{alert.text}</span>
                </small>
              </div>
              <time dateTime={alert.observed_at}>{clock(alert.observed_at)}</time>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function BandCard({ band, overview }: { band: RfBandAdvice; overview: RfOverview }) {
  const key = band.band === "5" ? "count_5" : "count_24";
  const points = overview.sparkline.map((point) => ({ t: point.t, value: point[key] }));
  return (
    <section className="aw-card aw-band" aria-label={`${band.band} GHz`}>
      <div className="aw-band-top">
        <h2>{band.band} GHz</h2>
        <span>{band.ap_count} networks</span>
      </div>
      <div className="aw-band-body">
        <div className="aw-stat">
          <span>Busiest</span>
          <strong>{band.busiest_channel == null ? "—" : `Ch ${band.busiest_channel}`}</strong>
        </div>
        <div className="aw-stat best">
          <span>Best</span>
          <strong>Ch {band.recommended_channel}</strong>
        </div>
        {points.length > 1 ? (
          <div className="aw-spark" role="img" aria-label={`Networks heard on ${band.band} GHz over the last hour`}>
            <ResponsiveContainer width="100%" height={44}>
              <AreaChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                <YAxis hide domain={[0, "auto"]} />
                <Area dataKey="value" stroke={CHART_COLORS[0]} fill={CHART_COLORS[0]} fillOpacity={0.22} strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </div>
    </section>
  );
}
