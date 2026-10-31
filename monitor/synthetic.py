"""Run RTN PackGen on the physical NIC. Tunnel addresses are refused."""

from __future__ import annotations

import ipaddress
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from checks import _interface_ipv4, _link_interface

UDP_PORT = 8998
CONTROL_PORT = 8999
KEY_PATH = Path(os.environ.get("MONITOR_RTNP_KEY", "/etc/monitor/rtnp.key"))
SRTP_PATH = Path(os.environ.get("MONITOR_RTNP_SRTP", "/etc/monitor/rtnp-srtp.key"))
BUDGET_PATH = Path(os.environ.get("MONITOR_RTNP_BUDGET", "/etc/monitor/rtnp-budget.json"))
BINARY = os.environ.get("MONITOR_RTNP_BIN", "/opt/monitor/rtnp-send")
BIND_LIB = Path(os.environ.get("MONITOR_RTNP_BIND", "/opt/monitor/rtnp-bind.so"))
PRESETS = {
    "voip-g711",
    "voip-g711a",
    "voip-g729",
    "voip-g723",
    "video-conference",
    "fortnite",
    "call-of-duty",
    "remote-desktop",
    "market-data",
}
VOICE_PRESETS = {"voip-g711", "voip-g711a", "voip-g729", "voip-g723"}
CONTROL_HOST = {"wired": "10.7.0.1", "wireless": "10.7.1.1"}
MAX_PPS = 200
MAX_DURATION = 3600
MAX_RUN_BYTES = 2 * 1024 * 1024 * 1024
# There is no daily cap. One that stopped every continuous flow for the rest of the UTC day once ran out
# about 15:35 UTC each day, leaving the Apps tiles on "Not running" until midnight. The packet-rate and
# per-run limits below still stop a runaway sender, and a stopped flow is relaunched on the next sync.

_lock = threading.Lock()
_running: dict[tuple[str, str], dict[str, Any]] = {}
_last_failure = ""
_budget_mem: dict[str, Any] = {}
_budget_saved_at: float = 0.0


def is_tunnel_target(host: str) -> bool:
    value = host.strip().lower()
    return value.startswith("10.7.0.") or value.startswith("10.7.1.")


def launch_plan(link: str, target: str, interface: Optional[str]) -> dict[str, str]:
    if link not in {"wired", "wireless"}:
        raise ValueError("Choose wired or Wi-Fi")
    if is_tunnel_target(target):
        raise ValueError("Synthetic traffic must not use the tunnel")
    if not interface:
        raise ValueError("Synthetic traffic needs the physical NIC for this link")
    if interface.startswith("wg"):
        raise ValueError("Synthetic traffic must not leave through WireGuard")
    return {"link": link, "target": target, "interface": interface}


def route_device(route_text: str) -> Optional[str]:
    parts = route_text.split()
    if "dev" not in parts:
        return None
    index = parts.index("dev")
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def route_is_physical(route_text: str, interface: str) -> bool:
    device = route_device(route_text)
    if not device or device.startswith("wg"):
        return False
    return device == interface


def send_args(
    binary: str,
    options: dict[str, Any],
    data_host: str,
    *,
    control_host: str,
    key_file: str,
    srtp_key_file: str,
    forever: bool,
    duration_seconds: Optional[int] = None,
) -> list[str]:
    if is_tunnel_target(data_host) or ":" in str(data_host):
        raise ValueError("Synthetic traffic must use the public IPv4 address")
    if control_host not in CONTROL_HOST.values():
        raise ValueError("The control channel must use the tunnel")
    preset = str(options.get("preset") or "")
    if preset not in PRESETS:
        raise ValueError("Unknown traffic preset")
    if options.get("meter") or options.get("agent") or options.get("sip"):
        raise ValueError("That option is not allowed")
    payload = str(options.get("payload") or "")
    if payload.startswith("file:") or payload.startswith("wav:"):
        raise ValueError("File payloads are not allowed")
    rate = options.get("rate_pps")
    if rate is not None and float(rate) > MAX_PPS:
        raise ValueError("Packet rate is too high")
    args = [
        binary,
        "flow",
        "-t",
        f"{data_host}:{UDP_PORT}",
        "-c",
        f"{control_host}:{CONTROL_PORT}",
        "--preset",
        preset,
        "--json",
        "-i",
        f"{int(options['report_interval_s'])}s",
        "--key-file",
        key_file,
        "--max-rtt",
        str(int(options["max_rtt_ms"])),
        "--max-jitter",
        str(int(options["max_jitter_ms"])),
        "--max-loss",
        str(options["max_loss_pct"]),
    ]
    if options.get("wire") == "rtp":
        args.extend(["--wire", "rtp", "--srtp-key-file", srtp_key_file])
    else:
        # Without RTP there is no RTCP round trip, and the sender's default one-way delay compares this
        # device's clock with the server's. Ask the receiver to reflect packets (same size, so no
        # amplification; UDP 8998 only accepts the WireGuard peers' addresses) and time a real round trip
        # on the agent's own clock, so every agent's figure means the same thing.
        args.extend(["--meter", "rtt"])
    if options.get("rtp"):
        args.extend(["--rtp", str(options["rtp"])])
    if options.get("ptime_ms"):
        args.extend(["--ptime", str(int(options["ptime_ms"]))])
    if options.get("video"):
        args.append("--video")
    if options.get("bidir"):
        if preset not in VOICE_PRESETS:
            raise ValueError("Two-way traffic is only for the voice profiles")
        args.append("--bidir")
    if options.get("rtcp_interval_s") is not None:
        args.extend(["--rtcp-interval", f"{int(options['rtcp_interval_s'])}s"])
    if forever:
        args.append("--forever")
    else:
        duration = int(duration_seconds or 0)
        if duration < 1 or duration > MAX_DURATION:
            raise ValueError("A timed run needs a duration of at most 3600 seconds")
        args.extend(["-d", f"{duration}s"])
    return args


STRESS_PRESETS = {
    "voip-g711",
    "voip-g711a",
    "voip-g729",
    "voip-g723",
    "video-conference",
    "remote-desktop",
    "market-data",
}
STRESS_MAX_PPS = 200_000


def _int_in(value: Any, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{name} is out of range")
    return int(value)


def _dist(value: Any, name: str) -> str:
    text = str(value)
    if not text.startswith("normal:") or not all(part.replace(".", "", 1).isdigit() for part in text[7:].split(",")):
        raise ValueError(f"{name} is not valid")
    return text


def stress_args(
    binary: str,
    options: dict[str, Any],
    data_host: str,
    *,
    control_host: str,
    key_file: str,
    srtp_key_file: str,
) -> list[str]:
    """A stress session. It runs until the agent stops it; the server sets the five-minute end."""
    if is_tunnel_target(data_host) or ":" in str(data_host):
        raise ValueError("Synthetic traffic must use the public IPv4 address")
    if control_host not in CONTROL_HOST.values():
        raise ValueError("The control channel must use the tunnel")
    if options.get("agent") or options.get("sip") or str(options.get("payload") or ""):
        raise ValueError("That option is not allowed")
    proto = str(options.get("proto") or "udp")
    if proto not in {"udp", "icmp"}:
        raise ValueError("Only UDP and ICMP are available")
    kind = str(options.get("kind") or "")
    if not kind.isalnum():
        raise ValueError("Unknown test")
    args = [binary, "flow"]
    if proto == "icmp":
        args += ["-t", data_host, "-p", "icmp"]
    else:
        args += ["-t", f"{data_host}:{UDP_PORT}", "-c", f"{control_host}:{CONTROL_PORT}"]
    preset = options.get("preset")
    if preset:
        if preset not in STRESS_PRESETS:
            raise ValueError("Unknown traffic preset")
        args += ["--preset", str(preset)]
    args += ["--name", f"stress-{kind}", "--json", "-i", "1s", "--key-file", key_file, "--forever"]
    if options.get("wire") == "rtp":
        args += ["--wire", "rtp", "--srtp-key-file", srtp_key_file]
    if options.get("rtp"):
        if options["rtp"] not in {"g711", "g711a", "g729", "g723"}:
            raise ValueError("Unknown codec")
        args += ["--rtp", str(options["rtp"])]
    if options.get("ptime_ms"):
        args += ["--ptime", str(_int_in(options["ptime_ms"], 10, 200, "Packet time"))]
    if options.get("bidir"):
        args.append("--bidir")
    if options.get("vad"):
        args.append("--vad")
    if options.get("video"):
        args.append("--video")
        args += ["--video-fps", str(_int_in(options.get("video_fps"), 1, 120, "Frame rate"))]
        args += ["--video-gop", str(_int_in(options.get("video_gop"), 1, 600, "Keyframe interval"))]
        args += ["--video-i-size", _dist(options.get("video_i_size"), "Keyframe size")]
        args += ["--video-p-size", _dist(options.get("video_p_size"), "Frame size")]
    if options.get("meter"):
        if options["meter"] != "rtt":
            raise ValueError("Unknown meter")
        args += ["--meter", "rtt"]
    if options.get("rate_pps") is not None:
        args += ["--rate-pps", str(_int_in(options["rate_pps"], 1, STRESS_MAX_PPS, "Packet rate"))]
    if options.get("size") is not None:
        args += ["--size", str(_int_in(options["size"], 32, 1472, "Packet size"))]
    if options.get("tos"):
        args += ["--tos", str(_int_in(options["tos"], 0, 252, "Marking"))]
    return args


def endpoint_host(text: str) -> str:
    """Public IPv4 address from a WireGuard endpoint."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            continue
        if stripped.lower().startswith("endpoint"):
            candidate = stripped.split("=", 1)[-1].strip()
        else:
            parts = stripped.split()
            candidate = parts[-1] if len(parts) >= 2 else ""
        if not candidate or candidate == "(none)":
            continue
        host = candidate.rsplit(":", 1)[0].strip()
        if host.startswith("[") or ":" in host or is_tunnel_target(host):
            continue
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version == 4:
            return str(address)
    return ""


def _read_conf(path: Path) -> str:
    """Read a WireGuard config. Python 3.13 raises PermissionError from is_file()."""
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _endpoint_text() -> str:
    chunks = []
    for iface in ("wg0", "wg1"):
        text = _read_conf(Path(f"/etc/wireguard/{iface}.conf"))
        if text:
            chunks.append(text)
        try:
            completed = subprocess.run(
                ["wg", "show", iface, "endpoints"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        chunks.append(completed.stdout)
    return "\n".join(chunks)


def tunnel_warning() -> str:
    """Report a tunnel that would pull measurement traffic back inside WireGuard."""
    if "0.0.0.0/0" in _tunnel_text().replace(" ", ""):
        return "WireGuard AllowedIPs includes 0.0.0.0/0. Synthetic traffic is stopped."
    return ""


def _tunnel_text() -> str:
    chunks = []
    for iface in ("wg0", "wg1"):
        text = _read_conf(Path(f"/etc/wireguard/{iface}.conf"))
        if text:
            chunks.append(text)
        try:
            completed = subprocess.run(
                ["wg", "show", iface, "allowed-ips"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        chunks.append(completed.stdout)
    return "\n".join(chunks)


def _write_srtp(hex_key: str) -> None:
    """Store the SRTP master key as hex text. RTNP 0.2 refuses raw bytes."""
    text = hex_key.strip().lower()
    if len(text) != 60:
        return
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return
    if len(raw) != 30:
        return
    body = (text + "\n").encode()
    try:
        if SRTP_PATH.is_file() and SRTP_PATH.read_bytes() == body:
            return
    except OSError:
        pass
    SRTP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SRTP_PATH.with_name(f"{SRTP_PATH.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(SRTP_PATH)


def _budget() -> dict[str, Any]:
    global _budget_mem
    today = datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if _budget_mem.get("day") == today:
            return dict(_budget_mem)
    try:
        stored = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    if stored.get("day") != today:
        stored = {"day": today, "bytes": 0, "run": 0}
    with _lock:
        _budget_mem = dict(stored)
    return stored


def _save_budget(state: dict[str, Any], *, force: bool = False) -> None:
    global _budget_mem, _budget_saved_at
    now = time.monotonic()
    with _lock:
        _budget_mem = dict(state)
        if not force and _budget_saved_at > 0 and now - _budget_saved_at < 300:
            return
        _budget_saved_at = now
    try:
        BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = BUDGET_PATH.with_name(f"{BUDGET_PATH.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(BUDGET_PATH)
    except OSError:
        return


def _charge(payload: dict[str, Any]) -> bool:
    interval = float(payload.get("interval_s") or 0)
    sent = 0
    octets = 0
    for flow in payload.get("flows") or []:
        sent += int(flow.get("sent") or 0)
        octets += int(float(flow.get("tx_kbps") or 0) * interval * 1000 / 8)
    state = _budget()
    state["bytes"] = int(state.get("bytes") or 0) + octets
    state["run"] = int(state.get("run") or 0) + octets
    over = (
        (interval > 0 and sent / interval > MAX_PPS)
        or int(state["run"]) >= MAX_RUN_BYTES
    )
    _save_budget(state, force=over)
    return over


def _write_key(key: str) -> None:
    content = key + "\n"
    try:
        if KEY_PATH.is_file() and KEY_PATH.read_text(encoding="ascii") == content:
            return
    except OSError:
        pass
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEY_PATH.with_name(f"{KEY_PATH.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(content)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(KEY_PATH)


def route_get_args(target: str, source: Optional[str] = None) -> list[str]:
    """Ask the kernel which NIC would carry this address. A source follows that link's rule."""
    args = ["ip", "route", "get", target]
    if source:
        args.extend(["from", source])
    return args


def jobs_for_links(jobs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """One flow per job and link, so a link can carry several checks at once.

    Continuous checks come first, so they start before a timed run when the
    budget cuts the list short.
    """
    ordered = sorted(
        jobs,
        key=lambda job: (0 if job.get("mode") == "continuous" else 1, str(job.get("link") or "")),
    )
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for job in ordered:
        link = str(job.get("link") or "")
        run_id = job.get("run_id")
        if link in {"wired", "wireless"} and run_id:
            chosen[(str(run_id), link)] = job
    return chosen


def _route_text(target: str, source: Optional[str] = None) -> str:
    try:
        completed = subprocess.run(
            route_get_args(target, source),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout


def _send_env(target: str, source: Optional[str], interface: Optional[str] = None) -> dict[str, str]:
    """Pin measurement packets to this link. The control channel stays on the tunnel."""
    env = os.environ.copy()
    if source and BIND_LIB.is_file():
        env["LD_PRELOAD"] = str(BIND_LIB)
        env["RTNP_BIND_HOST"] = target
        env["RTNP_BIND_SRC"] = source
        if interface:
            env["RTNP_BIND_IFACE"] = interface
    return env


def last_failure() -> str:
    """Why the most recent flow stopped on its own, for the agent's status page."""
    with _lock:
        return _last_failure


def _reap_strays() -> None:
    """A reload leaves the previous senders running. Stop any this process did not start."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return
    with _lock:
        ours = {item["proc"].pid for item in _running.values()}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in ours or pid == os.getpid():
            continue
        try:
            command = (entry / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if command != "rtnp-send":
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass


_routing_pinned: set[tuple[str, str, str]] = set()


def _ensure_link_routing(link: str, interface: Optional[str], source: Optional[str], *, force: bool = False) -> None:
    """Ensure policy routing table and WireGuard fwmark exist for this physical link."""
    if not interface or not source or not Path("/proc").is_dir():
        return
    key = (link, interface, source)
    with _lock:
        if not force and key in _routing_pinned:
            return
    fwmark, table, wg_iface = (51821, 101, "wg1") if link == "wireless" else (51820, 100, "wg0")
    source_priority = table + 1000
    script = (
        f"wg set {wg_iface} fwmark {fwmark} 2>/dev/null || true; "
        f"ip rule del fwmark {fwmark} lookup {table} priority {table} 2>/dev/null || true; "
        f"ip rule add fwmark {fwmark} lookup {table} priority {table} 2>/dev/null || true; "
        f"GW=$(ip route show default dev {interface} 2>/dev/null | awk '{{print $3; exit}}'); "
        f"LINK=$(ip -4 route show dev {interface} scope link 2>/dev/null | awk '{{print $1; exit}}'); "
        f"if [ -n \"$LINK\" ]; then ip route replace \"$LINK\" dev {interface} table {table} 2>/dev/null || true; fi; "
        f"if [ -n \"$GW\" ]; then ip route replace default via \"$GW\" dev {interface} table {table} 2>/dev/null || true; "
        f"else ip route replace default dev {interface} table {table} 2>/dev/null || true; fi; "
        f"ip rule del from {source} lookup {table} priority {table} 2>/dev/null || true; "
        f"ip rule del from {source} lookup {table} priority {source_priority} 2>/dev/null || true; "
        f"ip rule add from {source} lookup {table} priority {source_priority} 2>/dev/null || true"
    )
    try:
        subprocess.run(["sh", "-c", script], check=False, capture_output=True, timeout=5)
        with _lock:
            _routing_pinned.add(key)
    except (OSError, subprocess.TimeoutExpired):
        return


def sync(config: dict, get_json: Callable, post_json: Callable) -> bool:
    """Start one rtnp-send per link. The data target is the enrolled public address."""
    base = str(config.get("server_url") or "").rstrip("/")
    token = config.get("token")
    name = config.get("name")
    if not base or not token or not name:
        return False
    config = dict(config)
    config["post_json"] = post_json
    _reap_strays()
    problem = tunnel_warning()
    body = get_json(f"{base}/api/v1/synthetic/assignment", token, name)
    key = str(body.get("key") or "")
    if key:
        _write_key(key)
    _write_srtp(str(body.get("srtp_key") or ""))
    fast = bool(body.get("fast"))
    wanted = jobs_for_links(list(body.get("jobs") or []))
    if problem:
        for run_id, link in wanted:
            _finish(config, run_id, link, "failed", problem)
        return False
    with _lock:
        running = [item for item in _running if item in wanted]
        stale = [item for item in _running if item not in wanted]
    for item in stale:
        _stop(item)
    data_host = (
        str(body.get("target_host") or "")
        or str(config.get("measurement_host") or "")
        or endpoint_host(_endpoint_text())
    )
    for item, job in wanted.items():
        if item in running:
            continue
        _ensure(config, item, job, data_host)
    return fast


def _ensure(config: dict, item: tuple[str, str], job: dict[str, Any], target: str) -> None:
    run_id, link = item
    try:
        interface = _link_interface(link)
        plan = launch_plan(link, target, interface)
        source = _interface_ipv4(interface) if interface else None
        if link == "wireless" and not source:
            raise ValueError("Wi-Fi has no address")
        if link == "wireless" and not BIND_LIB.is_file():
            raise ValueError("Wi-Fi traffic needs the interface binding")
        _ensure_link_routing(link, interface, source)
        route = _route_text(target, source)
        if route and not route_is_physical(route, plan["interface"]):
            _ensure_link_routing(link, interface, source, force=True)
            route = _route_text(target, source)
        if route and not route_is_physical(route, plan["interface"]):
            device = route_device(route) or ""
            if device.startswith("wg"):
                raise ValueError("Synthetic traffic would leave through WireGuard")
            raise ValueError("Synthetic traffic would leave through the wrong interface")
        control = CONTROL_HOST[link]
        forever = job.get("mode") == "continuous"
        if job.get("mode") == "stress":
            args = stress_args(
                BINARY,
                job["options"],
                target,
                control_host=control,
                key_file=str(KEY_PATH),
                srtp_key_file=str(SRTP_PATH),
            )
        else:
            args = send_args(
                BINARY,
                job["options"],
                target,
                control_host=control,
                key_file=str(KEY_PATH),
                srtp_key_file=str(SRTP_PATH),
                forever=forever,
                duration_seconds=job.get("duration_seconds"),
            )
    except (ValueError, KeyError) as exc:
        _finish(config, run_id, link, "failed", str(exc))
        return
    wanted_restart = int(job.get("restart_token") or 0)
    with _lock:
        current = _running.get(item)
        if (
            current
            and current["args"] == args
            and current["proc"].poll() is None
            and not restart_pending(current.get("restart_token"), wanted_restart)
        ):
            return
    if current:
        _stop(item)
    if not Path(BINARY).is_file():
        _finish(config, run_id, link, "failed", "rtnp-send is not installed")
        return
    if job.get("mode") != "stress":
        state = _budget()
        state["run"] = 0
        _save_budget(state)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_send_env(target, source, interface),
    )
    with _lock:
        _running[item] = {"args": args, "proc": proc, "mode": job.get("mode"), "restart_token": wanted_restart}
    if job.get("mode") == "stress":
        # The server ends a stress test after five minutes at most. If it cannot be
        # reached, this timer still stops the flow.
        seconds = max(1, min(int(job.get("ends_in_s") or 1), 300))
        timer = threading.Timer(seconds, _stop_if_running, args=(item, proc))
        timer.daemon = True
        timer.start()
    threading.Thread(
        target=_read_output,
        args=(config, item, proc),
        name=f"rtnp-{link}",
        daemon=True,
    ).start()


# A continuous flow that reports nothing for 3 intervals, or loses every packet for 6, is restarted.
# The second case matters: a sender that came back after a restart once lost every packet for 98 minutes
# while the rest of the network was fine, and only a fresh start cleared it.
MISSING_INTERVALS = 3
SILENT_INTERVALS = 6


def restart_pending(running: Optional[int], wanted: int) -> bool:
    """True when the server has asked for a newer restart than the one this process applied."""
    return int(wanted or 0) > int(running or 0)


def flow_state(flow: dict) -> str:
    """"missing" when an interval has no receive figures, "lost" when every packet was lost, else "" for a live flow."""
    if flow.get("rx") is None and flow.get("loss_pct") is None:
        return "missing"
    loss = flow.get("loss_pct")
    if isinstance(loss, (int, float)) and loss >= 99.9:
        return "lost"
    return ""


def _read_output(config: dict, item: tuple[str, str], proc: subprocess.Popen) -> None:
    global _last_failure
    run_id, link = item
    base = str(config.get("server_url") or "").rstrip("/")
    assert proc.stdout is not None
    missing_rx = 0
    for line in proc.stdout:
        payload = _json_line(line)
        if payload is None or payload.get("type") not in {None, "interval"}:
            if payload and payload.get("type") == "final":
                continue
            if payload is None:
                continue
        if payload.get("type") == "final":
            continue
        if "flows" not in payload:
            continue
        flow0 = (payload.get("flows") or [{}])[0]
        silent = flow_state(flow0)
        if silent:
            missing_rx += 1
            limit = MISSING_INTERVALS if silent == "missing" else SILENT_INTERVALS
            if mode_of(item) == "continuous" and missing_rx >= limit:
                with _lock:
                    state = _running.get(item)
                    if state and state["proc"] is proc:
                        _running.pop(item, None)
                try:
                    import agent_state
                    reason = "3 missing intervals" if silent == "missing" else f"{SILENT_INTERVALS} intervals with every packet lost"
                    agent_state.note_log("synthetic", "info", f"{link} flow restarted after {reason}")
                except Exception:
                    pass
                proc.terminate()
                break
        else:
            missing_rx = 0
            if mode_of(item) == "continuous":
                with _lock:
                    if _last_failure.startswith(f"{link}:"):
                        _last_failure = ""
        if mode_of(item) != "stress" and _charge(payload):
            proc.terminate()
            _finish(config, run_id, link, "failed", "Traffic cap reached")
            break
        index = int(payload.get("index") or 0)
        unix_ms = payload.get("unix_ms")
        recorded = datetime.now(timezone.utc).isoformat()
        if isinstance(unix_ms, (int, float)):
            recorded = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).isoformat()
        try:
            post = config["post_json"]
            post(
                f"{base}/api/v1/synthetic/samples",
                {
                    "run_id": run_id,
                    "link": link,
                    "interval_index": index,
                    "recorded_at": recorded,
                    "metrics": payload,
                },
                config.get("token"),
                config.get("name"),
            )
        except Exception:
            continue
    try:
        code = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait(timeout=5)
    note = ""
    if proc.stderr is not None:
        try:
            lines = [line for line in proc.stderr.read().splitlines() if line.strip()]
            note = lines[-1].strip()[:200] if lines else ""
        except (OSError, ValueError):
            note = ""
        finally:
            try:
                proc.stderr.close()
            except OSError:
                pass
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass
    with _lock:
        state = _running.get(item)
        mode = state["mode"] if state else None
        if state and state["proc"] is proc:
            _running.pop(item, None)
    if mode == "manual":
        _finish(config, run_id, link, "finished" if code == 0 else "failed", None)
    elif mode == "stress":
        if state is not None and state["proc"] is proc and code != 0:
            _finish(config, run_id, link, "failed", note or f"rtnp-send exited with code {code}")
    elif state is not None and code != 0:
        # A continuous flow that stops by itself is relaunched on the next sync.
        # Without this it fails in silence: nothing finishes the run, and the
        # sender's own complaint used to go to /dev/null.
        trouble = note or f"rtnp-send exited with code {code}"
        with _lock:
            _last_failure = f"{link}: {trouble}"
        try:
            import agent_state
            agent_state.note_log("synthetic", "warning", f"{link} flow stopped: {trouble}")
        except Exception:
            pass
        print(f"synthetic: {link} flow stopped: {trouble}", file=sys.stderr, flush=True)


def mode_of(item: tuple[str, str]) -> Optional[str]:
    with _lock:
        state = _running.get(item)
    return state["mode"] if state else None


def _stop_if_running(item: tuple[str, str], proc: subprocess.Popen) -> None:
    with _lock:
        state = _running.get(item)
        same = state is not None and state["proc"] is proc
    if same:
        _stop(item)
    elif proc.poll() is None:
        proc.terminate()


def _json_line(line: str) -> Optional[dict]:
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _finish(config: dict, run_id: str, link: str, status: str, message: Optional[str]) -> None:
    base = str(config.get("server_url") or "").rstrip("/")
    try:
        config["post_json"](
            f"{base}/api/v1/synthetic/runs/{run_id}/finish",
            {"link": link, "status": status, "message": message},
            config.get("token"),
            config.get("name"),
        )
    except (OSError, ValueError, KeyError):
        return


def _stop(item: tuple[str, str]) -> None:
    with _lock:
        state = _running.pop(item, None)
    if not state:
        return
    proc = state["proc"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def stop_all() -> None:
    with _lock:
        items = list(_running)
    for item in items:
        _stop(item)
