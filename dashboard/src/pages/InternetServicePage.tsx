import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { canOperate, useGuestGate } from "../guestGate";
import ConfirmDialog from "../components/ConfirmDialog";
import type { InternetServiceSetting, User } from "../types";

const EMPTY: InternetServiceSetting = {
  interval_seconds: 60,
  http_enabled: true,
  icmp_enabled: true,
  mtr_enabled: true,
  http_variance_pct: 25,
  icmp_variance_pct: 25,
  mtr_variance_pct: 25,
  librespeed_enabled: true,
  librespeed_interval_seconds: 900,
  librespeed_variance_pct: 25,
};

type EnabledField = "http_enabled" | "icmp_enabled" | "mtr_enabled" | "librespeed_enabled";
type VarianceField =
  | "http_variance_pct"
  | "icmp_variance_pct"
  | "mtr_variance_pct"
  | "librespeed_variance_pct"
;
type IntervalField = "interval_seconds" | "librespeed_interval_seconds";

const SIGNATURE_FIELDS = [
  "interval_seconds",
  "http_enabled",
  "icmp_enabled",
  "mtr_enabled",
  "http_variance_pct",
  "icmp_variance_pct",
  "mtr_variance_pct",
  "librespeed_enabled",
  "librespeed_interval_seconds",
  "librespeed_variance_pct",
] as const;

function signatureOf(row: InternetServiceSetting): string {
  return SIGNATURE_FIELDS.map((field) => String(row[field])).join(":");
}

function ToolRow({
  label,
  intervalLabel,
  intervalField,
  enabledLabel,
  enabledField,
  varianceLabel,
  varianceField,
  row,
  locked,
  onNumber,
  onToggle,
}: {
  label: string;
  intervalLabel: string;
  intervalField: IntervalField;
  enabledLabel: string;
  enabledField: EnabledField;
  varianceLabel: string;
  varianceField: VarianceField;
  row: InternetServiceSetting;
  locked: boolean;
  onNumber: (field: IntervalField | VarianceField, value: string) => void;
  onToggle: (field: EnabledField, value: boolean) => void;
}) {
  return (
    <tr>
      <td>{label}</td>
      <td>
        <input
          aria-label={intervalLabel}
          type="number"
          min={300}
          max={86400}
          step={60}
          value={row[intervalField]}
          disabled={locked}
          onChange={(event) => onNumber(intervalField, event.target.value)}
        />
        <span className="muted"> s</span>
      </td>
      <td className="check">
        <input
          aria-label={enabledLabel}
          type="checkbox"
          checked={row[enabledField]}
          disabled={locked}
          onChange={(event) => onToggle(enabledField, event.target.checked)}
        />
      </td>
      <td>
        <input
          aria-label={varianceLabel}
          type="number"
          min={0}
          max={varianceField === "librespeed_variance_pct" ? 50 : 500}
          step={5}
          value={row[varianceField]}
          disabled={locked}
          onChange={(event) => onNumber(varianceField, event.target.value)}
        />
        <span className="muted"> %</span>
      </td>
    </tr>
  );
}

function errorText(error: Error): string {
  try {
    const body = JSON.parse(error.message) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    return error.message || "Could not save";
  }
  return "Could not save";
}

export default function InternetServicePage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const operate = canOperate(me.data?.role);
  const run = useGuestGate();
  const settingQ = useQuery({
    queryKey: ["internet-service"],
    queryFn: () => api.internetService() as Promise<InternetServiceSetting>,
  });
  const [row, setRow] = useState<InternetServiceSetting>(EMPTY);
  const [synced, setSynced] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signature = settingQ.data ? signatureOf(settingQ.data) : "";

  useEffect(() => {
    if (!settingQ.data || synced === signature) return;
    setSynced(signature);
    setRow(settingQ.data);
  }, [settingQ.data, signature, synced]);

  const save = useMutation({
    mutationFn: () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      return api.saveInternetService(row, csrf) as Promise<InternetServiceSetting>;
    },
    onSuccess: async (saved) => {
      setError(null);
      setConfirming(false);
      setRow(saved);
      setSynced("");
      await qc.invalidateQueries({ queryKey: ["internet-service"] });
      await qc.invalidateQueries({ queryKey: ["monitor"] });
    },
    onError: (e: Error) => {
      setConfirming(false);
      setError(errorText(e));
    },
  });

  function setNumber(field: IntervalField | VarianceField, value: string) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    setRow((current) => ({ ...current, [field]: parsed }));
  }

  function setToggle(field: EnabledField, value: boolean) {
    setRow((current) => ({ ...current, [field]: value }));
  }

  if (settingQ.isLoading) return <div className="state-box">Loading Internet Service…</div>;
  if (settingQ.isError) return <div className="state-box">Could not load Internet Service settings.</div>;

  const currentSignature = signatureOf(row);
  const dirty = synced !== "" && currentSignature !== signature;
  const locked = !operate || save.isPending;

  return (
    <div>
      <h2 className="page-title">Internet Service</h2>
      <p className="page-sub">
        Each check has its own variance. A response more than that percent off the recent normal is amber, as is an
        error status or packet loss. Red means an active check got no response. HTTP, ICMP, and MTR share one poll
        interval. MTR runs at least every 2 minutes. Bandwidth Test runs at least every 5 minutes, and turns
        amber when download or upload falls more than its percent below the recent normal.
      </p>
      <div className="panel table-scroll">
        <table className="table web-service-table">
          <thead>
            <tr>
              <th>Check</th>
              <th>Poll every</th>
              <th>Enabled</th>
              <th>Variance</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>HTTP</td>
              <td>
                <input
                  aria-label="Path interval"
                  type="number"
                  min={60}
                  max={86400}
                  step={30}
                  value={row.interval_seconds}
                  disabled={locked}
                  onChange={(event) => setNumber("interval_seconds", event.target.value)}
                />
                <span className="muted"> s</span>
              </td>
              <td className="check">
                <input
                  aria-label="Path HTTP"
                  type="checkbox"
                  checked={row.http_enabled}
                  disabled={locked}
                  onChange={(event) => setToggle("http_enabled", event.target.checked)}
                />
              </td>
              <td>
                <input
                  aria-label="HTTP variance"
                  type="number"
                  min={0}
                  max={500}
                  step={5}
                  value={row.http_variance_pct}
                  disabled={locked}
                  onChange={(event) => setNumber("http_variance_pct", event.target.value)}
                />
                <span className="muted"> %</span>
              </td>
            </tr>
            <tr>
              <td>ICMP</td>
              <td className="muted">Same as HTTP</td>
              <td className="check">
                <input
                  aria-label="Path ICMP"
                  type="checkbox"
                  checked={row.icmp_enabled}
                  disabled={locked}
                  onChange={(event) => setToggle("icmp_enabled", event.target.checked)}
                />
              </td>
              <td>
                <input
                  aria-label="ICMP variance"
                  type="number"
                  min={0}
                  max={500}
                  step={5}
                  value={row.icmp_variance_pct}
                  disabled={locked}
                  onChange={(event) => setNumber("icmp_variance_pct", event.target.value)}
                />
                <span className="muted"> %</span>
              </td>
            </tr>
            <tr>
              <td>MTR</td>
              <td className="muted">Same as HTTP</td>
              <td className="check">
                <input
                  aria-label="Path MTR"
                  type="checkbox"
                  checked={row.mtr_enabled}
                  disabled={locked}
                  onChange={(event) => setToggle("mtr_enabled", event.target.checked)}
                />
              </td>
              <td>
                <input
                  aria-label="MTR variance"
                  type="number"
                  min={0}
                  max={500}
                  step={5}
                  value={row.mtr_variance_pct}
                  disabled={locked}
                  onChange={(event) => setNumber("mtr_variance_pct", event.target.value)}
                />
                <span className="muted"> %</span>
              </td>
            </tr>
            <ToolRow
              label="Bandwidth Test"
              intervalLabel="Bandwidth Test interval"
              intervalField="librespeed_interval_seconds"
              enabledLabel="Bandwidth Test"
              enabledField="librespeed_enabled"
              varianceLabel="Bandwidth Test variance"
              varianceField="librespeed_variance_pct"
              row={row}
              locked={locked}
              onNumber={setNumber}
              onToggle={setToggle}
            />
          </tbody>
        </table>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {operate ? (
        <div className="form-actions">
          <button
            className={`btn btn-primary${dirty ? " is-dirty" : ""}`}
            type="button"
            disabled={save.isPending}
            onClick={() => run(() => setConfirming(true))}
          >
            Save
          </button>
        </div>
      ) : (
        <p className="muted">Viewers can see these settings. An admin can change them.</p>
      )}
      {confirming ? (
        <ConfirmDialog
          title="Save Internet Service"
          confirmLabel="Save"
          busy={save.isPending}
          onConfirm={() => save.mutate()}
          onCancel={() => setConfirming(false)}
        >
          Save path checks, Bandwidth Test, and variance? Agents pick up the schedule on the next health report.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
