import { XAxis } from "recharts";
import { chartTicks, formatChartTick } from "../chartTime";

/** Shared time axis so every chart in a stack uses the same window and tick marks. */
export function TimeAxis({ domain }: { domain: [number, number] }) {
  const span = domain[1] - domain[0];
  return (
    <XAxis
      dataKey="ts"
      type="number"
      scale="time"
      domain={domain}
      allowDataOverflow
      ticks={chartTicks(domain)}
      tickFormatter={(value) => formatChartTick(value, span)}
      tick={{ fontSize: 10, fill: "var(--muted)" }}
      minTickGap={56}
      angle={-28}
      textAnchor="end"
      height={40}
    />
  );
}
