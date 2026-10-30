import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { AnonText } from "../anonymized";
import { canOperate, useGuestGate } from "../guestGate";
import ConfirmDialog from "../components/ConfirmDialog";
import SelectMenu from "../components/SelectMenu";
import LocationForm from "../components/LocationForm";
import StatusMark from "../components/StatusMark";
import { deviceTypeLabel, isAndroid } from "../deviceType";
import type { DeviceReport, MonitorAdmin, User } from "../types";

const INTERVALS = [
  { value: 30, label: "30s" },
  { value: 60, label: "60s" },
  { value: 120, label: "2m" },
  { value: 300, label: "5m" },
];

const TUNNEL_STATE: Record<NonNullable<MonitorAdmin["tunnel_state"]>, string> = {
  never_connected: "Approved, never connected",
  connected: "Connected",
  disconnected: "Not connected",
};

function errorText(error: Error, fallback: string): string {
  try {
    const body = JSON.parse(error.message) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    return error.message || fallback;
  }
  return fallback;
}

function formatSeen(iso: string | null): string {
  if (!iso) return "Not seen yet";
  return new Date(iso).toLocaleString();
}

export default function RemoteAgentPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const operate = canOperate(me.data?.role);
  const run = useGuestGate();
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
  });
  const deviceQ = useQuery({
    queryKey: ["monitor-device", id],
    queryFn: () => api.monitorDevice(id) as Promise<DeviceReport>,
    enabled: !!id,
    refetchInterval: 15000,
  });
  const monitor = monitorsQ.data?.find((item) => item.id === id) ?? null;
  const [name, setName] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(60);
  const [syncedKey, setSyncedKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmingRestart, setConfirmingRestart] = useState(false);

  const serverKey = monitor ? [monitor.id, monitor.config_version, monitor.name, monitor.health_interval_seconds].join("|") : "";

  useEffect(() => {
    if (!monitor || syncedKey === serverKey) return;
    setSyncedKey(serverKey);
    setName(monitor.name);
    setIntervalSeconds(monitor.health_interval_seconds);
  }, [monitor, serverKey, syncedKey]);

  const save = useMutation({
    mutationFn: () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.updateMonitorLocation(id, {
        name: name.trim(),
        health_interval_seconds: intervalSeconds,
      }, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setConfirming(false);
      await qc.invalidateQueries({ queryKey: ["monitors"] });
      await qc.invalidateQueries({ queryKey: ["monitor", id] });
      await qc.invalidateQueries({ queryKey: ["monitor-device", id] });
    },
    onError: (e: Error) => {
      setConfirming(false);
      setError(errorText(e, "Could not save"));
    },
  });

  const restart = useMutation({
    mutationFn: () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.restartMonitor(id, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setConfirmingRestart(false);
      await qc.invalidateQueries({ queryKey: ["monitors"] });
      await qc.invalidateQueries({ queryKey: ["monitor-device", id] });
    },
    onError: (e: Error) => {
      setConfirmingRestart(false);
      setError(errorText(e, "Could not restart the probe"));
    },
  });

  const busy = save.isPending || restart.isPending;
  const report = deviceQ.data;
  const applying = monitor != null && monitor.config_version !== monitor.applied_config_version;
  const setupDirty =
    monitor != null &&
    syncedKey === serverKey &&
    (name.trim() !== monitor.name || intervalSeconds !== monitor.health_interval_seconds);
  const intervalOptions = INTERVALS.some((item) => item.value === intervalSeconds)
    ? INTERVALS
    : [...INTERVALS, { value: intervalSeconds, label: `${intervalSeconds}s` }];

  function requestSave(event: FormEvent) {
    event.preventDefault();
    run(() => {
      if (!name.trim()) {
        setError("Enter a device name.");
        return;
      }
      setError(null);
      setConfirming(true);
    });
  }

  if (monitorsQ.isLoading || deviceQ.isLoading) return <div className="state-box">Loading remote agent…</div>;
  if (!monitor) return <div className="state-box">Remote agent not found.</div>;

  const android = isAndroid(monitor.platform);
  const renamed = name.trim() !== monitor.name;
  // Agents added before installer codes send their name with every report.
  const legacy = monitor.tunnel_state === null;
  const confirmBody = !renamed
    ? `Save changes for “${monitor.name}”?`
    : legacy
      ? `Rename “${monitor.name}” to “${name.trim()}”? This agent was added before installer codes and must use “${name.trim()}” exactly, including capital letters, or it will stop connecting.`
      : `Rename “${monitor.name}” to “${name.trim()}”? The agent picks up the new name on its next report.`;
  const tunnelLine = (state: string | null | undefined, address: string | null | undefined) =>
    state ? (
      <>
        {TUNNEL_STATE[state] ?? state}
        {address ? (
          <>
            {" · "}
            <AnonText value={address} />
          </>
        ) : null}
      </>
    ) : (
      <AnonText value={address} fallback="Not issued" />
    );

  return (
    <div>
      <h2 className="page-title">Configure Remote Agent</h2>

      <section className="panel">
        <div className="device-head">
          <h3 className="wifi-heading" style={{ marginTop: 0 }}>
            Agent
          </h3>
          <StatusMark status={report?.connected ? "online" : monitor.status} />
        </div>
        <div className="device-stats">
          <div>
            <span>Device type</span>
            <strong>{deviceTypeLabel(monitor.platform)}</strong>
          </div>
          <div>
            <span>Last seen</span>
            <strong>{formatSeen(report?.last_seen_at ?? monitor.last_seen_at)}</strong>
          </div>
          <div>
            <span>Version</span>
            <strong>{report?.agent_version || "—"}</strong>
          </div>
          <div>
            <span>Address</span>
            <strong>
              <AnonText value={report?.lan_ip || monitor.lan_ip} />
            </strong>
          </div>
          {android ? (
            // An Android agent is on Wi-Fi only and has a single tunnel.
            <div>
              <span>Tunnel</span>
              <strong>{tunnelLine(monitor.tunnel_state, monitor.tunnel_address)}</strong>
            </div>
          ) : (
            <>
              <div>
                <span>Wired tunnel</span>
                <strong>{tunnelLine(monitor.tunnel_state, monitor.tunnel_address)}</strong>
              </div>
              <div>
                <span>Wi-Fi tunnel</span>
                <strong>{tunnelLine(monitor.wireless_tunnel_state, monitor.wireless_tunnel_address)}</strong>
              </div>
            </>
          )}
          <div>
            <span>External IP</span>
            <strong>
              <AnonText value={report?.public_ip} className="mono" />
            </strong>
          </div>
          <div>
            <span>Service provider</span>
            <strong>
              <AnonText value={report?.isp} />
            </strong>
          </div>
        </div>
        {applying ? <p className="muted">The agent applies a new report interval on its next check-in.</p> : null}
        {report?.restart_requested_at ? (
          <p className="muted">A restart is waiting. The probe picks it up within about 15 seconds.</p>
        ) : null}
        {android ? (
          <p className="muted">
            This Android agent runs the Wi-Fi voice stream and the Speed Test only. Restart it from the TV if needed.
          </p>
        ) : null}
        {operate && !android ? (
          <div className="form-actions">
            <button className="btn" type="button" disabled={busy} onClick={() => run(() => setConfirmingRestart(true))}>
              Restart probe
            </button>
          </div>
        ) : null}
      </section>

      {operate ? (
        <section className="panel">
          <h3 className="wifi-heading" style={{ marginTop: 0 }}>
            Setup
          </h3>
          <form className="device-form" onSubmit={requestSave}>
            <div className="field">
              <label htmlFor="agent-name">Device name</label>
              <input
                id="agent-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="PiPocono"
                maxLength={128}
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label htmlFor="agent-interval">Report every</label>
              <SelectMenu
                id="agent-interval"
                value={intervalSeconds}
                onChange={(event) => setIntervalSeconds(Number(event.target.value))}
              >
                {intervalOptions.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </SelectMenu>
            </div>
            <div className="form-actions">
              <button className={`btn btn-primary${setupDirty ? " is-dirty" : ""}`} type="submit" disabled={busy}>
                Save
              </button>
            </div>
          </form>
          {error ? <div className="error">{error}</div> : null}
          <h3 className="wifi-heading">Location</h3>
          <LocationForm monitorId={id} />
        </section>
      ) : (
        <p className="muted">An admin can change the device name, report interval, and location.</p>
      )}

      {confirming ? (
        <ConfirmDialog
          title="Save agent"
          confirmLabel="Save"
          busy={busy}
          onConfirm={() => save.mutate()}
          onCancel={() => {
            if (!busy) setConfirming(false);
          }}
        >
          {confirmBody}
        </ConfirmDialog>
      ) : null}
      {confirmingRestart ? (
        <ConfirmDialog
          title="Restart probe"
          confirmLabel="Restart"
          busy={busy}
          onConfirm={() => restart.mutate()}
          onCancel={() => {
            if (!busy) setConfirmingRestart(false);
          }}
        >
          {`Restart the probe on “${monitor.name}”? It picks this up within about 15 seconds. A check in progress stops and starts again.`}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
