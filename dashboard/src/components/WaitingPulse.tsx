import { useEffect, useState } from "react";

function elapsed(since: string | null | undefined, now: number): string | null {
  if (!since) return null;
  const seconds = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * A moving "still waiting" banner for steps that wait on another device, so a quiet screen never looks stuck.
 * It counts up from `since` and says when the dashboard last asked the server.
 */
export default function WaitingPulse({
  label,
  since,
  checkedAt,
  hint,
}: {
  label: string;
  since?: string | null;
  /** When the data behind this banner was last fetched, in epoch milliseconds. */
  checkedAt?: number;
  hint?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const waited = elapsed(since, now);
  const ago = checkedAt ? Math.max(0, Math.round((now - checkedAt) / 1000)) : null;
  return (
    <div className="waiting-pulse" role="status" aria-live="polite">
      <span className="waiting-pulse-mark" aria-hidden="true">
        <span />
        <span />
      </span>
      <span className="waiting-pulse-text">
        <strong>
          {label}
          <span className="waiting-pulse-dots" aria-hidden="true" />
        </strong>
        <span className="muted">
          {waited ? `Waiting ${waited}` : null}
          {waited && ago !== null ? " · " : null}
          {ago !== null ? `checked ${ago}s ago` : null}
        </span>
        {hint ? <span className="muted">{hint}</span> : null}
      </span>
    </div>
  );
}
