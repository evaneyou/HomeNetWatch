import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  YAxis,
} from "recharts";
import { api } from "../api";
import { isAnonymized, shownAnon, stableId } from "../anonymized";
import { CHART_COLORS } from "../chartTheme";
import { dataWindow, formatChartTime, zoomedDomain } from "../chartTime";
import { TimeAxis } from "./TimeAxis";
import { useChartZoom } from "../useChartZoom";
import type { WifiObservation } from "../types";

const RANGES = ["1h", "24h", "7d"] as const;
const COLORS = CHART_COLORS;

function seriesKey(row: WifiObservation): string {
  if (isAnonymized(row.ssid) || isAnonymized(row.bssid)) return stableId(row, row.bssid);
  return `${row.ssid} ${row.band} GHz`;
}

export default function WifiScans({ monitorId }: { monitorId: string }) {
  const [range, setRange] = useState<(typeof RANGES)[number]>("1h");
  const q = useQuery({
    queryKey: ["wifi-scans", monitorId, range],
    queryFn: () => api.wifiScans(monitorId, range) as Promise<WifiObservation[]>,
  });

  const rows = q.data ?? [];
  const keys = useMemo(() => [...new Set(rows.map(seriesKey))], [rows]);
  const anonymizedScan = rows.some((row) => isAnonymized(row.ssid) || isAnonymized(row.bssid));
  const scanLabels = anonymizedScan
    ? keys.map((key) => {
        const row = rows.find((item) => stableId(item, item.bssid) === key);
        return row?.ssid ? shownAnon(row.ssid) : key;
      })
    : undefined;

  const chart = useMemo(() => {
    const byTime = new Map<string, Record<string, string | number>>();
    for (const row of rows) {
      const point = byTime.get(row.scanned_at) ?? { t: row.scanned_at };
      point[seriesKey(row)] = row.rssi_dbm;
      point[`${seriesKey(row)} noise`] = row.noise_dbm;
      point[`${seriesKey(row)} rate`] = row.max_link_rate_mbps;
      byTime.set(row.scanned_at, point);
    }
    return [...byTime.values()];
  }, [rows]);

  const rssiChange = useMemo(() => changeFromStart(chart, keys), [chart, keys]);
  const changeDomain = useMemo(() => symmetricDomain(rssiChange, keys), [rssiChange, keys]);

  const latest = useMemo(() => {
    const byBssid = new Map<string, WifiObservation>();
    for (const row of rows) byBssid.set(stableId(row, row.bssid), row);
    return [...byBssid.values()].sort((a, b) => b.rssi_dbm - a.rssi_dbm);
  }, [rows]);

  const recent = useMemo(() => [...rows].reverse().slice(0, 36), [rows]);

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0, fontSize: "1rem" }}>Wi-Fi scanning (nearby networks)</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        SSID, access point, signal, and link details heard by this remote agent.
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

      {q.isLoading ? (
        <div className="muted">Loading scans…</div>
      ) : q.isError ? (
        <div className="muted">Could not load Wi-Fi scans.</div>
      ) : rows.length === 0 ? (
        <div className="muted">No scans in this range.</div>
      ) : (
        <>
          <h3 className="wifi-heading">Latest scan</h3>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>SSID</th>
                  <th>BSSID</th>
                  <th>Vendor</th>
                  <th>Band</th>
                  <th>Channel</th>
                  <th>Width</th>
                  <th>RSSI</th>
                  <th>Noise</th>
                  <th>Security</th>
                  <th>PHY</th>
                  <th>Max rate</th>
                </tr>
              </thead>
              <tbody>
                {latest.map((row) => (
                  <tr key={stableId(row, row.bssid)}>
                    <td className={isAnonymized(row.ssid) ? "anon-mark" : undefined}>
                      {isAnonymized(row.ssid) ? shownAnon(row.ssid) : row.ssid}
                    </td>
                    <td className={isAnonymized(row.bssid) ? "anon-mark" : "mono"}>
                      {isAnonymized(row.bssid) ? shownAnon(row.bssid) : row.bssid}
                    </td>
                    <td className={isAnonymized(row.vendor) ? "anon-mark" : undefined}>
                      {isAnonymized(row.vendor) ? shownAnon(row.vendor) : row.vendor}
                    </td>
                    <td className="mono">{row.band} GHz</td>
                    <td className="mono">{row.channel}</td>
                    <td className="mono">{row.channel_width} MHz</td>
                    <td className="mono">{row.rssi_dbm} dBm</td>
                    <td className="mono">{row.noise_dbm} dBm</td>
                    <td>{row.security}</td>
                    <td className="mono">{row.phy}</td>
                    <td className="mono">{row.max_link_rate_mbps} Mbps</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ScanChart
            title="Signal change"
            data={rssiChange}
            keys={keys}
            labels={scanLabels}
            blurred={anonymizedScan}
            unit=" dB"
            domain={changeDomain}
            zeroLine
          />
          <ScanChart
            title="Noise"
            data={chart}
            keys={keys.map((key) => `${key} noise`)}
            labels={scanLabels ?? keys}
            blurred={anonymizedScan}
            unit=" dBm"
            domain={[-100, -70]}
          />
          <ScanChart
            title="Max link rate"
            data={chart}
            keys={keys.map((key) => `${key} rate`)}
            labels={scanLabels ?? keys}
            blurred={anonymizedScan}
            unit=" Mbps"
          />

          <h3 className="wifi-heading">Recent scans</h3>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>SSID</th>
                  <th>BSSID</th>
                  <th>Vendor</th>
                  <th>Band</th>
                  <th>Channel</th>
                  <th>Width</th>
                  <th>RSSI</th>
                  <th>Noise</th>
                  <th>Security</th>
                  <th>PHY</th>
                  <th>Max rate</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => (
                  <tr key={`${row.scanned_at}-${stableId(row, row.bssid)}`}>
                    <td className="mono">{new Date(row.scanned_at).toLocaleString()}</td>
                    <td className={isAnonymized(row.ssid) ? "anon-mark" : undefined}>
                      {isAnonymized(row.ssid) ? shownAnon(row.ssid) : row.ssid}
                    </td>
                    <td className={isAnonymized(row.bssid) ? "anon-mark" : "mono"}>
                      {isAnonymized(row.bssid) ? shownAnon(row.bssid) : row.bssid}
                    </td>
                    <td className={isAnonymized(row.vendor) ? "anon-mark" : undefined}>
                      {isAnonymized(row.vendor) ? shownAnon(row.vendor) : row.vendor}
                    </td>
                    <td className="mono">{row.band} GHz</td>
                    <td className="mono">{row.channel}</td>
                    <td className="mono">{row.channel_width} MHz</td>
                    <td className="mono">{row.rssi_dbm}</td>
                    <td className="mono">{row.noise_dbm}</td>
                    <td>{row.security}</td>
                    <td className="mono">{row.phy}</td>
                    <td className="mono">{row.max_link_rate_mbps}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function changeFromStart(
  data: Record<string, string | number>[],
  keys: string[],
): Record<string, string | number>[] {
  const baseline = new Map<string, number>();
  return data.map((point) => {
    const next: Record<string, string | number> = { t: point.t };
    for (const key of keys) {
      const value = point[key];
      if (typeof value !== "number") continue;
      const start = baseline.get(key);
      if (start === undefined) baseline.set(key, value);
      next[key] = Math.round((value - (start ?? value)) * 10) / 10;
    }
    return next;
  });
}

function symmetricDomain(
  data: Record<string, string | number>[],
  keys: string[],
): [number, number] {
  let peak = 1;
  for (const point of data) {
    for (const key of keys) {
      const value = point[key];
      if (typeof value === "number") peak = Math.max(peak, Math.abs(value));
    }
  }
  const edge = Math.ceil(peak + 1);
  return [-edge, edge];
}

function ScanChart({
  title,
  data,
  keys,
  labels,
  blurred,
  unit,
  domain,
  zeroLine,
}: {
  title: string;
  data: Record<string, string | number>[];
  keys: string[];
  labels?: string[];
  blurred?: boolean;
  unit: string;
  domain?: [number, number];
  zeroLine?: boolean;
}) {
  const rows = data.map((row) => ({ ...row, ts: new Date(String(row.t)).getTime() }));
  const zoom = useChartZoom(rows);
  const span = zoomedDomain(dataWindow(rows), rows.length, zoom.visible);
  return (
    <>
      <h3 className="wifi-heading">{title}</h3>
      <div className={`chart-zoom${blurred ? " is-blurred" : ""}`} ref={zoom.boxRef} style={{ width: "100%", height: 300 }} onDoubleClick={zoom.onDoubleClick}>
        <ResponsiveContainer>
          <LineChart
            data={zoom.visible}
            margin={{ top: 8, right: 8, left: 0, bottom: 4 }}
            onMouseDown={zoom.onMouseDown}
            onMouseMove={zoom.onMouseMove}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
            <TimeAxis domain={span} />
            <YAxis tick={{ fontSize: 11 }} unit={unit} domain={domain} width={72} />
            {zeroLine ? <ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="4 4" /> : null}
            <Tooltip labelFormatter={(value) => formatChartTime(Number(value))} />
            {zoom.selection}
            {keys.map((key, i) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                name={labels?.[i] ?? key}
                stroke={COLORS[i % COLORS.length]}
                dot={false}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
