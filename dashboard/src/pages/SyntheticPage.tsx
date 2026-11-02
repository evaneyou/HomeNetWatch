import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { MonitorAdmin } from "../types";
import { PresetForm } from "./SyntheticForm";
import {
  collapseAll,
  expandAll,
  groupPresets,
  initialOpenIds,
  toggleOpen,
} from "./syntheticList";

type Options = {
  report_interval_s: number;
  max_rtt_ms: number;
  max_jitter_ms: number;
  max_loss_pct: number;
  ptime_ms?: number;
};

type Continuous = {
  links: string[];
  options: Options;
  status: string;
  started_at: string;
};

type Profile = {
  slug: string;
  name: string;
  category?: string | null;
  options: Options;
  continuous: Continuous | null;
};

const OPEN_KEY = "app-health-open-rows";

export default function SyntheticPage() {
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
  });
  const monitors = monitorsQ.data ?? [];
  const [monitorId, setMonitorId] = useState("");
  const selected = monitorId || monitors[0]?.id || "";

  if (monitorsQ.isLoading) return <div className="state-box">Loading synthetic traffic…</div>;
  if (monitors.length === 0) return <div className="state-box">No remote agents yet.</div>;

  return (
    <div className="app-health">
      <h2 className="app-health-title">Real-Time Application Health</h2>
      <p className="app-health-intro">
        Recommended settings are filled in. A continuous run keeps going until you turn it off. Measurement packets
        use the server&apos;s public address and do not enter the tunnel.
      </p>
      {selected ? <Profiles monitorId={selected} monitors={monitors} onMonitor={setMonitorId} /> : null}
    </div>
  );
}

function Profiles({
  monitorId,
  monitors,
  onMonitor,
}: {
  monitorId: string;
  monitors: MonitorAdmin[];
  onMonitor: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const profilesQ = useQuery({
    queryKey: ["synthetic-profiles", monitorId],
    queryFn: () => api.syntheticProfiles(monitorId) as Promise<{ profiles: Profile[] }>,
  });
  const profiles = profilesQ.data?.profiles ?? [];
  const groups = groupPresets(profiles);
  const ids = groups.flatMap((group) => group.items.map((item) => item.slug));
  const [openIds, setOpenIds] = useState<string[] | null>(null);
  const open = new Set(openIds ?? initialOpenIds(ids, readOpen()));
  const anyOpen = ids.some((id) => open.has(id));

  useEffect(() => {
    if (openIds !== null || ids.length === 0) return;
    setOpenIds(initialOpenIds(ids, readOpen()));
  }, [ids, openIds]);

  function remember(next: string[]) {
    setOpenIds(next);
    sessionStorage.setItem(OPEN_KEY, JSON.stringify(next));
  }

  if (profilesQ.isLoading) return <p className="app-health-intro">Loading traffic types…</p>;
  if (profilesQ.isError) return <p className="app-health-intro">Could not load traffic types.</p>;

  return (
    <>
      <div className="app-health-toolbar">
        <label className="app-health-agent">
          <span>Remote agent</span>
          <select value={monitorId} onChange={(event) => onMonitor(event.target.value)}>
            {monitors.map((monitor) => (
              <option key={monitor.id} value={monitor.id}>
                {monitor.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="app-health-text"
          type="button"
          onClick={() => remember(anyOpen ? collapseAll() : expandAll(ids))}
        >
          {anyOpen ? "Collapse all" : "Expand all"}
        </button>
      </div>
      <div className="app-health-presets">
      {groups.map((group) => (
        <section className="app-health-card" key={group.category}>
          <header className="app-health-group">
            <h3>
              {group.name} <span className="app-health-count">{group.items.length}</span>
            </h3>
            <p>{groupSummary(group.items)}</p>
          </header>
          <div className="app-health-head" aria-hidden="true">
            <span>Name</span>
            <span>Status</span>
            <span>Path</span>
            <span>Report</span>
            <span>Max round trip</span>
            <span>Max loss</span>
            <span />
          </div>
          {group.items.map((profile) => (
            <PresetRow
              key={profile.slug}
              monitorId={monitorId}
              profile={profile}
              expanded={open.has(profile.slug)}
              onToggle={() => remember(toggleOpen([...open], profile.slug))}
              onSaved={() => queryClient.invalidateQueries({ queryKey: ["synthetic-profiles", monitorId] })}
            />
          ))}
        </section>
      ))}
      </div>
    </>
  );
}

function PresetRow({
  monitorId,
  profile,
  expanded,
  onToggle,
  onSaved,
}: {
  monitorId: string;
  profile: Profile;
  expanded: boolean;
  onToggle: () => void;
  onSaved: () => void;
}) {
  const status = statusOf(profile);
  const source = profile.continuous?.options ?? profile.options;
  const links = profile.continuous?.links ?? [];
  const running = status.kind === "running" || status.kind === "pending";
  return (
    <div className="app-health-item">
      <button className="app-health-row" type="button" aria-expanded={expanded} onClick={onToggle}>
        <span className="app-health-name">
          <strong>{profile.name}</strong>
          <span className={`app-health-badge ${status.kind}`}>{status.label}</span>
        </span>
        <span className="app-health-col app-health-status">
          <span className={`app-health-badge ${status.kind}`}>{status.label}</span>
        </span>
        <span className="app-health-col app-health-pathcol">{running ? pathPhrase(links) : "Both"}</span>
        <span className="app-health-col app-health-interval">{source.report_interval_s} s</span>
        <span className="app-health-col app-health-rtt">{source.max_rtt_ms} ms</span>
        <span className="app-health-col app-health-loss">{source.max_loss_pct}%</span>
        <span className="app-health-chevron" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span className="app-health-sub">{rowSummary(profile)}</span>
      </button>
      {expanded ? <PresetForm monitorId={monitorId} profile={profile} onSaved={onSaved} /> : null}
    </div>
  );
}

function readOpen(): string[] | null {
  try {
    const raw = sessionStorage.getItem(OPEN_KEY);
    if (raw == null) return null;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : null;
  } catch {
    return null;
  }
}

function statusOf(profile: Profile): { label: string; kind: string } {
  if (profile.continuous?.status === "running") return { label: "Running", kind: "running" };
  if (profile.continuous?.status === "pending") return { label: "Pending", kind: "pending" };
  return { label: "Off", kind: "off" };
}

function groupSummary(items: Profile[]): string {
  const running = items.filter((item) => item.continuous?.status === "running").length;
  const pending = items.filter((item) => item.continuous?.status === "pending").length;
  if (pending > 0) return `${running} running · ${pending} pending`;
  return `${running} running`;
}

function rowSummary(profile: Profile): string {
  const source = profile.continuous?.options ?? profile.options;
  const running = profile.continuous?.status === "running" || profile.continuous?.status === "pending";
  const path = running ? pathPhrase(profile.continuous?.links ?? []) : "both paths";
  return `Continuous ${running ? "on" : "off"} · ${path} · every ${source.report_interval_s} s`;
}

function pathPhrase(links: string[]): string {
  const wired = links.includes("wired");
  const wifi = links.includes("wireless");
  if (wired && wifi) return "both paths";
  if (wifi) return "Wi-Fi";
  if (wired) return "wired";
  return "both paths";
}
