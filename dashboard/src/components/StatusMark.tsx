import { useState } from "react";
import { createPortal } from "react-dom";

type StatusKind = "ok" | "degraded" | "offline" | "info" | "unknown";

const PRESENTATION: Record<string, { kind: StatusKind; label: string }> = {
  ok: { kind: "ok", label: "Ok" },
  online: { kind: "ok", label: "Online" },
  degraded: { kind: "degraded", label: "Degraded" },
  fail: { kind: "offline", label: "Offline" },
  offline: { kind: "offline", label: "Offline" },
  info: { kind: "info", label: "Notice" },
  software: { kind: "info", label: "Software" },
  unknown: { kind: "unknown", label: "Unknown" },
};

function OkIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" fill="none" stroke="currentColor" strokeWidth="2" />
      <path
        d="M8.15 12.25 10.85 14.9 15.9 9.25"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DegradedIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 4.35 20.15 19.15H3.85L12 4.35Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M12 10.1v4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="16.55" r="1.05" fill="currentColor" />
    </svg>
  );
}

function OfflineIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" fill="none" stroke="currentColor" strokeWidth="2" />
      <path
        d="M8.05 14.85 15.85 9.05"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="12" cy="8.2" r="1.15" fill="currentColor" />
      <path d="M12 11.4v4.9" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function UnknownIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M9.4 9.7a2.6 2.6 0 1 1 3.5 2.45c-.7.35-1.15.9-1.15 1.7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="11.75" cy="16.55" r="1.05" fill="currentColor" />
    </svg>
  );
}

export function statusLabel(status: string): string {
  return PRESENTATION[status]?.label ?? (status || "Unknown");
}

function MarkIcon({ kind }: { kind: StatusKind }) {
  if (kind === "ok") return <OkIcon />;
  if (kind === "degraded") return <DegradedIcon />;
  if (kind === "offline") return <OfflineIcon />;
  if (kind === "info") return <InfoIcon />;
  return <UnknownIcon />;
}

export default function StatusMark({
  status,
  layout = "inline",
  detail,
  focusable = true,
}: {
  status: string;
  layout?: "inline" | "stack";
  detail?: string | null;
  focusable?: boolean;
}) {
  const view = PRESENTATION[status] ?? { kind: "unknown" as const, label: statusLabel(status) };
  const [tip, setTip] = useState<{ x: number; y: number; below: boolean } | null>(null);
  const label = detail ? `${view.label}. ${detail}` : view.label;

  function show(target: HTMLElement) {
    if (!detail) return;
    const rect = target.getBoundingClientRect();
    const y = rect.top;
    setTip({ x: rect.left + rect.width / 2, y, below: y < 72 });
  }

  return (
    <span
      className={`status-mark ${layout} status-mark-${view.kind}${detail ? " has-detail" : ""}`}
      role="img"
      aria-label={label}
      tabIndex={detail && focusable ? 0 : undefined}
      onMouseEnter={(event) => show(event.currentTarget)}
      onMouseLeave={() => setTip(null)}
      onFocus={(event) => show(event.currentTarget)}
      onBlur={() => setTip(null)}
    >
      <span className="status-mark-icon">
        <MarkIcon kind={view.kind} />
      </span>
      {tip && detail
        ? createPortal(
            <span className={`status-tip${tip.below ? " below" : ""}`} role="tooltip" style={{ left: tip.x, top: tip.y }}>
              {detail}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}
