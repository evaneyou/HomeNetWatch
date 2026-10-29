import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../api";
import ConfirmDialog from "../components/ConfirmDialog";
import type { User } from "../types";
import "../telegram.css";

type GeminiSettings = {
  configured: boolean;
  key_set: boolean;
  key_hint: string;
  model: string;
  allowed_models: string[];
  last_ok_at: string | null;
  last_error: string;
};

function messageOf(err: unknown): string {
  const text = err instanceof Error ? err.message : "That did not work.";
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // not JSON: use text as-is
  }
  return text;
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
}

export default function GeminiPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const admin = me.data?.role === "admin";
  const settings = useQuery({
    queryKey: ["gemini-settings"],
    queryFn: () => api.geminiSettings() as Promise<GeminiSettings>,
    enabled: admin,
  });
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gemini-2.5-flash");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const data = settings.data;
  useEffect(() => {
    if (!data || loaded) return;
    setModel(data.model || "gemini-2.5-flash");
    setLoaded(true);
  }, [data, loaded]);

  if (me.isLoading) return <div className="state-box">Loading…</div>;
  if (!admin) return <Navigate to="/settings/onboarding" replace />;
  if (settings.isLoading) return <div className="state-box">Loading…</div>;
  if (settings.isError || !data) return <div className="state-box">Could not load Gemini AI settings.</div>;

  const csrf = me.data?.csrf_token ?? "";

  async function run(name: string, work: () => Promise<void>) {
    setBusy(name);
    setNotice(null);
    try {
      await work();
    } catch (err) {
      setNotice({ kind: "error", text: messageOf(err) });
    } finally {
      setBusy("");
    }
  }

  const refresh = () => qc.invalidateQueries({ queryKey: ["gemini-settings"] });

  const save = () =>
    run("save", async () => {
      await api.geminiSave({ api_key: apiKey.trim() || undefined, model }, csrf);
      setApiKey("");
      await refresh();
      setNotice({ kind: "ok", text: "Saved and verified with Google Gemini." });
    });

  const disconnect = () =>
    run("disconnect", async () => {
      await api.geminiDisconnect(csrf);
      setApiKey("");
      await refresh();
      setNotice({ kind: "ok", text: "Removed saved Gemini API key." });
    });

  const status = !data.key_set ? "Not set up" : data.last_error ? "Problem" : "Ready";
  const statusClass = status === "Ready" ? "ok" : status === "Problem" ? "bad" : "off";

  return (
    <div className="tg">
      <div className="tg-head">
        <div>
          <h2 className="page-title">Configure Gemini AI</h2>
          <p className="page-sub">
            Connect a Google Gemini API key to enable the on-demand <b>Determine Root Cause</b> button on the{" "}
            <Link to="/kpi">KPI Dashboard</Link>. The key is encrypted at rest and never sent back to the browser.
          </p>
        </div>
        <span className={`tg-badge ${statusClass}`}>{status}</span>
      </div>

      {notice ? <div className={`tg-notice ${notice.kind}`}>{notice.text}</div> : null}
      {data.last_error ? <div className="tg-notice error">Last error: {data.last_error}</div> : null}

      <section className="tg-card">
        <h3>1. Google AI Studio API key</h3>
        <p className="tg-help">
          Create a Gemini API key in Google AI Studio and paste it below. Saving verifies the key immediately.
        </p>
        <div className="tg-grid">
          <label className="tg-field">
            <span>
              API key{" "}
              {data.key_set ? (
                <small className="muted">(saved ending in …{data.key_hint})</small>
              ) : null}
            </span>
            <input
              type="password"
              autoComplete="off"
              placeholder={data.key_set ? "Leave blank to keep the saved key" : "AIza..."}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
          <label className="tg-field">
            <span>Model</span>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {data.allowed_models.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="tg-meta">
          <span>Last verified: {when(data.last_ok_at)}</span>
        </div>
        <div className="tg-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy !== "" || (!apiKey.trim() && !data.key_set)}
            onClick={save}
          >
            {busy === "save" ? "Verifying & saving…" : "Save & verify"}
          </button>
          {data.key_set ? (
            <button
              type="button"
              className="btn"
              disabled={busy !== ""}
              onClick={() => setConfirming(true)}
            >
              Remove key
            </button>
          ) : null}
        </div>
      </section>

      {confirming ? (
        <ConfirmDialog
          title="Remove Gemini API key"
          confirmLabel="Remove"
          busy={busy === "disconnect"}
          onConfirm={() => {
            setConfirming(false);
            void disconnect();
          }}
          onCancel={() => setConfirming(false)}
        >
          Remove the saved Gemini API key? On-demand root cause analysis on the KPI Dashboard will be disabled until a new key is added.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
