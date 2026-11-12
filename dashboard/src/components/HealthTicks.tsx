import { useLayoutEffect, useRef, useState } from "react";
import { RANGE_HOURS, type ChartRange } from "./pathSeries";

/** How many ticks a health row shows, whatever the range and however many samples it holds. */
const TICKS = 48;
/** The space between two ticks, in CSS pixels. */
const GAP = 2;

// The worse colour wins when one tick covers several samples, so a short failure is never hidden.
const WORST_FIRST = ["var(--fail)", "var(--degraded)", "var(--ok)", "var(--unknown)", "var(--line)"];

function rank(color: string): number {
  const index = WORST_FIRST.indexOf(color);
  return index < 0 ? WORST_FIRST.length : index;
}

/** One colour for each tick, taken from the samples that fall inside it. */
export function tickColors(colors: string[], ticks = TICKS): string[] {
  if (colors.length === 0) return Array.from({ length: ticks }, () => "var(--line)");
  return Array.from({ length: ticks }, (_, tick) => {
    const from = Math.floor((tick * colors.length) / ticks);
    const to = Math.max(from + 1, Math.floor(((tick + 1) * colors.length) / ticks));
    let worst = colors[Math.min(from, colors.length - 1)];
    for (let index = from + 1; index < to && index < colors.length; index += 1) {
      if (rank(colors[index]) < rank(worst)) worst = colors[index];
    }
    return worst;
  });
}

/**
 * Tick widths and the gap, in CSS pixels, that fill `width` exactly with every edge on a whole screen pixel.
 *
 * Sharing the width evenly gives each tick a fraction of a pixel, and the browser then rounds each gap up or
 * down on its own, so the gaps look uneven. Here the gap is always the same whole number of screen pixels and
 * the pixels left over go one each to the first ticks: a tick one pixel wider is not noticeable, an uneven gap is.
 */
export function tickLayout(width: number, ratio: number, ticks = TICKS): { widths: number[]; gap: number } | null {
  const scale = ratio > 0 ? ratio : 1;
  const gap = Math.max(1, Math.round(GAP * scale));
  const room = Math.floor(width * scale) - gap * (ticks - 1);
  const base = Math.floor(room / ticks);
  if (base < 1) return null;
  const extra = room - base * ticks;
  return {
    widths: Array.from({ length: ticks }, (_, index) => (base + (index < extra ? 1 : 0)) / scale),
    gap: gap / scale,
  };
}

function ago(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} min ago`;
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${Number(hours.toFixed(1))} hours ago`;
  const days = Number((hours / 24).toFixed(1));
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

/**
 * The status of a check over the selected range, as a row of ticks from the start of the range to now.
 * Separate ticks and a time axis show that the row is a timeline, even when every tick is green.
 */
export default function HealthTicks({ colors, range, title }: { colors: string[]; range?: ChartRange; title: string }) {
  const hours = range ? RANGE_HOURS[range] : null;
  const row = useRef<HTMLSpanElement>(null);
  const [layout, setLayout] = useState<{ widths: number[]; gap: number } | null>(null);

  useLayoutEffect(() => {
    const element = row.current;
    if (!element) return;
    const measure = () => setLayout(tickLayout(element.getBoundingClientRect().width, window.devicePixelRatio || 1));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <>
      <span
        className="health-ticks"
        ref={row}
        title={title}
        role="img"
        aria-label={`${title}: status over time`}
        style={layout ? { gap: `${layout.gap}px` } : undefined}
      >
        {tickColors(colors).map((color, index) => (
          <i key={index} style={layout ? { background: color, flex: `0 0 ${layout.widths[index]}px` } : { background: color }} />
        ))}
      </span>
      {hours ? (
        <span className="health-axis" aria-hidden="true">
          <span>{ago(hours)}</span>
          <span>{ago(hours / 2)}</span>
          <span>now</span>
        </span>
      ) : null}
    </>
  );
}
