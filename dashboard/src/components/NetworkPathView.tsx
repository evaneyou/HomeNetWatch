import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { isAnonymized } from "../anonymized";
import { useGuestGate } from "../guestGate";
import type { User } from "../types";
import BgpPath, { bgpVerdict } from "./BgpPath";
import PathFlow from "./PathFlow";
import SelectMenu from "./SelectMenu";
import WaitButton from "./WaitButton";
import "../pathview.css";

export type PathHop = {
  ttl: number;
  ip: string | null;
  rdns: string | null;
  silent: boolean;
  loss_pct: number | null;
  best_ms: number | null;
  avg_ms: number | null;
  worst_ms: number | null;
  asn: number | string | null;
  anon_key?: string;
  as_name: string | null;
  prefix: string | null;
  registry: string | null;
  country: string | null;
  role: string;
  name: string;
  role_label: string;
  asn_label: string;
  inferred: boolean;
};

export type PathGroup = {
  role: string;
  asn: number | string | null;
  anon_key?: string;
  name: string;
  asn_label: string;
  role_label: string;
  hop_ttls: number[];
  delta_in_ms: number | null;
};

export type AsChip = {
  type: string;
  asn: number | string | null;
  anon_key?: string;
  name: string;
  role: string;
  label: string;
};

export type PathTrace = {
  /** Only on an on-demand trace: where the agent has got to with it. */
  state?: "pending" | "running" | "done" | "failed";
  error?: string;
  status: string;
  traced_at: string | null;
  probes_per_hop: number;
  agent: { name: string; online: boolean };
  target: { name: string; ip: string };
  hops: PathHop[];
  groups: PathGroup[];
  warn_ttls: number[];
  focus_ttl: number | null;
  insight: string;
  stats: {
    hop_count: number;
    silent_count: number;
    network_count: number;
    asn_count: number;
    ixp_count: number;
    end_to_end_ms: number | null;
    loss_pct: number | null;
    biggest_step_ms: number | null;
    biggest_step_name: string | null;
  };
  as_path_observed: AsChip[];
  bgp: {
    origin_asn: number | string | null;
    anon_key?: string;
    prefix: string | null;
    rpki: string | null;
    as_path_announced: AsChip[];
    path_match: boolean | null;
    stable_days: number | null;
  };
  history: Record<string, (number | null)[]>;
};

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function dash(value: string | null | undefined): string {
  return value ? value : "—";
}

function networkLabel(label: string): string {
  return label === "AS ?" ? "Unknown network" : label;
}

export type PathChoice = { id: string; label: string };

/** The destination dropdown value that means "a host I will type in". */
const HOST_CHOICE = "__host";

export default function NetworkPathView({
  monitorId,
  link,
  destination,
  agents,
  destinations,
  agentOnline,
  onAgent,
  onDestination,
}: {
  monitorId: string;
  link: "wired" | "wireless";
  destination: string;
  agents: { id: string; name: string }[];
  destinations: PathChoice[];
  agentOnline: boolean;
  onAgent: (id: string) => void;
  onDestination: (id: string) => void;
}) {
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [group, setGroup] = useState<number | null>(null);
  const [bgpOpen, setBgpOpen] = useState(true);
  // A host someone typed in, traced on demand, instead of one of the usual destinations.
  const [hostMode, setHostMode] = useState(false);
  const [hostText, setHostText] = useState("");
  const [custom, setCustom] = useState<{ id: string; target: string } | null>(null);
  const [traceError, setTraceError] = useState("");
  const run = useGuestGate();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const pathQ = useQuery({
    queryKey: ["monitor-path", monitorId, link, destination],
    queryFn: () => api.monitorPath(monitorId, link, destination) as Promise<PathTrace>,
    enabled: Boolean(monitorId) && !custom,
  });
  const customQ = useQuery({
    queryKey: ["monitor-trace", monitorId, custom?.id],
    queryFn: () => api.monitorTrace(monitorId, custom?.id ?? "") as Promise<PathTrace>,
    enabled: Boolean(monitorId && custom),
    refetchInterval: (query) => (query.state.data && query.state.data.state !== "pending" && query.state.data.state !== "running" ? false : 1500),
  });
  const waiting = Boolean(custom) && (!customQ.data || customQ.data.state === "pending" || customQ.data.state === "running");
  const trace = custom ? (customQ.data && !waiting && customQ.data.state === "done" ? customQ.data : undefined) : pathQ.data;

  // Picking another agent, link or destination goes back to the usual path.
  useEffect(() => {
    setCustom(null);
    setTraceError("");
  }, [monitorId, link, destination]);

  const groupOf = (data: PathTrace | undefined, ttl: number | null) =>
    data && ttl != null ? data.groups.findIndex((item) => item.hop_ttls.includes(ttl)) : -1;

  useEffect(() => {
    const focus = trace?.focus_ttl ?? null;
    setSelected(focus);
    const index = groupOf(trace, focus);
    setGroup(index >= 0 ? index : null);
  }, [trace?.traced_at, trace?.focus_ttl, custom?.id]);

  function pickHop(ttl: number) {
    setSelected(ttl);
    const index = groupOf(trace, ttl);
    if (index >= 0) setGroup(index);
  }

  /** Choosing a network shows it, and its first hop that answered, or failing that its first hop. */
  function pickGroup(index: number) {
    const item = trace?.groups[index];
    if (!item) return;
    setGroup(index);
    const answered = item.hop_ttls.find((ttl) => trace?.hops.find((h) => h.ttl === ttl && !h.silent));
    setSelected(answered ?? item.hop_ttls[0] ?? null);
  }

  /** With a host typed in, trace to it now; without, refresh the path for the chosen destination. */
  async function runTrace() {
    if (running || waiting) return;
    setTraceError("");
    const typed = hostMode ? hostText.trim() : "";
    if (hostMode && !typed) {
      setTraceError("Type a host name or a public IP address to trace.");
      return;
    }
    // A guest can look but not start work on the Pi; the gate explains that instead of failing.
    if (typed && me.data?.role === "guest") {
      run(() => undefined);
      return;
    }
    setRunning(true);
    try {
      if (typed) {
        const csrf = me.data?.csrf_token;
        if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
        const queued = (await api.requestTrace(monitorId, link, typed, csrf)) as { id: string; target: string };
        setCustom({ id: queued.id, target: queued.target });
      } else {
        setCustom(null);
        await pathQ.refetch();
      }
    } catch (error) {
      let message = error instanceof Error ? error.message : "Could not start the trace.";
      try {
        const detail = (JSON.parse(message) as { detail?: unknown }).detail;
        if (typeof detail === "string") message = detail;
      } catch {
        // not JSON: show it as it is
      }
      setTraceError(message);
    } finally {
      setRunning(false);
    }
  }

  const destinationName = custom ? custom.target : (destinations.find((item) => item.id === destination)?.label ?? trace?.target.name ?? "HomeNetWatch server");
  const hop = trace?.hops.find((item) => item.ttl === selected) ?? trace?.hops[trace.hops.length - 1];
  const linkName = link === "wired" ? "Wired" : "Wi-Fi";
  // Only said when there is something to say: tracing, failed, nothing yet. A good trace needs no line.
  const status = running || waiting || (!custom && pathQ.isLoading)
    ? "Tracing…"
    : custom && customQ.data?.state === "failed"
      ? `The trace to ${destinationName} did not finish.`
      : (custom ? customQ.isError : pathQ.isError) || !trace
        ? "Could not load this path."
        : trace.hops.length === 0 || !trace.traced_at
          ? `No traceroute yet on the ${linkName} link.`
          : "";

  function chooseDestination(value: string) {
    if (value === HOST_CHOICE) {
      setHostMode(true);
      return;
    }
    setHostMode(false);
    setHostText("");
    setCustom(null);
    setTraceError("");
    onDestination(value);
  }

  return (
    <div className="npv airwaves aw pv">
      <div className="aw-top">
        <header className="aw-head">
          <h1>Path View</h1>
          {status ? (
            <p className="aw-scanned" role="status">
              <i aria-hidden="true" />
              {status}
            </p>
          ) : null}
        </header>

        <div className="aw-bar">
          <div className="aw-agent">
            <SelectMenu className="aw-select" value={monitorId} onChange={(event) => onAgent(event.target.value)} aria-label="Remote agent">
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </SelectMenu>
          </div>
          <div className="aw-agent">
            <SelectMenu
              className="aw-select"
              value={hostMode ? HOST_CHOICE : destinations.some((item) => item.id === destination) ? destination : "server"}
              onChange={(event) => chooseDestination(event.target.value)}
              aria-label="Destination"
            >
              {destinations.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
              <option value={HOST_CHOICE}>Type host or IP</option>
            </SelectMenu>
          </div>
          {!hostMode ? (
            <WaitButton
              className="aw-scan"
              busy={running || waiting}
              disabled={!agentOnline || !monitorId}
              label="Trace"
              busyLabel="Tracing"
              onClick={() => void runTrace()}
            />
          ) : null}
        </div>

        {hostMode ? (
          <div className="aw-bar">
            <div className="aw-agent">
              <input
                className="pv-host"
                type="text"
                value={hostText}
                placeholder="Host name or IP, e.g. cloudflare.com"
                aria-label="Host name or IP address to trace"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                maxLength={300}
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
                onChange={(event) => setHostText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void runTrace();
                }}
              />
            </div>
            <WaitButton
              className="aw-scan"
              busy={running || waiting}
              disabled={!agentOnline || !monitorId}
              label="Trace"
              busyLabel="Tracing"
              onClick={() => void runTrace()}
            />
          </div>
        ) : null}
      </div>

      {traceError ? (
        <div className="npv-notice silent" role="alert">
          {traceError}
        </div>
      ) : null}

      {waiting || (!custom && pathQ.isLoading) ? (
        <div className="state-box">{custom ? `Tracing to ${destinationName}…` : "Loading path…"}</div>
      ) : custom && customQ.data?.state === "failed" ? (
        <div className="state-box">{customQ.data.error || "The trace did not finish."}</div>
      ) : (custom ? customQ.isError : pathQ.isError) || !trace ? (
        <div className="state-box">Could not load the path.</div>
      ) : trace.hops.length === 0 ? (
        <section className="npv-card">
          <h2>{destinationName}</h2>
          <p className="npv-hint">This destination has not reported a traceroute yet.</p>
        </section>
      ) : (
        <>
          <StatCards trace={trace} />
          <PathStrip trace={trace} selected={hop?.ttl ?? null} onSelect={pickHop} group={group} onGroup={pickGroup} />
          <div className="pv-lower">
            <NetworkDetail trace={trace} group={group == null ? undefined : trace.groups[group]} hop={hop} onSelect={pickHop} />
            <FoldCard title="BGP view" hint="Announced path and RPKI" open={bgpOpen} onToggle={() => setBgpOpen((open) => !open)}>
              <BgpView trace={trace} headless />
            </FoldCard>
          </div>
        </>
      )}
    </div>
  );
}

function FoldCard({
  title,
  hint,
  open,
  onToggle,
  className = "",
  children,
}: {
  title: string;
  hint?: string;
  open: boolean;
  onToggle: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`npv-card npv-fold ${open ? "open" : ""} ${className}`.trim()}>
      <button type="button" className="npv-fold-toggle" aria-expanded={open} onClick={onToggle}>
        <span className="npv-fold-copy">
          <span className="npv-fold-title">{title}</span>
          {hint ? <span className="npv-fold-hint">{hint}</span> : null}
        </span>
        <svg className="npv-fold-chevron" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      </button>
      {open ? <div className="npv-fold-body">{children}</div> : null}
    </section>
  );
}

function StatCards({ trace }: { trace: PathTrace }) {
  const stats = trace.stats;
  const ixp = stats.ixp_count ? ` + ${stats.ixp_count} IXP` : "";
  const cards: { k: string; v: string; title?: string; cls?: string }[] = [
    { k: "Hops", v: String(stats.hop_count), title: `${stats.silent_count} not replying` },
    {
      k: "Networks",
      v: stats.asn_count ? `${stats.asn_count} AS${ixp}` : stats.network_count ? String(stats.network_count) : "—",
    },
    { k: "End to end", v: `${fmt(stats.end_to_end_ms)} ms`, title: `average over ${trace.probes_per_hop} probes` },
    {
      k: "Loss",
      v: stats.loss_pct == null ? "—" : `${fmt(stats.loss_pct, stats.loss_pct % 1 === 0 ? 0 : 1)}%`,
      title: "packet loss at the destination",
      cls: stats.loss_pct === 0 ? "ok" : "warn",
    },
    {
      k: "Big step",
      v: stats.biggest_step_ms == null ? "—" : `+${fmt(stats.biggest_step_ms)} ms`,
      title: stats.biggest_step_name ? `entering ${stats.biggest_step_name}` : undefined,
      cls: "warn",
    },
  ];
  return (
    <div className="pv-stats">
      {cards.map((card) => (
        <div key={card.k} className={`pv-stat ${card.cls ?? ""}${card.k === "Networks" ? " optional" : ""}`} title={card.title}>
          <span className="k">{card.k}</span>
          <span className="v">{card.v}</span>
        </div>
      ))}
    </div>
  );
}

function PathStrip({
  trace,
  selected,
  onSelect,
  group,
  onGroup,
}: {
  trace: PathTrace;
  selected: number | null;
  onSelect: (ttl: number) => void;
  group: number | null;
  onGroup: (index: number) => void;
}) {
  return (
    <section className="npv-card pv-path" aria-label="Network path">
      <div className="pv-path-head">
        <h2>Network path</h2>
        <p>Pick a network or a hop for details. Pills show the latency added at each handoff.</p>
        <div className="npv-legend">
          <span>
            <i className="npv-swatch" />
            Step of 15 ms or more
          </span>
          <span>
            <i className="npv-swatch dashed" />
            No reply
          </span>
        </div>
      </div>
      <PathFlow trace={trace} selected={selected} onSelect={onSelect} group={group} onGroup={onGroup} />
    </section>
  );
}

/** The chosen network and, under it, the chosen hop, with buttons to step through the hops. */
function NetworkDetail({
  trace,
  group,
  hop,
  onSelect,
}: {
  trace: PathTrace;
  group: PathGroup | undefined;
  hop: PathHop | undefined;
  onSelect: (ttl: number) => void;
}) {
  if (!group) return <aside className="npv-card pv-detail" />;
  const members = group.hop_ttls.map((ttl) => trace.hops.find((item) => item.ttl === ttl)).filter((item): item is PathHop => Boolean(item));
  const times = members.filter((item) => !item.silent && item.avg_ms != null).map((item) => item.avg_ms as number);
  const lo = Math.min(...times);
  const hi = Math.max(...times);
  const range = times.length ? (lo === hi ? `${fmt(lo)} ms` : `${fmt(lo)} to ${fmt(hi)} ms`) : "—";
  const inGroup = hop && group.hop_ttls.includes(hop.ttl) ? hop : undefined;
  const position = inGroup ? group.hop_ttls.indexOf(inGroup.ttl) : -1;
  const added = group.delta_in_ms == null ? "—" : `${group.delta_in_ms >= 0 ? "+" : ""}${fmt(group.delta_in_ms)} ms`;
  return (
    <aside className="npv-card pv-detail" style={{ ["--c" as string]: `var(--role-${group.role})` }} aria-label="Selected network">
      <div className="pv-dhead">
        <span className="npv-asn">{networkLabel(group.asn_label)}</span>
        <b>{group.name}</b>
        <span className="pv-role">{group.role_label}</span>
      </div>
      <div className="pv-tiles">
        <div className="npv-tile">
          <span className="k">Hops</span>
          <span className="v">{members.length}</span>
        </div>
        <div className="npv-tile">
          <span className="k">Latency</span>
          <span className="v">{range}</span>
        </div>
        <div className="npv-tile">
          <span className="k">Added</span>
          <span className="v">{added}</span>
        </div>
      </div>
      {inGroup ? (
        <div className="pv-hop">
          <div className="pv-hopnav">
            <button type="button" aria-label="Previous hop" disabled={position <= 0} onClick={() => onSelect(group.hop_ttls[position - 1])}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 6l-6 6 6 6" />
              </svg>
            </button>
            <span>
              Hop {inGroup.ttl} <small>{position + 1} of {group.hop_ttls.length} in this network</small>
            </span>
            <button type="button" aria-label="Next hop" disabled={position >= group.hop_ttls.length - 1} onClick={() => onSelect(group.hop_ttls[position + 1])}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          </div>
          <HopDetail trace={trace} hop={inGroup} headless />
        </div>
      ) : null}
      {trace.insight ? (
        <div className="pv-insight">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9.5" />
            <path d="M12 11v5.5" />
            <circle cx="12" cy="7.6" r="0.6" fill="currentColor" />
          </svg>
          <span>{trace.insight}</span>
        </div>
      ) : null}
    </aside>
  );
}

function HopDetail({ trace, hop, headless = false }: { trace: PathTrace; hop: PathHop | undefined; headless?: boolean }) {
  if (!hop) return <aside className={headless ? "npv-detail" : "npv-card npv-detail"} />;
  const registry = [hop.registry, hop.country].filter(Boolean).join(", ");
  const network = hop.silent
    ? "Unknown"
    : hop.asn
      ? `AS${hop.asn} ${hop.as_name || hop.name}`
      : hop.name;
  const rows: [string, string, boolean?][] = hop.silent
    ? [
        ["Address", "* * *", true],
        ["Reverse DNS", "—"],
        ["Network", "Unknown"],
        ["Packet loss", "100% (no replies)"],
      ]
    : [
        ["IP address", dash(hop.ip), true],
        ["Reverse DNS", dash(hop.rdns), true],
        ["Network", network],
        ["Prefix", dash(hop.prefix), true],
        ["Registry", dash(registry || null)],
        ["Packet loss", hop.loss_pct == null ? "—" : `${fmt(hop.loss_pct, hop.loss_pct % 1 === 0 ? 0 : 1)}%`],
      ];
  const history = (trace.history[String(hop.ttl)] ?? []).filter((value): value is number => value != null);
  const lo = history.length ? Math.min(...history) : 0;
  const hi = history.length ? Math.max(...history) : 0;
  return (
    <aside className={headless ? "npv-detail" : "npv-card npv-detail"} style={{ ["--c" as string]: `var(--role-${hop.role})` }}>
      {headless ? null : (
      <div className="npv-detail-head">
        <div>
          <div className="cap">Selected hop</div>
          <div className="ttl">Hop {hop.ttl}</div>
        </div>
        <span className="npv-asn">{hop.silent ? "Unknown network" : networkLabel(hop.asn_label)}</span>
      </div>
      )}
      {hop.inferred ? (
        <div className="npv-notice inferred">
          Network inferred. This address sits on an interconnect link, so the owner shown is a best guess.
        </div>
      ) : null}
      {hop.silent ? (
        <div className="npv-notice silent">
          No reply. This router ignores traceroute probes, which is common and not a fault on its own.
        </div>
      ) : null}
      <div className="npv-rows">
        {rows.map(([label, value, mono]) => (
          <div className="npv-kv" key={label}>
            <span className="k">{label}</span>
            <span className={isAnonymized(value) ? "v anon-mark" : mono ? "v mono" : "v"}>{value}</span>
          </div>
        ))}
      </div>
      {hop.silent ? null : (
        <>
          <div className="npv-tiles">
            {(
              [
                ["Min", hop.best_ms],
                ["Avg", hop.avg_ms],
                ["Max", hop.worst_ms],
              ] as const
            ).map(([label, value]) => (
              <div className="npv-tile" key={label}>
                <span className="k">{label}</span>
                <span className="v">{fmt(value)} ms</span>
              </div>
            ))}
          </div>
          {history.length > 0 ? (
            <div>
              <div className="npv-spark-title">Last {history.length} traces</div>
              <div className="npv-spark">
                {history.map((value, index) => {
                  const scale = hi === lo ? 0.5 : (value - lo) / (hi - lo);
                  return (
                    <i
                      key={`${hop.ttl}-${index}`}
                      style={{ height: Math.round(10 + scale * 34), opacity: index === history.length - 1 ? 1 : 0.55 }}
                    />
                  );
                })}
              </div>
            </div>
          ) : null}
        </>
      )}
    </aside>
  );
}

function BgpView({ trace, headless = false }: { trace: PathTrace; headless?: boolean }) {
  const bgp = trace.bgp;
  const rpki = bgp.rpki ? bgp.rpki[0].toUpperCase() + bgp.rpki.slice(1) : "—";
  const rpkiValid = bgp.rpki?.toLowerCase() === "valid";
  const verdict = bgpVerdict(trace);
  return (
    <section className={headless ? "npv-bgp" : "npv-card"}>
      {headless ? null : (
      <div>
        <h2>BGP view</h2>
        <p className="npv-hint">The networks your packets crossed, next to the path BGP says should be used for the destination prefix.</p>
      </div>
      )}
      <div className="bx-wrap">
        {verdict ? (
          <div className={`bx-verdict ${verdict.tone}`}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              {verdict.tone === "ok" ? <path d="M5 12.5l4.5 4.5L19 7.5" /> : <path d="M12 7v6M12 17h.01" />}
            </svg>
            <div>
              <b>{verdict.title}</b>
              <span>{verdict.body}</span>
            </div>
          </div>
        ) : null}
        <BgpPath trace={trace} />
        <div className="bx-facts">
          <div className="bx-fact">
            <span className="k">Origin AS</span>
            <span className="v">{bgp.origin_asn ? `AS${bgp.origin_asn}` : "—"}</span>
          </div>
          <div className="bx-fact">
            <span className="k">RPKI</span>
            <span className={`v${rpkiValid ? " ok" : ""}`}>{rpki}</span>
          </div>
          <div className="bx-fact">
            <span className="k">Path stability</span>
            <span className="v">{bgp.stable_days == null ? "—" : `${bgp.stable_days} days`}</span>
          </div>
        </div>
        <p className="bx-prefix">
          Destination prefix <b>{dash(bgp.prefix)}</b>
        </p>
      </div>
    </section>
  );
}