"""Device health: CPU, memory, disk, the public address, and the report loop."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import agent_state as state
import synthetic
from checks import interface_roles, lan_address
from enrollment import load_config, save_config

def _read_cpu() -> tuple[int, int]:
    parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    numbers = [int(part) for part in parts[1:]]
    idle = numbers[3]
    return idle, sum(numbers)

def cpu_percent() -> float:
    try:
        idle_a, total_a = _read_cpu()
        time.sleep(0.2)
        idle_b, total_b = _read_cpu()
    except OSError:
        return 0.0
    total = total_b - total_a
    if total <= 0:
        return 0.0
    return round((1 - (idle_b - idle_a) / total) * 100, 1)

def memory_mb() -> tuple[int, int]:
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.strip().split()[0])
    total = info.get("MemTotal", 0) // 1024
    available = info.get("MemAvailable", info.get("MemFree", 0)) // 1024
    return max(total - available, 0), total

def disk_mb() -> tuple[int, int]:
    usage = shutil.disk_usage("/")
    return usage.used // (1024 * 1024), usage.total // (1024 * 1024)

def temperature_c() -> float | None:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    if not path.exists():
        return None
    return round(int(path.read_text(encoding="utf-8").strip()) / 1000, 1)

def uptime_seconds() -> int:
    return int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))

def load_1() -> float:
    return float(Path("/proc/loadavg").read_text(encoding="utf-8").split()[0])

def lan_ip(server_url: str) -> str | None:
    """The Pi's address on the home LAN.

    Read from the adapter that sits on the LAN. The route to the server is only a fallback: it is
    right while enrolling, when the server is a public address, but once enrolled it goes through
    the tunnel and would report the tunnel's own address.
    """
    try:
        address = lan_address()
    except OSError:
        address = None
    if address:
        return address
    routed = _routed_ip(server_url)
    return None if routed and synthetic.is_tunnel_target(routed) else routed

def _routed_ip(server_url: str) -> str | None:
    parsed = urllib.parse.urlparse(server_url)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, port))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()

def _global_ip(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if not address.is_global:
        return None
    return str(address)

def _lookup_public() -> tuple[str, str | None] | None:
    request = urllib.request.Request(state.PUBLIC_LOOKUP_URL, headers={"User-Agent": "home-sensor-agent"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict) or body.get("status") != "success":
        return None
    address = _global_ip(body.get("query"))
    if address is None:
        return None
    provider = body.get("isp")
    if not isinstance(provider, str):
        return address, None
    cleaned = " ".join(provider.split())[:128]
    return address, cleaned or None

def _read_public_cache() -> tuple[str, str | None, float] | None:
    try:
        body = json.loads(state.PUBLIC_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    address = _global_ip(body.get("public_ip"))
    checked = body.get("checked_at")
    if address is None or not isinstance(checked, (int, float)):
        return None
    provider = body.get("isp")
    if isinstance(provider, str):
        provider = " ".join(provider.split())[:128] or None
    else:
        provider = None
    return address, provider, float(checked)

def _write_public_cache(address: str, provider: str | None, checked: float) -> None:
    payload = {"public_ip": address, "isp": provider, "checked_at": checked}
    try:
        state.PUBLIC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.PUBLIC_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o640)
        tmp.replace(state.PUBLIC_CACHE_PATH)
    except OSError:
        return

def public_network() -> tuple[str | None, str | None]:
    """Public address and service provider, cached on this agent for 24 hours."""
    now = time.time()
    with state._public_lock:
        cached = state._public_cached
        fresh = cached is not None and now - state._public_cached_at < state.PUBLIC_LOOKUP_TTL
    if fresh and cached is not None:
        return cached
    stored = _read_public_cache()
    if stored is not None and now - stored[2] < state.PUBLIC_LOOKUP_TTL:
        with state._public_lock:
            state._public_cached = (stored[0], stored[1])
            state._public_cached_at = stored[2]
        return stored[0], stored[1]
    found = _lookup_public()
    if found is None:
        if stored is not None:
            return stored[0], stored[1]
        return cached if cached is not None else (None, None)
    _write_public_cache(found[0], found[1], now)
    with state._public_lock:
        state._public_cached = found
        state._public_cached_at = now
    return found

def _device_headers(token: str | None, device_name: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if device_name:
        headers["X-Monitor-Name"] = urllib.parse.quote(device_name, safe="")
    return headers

def get_json(url: str, token: str | None = None, device_name: str | None = None) -> dict:
    request = urllib.request.Request(url, headers=_device_headers(token, device_name), method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def post_json(url: str, payload: dict, token: str | None = None, device_name: str | None = None, timeout: int = 10) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=_device_headers(token, device_name), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def sample(config: dict) -> dict:
    import radio
    mem_used, mem_total = memory_mb()
    disk_used, disk_total = disk_mb()
    public_ip, isp = public_network()
    ssid, bssid = radio.joined_link()
    try:
        ifaces = interface_roles()
    except OSError:
        ifaces = {"wired": None, "link": None, "radio": None}
    return {
        "hostname": socket.gethostname(),
        "agent_version": state.AGENT_VERSION,
        "uptime_seconds": uptime_seconds(),
        "cpu_percent": cpu_percent(),
        "load_1": load_1(),
        "mem_used_mb": mem_used,
        "mem_total_mb": mem_total,
        "disk_used_mb": disk_used,
        "disk_total_mb": disk_total,
        "temp_c": temperature_c(),
        "lan_ip": lan_ip(config.get("server_url", "")),
        "public_ip": public_ip,
        "isp": isp,
        "config_version": int(config.get("config_version") or 0),
        "airwave_mode": radio.airwave_mode(),
        "wired_iface": ifaces["wired"],
        "wifi_link_iface": ifaces["link"],
        "wifi_radio_iface": ifaces["radio"],
        "link_ssid": ssid,
        "link_bssid": bssid,
        "last_error": synthetic.tunnel_warning() or synthetic.last_failure() or state._last_error,
    }

def apply_config(config: dict, body: dict) -> bool:
    changed = False
    if body.get("name") and body["name"] != config.get("name"):
        config["name"] = body["name"]
        changed = True
    interval = body.get("health_interval_seconds")
    if interval and int(interval) != int(config.get("health_interval_seconds") or 60):
        config["health_interval_seconds"] = int(interval)
        changed = True
    scan_interval = body.get("scan_interval_seconds")
    if scan_interval and int(scan_interval) != int(config.get("scan_interval_seconds") or 300):
        config["scan_interval_seconds"] = int(scan_interval)
        changed = True
    version = body.get("config_version")
    if version is not None and int(version) != int(config.get("config_version") or 0):
        config["config_version"] = int(version)
        changed = True
    return changed

def report_once(config: dict) -> None:
    import radio
    import scheduler
    server = config["server_url"].rstrip("/")
    sent = int(config.get("config_version") or 0)
    payload = sample(config)
    pending_logs = state.take_logs()
    if pending_logs:
        payload["logs"] = pending_logs
    try:
        body = post_json(
            f"{server}/api/v1/monitors/health",
            payload,
            config.get("token"),
            config.get("name"),
        )
    except Exception:
        state.restore_logs(pending_logs)
        raise
    radio.remember_trusted(body)
    if apply_config(config, body):
        save_config(config)
        if int(config.get("config_version") or 0) != sent:
            body = post_json(
                f"{server}/api/v1/monitors/health",
                sample(config),
                config.get("token"),
                config.get("name"),
            )
    scheduler.remember_tests(body.get("tests"))
    if not state._last_error.startswith("Kismet HTTP"):
        state._last_error = ""
    state._last_health_at = time.strftime("%Y-%m-%d %H:%M:%S")
    scheduler.honor_restart(config, bool(body.get("restart")))

def health_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                report_once(dict(config))
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                state._last_error = str(exc)
        with state._lock:
            interval = int(load_config().get("health_interval_seconds") or 60)
        state._stop.wait(max(interval, 15))
