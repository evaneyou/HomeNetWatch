"""Watch WireGuard tunnels and restart a dead one locally.

The Wi-Fi radio can stay up while wg1 stops handshaking. App checks then fail against
10.7.1.1 even though internet over wlan0 is fine. This loop pings the tunnel peers and
asks restore_tunnel.sh (via a narrow sudoers rule) to restart the unit.

When the ISP was down, a restore may have re-pinned routes against an empty default
gateway. After the access plane recovers, retry quickly so the measurement tunnel comes
back without waiting for the long outage cooldown.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import agent_state as state

RESTORE = Path(os.environ.get("MONITOR_RESTORE_TUNNEL", "/opt/monitor/restore_tunnel.sh"))
FAILS_BEFORE_RESTORE = 2
# While the home internet path is also down, do not thrash wg-quick.
COOLDOWN_OUTAGE_S = 15 * 60
# Once public access on that link works again, re-pin / restart within about a minute.
COOLDOWN_RECOVERY_S = 60
PROBE_TIMEOUT_S = 2
INTERVAL_S = 30
RESTORE_SETTLE_S = 5
ACCESS_PROBES = ("1.1.1.1", "8.8.8.8")

_fail_counts: dict[str, int] = {"wg0": 0, "wg1": 0}
# None means never restored; do not seed 0.0 or a fresh boot's small monotonic clock
# looks like "just restored" for the outage cooldown window.
_last_restore_at: dict[str, float | None] = {"wg0": None, "wg1": None}


def _configured(iface: str) -> bool:
    """True when this tunnel is set up on the Pi.

    The agent runs as the monitor user, which cannot read /etc/wireguard (root only, mode 700), so asking whether
    the .conf file exists raises PermissionError and the whole check stopped every 30 seconds. Whether the
    wg-quick unit is enabled says the same thing, and any user may ask systemd.
    """
    try:
        if Path(f"/etc/wireguard/{iface}.conf").is_file():
            return True
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["systemctl", "is-enabled", "--quiet", f"wg-quick@{iface}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _iface_up(iface)
    return completed.returncode == 0


def _iface_up(iface: str) -> bool:
    return Path(f"/sys/class/net/{iface}").is_dir()


def _link_dev(link: str) -> str | None:
    """Physical NIC for this tunnel (eth*/en* for wired, associated wlan* for Wi-Fi)."""
    if link == "wired":
        try:
            completed = subprocess.run(
                ["ip", "-o", "link", "show"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in (completed.stdout or "").splitlines():
            parts = line.split(": ")
            if len(parts) < 2:
                continue
            name = parts[1].split("@", 1)[0]
            if name.startswith(("eth", "enp", "end", "eno", "ens")):
                return name
        return None
    # Prefer an associated Wi-Fi NIC with an IPv4 address.
    net = Path("/sys/class/net")
    if not net.is_dir():
        return None
    for pass_name in ("associated", "any"):
        for path in sorted(net.iterdir()):
            name = path.name
            if name in {"lo"} or name.startswith(("wg", "tun", "docker", "veth", "br-")):
                continue
            if not (path / "wireless").is_dir():
                continue
            try:
                carrier = (path / "carrier").read_text(encoding="utf-8").strip()
            except OSError:
                carrier = ""
            if pass_name == "associated" and carrier != "1":
                continue
            try:
                addrs = subprocess.run(
                    ["ip", "-4", "-o", "addr", "show", "dev", name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if addrs.stdout and addrs.stdout.strip():
                return name
    return None


def _ping(host: str, *, iface: str | None = None) -> bool:
    cmd = ["ping", "-n", "-c", "1", "-W", str(PROBE_TIMEOUT_S)]
    if iface:
        cmd.extend(["-I", iface])
    cmd.append(host)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=PROBE_TIMEOUT_S + 2,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _access_ok(link: str) -> bool:
    """True when this link can still reach the public internet (measurement tunnel aside)."""
    dev = _link_dev(link)
    if not dev:
        return False
    return any(_ping(host, iface=dev) for host in ACCESS_PROBES)


def _peer_ok(host: str, iface: str) -> bool:
    if _iface_up(iface) and _ping(host, iface=iface):
        return True
    return _ping(host)


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
        if _peer_ok(host, iface):
            _fail_counts[iface] = 0
            continue
        access_ok = _access_ok(link)
        _fail_counts[iface] = _fail_counts.get(iface, 0) + 1
        print(
            f"tunnel_watch: {link} peer {host} unreachable "
            f"(fail {_fail_counts[iface]}, access={'ok' if access_ok else 'down'})",
            flush=True,
        )
        if _fail_counts[iface] < FAILS_BEFORE_RESTORE:
            continue
        cooldown = COOLDOWN_RECOVERY_S if access_ok else COOLDOWN_OUTAGE_S
        last = _last_restore_at.get(iface)
        if last is not None and moment - last < cooldown:
            continue
        # Record the attempt time even when restore fails, so we do not spin sudo.
        _last_restore_at[iface] = moment
        if not _restore(iface):
            continue
        # Give WireGuard time to handshake and for PostUp to re-pin the default route.
        time.sleep(RESTORE_SETTLE_S)
        if _peer_ok(host, iface):
            _fail_counts[iface] = 0
            try:
                state.note_log("tunnel", "info", f"{iface} peer {host} reachable after restart")
            except Exception:
                pass
            continue
        # Keep the fail count high so the next recovery window retries quickly.
        _fail_counts[iface] = FAILS_BEFORE_RESTORE
        try:
            state.note_log(
                "tunnel",
                "warning",
                f"{iface} still cannot reach {host} after restart"
                + (" (home internet on this link is up)" if access_ok else ""),
            )
        except Exception:
            pass


def restore_all_configured() -> None:
    """Best-effort bounce of every configured tunnel (used after a probe restart)."""
    for _link, (_host, _port, _table, iface) in state._TUNNELS.items():
        if not _configured(iface):
            continue
        _restore(iface)


def tunnel_loop() -> None:
    # First pass soon after start so a stale pin from an overnight outage is not left for minutes.
    while not state._stop.is_set():
        try:
            check_once()
        except Exception as exc:
            print(f"tunnel_watch: {exc}", flush=True)
        state._stop.wait(INTERVAL_S)
