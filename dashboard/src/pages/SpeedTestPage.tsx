import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { api } from "../api";
import SelectMenu from "../components/SelectMenu";
import SpeedFlow, { type SpeedPhase } from "../components/SpeedFlow";
import { useGuestGate } from "../guestGate";
import type { MonitorAdmin, User } from "../types";
import "../speedtest.css";

type Link = "wired" | "wireless";
type Test = {
  id: string;
  monitor_id: string;
  link: Link;
  status: "pending" | "running" | "finished" | "failed" | "cancelled";
  phase: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  down_seconds: number;
  up_seconds: number;
  down_mbps: number | null;
  up_mbps: number | null;
  ping_ms: number | null;
  jitter_ms: number | null;
  error: string;
};
type Sample = { seq: number; phase: "download" | "upload"; t_ms: number; mbps: number };
type Live = Test & { samples: Sample[] };

const TICK_MS = 500;
const LINK_LABEL: Record<Link, string> = { wired: "Wired", wireless: "Wi-Fi" };

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(digits);
}

function messageOf(err: unknown): string {
  const text = err instanceof Error ? err.message : "That did not work.";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // not JSON: show the text as it is
  }
  return text;
}

/** Eases a number toward its target so the big readout climbs instead of jumping. */
function useTween(target: number): number {
  const [value, setValue] = useState(0);
  const current = useRef(0);
  useEffect(() => {
    let frame = 0;
    const step = () => {
      const next = current.current + (target - current.current) * 0.18;
      current.current = Math.abs(target - next) < 0.05 ? target : next;
      setValue(current.current);
      if (current.current !== target) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return value;
}

const DEMO = "demo";

/** A made-up test for the demo button: the numbers ramp up like a real one, with a little noise. */
function runDemo(link: Link, setLive: (live: Live) => void, setSamples: (samples: Sample[]) => void): () => void {
  const top = link === "wired" ? { download: 468, upload: 34 } : { download: 212, upload: 46 };
  const ticks = 30;
  const all: Sample[] = [];
  const at = new Date().toISOString();
  let index = 0;
  const base = { id: DEMO, monitor_id: "", link, requested_at: at, started_at: at, down_seconds: 15, up_seconds: 15, ping_ms: 8.4, jitter_ms: 1.1, error: "", samples: [] as Sample[] };
  const publish = (status: Live["status"], phase: string, down: number | null, up: number | null) =>
    setLive({ ...base, status, phase, finished_at: status === "finished" ? new Date().toISOString() : null, down_mbps: down, up_mbps: up });
  publish("running", "ping", null, null);
  const timer = window.setInterval(() => {
    index += 1;
    if (index <= 2) return; // a second of "latency", as the real test does
    const step = index - 3;
    const phase = step < ticks ? "download" : "upload";
    const within = (phase === "download" ? step : step - ticks) * 0.5 + 0.5;
    const rate = (phase === "download" ? top.download : top.upload) * (1 - Math.exp(-within / (phase === "download" ? 1.5 : 1.1))) * (1 + (Math.random() - 0.5) * 0.06);
    all.push({ seq: all.length, phase: phase as Sample["phase"], t_ms: Math.round(within * 1000), mbps: Math.round(rate * 100) / 100 });
    setSamples([...all]);
    if (step + 1 < ticks * 2) {
      publish("running", phase, null, null);
      return;
    }
    window.clearInterval(timer);
    const steady = (name: string) => {
      const mine = all.filter((item) => item.phase === name && item.t_ms >= 3000);
      return mine.reduce((sum, item) => sum + item.mbps, 0) / Math.max(1, mine.length);
    };
    publish("finished", "done", Math.round(steady("download") * 100) / 100, Math.round(steady("upload") * 100) / 100);
  }, TICK_MS);
  return () => window.clearInterval(timer);
}

/** Follows one test: asks for new readings twice a second until it ends. */
function useLiveTest(testId: string | null, link: Link) {
  const [live, setLive] = useState<Live | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const last = useRef(-1);

  useEffect(() => {
    last.current = -1;
    setSamples([]);
    setLive(null);
    if (!testId) return undefined;
    if (testId === DEMO) return runDemo(link, setLive, setSamples);
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const body = (await api.speedtestRead(testId, last.current)) as Live;
        if (stopped) return;
        setLive(body);
        if (body.samples.length) {
          last.current = body.samples[body.samples.length - 1].seq;
          setSamples((old) => [...old, ...body.samples.filter((item) => !old.some((known) => known.seq === item.seq))]);
        }
        if (body.status === "finished" || body.status === "failed" || body.status === "cancelled") return;
      } catch {
        // a missed read is made up for by the next one
      }
      if (!stopped) timer = window.setTimeout(() => void tick(), TICK_MS);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [testId, link]);

  return { live, samples };
}

function slots(seconds: number, phase: "download" | "upload", samples: Sample[]): { label: string; mbps: number | null; fresh: boolean }[] {
  const count = Math.max(1, Math.round((seconds * 1000) / TICK_MS));
  const mine = samples.filter((item) => item.phase === phase);
  return Array.from({ length: count }, (_unused, index) => {
    const item = mine[index];
    const second = ((index + 1) * TICK_MS) / 1000;
    return { label: Number.isInteger(second) && second % 5 === 0 ? `${second}s` : "", mbps: item ? item.mbps : null, fresh: item != null && index === mine.length - 1 };
  });
}

function SpeedBars({ title, color, seconds, phase, samples, active, steady }: { title: string; color: string; seconds: number; phase: "download" | "upload"; samples: Sample[]; active: boolean; steady: number | null }) {
  const data = useMemo(() => slots(seconds, phase, samples), [seconds, phase, samples]);
  const peak = Math.max(0, ...data.map((item) => item.mbps ?? 0));
  const top = Math.max(100, Math.ceil((peak * 1.15) / 50) * 50);
  return (
    <section className={active ? "st-panel is-live" : "st-panel"}>
      <header>
        <h3>{title}</h3>
        <span>
          {samples.some((item) => item.phase === phase) ? `Peak ${fmt(peak)} Mbit/s${steady != null ? ` · steady ${fmt(steady)}` : ""}` : "Waiting"}
        </span>
      </header>
      <ResponsiveContainer width="100%" height={210}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="12%">
          <CartesianGrid stroke="#232d49" vertical={false} />
          <XAxis dataKey="label" stroke="#9aa6c4" tick={{ fontSize: 11 }} tickLine={false} interval={0} />
          <YAxis stroke="#9aa6c4" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={44} domain={[0, top]} />
          {steady != null ? <ReferenceLine y={steady} stroke="#ffc24d" strokeDasharray="5 4" /> : null}
          <Bar dataKey="mbps" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((item, index) => (
              <Cell key={index} fill={color} fillOpacity={item.fresh && active ? 1 : 0.78} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}

/** The mean of the readings after the first three seconds, which is the speed once it has settled. */
function steadySpeed(samples: Sample[], phase: "download" | "upload"): number | null {
  const mine = samples.filter((item) => item.phase === phase);
  const settled = mine.filter((item) => item.t_ms >= 3000);
  const use = settled.length ? settled : mine;
  return use.length >= 4 ? use.reduce((sum, item) => sum + item.mbps, 0) / use.length : null;
}

export default function SpeedTestPage() {
  const run = useGuestGate();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const monitorsQ = useQuery({ queryKey: ["monitors"], queryFn: () => api.monitors() as Promise<MonitorAdmin[]> });
  const [agentId, setAgentId] = useState("");
  const [draft, setDraft] = useState<string | null>(null); // the picker's choice, applied by Display
  const [link, setLink] = useState<Link>("wired");
  const [testId, setTestId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const monitors = monitorsQ.data ?? [];
  const agent = monitors.find((item) => item.id === agentId) ?? monitors[0];
  const sensorValue = draft ?? agent?.id ?? "";

  const activeQ = useQuery({
    queryKey: ["speedtest-active", agent?.id],
    queryFn: () => api.speedtestActive(agent.id) as Promise<{ test: Test | null }>,
    enabled: Boolean(agent) && !testId,
    refetchInterval: 5000,
  });
  useEffect(() => {
    const found = activeQ.data?.test;
    if (found && !testId) {
      setTestId(found.id);
      setLink(found.link);
    }
  }, [activeQ.data, testId]);

  const historyQ = useQuery({
    queryKey: ["speedtest-history", agent?.id],
    queryFn: () => api.speedtestHistory(agent.id) as Promise<Test[]>,
    enabled: Boolean(agent),
  });
  const { live, samples } = useLiveTest(testId, link);

  const status = live?.status ?? "idle";
  const running = status === "pending" || status === "running";
  const finished = status === "finished" || status === "failed" || status === "cancelled";
  const phase: SpeedPhase = !live ? "idle" : status === "pending" ? "waiting" : finished ? "done" : live.phase === "download" || live.phase === "upload" ? live.phase : "ping";
  const latest = samples.length ? samples[samples.length - 1] : null;
  const instant = phase === "download" || phase === "upload" ? (latest && latest.phase === phase ? latest.mbps : 0) : 0;
  const shown = useTween(instant);
  const downSeconds = live?.down_seconds ?? 15;
  const upSeconds = live?.up_seconds ?? 15;
  const total = downSeconds + upSeconds;
  const elapsed = latest ? (latest.phase === "download" ? latest.t_ms : downSeconds * 1000 + latest.t_ms) / 1000 : 0;
  const progress = finished && status === "finished" ? 1 : Math.min(1, elapsed / total);
  const left = Math.max(0, Math.ceil(total - elapsed));
  const downSteady = status === "finished" && live?.down_mbps != null ? live.down_mbps : steadySpeed(samples, "download");
  const upSteady = status === "finished" && live?.up_mbps != null ? live.up_mbps : steadySpeed(samples, "upload");
  const csrf = me.data?.csrf_token ?? "";
  const isGuest = me.data?.role === "guest";

  const headline =
    phase === "idle"
      ? "Ready to test"
      : phase === "waiting"
        ? "Waiting for the Pi to start"
        : phase === "ping"
          ? "Measuring latency"
          : phase === "download"
            ? "Download: measuring receive speed"
            : phase === "upload"
              ? "Upload: measuring send speed"
              : status === "finished"
                ? "Test complete"
                : status === "cancelled"
                  ? "Test stopped"
                  : "Test failed";

  function start() {
    if (!agent) return;
    // A guest cannot run a real test, so Start plays the simulated one, labelled as a demo.
    if (isGuest) {
      startDemo();
      return;
    }
    run(() => {
      setBusy(true);
      setError("");
      api
        .speedtestStart({ monitor_id: agent.id, link }, csrf)
        .then((body) => {
          setTestId((body as Test).id);
        })
        .catch((err) => setError(messageOf(err)))
        .finally(() => setBusy(false));
    });
  }

  function startDemo() {
    setError("");
    setTestId(DEMO);
  }

  function stop() {
    if (!testId) return;
    if (testId === DEMO) {
      setTestId(null);
      return;
    }
    setBusy(true);
    api
      .speedtestCancel(testId, csrf)
      .catch((err) => setError(messageOf(err)))
      .finally(() => {
        setBusy(false);
        void qc.invalidateQueries({ queryKey: ["speedtest-history"] });
      });
  }

  // A finished test goes into the history list.
  useEffect(() => {
    if (finished && testId !== DEMO) void qc.invalidateQueries({ queryKey: ["speedtest-history"] });
  }, [finished, qc, testId]);

  if (monitorsQ.isLoading) return <div className="state-box">Loading Speed Test…</div>;
  if (monitorsQ.isError) return <div className="state-box">Could not load Speed Test.</div>;
  if (!agent) return <div className="state-box">No remote agents yet.</div>;

  const idle = !running;
  const tone = phase === "upload" ? "up" : "down";

  return (
    <div className="st">
      <header className="st-head">
        <div>
          <h1>Speed Test</h1>
          <p>Measures the most your connection can download and upload, live. It runs for 30 seconds.</p>
        </div>
        {monitors.length > 0 ? (
          <div className="st-sensor">
            <SelectMenu className="st-select" aria-label="Remote sensor" value={sensorValue} disabled={running} onChange={(event) => setDraft(event.target.value)}>
              {monitors.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </SelectMenu>
            <button
              type="button"
              className="st-display"
              disabled={running || sensorValue === agent.id}
              onClick={() => {
                setAgentId(sensorValue);
                setTestId(null);
                setDraft(null);
              }}
            >
              Display
            </button>
          </div>
        ) : null}
        <div className="st-controls">
          <div className="st-seg" role="group" aria-label="Connection to test">
            {(["wired", "wireless"] as const).map((item) => (
              <button key={item} type="button" className={link === item ? "active" : ""} aria-pressed={link === item} disabled={running} onClick={() => setLink(item)}>
                {item === "wired" ? "Wired" : "Wi-Fi"}
              </button>
            ))}
          </div>
          {idle ? (
            <button type="button" className="st-go" disabled={busy || (!isGuest && agent.status !== "online")} onClick={start}>
              {busy ? "Starting…" : finished ? "Run again" : "Start test"}
            </button>
          ) : (
            <button type="button" className="st-stop" disabled={busy} onClick={stop}>
              Stop
            </button>
          )}
        </div>
      </header>

      {error ? (
        <div className="st-note bad" role="alert">
          {error}
        </div>
      ) : null}
      {live && status === "failed" ? (
        <div className="st-note bad" role="alert">
          {live.error || "The test did not finish."}
        </div>
      ) : null}
      {agent.status !== "online" && !running && !isGuest ? <div className="st-note">The Pi is offline, so it cannot run a test right now.</div> : null}

      <section className={`st-hero tone-${tone}${running && phase !== "waiting" ? " is-running" : ""}`}>
        <div className="st-gauge">
          <span className="st-phase">
            {headline}
            {testId === DEMO ? <em className="st-badge">Demo, simulated numbers</em> : null}
          </span>
          {phase === "done" && status === "finished" ? (
            <div className="st-pair" aria-label="Final speeds">
              <div className="down">
                <small>Download</small>
                <b>{fmt(live?.down_mbps)}</b>
                <i>Mbit/s</i>
              </div>
              <div className="up">
                <small>Upload</small>
                <b>{fmt(live?.up_mbps)}</b>
                <i>Mbit/s</i>
              </div>
            </div>
          ) : (
            <div className="st-number" aria-live="off">
              {phase === "download" || phase === "upload" ? fmt(shown) : "0"}
              <small>Mbit/s</small>
            </div>
          )}
          <span className="st-sub">
            {running ? `${LINK_LABEL[link]} · ${left} seconds left` : finished && status === "finished" ? `${LINK_LABEL[link]} · test complete` : `${LINK_LABEL[link]} link to the HomeNetWatch server`}
          </span>
          <div className="st-progress" role="progressbar" aria-valuemin={0} aria-valuemax={30} aria-valuenow={Math.round(progress * 30)} aria-label="Test progress">
            <i style={{ width: `${progress * 100}%` }} />
            <b style={{ left: `${(downSeconds / total) * 100}%` }} />
          </div>
          <div className="st-legend">
            <span className={phase === "download" ? "on" : ""}>Download {downSeconds}s</span>
            <span className={phase === "upload" ? "on" : ""}>Upload {upSeconds}s</span>
          </div>
        </div>
        <SpeedFlow link={link} phase={phase} mbps={instant} />
      </section>

      <div className="st-results">
        <div className="st-card down">
          <span>Download</span>
          <b>{fmt(downSteady)}</b>
          <small>Mbit/s</small>
        </div>
        <div className="st-card up">
          <span>Upload</span>
          <b>{fmt(upSteady)}</b>
          <small>Mbit/s</small>
        </div>
        <div className="st-card">
          <span>Ping</span>
          <b>{fmt(live?.ping_ms)}</b>
          <small>ms</small>
        </div>
        <div className="st-card">
          <span>Jitter</span>
          <b>{fmt(live?.jitter_ms)}</b>
          <small>ms</small>
        </div>
      </div>

      <div className="st-charts">
        <SpeedBars title="Download speed" color="#4fd1b5" seconds={downSeconds} phase="download" samples={samples} active={phase === "download"} steady={downSteady} />
        <SpeedBars title="Upload speed" color="#6aa8ff" seconds={upSeconds} phase="upload" samples={samples} active={phase === "upload"} steady={upSteady} />
      </div>

      <section className="st-panel">
        <header>
          <h3>Recent tests</h3>
          <span>Speeds are the steady average, after the first three seconds.</span>
        </header>
        {historyQ.data && historyQ.data.length > 0 ? (
          <div className="st-table-wrap">
            <table className="st-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Link</th>
                  <th>Download</th>
                  <th>Upload</th>
                  <th>Ping</th>
                </tr>
              </thead>
              <tbody>
                {historyQ.data.map((item) => (
                  <tr key={item.id}>
                    <td>{new Date(item.requested_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td>
                    <td>{LINK_LABEL[item.link]}</td>
                    <td>{fmt(item.down_mbps)} Mbit/s</td>
                    <td>{fmt(item.up_mbps)} Mbit/s</td>
                    <td>{fmt(item.ping_ms)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="st-empty">No tests yet. Press Start test.</p>
        )}
      </section>
    </div>
  );
}
