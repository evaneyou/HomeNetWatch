import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import SelectMenu from "../components/SelectMenu";
import WaitButton from "../components/WaitButton";
import { useGuestGate } from "../guestGate";
import type { MonitorAdmin, User } from "../types";
import "../airwaves.css";
import "../networktest.css";

type Field = {
  key: string;
  label: string;
  type: "choice" | "number" | "bool";
  default: string | number | boolean;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  choices?: { value: string | number; label: string }[];
};
type Test = { kind: string; name: string; description: string; fields: Field[] };
type Catalog = { tests: Test[]; max_seconds: number; max_sessions: number; max_count: number };
type Params = Record<string, string | number | boolean>;
type LinkName = "wired" | "wireless";
type LinkNow = { tx_mbps: number; rtt_ms: number | null; jitter_ms: number | null; loss_pct: number | null };
type Session = { id: string; kind: string; label: string; status: string; error: string | null; links: Record<string, LinkNow> };
type Point = {
  t: string;
  link: LinkName;
  tx_mbps: number;
  rx_mbps: number;
  rtt_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number | null;
  sessions: number;
};
type Live = {
  id: string;
  links: LinkName[];
  status: string;
  started_at: string;
  ends_at: string;
  finished_at: string | null;
  sessions: Session[];
  series: Point[];
};
type Planned = { key: number; kind: string; params: Params; count: number };

const DURATIONS = [30, 60, 120, 180, 240, 300];
const COLORS: Record<LinkName, string> = { wired: "#4fd1b5", wireless: "#ffc24d" };
const NAMES: Record<LinkName, string> = { wired: "Wired", wireless: "Wi-Fi" };

function clock(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(digits);
}

function defaults(test: Test): Params {
  return Object.fromEntries(test.fields.map((field) => [field.key, field.default]));
}

/** The settings that differ from the defaults, for the planned list. */
function summary(test: Test, params: Params): string {
  const parts: string[] = [];
  for (const field of test.fields) {
    const value = params[field.key];
    if (value === field.default) continue;
    if (field.type === "bool") parts.push(`${field.label.split(" (")[0]}: ${value ? "on" : "off"}`);
    else if (field.type === "choice") parts.push(field.choices?.find((item) => item.value === value)?.label ?? String(value));
    else parts.push(`${value}${field.unit ? ` ${field.unit}` : ""}`);
  }
  return parts.join(" · ");
}

/** Polls a stress test and keeps every point of its series, fetching only what is new. */
function useLive(stressId: string | null) {
  const [live, setLive] = useState<Live | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [failed, setFailed] = useState(false);
  const since = useRef<string | undefined>(undefined);

  const poll = useCallback(async () => {
    if (!stressId) return null;
    try {
      const body = (await api.stressLive(stressId, since.current)) as Live;
      setFailed(false);
      setLive(body);
      if (body.series.length) {
        since.current = body.series[body.series.length - 1].t;
        setPoints((old) => {
          const seen = new Set(old.map((item) => `${item.link}:${item.t}`));
          return [...old, ...body.series.filter((item) => !seen.has(`${item.link}:${item.t}`))];
        });
      }
      return body;
    } catch {
      setFailed(true);
      return null;
    }
  }, [stressId]);

  useEffect(() => {
    since.current = undefined;
    setPoints([]);
    setLive(null);
    if (!stressId) return undefined;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      const body = await poll();
      if (stopped) return;
      if (body && body.status !== "running") {
        // One more read picks up samples that arrived while it stopped.
        timer = window.setTimeout(() => void poll(), 3000);
        return;
      }
      timer = window.setTimeout(() => void tick(), 1500);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [stressId, poll]);

  return { live, points, failed, refresh: poll };
}

export default function NetworkTestPage() {
  const run = useGuestGate();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const monitorsQ = useQuery({ queryKey: ["monitors"], queryFn: () => api.monitors() as Promise<MonitorAdmin[]> });
  const catalogQ = useQuery({ queryKey: ["stress-catalog"], queryFn: () => api.stressCatalog() as Promise<Catalog>, staleTime: Infinity });
  const [agentId, setAgentId] = useState("");
  const [links, setLinks] = useState<LinkName[]>(["wired"]);
  const [seconds, setSeconds] = useState(120);
  const [planned, setPlanned] = useState<Planned[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stressId, setStressId] = useState<string | null>(null);
  const nextKey = useRef(1);

  const monitors = monitorsQ.data ?? [];
  const agent = monitors.find((item) => item.id === agentId) ?? monitors[0];

  const activeQ = useQuery({
    queryKey: ["stress-active", agent?.id],
    queryFn: () => api.stressActive(agent.id) as Promise<{ stress_id: string | null }>,
    enabled: Boolean(agent),
    refetchInterval: stressId ? false : 8000,
  });
  useEffect(() => {
    if (activeQ.data?.stress_id && !stressId) setStressId(activeQ.data.stress_id);
  }, [activeQ.data, stressId]);

  const { live, points, failed, refresh } = useLive(stressId);
  const running = live?.status === "running";
  const catalog = catalogQ.data;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);

  const csrf = me.data?.csrf_token;
  async function act(work: (token: string) => Promise<unknown>) {
    setError("");
    if (!csrf) {
      setError("The session is missing a CSRF token. Reload the page.");
      return;
    }
    setBusy(true);
    try {
      await work(csrf);
    } catch (err) {
      const text = err instanceof Error ? err.message : "That did not work.";
      try {
        const parsed = JSON.parse(text) as { detail?: unknown };
        setError(typeof parsed.detail === "string" ? parsed.detail : text);
      } catch {
        setError(text);
      }
    } finally {
      setBusy(false);
    }
  }

  function addTest(kind: string, params: Params, count: number) {
    if (running && live) {
      run(() => void act(async (token) => {
        await api.stressAdd(live.id, { kind, params, count }, token);
        await refresh();
      }));
      return;
    }
    setPlanned((old) => [...old, { key: nextKey.current++, kind, params, count }]);
  }

  function start() {
    if (!agent || planned.length === 0) return;
    run(() => void act(async (token) => {
      const body = (await api.stressStart(
        { monitor_id: agent.id, links, duration_seconds: seconds, sessions: planned.map((item) => ({ kind: item.kind, params: item.params, count: item.count })) },
        token,
      )) as { stress_id: string };
      setPlanned([]);
      setStressId(body.stress_id);
      void qc.invalidateQueries({ queryKey: ["stress-active"] });
    }));
  }

  function stopAll() {
    if (!live) return;
    run(() => void act(async (token) => {
      await api.stressStop(live.id, token);
      await refresh();
    }));
  }

  function removeSession(id: string) {
    if (!live) return;
    run(() => void act(async (token) => {
      await api.stressRemove(live.id, id, token);
      await refresh();
    }));
  }

  function reset() {
    setStressId(null);
    void qc.invalidateQueries({ queryKey: ["stress-active"] });
  }

  function toggleLink(link: LinkName) {
    setLinks((old) => (old.includes(link) ? (old.length > 1 ? old.filter((item) => item !== link) : old) : [...old, link]));
  }

  if (monitorsQ.isLoading || catalogQ.isLoading) return <div className="state-box">Loading Network Test…</div>;
  if (monitorsQ.isError || catalogQ.isError || !catalog) return <div className="state-box">Could not load Network Test.</div>;
  if (!agent) return <div className="state-box">No remote agents yet.</div>;

  const total = live ? (new Date(live.ends_at).getTime() - new Date(live.started_at).getTime()) / 1000 : 0;
  const left = live ? (new Date(live.ends_at).getTime() - now) / 1000 : 0;
  const ended = live != null && live.status !== "running";
  const status = running
    ? `Running · ${clock(left)} left`
    : ended
      ? live.status === "stopped"
        ? "Stopped"
        : "Finished"
      : agent.status === "online"
        ? ""
        : "Agent offline";
  const tests = new Map(catalog.tests.map((item) => [item.kind, item]));
  const activeSessions = live?.sessions.filter((item) => item.status === "running" || item.status === "pending").length ?? 0;

  return (
    <div className="npv airwaves aw nt">
      <div className="aw-top">
        <header className="aw-head">
          <h1>Network Test</h1>
          {status ? (
            <p className="aw-scanned" role="status">
              <i aria-hidden="true" />
              {status}
            </p>
          ) : null}
        </header>

        <div className="aw-bar">
          <div className="aw-agent">
            <SelectMenu className="aw-select" aria-label="Remote agent" value={agent.id} disabled={running} onChange={(event) => { setAgentId(event.target.value); setStressId(null); }}>
              {monitors.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </SelectMenu>
          </div>
          {running ? (
            <button className="nt-stop" type="button" disabled={busy} onClick={stopAll}>
              Stop all
            </button>
          ) : ended ? (
            <button className="aw-scan" type="button" onClick={reset}>
              New test
            </button>
          ) : (
            <WaitButton
              className="aw-scan"
              busy={busy}
              disabled={agent.status !== "online" || planned.length === 0}
              label="Run Stress Test"
              busyLabel="Starting"
              onClick={start}
            />
          )}
        </div>
      </div>

      {!running && !ended ? (
        <div className="nt-setup">
          <div className="aw-pills" role="group" aria-label="Links to stress">
            {(["wired", "wireless"] as const).map((link) => (
              <button key={link} className={links.includes(link) ? "aw-pill active" : "aw-pill"} type="button" aria-pressed={links.includes(link)} onClick={() => toggleLink(link)}>
                {NAMES[link]}
              </button>
            ))}
          </div>
          <label className="nt-duration">
            <SelectMenu className="aw-select" aria-label="Run length" value={String(seconds)} onChange={(event) => setSeconds(Number(event.target.value))}>
              {DURATIONS.filter((item) => item <= catalog.max_seconds).map((item) => (
                <option key={item} value={item}>
                  {item < 60 ? `${item} seconds` : `${item / 60} minute${item === 60 ? "" : "s"}`}
                </option>
              ))}
            </SelectMenu>
          </label>
        </div>
      ) : null}

      {error ? <div className="error">{error}</div> : null}
      {failed && stressId ? <div className="error">Lost contact with the server. Retrying.</div> : null}

      {live ? (
        <>
          {running ? (
            <div className="nt-progress" aria-hidden="true">
              <i style={{ width: `${Math.min(100, Math.max(0, ((total - left) / total) * 100))}%` }} />
            </div>
          ) : null}
          <NowTiles points={points} links={live.links} />
          <div className="nt-charts">
            <Chart title="Throughput" unit="Mbit/s" field="tx_mbps" points={points} links={live.links} />
            <Chart title="Round trip" unit="ms" field="rtt_ms" points={points} links={live.links} />
            <Chart title="Jitter" unit="ms" field="jitter_ms" points={points} links={live.links} />
            <Chart title="Packet loss" unit="%" field="loss_pct" points={points} links={live.links} />
          </div>
        </>
      ) : null}

      {live ? (
        <section className="nt-card" aria-label="Sessions">
          <h2>
            Sessions <small>{activeSessions} running</small>
          </h2>
          {live.sessions.length === 0 ? <p className="nt-empty">No sessions.</p> : null}
          <ul className="nt-sessions">
            {live.sessions.map((item) => {
              const live1 = item.status === "running" || item.status === "pending";
              return (
                <li key={item.id} className={live1 ? "" : "is-off"}>
                  <div className="nt-s-head">
                    <b>{item.label}</b>
                    <span className={`nt-chip ${item.status}`}>{item.status}</span>
                    {running && live1 ? (
                      <button type="button" className="nt-x" aria-label={`Remove ${item.label}`} disabled={busy} onClick={() => removeSession(item.id)}>
                        ×
                      </button>
                    ) : null}
                  </div>
                  {item.error ? <p className="nt-err">{item.error}</p> : null}
                  <div className="nt-s-links">
                    {live.links.map((link) => {
                      const now1 = item.links[link];
                      return (
                        <span key={link}>
                          <i style={{ background: COLORS[link] }} aria-hidden="true" />
                          {NAMES[link]} {now1 ? `${fmt(now1.tx_mbps)} Mbit/s · ${fmt(now1.rtt_ms)} ms · jitter ${fmt(now1.jitter_ms)} · loss ${fmt(now1.loss_pct)}%` : "waiting"}
                        </span>
                      );
                    })}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : planned.length > 0 ? (
        <section className="nt-card" aria-label="Planned sessions">
          <h2>
            Planned <small>{planned.reduce((sum, item) => sum + item.count, 0)} sessions</small>
          </h2>
          <ul className="nt-sessions">
            {planned.map((item) => {
              const test = tests.get(item.kind);
              if (!test) return null;
              return (
                <li key={item.key}>
                  <div className="nt-s-head">
                    <b>
                      {test.name} × {item.count}
                    </b>
                    <button type="button" className="nt-x" aria-label={`Remove ${test.name}`} onClick={() => setPlanned((old) => old.filter((row) => row.key !== item.key))}>
                      ×
                    </button>
                  </div>
                  <div className="nt-s-links">{summary(test, item.params) || "Default settings"}</div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {!ended ? (
        <section className="nt-card" aria-label="Tests">
          <h2>
            {running ? "Add tests while it runs" : "Choose tests"} <small>pick as many sessions of each as you like</small>
          </h2>
          <div className="nt-tests">
            {catalog.tests.map((test) => (
              <TestCard key={test.kind} test={test} maxCount={catalog.max_count} adding={busy} onAdd={(params, count) => addTest(test.kind, params, count)} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function TestCard({ test, maxCount, adding, onAdd }: { test: Test; maxCount: number; adding: boolean; onAdd: (params: Params, count: number) => void }) {
  const [open, setOpen] = useState(false);
  const [params, setParams] = useState<Params>(() => defaults(test));
  const [count, setCount] = useState(1);
  return (
    <article className="nt-test">
      <div className="nt-t-head">
        <div>
          <b>{test.name}</b>
          <p>{test.description}</p>
        </div>
      </div>
      {test.fields.length > 0 ? (
        <button type="button" className="nt-more" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? "Hide options" : "Options"}
        </button>
      ) : null}
      {open ? (
        <div className="nt-fields">
          {test.fields.map((field) => (
            <label key={field.key} className={field.type === "bool" ? "nt-field bool" : "nt-field"}>
              <span>
                {field.label}
                {field.unit ? <small> ({field.unit})</small> : null}
              </span>
              {field.type === "bool" ? (
                <input type="checkbox" checked={Boolean(params[field.key])} onChange={(event) => setParams({ ...params, [field.key]: event.target.checked })} />
              ) : field.type === "choice" ? (
                <select
                  value={String(params[field.key])}
                  onChange={(event) => {
                    const chosen = field.choices?.find((item) => String(item.value) === event.target.value);
                    setParams({ ...params, [field.key]: chosen ? chosen.value : event.target.value });
                  }}
                >
                  {field.choices?.map((item) => (
                    <option key={String(item.value)} value={String(item.value)}>
                      {item.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  inputMode="numeric"
                  min={field.min}
                  max={field.max}
                  step={field.step ?? 1}
                  value={Number(params[field.key])}
                  onChange={(event) => setParams({ ...params, [field.key]: Number(event.target.value) })}
                />
              )}
            </label>
          ))}
        </div>
      ) : null}
      <div className="nt-add">
        <div className="nt-count" role="group" aria-label="Sessions">
          <button type="button" aria-label="Fewer sessions" disabled={count <= 1} onClick={() => setCount(count - 1)}>
            −
          </button>
          <span>{count}</span>
          <button type="button" aria-label="More sessions" disabled={count >= maxCount} onClick={() => setCount(count + 1)}>
            +
          </button>
        </div>
        <button type="button" className="nt-addbtn" disabled={adding} onClick={() => onAdd(params, count)}>
          Add {count > 1 ? `${count} sessions` : "session"}
        </button>
      </div>
    </article>
  );
}

function NowTiles({ points, links }: { points: Point[]; links: LinkName[] }) {
  const latest = (link: LinkName) => {
    const rows = points.filter((item) => item.link === link);
    return rows.length ? rows[rows.length - 1] : undefined;
  };
  const tiles: { k: string; unit: string; field: "tx_mbps" | "rtt_ms" | "jitter_ms" | "loss_pct" }[] = [
    { k: "Throughput", unit: "Mbit/s", field: "tx_mbps" },
    { k: "Round trip", unit: "ms", field: "rtt_ms" },
    { k: "Jitter", unit: "ms", field: "jitter_ms" },
    { k: "Loss", unit: "%", field: "loss_pct" },
  ];
  return (
    <div className="nt-tiles">
      {tiles.map((tile) => (
        <div className="nt-tile" key={tile.k}>
          <span className="k">{tile.k}</span>
          {links.map((link) => (
            <span className="v" key={link} style={{ color: COLORS[link] }}>
              {fmt(latest(link)?.[tile.field])}
              <small> {tile.unit}</small>
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function Chart({ title, unit, field, points, links }: { title: string; unit: string; field: "tx_mbps" | "rtt_ms" | "jitter_ms" | "loss_pct"; points: Point[]; links: LinkName[] }) {
  const data = useMemo(() => {
    const byTime = new Map<number, Record<string, number | null>>();
    for (const item of points) {
      const ts = new Date(item.t).getTime();
      const row = byTime.get(ts) ?? { ts };
      row[item.link] = item[field];
      byTime.set(ts, row);
    }
    return [...byTime.values()].sort((a, b) => (a.ts as number) - (b.ts as number));
  }, [points, field]);
  const first = data.length ? (data[0].ts as number) : 0;
  return (
    <section className="nt-card nt-chart" aria-label={title}>
      <h2>
        {title} <small>{unit}</small>
      </h2>
      <div className="nt-plot">
        {data.length === 0 ? (
          <p className="nt-empty">Waiting for the first numbers…</p>
        ) : (
          <ResponsiveContainer width="100%" height={170}>
            <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#232d49" vertical={false} />
              <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(value: number) => clock((value - first) / 1000)} stroke="#9aa6c4" fontSize={11} tickLine={false} minTickGap={36} />
              <YAxis stroke="#9aa6c4" fontSize={11} tickLine={false} axisLine={false} width={40} domain={[0, "auto"]} />
              <Tooltip
                contentStyle={{ background: "#161d2f", border: "1px solid #232d49", borderRadius: 10, fontSize: 12 }}
                labelFormatter={(value) => clock((Number(value) - first) / 1000)}
                formatter={(value, name) => [`${fmt(typeof value === "number" ? value : null)} ${unit}`, name]}
              />
              {links.length > 1 ? <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} /> : null}
              {links.map((link) => (
                <Line key={link} name={NAMES[link]} dataKey={link} stroke={COLORS[link]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}
