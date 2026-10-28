"""Watch WireGuard tunnels and restart a dead one locally.

The Wi-Fi radio can stay up while wg1 stops handshaking. App checks then fail against
10.7.1.1 even though internet over wlan0 is fine. This loop pings the tunnel peers and
asks restore_tunnel.sh (via a narrow sudoers rule) to restart the unit.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import agent_state as state

RESTORE = Path(os.environ.get("MONITOR_RESTORE_TUNNEL", "/opt/monitor/restore_tunnel.sh"))
FAILS_BEFORE_RESTORE = 2
COOLDOWN_S = 15 * 60
PROBE_TIMEOUT_S = 2
INTERVAL_S = 60

_fail_counts: dict[str, int] = {"wg0": 0, "wg1": 0}
_last_restore_at: dict[str, float] = {"wg0": 0.0, "wg1": 0.0}


def _configured(iface: str) -> bool:
    return Path(f"/etc/wireguard/{iface}.conf").is_file()


def _ping(host: str) -> bool:
    try:
        completed = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(PROBE_TIMEOUT_S), host],
            check=False,
            capture_output=True,
            timeout=PROBE_TIMEOUT_S + 2,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _restore(iface: str) -> bool:
    try:
        completed = subprocess.run(
            ["sudo", "-n", str(RESTORE), iface],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"tunnel_watch: restore {iface} failed: {exc}", flush=True)
        try:
            state.note_log("tunnel", "warning", f"Could not restore {iface}: {exc}"[:200])
        except Exception:
            pass
        return False
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "restore failed").strip()[:200]
        print(f"tunnel_watch: restore {iface} exit {completed.returncode}: {err}", flush=True)
        try:
            state.note_log("tunnel", "warning", f"Restore {iface} failed: {err}")
        except Exception:
            pass
        return False
    print(f"tunnel_watch: restored {iface}", flush=True)
    try:
        state.note_log("tunnel", "info", f"Restarted {iface} after tunnel timeout")
    except Exception:
        pass
    return True


def check_once(now: float | None = None) -> None:
    """Probe each configured tunnel once and restore after consecutive failures."""
    moment = time.monotonic() if now is None else now
    for link, (host, _port, _table, iface) in state._TUNNELS.items():
        if not _configured(iface):
            _fail_counts[iface] = 0
            continue
        if _ping(host):
            _fail_counts[iface] = 0
            continue
        _fail_counts[iface] = _fail_counts.get(iface, 0) + 1
        print(
            f"tunnel_watch: {link} peer {host} unreachable (fail {_fail_counts[iface]})",
            flush=True,
        )
        if _fail_counts[iface] < FAILS_BEFORE_RESTORE:
            continue
        last = _last_restore_at.get(iface, 0.0)
        if moment - last < COOLDOWN_S:
            continue
        if _restore(iface):
            _last_restore_at[iface] = moment
            _fail_counts[iface] = 0
            # Confirm after restore; leave a note if still down.
            time.sleep(1)
            if not _ping(host):
                try:
                    state.note_log(
                        "tunnel",
                        "warning",
                        f"{iface} still cannot reach {host} after restart",
                    )
                except Exception:
                    pass


def tunnel_loop() -> None:
    while not state._stop.is_set():
        try:
            check_once()
        except Exception as exc:
            print(f"tunnel_watch: {exc}", flush=True)
        state._stop.wait(INTERVAL_S)
