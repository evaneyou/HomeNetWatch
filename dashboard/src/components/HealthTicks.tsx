import { RANGE_HOURS, type ChartRange } from "./pathSeries";

/** How many ticks a health row shows, whatever the range and however many samples it holds. */
const TICKS = 48;

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
  return (
    <>
      <span className="health-ticks" title={title} role="img" aria-label={`${title}: status over time`}>
        {tickColors(colors).map((color, index) => (
          <i key={index} style={{ background: color }} />
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
