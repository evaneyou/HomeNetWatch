"""Watch WireGuard tunnels and restart a dead one locally.

The Wi-Fi radio can stay up while wg1 stops handshaking. App checks then fail against
10.7.1.1 even though internet over wlan0 is fine. This loop pings the tunnel peers and
asks a root helper to restart the unit.

The agent cannot become root: its service sets NoNewPrivileges, which stops sudo. It asks by creating
/run/monitor/restore-wgN instead. systemd watches for that file (hnw-restore-tunnel@wgN.path) and runs
restore_tunnel.sh as root (hnw-restore-tunnel@wgN.service), which writes how it went to
/run/hnw-restore-wgN.result and removes the request.

The same loop watches the Wi-Fi link. After a failed security handshake NetworkManager decides the saved
password is wrong, finds nobody to ask for a new one and stops trying, so the Pi stays off Wi-Fi until someone
reconnects it by hand. When the Wi-Fi adapter has been down for about two minutes the agent asks a second root
helper (hnw-restore-wifi) to rejoin the saved network.

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

# Where the agent leaves a request (its own runtime folder) and where the root helper leaves the outcome.
REQUEST_DIR = Path(os.environ.get("MONITOR_RUN_DIR", "/run/monitor"))
RESULT_DIR = Path(os.environ.get("MONITOR_RESTORE_RESULT_DIR", "/run"))
RESTORE_WAIT_S = 90
RESTORE_POLL_S = 0.5
SYS_NET = Path("/sys/class/net")
# NetworkManager retries by itself for a while, so leave it four checks (about two minutes) first.
WIFI_FAILS_BEFORE_REJOIN = 4
# While the access point is really gone every attempt fails; do not ask more often than this.
WIFI_REJOIN_COOLDOWN_S = 5 * 60
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
_wifi_down_count = 0
_last_wifi_rejoin_at: float | None = None


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


def _restore_failed(iface: str, why: str) -> bool:
    print(f"tunnel_watch: restore {iface} failed: {why}", flush=True)
    try:
        state.note_log("tunnel", "warning", f"Restore {iface} failed: {why}"[:200])
    except Exception:
        pass
    return False


def _ask_helper(name: str, unit: str) -> str:
    """Leave a request for one root helper and wait for it. Returns "" on success, else what went wrong.

    `name` is one of a fixed set (wg0, wg1, wifi): it names the request file, never anything the server sent.
    """
    request = REQUEST_DIR / f"restore-{name}"
    try:
        request.write_text("", encoding="ascii")
    except OSError as exc:
        return f"could not leave a request in {REQUEST_DIR} ({exc}); run update.sh again"
    picked_up = False
    for _ in range(int(RESTORE_WAIT_S / RESTORE_POLL_S)):
        time.sleep(RESTORE_POLL_S)
        if not request.exists():
            picked_up = True
            break
    if not picked_up:
        try:
            request.unlink()
        except OSError:
            pass
        return f"nothing answered the request; check {unit}.path"
    try:
        result = (RESULT_DIR / f"hnw-restore-{name}.result").read_text(encoding="ascii").strip()
    except OSError:
        result = "no result"
    if result != "success":
        return f"the helper reported {result[:60]}; see journalctl -u {unit}"
    return ""


def _restore(iface: str) -> bool:
    """Ask the root helper to restart one tunnel and wait for it. True when the helper reported success."""
    if iface not in _fail_counts:
        return False
    trouble = _ask_helper(iface, f"hnw-restore-tunnel@{iface}")
    if trouble:
        return _restore_failed(iface, trouble)
    print(f"tunnel_watch: restored {iface}", flush=True)
    try:
        state.note_log("tunnel", "info", f"Restarted {iface} after tunnel timeout")
    except Exception:
        pass
    return True


def _wifi_link_down() -> str | None:
    """Name of the Wi-Fi adapter that should carry traffic but has no link, or None.

    Capture radios (hnwradio*, anything in monitor mode) and P2P devices are not links. When any Wi-Fi
    adapter has a link there is nothing to do.
    """
    try:
        paths = sorted(SYS_NET.iterdir())
    except OSError:
        return None
    down: str | None = None
    for path in paths:
        name = path.name
        if name.startswith(("hnwradio", "p2p-", "mon")):
            continue
        if not ((path / "wireless").exists() or (path / "phy80211").exists()):
            continue
        try:
            # 1 is an ordinary station interface; a monitor-mode interface reports 803.
            if (path / "type").read_text(encoding="ascii").strip() != "1":
                continue
        except OSError:
            continue
        try:
            carrier = (path / "carrier").read_text(encoding="ascii").strip()
        except OSError:
            # Reading the carrier of an interface that is switched off is an error: it has no link.
            carrier = "0"
        if carrier == "1":
            return None
        down = down or name
    return down


def check_wifi(now: float | None = None) -> None:
    """Ask for a Wi-Fi rejoin when the link has been down for a while. Only on a Pi with a Wi-Fi tunnel."""
    global _wifi_down_count, _last_wifi_rejoin_at
    moment = time.monotonic() if now is None else now
    if not _configured("wg1"):
        _wifi_down_count = 0
        return
    dev = _wifi_link_down()
    if dev is None:
        _wifi_down_count = 0
        return
    _wifi_down_count += 1
    if _wifi_down_count < WIFI_FAILS_BEFORE_REJOIN:
        return
    if _last_wifi_rejoin_at is not None and moment - _last_wifi_rejoin_at < WIFI_REJOIN_COOLDOWN_S:
        return
    _last_wifi_rejoin_at = moment
    print(f"tunnel_watch: {dev} has had no Wi-Fi link for {_wifi_down_count} checks, asking to rejoin", flush=True)
    trouble = _ask_helper("wifi", "hnw-restore-wifi")
    if trouble:
        print(f"tunnel_watch: Wi-Fi rejoin failed: {trouble}", flush=True)
        try:
            state.note_log("wifi", "warning", f"{dev} is off Wi-Fi and the rejoin failed: {trouble}"[:200])
        except Exception:
            pass
        return
    print(f"tunnel_watch: asked {dev} to rejoin Wi-Fi", flush=True)
    try:
        state.note_log("wifi", "info", f"{dev} had dropped off Wi-Fi and did not retry; asked it to rejoin")
    except Exception:
        pass


def check_once(now: float | None = None) -> None:
    """Probe each configured tunnel once and restore after consecutive failures."""
    moment = time.monotonic() if now is None else now
    try:
        # Wi-Fi first: a tunnel cannot come back over a link that is not there.
        check_wifi(moment)
    except Exception as exc:
        print(f"tunnel_watch: Wi-Fi check failed: {exc}", flush=True)
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
        # Record the attempt time even when restore fails, so we do not ask again at once.
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


def tunnel_loop() -> None:
    # First pass soon after start so a stale pin from an overnight outage is not left for minutes.
    while not state._stop.is_set():
        try:
            check_once()
        except Exception as exc:
            print(f"tunnel_watch: {exc}", flush=True)
        state._stop.wait(INTERVAL_S)
