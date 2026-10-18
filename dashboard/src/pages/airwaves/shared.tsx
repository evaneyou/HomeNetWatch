import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import SelectMenu from "../../components/SelectMenu";
import WaitButton from "../../components/WaitButton";
import { useGuestGate } from "../../guestGate";
import "../../airwaves.css";
import type { MonitorAdmin, RfOverview, User } from "../../types";

/** Sparklines and history charts. Lists use the server default, which is the same window. */
export const AIR_TREND_RANGE = "1h";

export type AirContext = {
  agent: MonitorAdmin;
  operate: boolean;
  refresh: boolean;
  overview: RfOverview | undefined;
  overviewLoading: boolean;
  overviewError: boolean;
};

export function scanLabel(lastScan: string | null | undefined): string {
  if (!lastScan) return "No scans yet";
  return `Last scan ${new Date(lastScan).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
}

/** "Scanned 6:57 PM" today, "Scanned Oct 14, 6:57 PM" on another day. */
export function scannedShort(lastScan: string | null | undefined): string {
  if (!lastScan) return "No scans yet";
  const when = new Date(lastScan);
  const today = new Date().toDateString() === when.toDateString();
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return today ? `Scanned ${time}` : `Scanned ${when.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

/** Four bars that fill with signal strength, the way a phone shows Wi-Fi. */
export function SignalBars({ dbm }: { dbm: number }) {
  const level = dbm >= -55 ? 4 : dbm >= -65 ? 3 : dbm >= -75 ? 2 : 1;
  return (
    <svg className="aw-bars" width="24" height="18" viewBox="0 0 24 18" aria-hidden="true">
      {[0, 1, 2, 3].map((index) => (
        <rect key={index} x={index * 6} y={12 - index * 4} width="4" height={6 + index * 4} rx="1" className={index < level ? "on" : undefined} />
      ))}
    </svg>
  );
}

/** Asks the agent for a fresh scan. Admins and guests see it; a guest gets the read-only notice. */
export function ScanNowButton({ ctx }: { ctx: AirContext }) {
  const run = useGuestGate();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const scan = useMutation({
    mutationFn: () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.rfScanNow(ctx.agent.id, csrf);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["rf-overview", ctx.agent.id] });
    },
  });
  if (!ctx.operate) return null;
  const waiting = scan.isPending || Boolean(ctx.overview?.scan_requested);
  return (
    <WaitButton
      className="aw-scan"
      busy={waiting}
      label="Scan"
      busyLabel="Scanning"
      onClick={() => run(() => scan.mutate())}
    />
  );
}

export function useAirwaves(): AirContext | "loading" | "error" | "empty" {
  const [params, setParams] = useSearchParams();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
  });
  const monitors = monitorsQ.data ?? [];
  const requested = params.get("agent");
  const agent = monitors.find((item) => item.id === requested) ?? monitors[0];
  const refresh = params.get("refresh") !== "off";

  useEffect(() => {
    if (!agent) return;
    if (!params.has("range") && requested === agent.id) return;
    const next = new URLSearchParams(params);
    next.delete("range");
    next.set("agent", agent.id);
    setParams(next, { replace: true });
  }, [agent, requested, params, setParams]);

  const overviewQ = useQuery({
    queryKey: ["rf-overview", agent?.id],
    queryFn: () => api.rfOverview(agent.id),
    enabled: Boolean(agent),
    refetchInterval: refresh ? 15_000 : false,
  });

  if (monitorsQ.isLoading || me.isLoading) return "loading";
  if (monitorsQ.isError || me.isError) return "error";
  if (!agent || !me.data) return "empty";

  return {
    agent,
    operate: me.data.role === "admin" || me.data.role === "guest",
    refresh,
    overview: overviewQ.data,
    overviewLoading: overviewQ.isLoading,
    overviewError: overviewQ.isError,
  };
}

export default function AirwavesLayout() {
  const state = useAirwaves();
  const [params, setParams] = useSearchParams();
  const loc = useLocation();
  const coarse = useCoarsePointer();

  if (state === "loading") return <div className="state-box">Loading airwaves…</div>;
  if (state === "error") return <div className="state-box">Could not load airwave data.</div>;
  if (state === "empty") return <div className="state-box">No remote agents yet.</div>;

  const { agent, overview, overviewLoading, overviewError } = state;

  function setQuery(patch: Record<string, string>) {
    const next = new URLSearchParams(params);
    next.delete("range");
    for (const [key, value] of Object.entries(patch)) next.set(key, value);
    setParams(next);
  }

  function pageHref(path: string) {
    const next = new URLSearchParams(params);
    next.delete("range");
    next.set("agent", agent.id);
    const query = next.toString();
    return query ? `${path}?${query}` : path;
  }

  const pages = [
    { path: "/airwaves", label: "Overview" },
    { path: "/airwaves/networks", label: "Networks" },
    { path: "/airwaves/clients", label: "Clients" },
    { path: "/airwaves/channels", label: "Channels" },
  ];

  return (
    <div className="npv airwaves aw">
      <div className="aw-top">
      <header className="aw-head">
        <h1>Airwaves</h1>
        <p className="aw-scanned" role="status">
          <i aria-hidden="true" />
          {overviewLoading ? "Checking scans…" : scannedShort(overview?.last_scan_at)}
        </p>
      </header>

      <div className="aw-bar">
        <div className="aw-agent">
          {coarse ? (
            <select className="aw-select" aria-label="Remote agent" value={agent.id} onChange={(event) => setQuery({ agent: event.target.value })}>
              <AgentOptions />
            </select>
          ) : (
            <SelectMenu className="aw-select" aria-label="Remote agent" value={agent.id} onChange={(event) => setQuery({ agent: event.target.value })}>
              <AgentOptions />
            </SelectMenu>
          )}
        </div>
        <button
          className={state.refresh ? "aw-toggle on" : "aw-toggle"}
          type="button"
          aria-label="Refresh every 15 seconds"
          aria-pressed={state.refresh}
          onClick={() => setQuery({ refresh: state.refresh ? "off" : "on" })}
        >
          <i aria-hidden="true" />
          <span>15 s</span>
        </button>
        <ScanNowButton ctx={state} />
      </div>
      </div>

      {overviewError ? <div className="error">Could not load the latest scan.</div> : null}

      <nav className="aw-tabs" aria-label="Airwaves sections">
        {pages.map((page) => (
          <Link
            key={page.path}
            to={pageHref(page.path)}
            className={loc.pathname === page.path ? "aw-tab active" : "aw-tab"}
            aria-current={loc.pathname === page.path ? "page" : undefined}
          >
            {page.label}
          </Link>
        ))}
      </nav>


      {overview?.headline && loc.pathname.replace(/\/$/, "") === "/airwaves" ? <p className="aw-advice">{overview.headline}</p> : null}
      <Outlet context={state} />
    </div>
  );
}

function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(pointer: coarse)");
    const update = () => setCoarse(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return coarse;
}

function AgentOptions() {
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
  });
  return (
    <>
      {(monitorsQ.data ?? []).map((monitor) => (
        <option key={monitor.id} value={monitor.id}>
          {monitor.name}
        </option>
      ))}
    </>
  );
}
