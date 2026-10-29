import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { AnonText, joinAnon } from "../anonymized";
import { canOperate, useGuestGate } from "../guestGate";
import ConfirmDialog from "../components/ConfirmDialog";
import SelectMenu from "../components/SelectMenu";
import StatusMark from "../components/StatusMark";
import type { EnrollmentRegistration, MonitorAdmin, TunnelIssue, User } from "../types";

const INTERVALS = [
  { value: 30, label: "30s" },
  { value: 60, label: "60s" },
  { value: 120, label: "2m" },
  { value: 300, label: "5m" },
];

type Pending =
  | { kind: "register"; code: string; name: string }
  | { kind: "approve"; registration: EnrollmentRegistration }
  | { kind: "replace"; registration: EnrollmentRegistration; monitor: MonitorAdmin }
  | { kind: "start-over"; registration: EnrollmentRegistration }
  | { kind: "remove-code"; registration: EnrollmentRegistration }
  | { kind: "delete"; monitor: MonitorAdmin }
  | { kind: "tunnel"; monitor: MonitorAdmin }
  | { kind: "save" };

function errorText(error: Error, fallback: string): string {
  try {
    const body = JSON.parse(error.message) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    return error.message || fallback;
  }
  return fallback;
}

function tunnelAction(monitor: MonitorAdmin): "issue" | "wireless" | "replace" {
  if (!monitor.tunnel_address) return "issue";
  if (!monitor.wireless_tunnel_address) return "wireless";
  return "replace";
}

function tunnelLabel(monitor: MonitorAdmin): string {
  const action = tunnelAction(monitor);
  if (action === "wireless") return "Issue Wi-Fi tunnel";
  if (action === "replace") return "Replace tunnels";
  return "Issue tunnels";
}

function placeLabel(city: string | null, region: string | null, country: string | null): string {
  return joinAnon([city?.trim(), region?.trim(), country?.trim()]) || "No location";
}

function formatSeen(iso: string | null): string {
  if (!iso) return "Not seen yet";
  return new Date(iso).toLocaleString();
}

/** The server address and TLS key pins as one string, for agents with a setup page (the Android agent). Not a secret. */
function SetupKey({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div style={{ margin: "8px 0" }}>
      <p className="muted" style={{ margin: "0 0 4px" }}>
        Setup key. Paste it into the device&apos;s setup page after entering this installer code there.
      </p>
      <textarea className="mono" readOnly rows={3} value={value} style={{ width: "100%" }} aria-label="Setup key" onFocus={(event) => event.currentTarget.select()} />
      <button className="btn" type="button" onClick={() => void copy()}>
        {copied ? "Copied" : "Copy setup key"}
      </button>
    </div>
  );
}

export default function MonitorOnboardingPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const operate = canOperate(me.data?.role);
  const run = useGuestGate();
  const monitorsQ = useQuery({
    queryKey: ["monitors"],
    queryFn: () => api.monitors() as Promise<MonitorAdmin[]>,
    refetchInterval: 5000,
  });
  const enrollmentQ = useQuery({
    queryKey: ["enrollment"],
    queryFn: () => api.enrollments(),
    refetchInterval: 5000,
  });
  const [draftCode, setDraftCode] = useState("");
  const [draftName, setDraftName] = useState("");
  const [draftLocation, setDraftLocation] = useState("");
  const [replaceChoice, setReplaceChoice] = useState<Record<string, string>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(60);
  const [syncedKey, setSyncedKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [issued, setIssued] = useState<TunnelIssue | null>(null);
  const [copiedTunnel, setCopiedTunnel] = useState(false);
  const [copiedWireless, setCopiedWireless] = useState(false);

  const monitors = monitorsQ.data ?? [];
  const selected = monitors.find((monitor) => monitor.id === selectedId) ?? null;
  const serverKey = selected
    ? [selected.id, selected.config_version, selected.name, selected.health_interval_seconds].join("|")
    : "";

  useEffect(() => {
    if (!selected || syncedKey === serverKey) return;
    setSyncedKey(serverKey);
    setName(selected.name);
    setIntervalSeconds(selected.health_interval_seconds);
  }, [selected, serverKey, syncedKey]);

  const registrations = enrollmentQ.data ?? [];

  function csrfOrError(): string | null {
    const token = me.data?.csrf_token;
    if (!token) {
      setError("The session is missing a CSRF token. Reload the page.");
      return null;
    }
    return token;
  }

  const registerCode = useMutation({
    mutationFn: (body: { installer_code: string; device_name: string; location: string }) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.registerEnrollment(
        {
          installer_code: body.installer_code,
          device_name: body.device_name,
          location: body.location.trim() || null,
        },
        csrf,
      );
    },
    onSuccess: async () => {
      setError(null);
      setDraftCode("");
      setDraftName("");
      setDraftLocation("");
      setPending(null);
      await qc.invalidateQueries({ queryKey: ["enrollment"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not register the installer code"));
    },
  });

  const approve = useMutation({
    mutationFn: (body: { id: string; replaceMonitorId: string | null }) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.approveEnrollment(body.id, body.replaceMonitorId, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setPending(null);
      await qc.invalidateQueries({ queryKey: ["enrollment"] });
      await qc.invalidateQueries({ queryKey: ["monitors"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not approve the remote agent"));
    },
  });

  const startOver = useMutation({
    mutationFn: (id: string) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.startOverEnrollment(id, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setPending(null);
      await qc.invalidateQueries({ queryKey: ["enrollment"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not start over"));
    },
  });

  const removeCode = useMutation({
    mutationFn: (id: string) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.removeEnrollment(id, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setPending(null);
      await qc.invalidateQueries({ queryKey: ["enrollment"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not remove the registration"));
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.deleteMonitor(id, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setPending(null);
      setSelectedId(null);
      setSyncedKey("");
      await qc.invalidateQueries({ queryKey: ["monitors"] });
      await qc.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not delete the device"));
    },
  });

  const tunnel = useMutation({
    mutationFn: (id: string) => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.issueMonitorTunnel(id, csrf) as Promise<TunnelIssue>;
    },
    onSuccess: (created) => {
      setError(null);
      setPending(null);
      setIssued(created);
      setCopiedTunnel(false);
      setCopiedWireless(false);
      void qc.invalidateQueries({ queryKey: ["monitors"] });
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not create the tunnel"));
    },
  });

  const save = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("Choose a device");
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.updateMonitorLocation(selected.id, {
        name: name.trim(),
        health_interval_seconds: intervalSeconds,
      }, csrf);
    },
    onSuccess: async () => {
      setError(null);
      setPending(null);
      await qc.invalidateQueries({ queryKey: ["monitors"] });
      if (selected) {
        await qc.invalidateQueries({ queryKey: ["monitor", selected.id] });
        await qc.invalidateQueries({ queryKey: ["monitor-device", selected.id] });
      }
    },
    onError: (e: Error) => {
      setPending(null);
      setError(errorText(e, "Could not save"));
    },
  });

  const busy =
    registerCode.isPending ||
    approve.isPending ||
    startOver.isPending ||
    removeCode.isPending ||
    remove.isPending ||
    tunnel.isPending ||
    save.isPending;

  function requestRegister(event: FormEvent) {
    event.preventDefault();
    run(() => submitRegister());
  }

  function submitRegister() {
    const deviceName = draftName.trim();
    const code = draftCode.trim();
    if (!code || !deviceName) {
      setError("Enter the installer code and a device name.");
      return;
    }
    if (!csrfOrError()) return;
    setError(null);
    setPending({ kind: "register", code, name: deviceName });
  }

  function requestSave(event: FormEvent) {
    event.preventDefault();
    run(() => submitSave());
  }

  function submitSave() {
    if (!name.trim()) {
      setError("Enter a device name.");
      return;
    }
    setError(null);
    setPending({ kind: "save" });
  }

  function confirm() {
    if (!pending) return;
    if (pending.kind === "register") {
      registerCode.mutate({
        installer_code: pending.code,
        device_name: pending.name,
        location: draftLocation,
      });
    } else if (pending.kind === "approve") approve.mutate({ id: pending.registration.id, replaceMonitorId: null });
    else if (pending.kind === "replace") {
      approve.mutate({ id: pending.registration.id, replaceMonitorId: pending.monitor.id });
    } else if (pending.kind === "start-over") startOver.mutate(pending.registration.id);
    else if (pending.kind === "remove-code") removeCode.mutate(pending.registration.id);
    else if (pending.kind === "delete") remove.mutate(pending.monitor.id);
    else if (pending.kind === "tunnel") tunnel.mutate(pending.monitor.id);
    else save.mutate();
  }

  async function copyText(value: string, mark: (copied: boolean) => void) {
    try {
      await navigator.clipboard.writeText(value);
      mark(true);
    } catch {
      mark(false);
    }
  }

  const applying = selected != null && selected.config_version !== selected.applied_config_version;
  const setupDirty =
    selected != null &&
    syncedKey === serverKey &&
    (name.trim() !== selected.name || intervalSeconds !== selected.health_interval_seconds);
  const intervalOptions = INTERVALS.some((item) => item.value === intervalSeconds)
    ? INTERVALS
    : [...INTERVALS, { value: intervalSeconds, label: `${intervalSeconds}s` }];
  const confirmCopy = confirmText(pending, selected, name.trim());

  return (
    <div>
      <h2 className="page-title">Remote Agent Onboarding</h2>
      <p className="page-sub">
        Register the installer code shown on the Pi, then approve the remote agent after it checks in. The device name
        is case-sensitive.
      </p>

      <section className="panel">
        <h3 className="wifi-heading" style={{ marginTop: 0 }}>
          Register an installer code
        </h3>
        <ol className="setup-steps">
          <li>On the Pi, open the LAN setup page and copy the installer code.</li>
          <li>Register that code here with the device name and an optional location. The code lasts 24 hours.</li>
          <li>When the Pi checks in, approve it as a new remote agent, or replace an existing one.</li>
        </ol>
        {me.isLoading ? (
          <p className="muted">Loading session…</p>
        ) : operate ? (
          <form className="device-form" onSubmit={requestRegister}>
            <div className="field">
              <label htmlFor="installer-code">Installer code</label>
              <input
                id="installer-code"
                value={draftCode}
                onChange={(event) => setDraftCode(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="K7QF-2MXD-9PLA"
              />
            </div>
            <div className="field">
              <label htmlFor="new-device-name">Device name</label>
              <input
                id="new-device-name"
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                autoComplete="off"
                placeholder="Living Room"
              />
            </div>
            <div className="field">
              <label htmlFor="new-device-location">Location</label>
              <input
                id="new-device-location"
                value={draftLocation}
                onChange={(event) => setDraftLocation(event.target.value)}
                autoComplete="off"
                placeholder="Optional"
              />
            </div>
            <div className="form-actions">
              <button
                className={`btn btn-primary${draftCode.trim() && draftName.trim() ? " is-dirty" : ""}`}
                type="submit"
                disabled={busy}
              >
                Register
              </button>
            </div>
          </form>
        ) : (
          <p className="muted">An admin can register installer codes and change settings.</p>
        )}
        {issued?.config ? (
          <div className="issued-key">
            <p>
              Wired tunnel for <strong>{issued.name}</strong>. Save it as <span className="mono">wg0.conf</span> next to
              the installer. It cannot be shown again. Health reports use <span className="mono">http://10.7.0.1:8847</span>.
            </p>
            <div className="copy-row">
              <textarea className="mono" readOnly value={issued.config} aria-label={`Wired WireGuard config for ${issued.name}`} />
              <button className="btn" type="button" onClick={() => void copyText(issued.config, setCopiedTunnel)}>
                {copiedTunnel ? "Copied" : "Copy"}
              </button>
            </div>
            {issued.wireless_config ? (
              <>
                <p>
                  Wi-Fi tunnel for <strong>{issued.name}</strong>. Save it as <span className="mono">wg1.conf</span> next
                  to the installer. It cannot be shown again. Wi-Fi path checks use{" "}
                  <span className="mono">http://10.7.1.1:8847</span>.
                </p>
                <div className="copy-row">
                  <textarea
                    className="mono"
                    readOnly
                    value={issued.wireless_config}
                    aria-label={`Wi-Fi WireGuard config for ${issued.name}`}
                  />
                  <button className="btn" type="button" onClick={() => void copyText(issued.wireless_config, setCopiedWireless)}>
                    {copiedWireless ? "Copied" : "Copy"}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h3 className="wifi-heading" style={{ marginTop: 0 }}>
          Waiting for approval
        </h3>
        {enrollmentQ.isLoading ? (
          <p className="muted">Loading registrations…</p>
        ) : enrollmentQ.isError ? (
          <p className="error">Could not load registrations.</p>
        ) : registrations.length === 0 ? (
          <p className="muted">No remote agents are waiting for approval.</p>
        ) : (
          <div className="monitor-picks">
            {registrations.map((registration) => {
              const count = registration.checkins.length;
              const ready = count === 1 && !registration.conflict;
              const chosen = replaceChoice[registration.id] ?? "";
              const replacement = monitors.find((monitor) => monitor.id === chosen) ?? null;
              return (
                <div key={registration.id} className="monitor-card open">
                  <div className="monitor-pick" style={{ cursor: "default" }}>
                    <span className="monitor-pick-name">
                      <strong>{registration.device_name}</strong>
                    </span>
                    <span className="muted">{registration.location || "No location"}</span>
                    <span className="muted">Registered by {registration.registered_by}</span>
                    <span className="muted">
                      {count === 1 ? "1 device checked in" : `${count} devices checked in`}
                    </span>
                    <span className="muted">Expires {formatSeen(registration.expires_at)}</span>
                  </div>
                  <div className="monitor-card-body">
                    {registration.conflict ? (
                      <p className="error">More than one device used this code. Start over, then have one device check in again.</p>
                    ) : null}
                    {count === 0 ? <p className="muted">Waiting for the device to check in.</p> : null}
                    {count === 0 && registration.setup_key ? <SetupKey value={registration.setup_key} /> : null}
                    {registration.checkins.map((checkin) => (
                      <p key={checkin.id} className="muted">
                        <AnonText value={checkin.hostname} className="mono" />
                        {" · "}
                        <AnonText value={checkin.lan_ip} className="mono" />
                        {checkin.observed_ip ? (
                          <>
                            {" · seen from "}
                            <AnonText value={checkin.observed_ip} className="mono" />
                          </>
                        ) : null}
                      </p>
                    ))}
                    {operate ? (
                      <div className="form-actions">
                        <button
                          className="btn btn-primary"
                          type="button"
                          disabled={busy || !ready}
                          onClick={() =>
                            run(() => {
                              setError(null);
                              setPending({ kind: "approve", registration });
                            })
                          }
                        >
                          Approve
                        </button>
                        <SelectMenu
                          id={`replace-${registration.id}`}
                          aria-label={`Replace an existing remote agent with ${registration.device_name}`}
                          value={chosen}
                          disabled={busy || !ready || monitors.length === 0}
                          onChange={(event) =>
                            setReplaceChoice((current) => ({ ...current, [registration.id]: event.target.value }))
                          }
                        >
                          <option value="">Replace…</option>
                          {monitors.map((monitor) => (
                            <option key={monitor.id} value={monitor.id}>
                              {monitor.name}
                            </option>
                          ))}
                        </SelectMenu>
                        <button
                          className="btn"
                          type="button"
                          disabled={busy || !ready || !replacement}
                          onClick={() =>
                            run(() => {
                              if (!replacement) return;
                              setError(null);
                              setPending({ kind: "replace", registration, monitor: replacement });
                            })
                          }
                        >
                          Replace
                        </button>
                        <button
                          className="btn"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            run(() => {
                              setError(null);
                              setPending({ kind: "start-over", registration });
                            })
                          }
                        >
                          Start over
                        </button>
                        <button
                          className="btn"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            run(() => {
                              setError(null);
                              setPending({ kind: "remove-code", registration });
                            })
                          }
                        >
                          Remove
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel">
        <h3 className="wifi-heading" style={{ marginTop: 0 }}>
          Devices
        </h3>
        {monitorsQ.isLoading ? (
          <p className="muted">Loading devices…</p>
        ) : monitorsQ.isError ? (
          <p className="error">Could not load devices.</p>
        ) : monitors.length === 0 ? (
          <p className="muted">No devices yet.</p>
        ) : (
          <div className="monitor-picks">
            {monitors.map((monitor) => {
              const open = selected?.id === monitor.id;
              return (
                <div key={monitor.id} className={`monitor-card${open ? " open" : ""}`}>
                  <button
                    type="button"
                    className="monitor-pick"
                    aria-expanded={open}
                    onClick={() => {
                      if (open) {
                        setSelectedId(null);
                        return;
                      }
                      setSelectedId(monitor.id);
                      setSyncedKey("");
                    }}
                  >
                    <span className="monitor-pick-name">
                      <strong>{monitor.name}</strong>
                      <StatusMark status={monitor.status} />
                    </span>
                    <span className="muted">
                      <AnonText value={placeLabel(monitor.location_label, monitor.region, monitor.country)} fallback="No location" />
                    </span>
                    <span className="mono">
                      <AnonText value={monitor.lan_ip} fallback="No address yet" />
                    </span>
                    <span className="muted">{formatSeen(monitor.last_seen_at)}</span>
                  </button>
                  {open ? (
                    <div className="monitor-card-body">
                      {operate ? (
                        <form className="device-form" onSubmit={requestSave}>
                          <div className="field">
                            <label htmlFor="onboard-name">Device name</label>
                            <input
                              id="onboard-name"
                              value={name}
                              onChange={(event) => setName(event.target.value)}
                              autoComplete="off"
                            />
                          </div>
                          <div className="field">
                            <label htmlFor="onboard-interval">Report every</label>
                            <SelectMenu
                              id="onboard-interval"
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
                            <button
                              className="btn"
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                run(() => {
                                  setError(null);
                                  setPending({ kind: "tunnel", monitor });
                                })
                              }
                            >
                              {tunnelLabel(monitor)}
                            </button>
                            <button
                              className="btn"
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                run(() => {
                                  setError(null);
                                  setPending({ kind: "delete", monitor });
                                })
                              }
                            >
                              Delete
                            </button>
                            <Link className="btn" to={`/settings/agents/${monitor.id}`}>
                              Configure Remote Agent
                            </Link>
                            {applying ? (
                              <span className="muted">The remote agent applies a new report interval on its next report.</span>
                            ) : null}
                          </div>
                        </form>
                      ) : (
                        <p className="page-sub" style={{ margin: 0 }}>
                          Reports every {monitor.health_interval_seconds}s.{" "}
                          <Link to={`/monitors/${monitor.id}`}>Open remote agent</Link>
                        </p>
                      )}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
        {error ? <div className="error">{error}</div> : null}
      </section>

      {pending && confirmCopy ? (
        <ConfirmDialog
          title={confirmCopy.title}
          confirmLabel={confirmCopy.confirmLabel}
          busy={busy}
          onConfirm={confirm}
          onCancel={() => {
            if (!busy) setPending(null);
          }}
        >
          {confirmCopy.body}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

function confirmText(
  pending: Pending | null,
  selected: MonitorAdmin | null,
  nextName: string,
): { title: string; body: string; confirmLabel: string } | null {
  if (!pending) return null;
  if (pending.kind === "register") {
    return {
      title: "Register installer code",
      confirmLabel: "Register",
      body: `Register installer code ${pending.code} for “${pending.name}”? The name is case-sensitive.`,
    };
  }
  if (pending.kind === "approve") {
    return {
      title: "Approve remote agent",
      confirmLabel: "Approve",
      body: `Add “${pending.registration.device_name}” as a new remote agent?`,
    };
  }
  if (pending.kind === "replace") {
    return {
      title: "Replace remote agent",
      confirmLabel: "Replace",
      body: `Replace “${pending.monitor.name}” with “${pending.registration.device_name}”? The tunnel address stays. The previous device key stops working.`,
    };
  }
  if (pending.kind === "start-over") {
    return {
      title: "Start over",
      confirmLabel: "Start over",
      body: `Clear the devices that checked in for “${pending.registration.device_name}”? The installer code stays registered.`,
    };
  }
  if (pending.kind === "remove-code") {
    return {
      title: "Remove registration",
      confirmLabel: "Remove",
      body: `Remove the registration for “${pending.registration.device_name}”? It has not been approved.`,
    };
  }
  if (pending.kind === "delete") {
    return {
      title: "Delete device",
      confirmLabel: "Delete device",
      body: `Delete “${pending.monitor.name}”? This removes the device, and it will no longer be able to connect.`,
    };
  }
  if (pending.kind === "tunnel") {
    const action = tunnelAction(pending.monitor);
    if (action === "wireless") {
      return {
        title: "Issue Wi-Fi tunnel",
        confirmLabel: "Issue Wi-Fi tunnel",
        body: `Create the Wi-Fi tunnel for “${pending.monitor.name}”? The wired tunnel keeps working. The Wi-Fi config is shown once. Wi-Fi checks use http://10.7.1.1:8847.`,
      };
    }
    if (action === "replace") {
      return {
        title: "Replace tunnels",
        confirmLabel: "Replace tunnels",
        body: `Replace the wired and Wi-Fi tunnels for “${pending.monitor.name}”? The current tunnels stop working. Both configs are shown once.`,
      };
    }
    return {
      title: "Issue tunnels",
      confirmLabel: "Issue tunnels",
      body: `Create a wired tunnel and a Wi-Fi tunnel for “${pending.monitor.name}”? Both configs are shown once. Wired checks use http://10.7.0.1:8847 and Wi-Fi checks use http://10.7.1.1:8847.`,
    };
  }
  const current = selected?.name ?? "this device";
  const renamed = selected != null && nextName !== selected.name;
  return {
    title: "Save device",
    confirmLabel: "Save",
    body: renamed
      ? `Save changes for “${current}”? The remote agent must use “${nextName}” exactly, including capital letters, or it will stop connecting.`
      : `Save changes for “${current}”?`,
  };
}
