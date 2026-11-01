import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { AnonPlate, AnonText, coordinate, isAnonymized, joinAnon } from "../anonymized";
import CityMap from "./CityMap";
import { formatChartHour } from "../chartTime";
import { isAndroid } from "../deviceType";
import { useChartZoom } from "../useChartZoom";
import type { DeviceReport } from "../types";

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

type DevicePoint = DeviceReport["points"][number];
type VitalKey = "cpu" | "mem" | "disk" | "temp";

const VITALS: Record<
  VitalKey,
  { title: string; dataKey: "cpu_percent" | "temp_c" | "mem_used_mb" | "disk_used_mb"; color: string; unit: string; axisWidth: number }
> = {
  cpu: { title: "CPU", dataKey: "cpu_percent", color: "var(--accent)", unit: "%", axisWidth: 36 },
  mem: { title: "Memory", dataKey: "mem_used_mb", color: "var(--ok)", unit: "MB", axisWidth: 48 },
  disk: { title: "Disk", dataKey: "disk_used_mb", color: "var(--role-ixp)", unit: "MB", axisWidth: 52 },
  temp: { title: "Temperature", dataKey: "temp_c", color: "var(--degraded)", unit: "°C", axisWidth: 36 },
};

const CPU_WINDOW_MS = 60 * 60 * 1000;

function percent(value: number): string {
  return value >= 10 ? `${Math.round(value)}%` : `${value.toFixed(1)}%`;
}

function gigabytes(mb: number): string {
  const gb = mb / 1024;
  return gb >= 100 ? `${Math.round(gb)}` : gb.toFixed(1);
}

/**
 * The agent measures CPU over a fraction of a second at each check-in, so any one
 * reading is usually idle. Averaging the last hour, with the peak, says what the
 * probe is actually doing.
 */
function cpuLastHour(points: DevicePoint[]): { average: number; peak: number } | null {
  if (points.length === 0) return null;
  const end = new Date(points[points.length - 1].t).getTime();
  const recent = points.filter((point) => end - new Date(point.t).getTime() <= CPU_WINDOW_MS);
  if (recent.length === 0) return null;
  const values = recent.map((point) => point.cpu_percent);
  return {
    average: values.reduce((sum, value) => sum + value, 0) / values.length,
    peak: Math.max(...values),
  };
}

function temperatureRange(points: DevicePoint[]): string | null {
  const values = points.map((point) => point.temp_c).filter((value): value is number => value != null);
  if (values.length === 0) return null;
  const low = Math.round(Math.min(...values));
  const high = Math.round(Math.max(...values));
  return low === high ? `24 h: ${low}°C` : `24 h: ${low} to ${high}°C`;
}

function HealthChart({ vital, data }: { vital: VitalKey; data: DevicePoint[] }) {
  const { title, dataKey, color, unit, axisWidth } = VITALS[vital];
  const zoom = useChartZoom(data);
  return (
    <div className="chart-zoom" ref={zoom.boxRef} onDoubleClick={zoom.onDoubleClick}>
      <ResponsiveContainer width="100%" height={145}>
        <LineChart data={zoom.visible} onMouseDown={zoom.onMouseDown} onMouseMove={zoom.onMouseMove}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="t" tickFormatter={formatChartHour} minTickGap={28} stroke="var(--muted)" tick={{ fontSize: 11 }} />
          <YAxis stroke="var(--muted)" width={axisWidth} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={(value) => formatChartHour(String(value))}
            formatter={(value) => [`${value} ${unit}`, title]}
          />
          {zoom.selection}
          <Line type="monotone" dataKey={dataKey} stroke={color} dot={false} strokeWidth={1.75} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function DevicePanel({
  monitorId,
  platform,
}: {
  monitorId: string;
  platform?: string;
}) {
  const [vital, setVital] = useState<VitalKey>("cpu");
  const deviceQ = useQuery({
    queryKey: ["monitor-device", monitorId],
    queryFn: () => api.monitorDevice(monitorId) as Promise<DeviceReport>,
    refetchInterval: 15000,
  });

  if (deviceQ.isLoading) return <div className="panel">Loading device health…</div>;
  if (deviceQ.isError || !deviceQ.data) return <div className="panel">Could not load device health.</div>;

  const report = deviceQ.data;
  const latest = report.latest;
  const android =
    isAndroid(platform ?? report.platform) || Boolean(report.agent_version?.startsWith("android-"));
  const place = joinAnon([report.location_label, report.region, report.country]);
  const latitude = coordinate(report.latitude);
  const longitude = coordinate(report.longitude);
  const savedPin = latitude != null && longitude != null ? { latitude, longitude } : null;
  const pinMark = [report.latitude, report.longitude].filter(isAnonymized).map(String).join("  ");
  const placeHidden = isAnonymized(place) || pinMark.length > 0;

  const cpu = cpuLastHour(report.points);
  const allTiles = latest
    ? [
        ...(!android
          ? [
              {
                key: "cpu" as const,
                label: "CPU (1h)",
                value: cpu ? `${percent(cpu.average)} avg` : percent(latest.cpu_percent),
                note: cpu ? `peak ${percent(cpu.peak)} · now ${percent(latest.cpu_percent)}` : "latest reading",
              },
            ]
          : []),
        {
          key: "mem" as const,
          label: "Memory",
          value: percent((latest.mem_used_mb / Math.max(latest.mem_total_mb, 1)) * 100),
          note: `${latest.mem_used_mb} / ${latest.mem_total_mb} MB`,
        },
        {
          key: "disk" as const,
          label: "Disk",
          value: percent((latest.disk_used_mb / Math.max(latest.disk_total_mb, 1)) * 100),
          note: `${gigabytes(latest.disk_used_mb)} / ${gigabytes(latest.disk_total_mb)} GB`,
        },
        ...(!android && latest.temp_c != null
          ? [
              {
                key: "temp" as const,
                label: "Temp",
                value: `${latest.temp_c.toFixed(1)}°C`,
                note: temperatureRange(report.points) ?? "latest reading",
              },
            ]
          : []),
      ]
    : [];

  const activeVital: VitalKey = allTiles.some((t) => t.key === vital)
    ? vital
    : (allTiles[0]?.key ?? "mem");

  return (
    <div className="probe-inspector">
      <section className="probe-inspector-section">
        <div className="probe-inspector-head">
          <div className="probe-inspector-place">
            <span className="probe-inspector-kicker">Location</span>
            {placeHidden ? (
              <span className="anon-mark">{place || pinMark}</span>
            ) : (
              <span className="probe-inspector-place-text">{place || "No location saved"}</span>
            )}
          </div>
          <span className={report.connected ? "device-live" : "device-quiet"}>
            {report.connected ? "● Connected" : "○ Waiting"}
          </span>
        </div>

        {latest ? (
          <dl className="probe-meta-strip">
            <div>
              <dt>Uptime</dt>
              <dd>{formatUptime(latest.uptime_seconds)}</dd>
            </div>
            <div>
              <dt>Agent</dt>
              <dd>{report.agent_version || "—"}</dd>
            </div>
            {!android ? (
              <div>
                <dt>Load</dt>
                <dd>{latest.load_1.toFixed(2)}</dd>
              </div>
            ) : null}
            <div>
              <dt>Address</dt>
              <dd>
                <AnonText value={report.lan_ip || latest.lan_ip} />
              </dd>
            </div>
            <div>
              <dt>Host</dt>
              <dd>
                <AnonText value={report.hostname} />
              </dd>
            </div>
          </dl>
        ) : (
          <p className="muted" style={{ margin: "0.35rem 0 0" }}>
            This remote agent has not reported health yet.
          </p>
        )}

        {latest?.last_error ? <p className="state-box">{latest.last_error}</p> : null}

        {savedPin ? (
          <CityMap
            className="location-map probe-mini-map"
            latitude={savedPin.latitude}
            longitude={savedPin.longitude}
            label={place || report.name}
            zoomControl={false}
          />
        ) : placeHidden ? (
          <AnonPlate value={pinMark || place} />
        ) : null}
      </section>

      {latest && allTiles.length > 0 ? (
        <section className="probe-inspector-section">
          <div
            className={`vital-tiles${allTiles.length <= 2 ? " vital-tiles-2" : ""}`}
            role="group"
            aria-label="Pick a chart"
          >
            {allTiles.map((tile) => (
              <button
                key={tile.key}
                type="button"
                className={`vital-tile${activeVital === tile.key ? " active" : ""}`}
                aria-pressed={activeVital === tile.key}
                onClick={() => setVital(tile.key)}
              >
                <div className="vital-tile-top">
                  <span className="vital-label">{tile.label}</span>
                  <strong className="vital-value">{tile.value}</strong>
                </div>
                <span className="vital-note">{tile.note}</span>
              </button>
            ))}
          </div>
          {report.points.length > 1 ? (
            <div className="probe-chart-wrap">
              <div className="vital-chart-head">
                <h3>{VITALS[activeVital].title}</h3>
                <span className="muted">Last 24 hours</span>
              </div>
              <HealthChart key={activeVital} vital={activeVital} data={report.points} />
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
