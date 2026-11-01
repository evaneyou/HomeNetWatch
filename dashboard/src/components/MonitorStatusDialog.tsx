import { useEffect } from "react";
import DevicePanel from "./DevicePanel";
import type { MonitorDetail } from "../types";

export default function MonitorStatusDialog({
  monitor,
  onClose,
}: {
  monitor: MonitorDetail;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="monitor-dialog-backdrop" onClick={onClose}>
      <div
        className="monitor-dialog monitor-status-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="monitor-status-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="monitor-dialog-head">
          <h2 id="monitor-status-title">Remote Probe Status</h2>
          <button className="btn btn-sm" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <DevicePanel monitorId={monitor.id} platform={monitor.platform} />
      </div>
    </div>
  );
}
