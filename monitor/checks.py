"""Bandwidth Test and web-service HTTP checks for the remote agent."""

from __future__ import annotations

import json
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TUNNEL_HOST = "10.7.0.1"
LIBRESPEED_PORT = 8080
LIBRESPEED_SECONDS = 8
LIBRESPEED_WIRELESS_CONCURRENT = 1
LIBRESPEED_WIRELESS_UPLOAD_SIZE = 256
HTTP_SLOW_MS = 3000
DOWNLOAD_LIMIT = 65536
# Ping's default gap is one second, so five probes take about four seconds of waiting.
# 200ms is the shortest gap ping allows for a normal user.
PROBE_GAP_SECONDS = "0.2"
_IFACE_TTL = 60.0
_DNS_TTL = 60.0

_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_SSL = ssl.create_default_context()
_cache_lock = threading.Lock()
# Scans on the link adapter and the test batch take this in turn.
link_lock = threading.Lock()
LINK_SCAN_FLOOR = 900
_iface_cache: dict[str, tuple[float, str | None]] = {}
_addr_cache: dict[str, tuple[float, str | None]] = {}
_dns_cache: dict[tuple[str, int], tuple[float, str | None]] = {}
_tools: dict[str, str | None] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stage(name: str, started: float, status: str) -> dict:
    return {"name": name, "duration_ms": round((time.perf_counter() - started) * 1000, 1), "status": status}


def _result(
    test: dict,
    started: datetime,
    status: str,
    stages: list[dict],
    metrics: dict,
    message: str | None = None,
) -> dict:
    finished = _now()
    body = {
        "id": uuid.uuid4().hex,
        "test_slug": test["slug"],
        "network_slug": test.get("network_slug") or "wan",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "status": status,
        "stages": stages,
        "metrics": metrics,
    }
    if message:
        body["message"] = message
    return body


def _valid_host(host: str) -> bool:
    return bool(_HOST.fullmatch(host)) and ".." not in host


def _tool(name: str) -> str | None:
    if name not in _tools:
        _tools[name] = shutil.which(name)
    return _tools[name]


def _remember(store: dict, key: str, value: str | None) -> str | None:
    with _cache_lock:
        store[key] = (time.monotonic(), value)
    return value


def _cached(store: dict, key: str, ttl: float, load) -> str | None:
    now = time.monotonic()
    with _cache_lock:
        hit = store.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    return _remember(store, key, load())


def _builtin_wifi(entry: Path) -> bool:
    try:
        return "/usb" not in (entry / "device").resolve().as_posix()
    except OSError:
        return True


def link_is_busy() -> bool:
    if link_lock.acquire(blocking=False):
        link_lock.release()
        return False
    return True


def scan_may_run(*, role: str, last_link_scan: float, now: float, link_busy: bool, forced: bool) -> bool:
    """A second adapter can scan on its own interval. The link adapter waits out tests and 15 minutes."""
    if role != "link":
        return True
    if link_busy:
        return False
    if forced:
        return True
    return now - last_link_scan >= LINK_SCAN_FLOOR


def _is_monitor_vif(entry: Path) -> bool:
    if entry.name.endswith("mon"):
        return True
    try:
        # 803 is ARPHRD_IEEE80211_RADIOTAP (Kismet / airmon monitor interface).
        return (entry / "type").read_text(encoding="utf-8").strip() == "803"
    except OSError:
        return False


def _wireless_entries(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name == "lo" or name.startswith(("wg", "tun", "docker", "veth", "br-", "_")):
            continue
        if _is_monitor_vif(entry):
            continue
        if (entry / "wireless").is_dir():
            found.append(entry)
    return found


def _device_path(entry: Path) -> str:
    try:
        return (entry / "device").resolve().as_posix().lower()
    except OSError:
        return ""


def _is_usb(entry: Path) -> bool:
    return "/usb" in _device_path(entry)


def _is_mediatek(entry: Path) -> bool:
    chunks = []
    for name in ("modalias", "uevent"):
        try:
            chunks.append((entry / "device" / name).read_text(encoding="utf-8", errors="replace").lower())
        except OSError:
            continue
    try:
        chunks.append((entry / "device" / "driver").resolve().name.lower())
    except OSError:
        pass
    text = "\n".join(chunks)
    return "0e8d" in text or "mt76" in text or "mediatek" in text


def _carrier_up(entry: Path) -> bool:
    try:
        return (entry / "carrier").read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def _link_from_entries(entries: list[Path], ipv4_of) -> str | None:
    ranked: list[tuple[int, str]] = []
    for entry in entries:
        if not _carrier_up(entry):
            continue
        if not ipv4_of(entry.name):
            continue
        ranked.append((0 if _builtin_wifi(entry) else 1, entry.name))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][1]


def _radio_from_entries(entries: list[Path], link: str | None) -> str | None:
    others = [entry for entry in entries if entry.name != link]
    if not others:
        return None
    others.sort(key=lambda entry: ((0 if _is_usb(entry) and _is_mediatek(entry) else 1 if _is_usb(entry) else 2), entry.name))
    return others[0].name


def _iw_first_interface() -> str | None:
    if shutil.which("iw") is None:
        return None
    try:
        completed = subprocess.run(["iw", "dev"], check=False, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Interface":
            return parts[1]
    return None


def parse_link_ssid(text: str) -> str:
    """SSID from `iw dev <iface> link`. Empty when the adapter is not joined."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("SSID:"):
            return stripped.split(":", 1)[1].strip()[:64]
    return ""


def parse_link_bssid(text: str) -> str:
    """BSSID from `iw dev <iface> link`. Empty when the adapter is not joined."""
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0].lower() == "connected" and parts[1].lower() == "to":
            mac = parts[2].lower()
            if len(mac.split(":")) == 6:
                return mac
    return ""


def wifi_roles(root: Path | None = None, ipv4_of=None) -> dict[str, str | None]:
    """link carries traffic. radio is the other wireless NIC, with a USB MediaTek preferred."""
    sys_root = root or Path("/sys/class/net")
    reader = ipv4_of or _read_interface_ipv4
    entries = _wireless_entries(sys_root)
    link = _link_from_entries(entries, reader)
    if link is None and root is None:
        link = _iw_first_interface()
    return {"link": link, "radio": _radio_from_entries(entries, link)}


def interface_roles() -> dict[str, str | None]:
    """Ethernet NIC, the Wi-Fi NIC that carries traffic, and the other NIC used to listen."""
    roles = wifi_roles()
    return {"wired": _wired_interface(), "link": roles["link"], "radio": roles["radio"]}


def lan_address(roles: dict[str, str | None] | None = None, ipv4_of=None) -> str | None:
    """The Pi's address on the home LAN: the wired NIC's, else the Wi-Fi NIC that carries traffic.

    Read from the adapters, never from the route to the server. Once enrolled, the server is
    reached through a WireGuard tunnel, so that route reports the tunnel's own address, which is
    not on the LAN at all.
    """
    found = roles if roles is not None else interface_roles()
    reader = ipv4_of or _read_interface_ipv4
    for name in (found.get("wired"), found.get("link")):
        if name:
            address = reader(name)
            if address:
                return address
    return None


def _wireless_interface() -> str | None:
    """Wi-Fi NIC. The built-in radio wins when a USB adapter is also associated."""
    return wifi_roles()["link"]


def _wired_interface() -> str | None:
    root = Path("/sys/class/net")
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        name = entry.name.split("@", 1)[0]
        if name.startswith(("eth", "enp", "end", "eno", "ens")):
            return name
    return None


def _link_interface(link: str) -> str | None:
    """Ethernet for the wired tunnel, Wi-Fi for the wireless tunnel."""
    if link == "wireless":
        return _cached(_iface_cache, "wireless", _IFACE_TTL, _wireless_interface)
    if link != "wired":
        return None
    return _cached(_iface_cache, "wired", _IFACE_TTL, _wired_interface)


def _read_interface_ipv4(name: str) -> str | None:
    try:
        completed = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            return parts[parts.index("inet") + 1].split("/", 1)[0]
    return None


def _interface_ipv4(name: str) -> str | None:
    return _cached(_addr_cache, name, _IFACE_TTL, lambda: _read_interface_ipv4(name))


def _probe_interface(test: dict) -> str | None:
    """Public checks leave through the NIC for this link. Tunnel targets follow the tunnel route."""
    link = str(test.get("link") or "")
    if link not in {"wired", "wireless"}:
        return None
    target = str(test.get("target") or "")
    if "10.7.0." in target or "10.7.1." in target:
        return None
    return _link_interface(link)


def _bind_socket(sock: socket.socket, iface: str | None) -> None:
    if not iface:
        return
    option = getattr(socket, "SO_BINDTODEVICE", 25)
    try:
        sock.setsockopt(socket.SOL_SOCKET, option, iface.encode() + b"\0")
    except OSError:
        return


def _threshold_ms(test: dict) -> float | None:
    value = test.get("threshold_ms")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > DOWNLOAD_LIMIT:
            break
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def check_http(test: dict) -> dict:
    started_at = _now()
    stages: list[dict] = []
    url = str(test.get("target") or "")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        parsed = urlsplit("")
        host = None
        port = 80
    if parsed.scheme not in {"http", "https"} or not host or not (1 <= port <= 65535):
        return _result(
            test,
            started_at,
            "fail",
            [{"name": "dns", "duration_ms": 0.0, "status": "fail"}],
            {"rtt_ms": 0.0, "loss_pct": 100.0, "total_ms": 0.0},
            "Web service target must be an http or https URL",
        )
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    sock: socket.socket | None = None
    try:
        mark = time.perf_counter()
        address = _resolve_ipv4(host, port)
        if address is None:
            stages.append(_stage("dns", mark, "fail"))
            return _result(test, started_at, "fail", stages, _http_metrics(stages), "DNS lookup failed")
        stages.append(_stage("dns", mark, "ok"))

        mark = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)
        _bind_socket(sock, _probe_interface(test))
        sock.connect((address, port))
        stages.append(_stage("tcp", mark, "ok"))

        mark = time.perf_counter()
        if parsed.scheme == "https":
            sock = _SSL.wrap_socket(sock, server_hostname=host)
            stages.append(_stage("tls", mark, "ok"))
        else:
            stages.append({"name": "tls", "duration_ms": 0.0, "status": "ok"})

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: home-network-sensor\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        )
        mark = time.perf_counter()
        sock.sendall(request.encode("ascii"))
        head, _rest = _read_headers(sock)
        stages.append(_stage("ttfb", mark, "ok" if head else "fail"))
        if not head:
            return _result(test, started_at, "fail", stages, _http_metrics(stages), "No HTTP response")
        status_line = head.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        code = 0
        parts = status_line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            code = int(parts[1])
    except (OSError, ssl.SSLError) as exc:
        if stages and stages[-1]["status"] == "ok":
            failed = {0: "dns", 1: "tcp", 2: "tls", 3: "ttfb"}.get(len(stages), "ttfb")
            stages.append({"name": failed, "duration_ms": 0.0, "status": "fail"})
        elif not stages:
            stages.append({"name": "dns", "duration_ms": 0.0, "status": "fail"})
        return _result(test, started_at, "fail", stages, _http_metrics(stages), str(exc))
    finally:
        if sock is not None:
            sock.close()

    total = sum(stage["duration_ms"] for stage in stages)
    threshold = _threshold_ms(test)
    status = "ok"
    message = status_line
    if threshold is None:
        if code >= 500 or code == 0:
            status = "fail"
        elif total >= HTTP_SLOW_MS:
            status = "degraded"
            message = f"{status_line} in {total:.0f} ms"
    elif code == 0:
        status = "fail"
    elif code >= 400 or total > threshold:
        status = "degraded"
        message = status_line if code >= 400 else f"{status_line} in {total:.0f} ms"
    if status != "ok":
        for stage in reversed(stages):
            if stage["status"] == "ok":
                stage["status"] = status
                break
    return _result(test, started_at, status, stages, _http_metrics(stages), message)


def _resolve_ipv4(host: str, port: int) -> str | None:
    key = (host, port)
    now = time.monotonic()
    with _cache_lock:
        hit = _dns_cache.get(key)
        if hit is not None and now - hit[0] < _DNS_TTL and hit[1] is not None:
            return hit[1]

    def load() -> str | None:
        try:
            return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        except OSError:
            return None

    # Failures stay cached for a few seconds so a down name is not looked up on every worker.
    found = load()
    with _cache_lock:
        _dns_cache[key] = (now, found) if found is not None else (now - _DNS_TTL + 5, found)
    return found


def _http_metrics(stages: list[dict]) -> dict:
    total = round(sum(stage["duration_ms"] for stage in stages), 1)
    failed = any(stage["status"] == "fail" for stage in stages)
    return {"rtt_ms": total, "loss_pct": 100.0 if failed else 0.0, "total_ms": total}


_LOSS = re.compile(r"([\d.]+)% packet loss")
_RTT = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?")


def parse_ping(output: str) -> tuple[float | None, float | None, float | None]:
    loss_match = _LOSS.search(output)
    rtt_match = _RTT.search(output)
    loss = float(loss_match.group(1)) if loss_match else None
    rtt = float(rtt_match.group(2)) if rtt_match else None
    jitter = float(rtt_match.group(4)) if rtt_match and rtt_match.group(4) else None
    return loss, rtt, jitter


def _path_status(loss: float | None, rtt: float | None) -> str:
    if loss is None or loss >= 20 or rtt is None:
        return "fail"
    if loss > 0 or rtt > 150:
        return "degraded"
    return "ok"


def check_ping(test: dict) -> dict:
    started_at = _now()
    started = time.perf_counter()
    host = _host_only(str(test.get("target") or TUNNEL_HOST))
    if _tool("ping") is None:
        return _result(test, started_at, "fail", [{"name": "icmp", "duration_ms": 0.0, "status": "fail"}], _path_metrics(None, 100.0, 0.0), "ping is not installed")
    if not _valid_host(host):
        return _result(test, started_at, "fail", [{"name": "icmp", "duration_ms": 0.0, "status": "fail"}], _path_metrics(None, 100.0, 0.0), "ICMP target is not a host")
    try:
        command = ["ping", "-n", "-c", "5", "-i", PROBE_GAP_SECONDS, "-W", "2", "-w", "12"]
        iface = _probe_interface(test)
        if iface:
            command.extend(["-I", iface])
        command.append(host)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        total = round((time.perf_counter() - started) * 1000, 1)
        return _result(test, started_at, "fail", [{"name": "icmp", "duration_ms": total, "status": "fail"}], _path_metrics(None, 100.0, total), str(exc))
    output = f"{completed.stdout}\n{completed.stderr}"
    loss, rtt, jitter = parse_ping(output)
    total = round((time.perf_counter() - started) * 1000, 1)
    threshold = _threshold_ms(test)
    if threshold is None:
        status = _path_status(loss, rtt)
    elif loss is None or rtt is None or loss >= 100:
        status = "fail"
    elif loss > 0 or rtt > threshold:
        status = "degraded"
    else:
        status = "ok"
    return _result(
        test,
        started_at,
        status,
        [{"name": "icmp", "duration_ms": rtt if rtt is not None else total, "status": status}],
        _path_metrics(rtt, loss if loss is not None else 100.0, total, jitter),
        host,
    )


def parse_mtr_hubs(payload: str) -> list[dict]:
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return []
    report = body.get("report") if isinstance(body, dict) else None
    hubs = report.get("hubs") if isinstance(report, dict) else None
    if not isinstance(hubs, list):
        return []
    hops = []
    for index, hub in enumerate(hubs, start=1):
        if not isinstance(hub, dict):
            continue
        try:
            hops.append(
                {
                    "hop": int(hub.get("count") or index),
                    "host": str(hub.get("host") or "?"),
                    "loss_pct": round(float(hub.get("Loss%") or 0), 2),
                    "best_ms": round(float(hub.get("Best") or 0), 2),
                    "avg_ms": round(float(hub.get("Avg") or 0), 2),
                    "worst_ms": round(float(hub.get("Wrst") or 0), 2),
                }
            )
        except (TypeError, ValueError):
            continue
    return hops


def check_mtr(test: dict) -> dict:
    started_at = _now()
    started = time.perf_counter()
    host = _host_only(str(test.get("target") or TUNNEL_HOST))
    if _tool("mtr") is None:
        return _result(test, started_at, "fail", [{"name": "trace", "duration_ms": 0.0, "status": "fail"}], _trace_metrics([], 0.0), "mtr is not installed")
    if not _valid_host(host):
        return _result(test, started_at, "fail", [{"name": "trace", "duration_ms": 0.0, "status": "fail"}], _trace_metrics([], 0.0), "MTR target is not a host")
    last_error = "mtr failed"
    hops: list[dict] = []
    for mode in ([], ["--udp"]):
        try:
            source = _interface_ipv4(iface) if (iface := _probe_interface(test)) else None
            # A bound source is an IPv4 address, so trace over IPv4: a host name may resolve to IPv6, which mtr refuses.
            bind = ["-4", "-a", source] if source else []
            completed = subprocess.run(
                ["mtr", "--json", "-c", "5", "-n", "-m", "15", *bind, *mode, host],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            continue
        hops = parse_mtr_hubs(completed.stdout)
        if hops:
            break
        detail = (completed.stderr or completed.stdout or last_error).strip().splitlines()
        last_error = detail[-1][:200] if detail else last_error
    total = round((time.perf_counter() - started) * 1000, 1)
    if not hops:
        return _result(test, started_at, "fail", [{"name": "trace", "duration_ms": total, "status": "fail"}], _trace_metrics([], total), last_error)
    loss = hops[-1]["loss_pct"]
    rtt = hops[-1]["avg_ms"]
    dest = rtt if rtt > 0 else None
    threshold = _threshold_ms(test)
    if threshold is None:
        status = _path_status(loss, dest)
    elif dest is None:
        status = "fail"
    elif loss > 0 or dest > threshold:
        status = "degraded"
    else:
        status = "ok"
    message = host
    if status == "fail" and len(hops) > 1 and _mtr_answered_before_last(hops):
        # Many services drop trace probes at the last hop. If an ordinary ping still
        # gets through, the destination is up and the trace is not a failure.
        # The ping goes to the trace target, not the test's own target: the path trace
        # aims at the public address while the ping test aims at the tunnel address.
        ping = check_ping({**test, "target": host, "threshold_ms": None})
        if ping["status"] != "fail":
            status = "ok"
            message = f"{host}: last hop does not answer trace probes, ping is fine"
    return _result(
        test,
        started_at,
        status,
        [{"name": "trace", "duration_ms": total, "status": status}],
        _trace_metrics(hops, total),
        message,
    )


def _mtr_answered_before_last(hops: list[dict]) -> bool:
    """True when at least one hop before the last one replied to the trace."""
    return any(hop["loss_pct"] < 100 and hop["avg_ms"] > 0 for hop in hops[:-1])


def _host_only(target: str) -> str:
    raw = target.strip()
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    return raw.split("/")[0].split("%")[0]


def _path_metrics(rtt: float | None, loss: float, total: float, jitter: float | None = None) -> dict:
    metrics = {"rtt_ms": round(rtt, 2) if rtt is not None else None, "loss_pct": round(loss, 2), "total_ms": total}
    if jitter is not None:
        metrics["jitter_ms"] = round(jitter, 2)
    return metrics


def _trace_metrics(hops: list[dict], total: float) -> dict:
    if not hops:
        return {"rtt_ms": None, "loss_pct": 100.0, "total_ms": total, "hops": []}
    return {
        "rtt_ms": hops[-1]["avg_ms"],
        "loss_pct": hops[-1]["loss_pct"],
        "total_ms": total,
        "hops": hops,
    }


def run_check(test: dict) -> dict | None:
    kind = test.get("kind")
    if kind == "librespeed":
        return check_librespeed(test)
    if kind == "http":
        return check_http(test)
    if kind == "ping":
        return check_ping(test)
    if kind == "mtr":
        return check_mtr(test)
    return None


def check_librespeed(test: dict) -> dict:
    started_at = _now()
    started = time.perf_counter()
    host = _host_only(str(test.get("target") or TUNNEL_HOST))
    if _tool("librespeed-cli") is None:
        return _result(test, started_at, "fail", [_stage("transfer", started, "fail")], _speed_metrics(None, None, None, None, 0), "librespeed-cli is not installed")
    if not _valid_host(host):
        return _result(test, started_at, "fail", [_stage("transfer", started, "fail")], _speed_metrics(None, None, None, None, 0), "Bandwidth Test target is not a host")
    servers = [
        {
            "id": 1,
            "name": "Home Sensor",
            "server": f"http://{host}:{LIBRESPEED_PORT}/",
            "dlURL": "backend/garbage",
            "ulURL": "backend/empty",
            "pingURL": "backend/empty",
            "getIpURL": "backend/getIP",
        }
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(servers, handle)
        listing = handle.name
    wireless = test.get("link") == "wireless" or test.get("network_slug") == "wifi"
    command = [
        "librespeed-cli",
        "--json",
        "--ipv4",
        "--no-icmp",
        "--duration",
        str(LIBRESPEED_SECONDS),
        "--timeout",
        "20",
    ]
    if wireless:
        command.extend(
            [
                "--concurrent",
                str(LIBRESPEED_WIRELESS_CONCURRENT),
                "--upload-size",
                str(LIBRESPEED_WIRELESS_UPLOAD_SIZE),
            ]
        )
    command.extend(["--local-json", listing])
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=LIBRESPEED_SECONDS * 2 + 25,
        )
    except subprocess.TimeoutExpired:
        return _result(test, started_at, "fail", [_stage("transfer", started, "fail")], _speed_metrics(None, None, None, None, 0), "Bandwidth Test timed out")
    finally:
        Path(listing).unlink(missing_ok=True)
    report = parse_librespeed_report(proc.stdout)
    total = round((time.perf_counter() - started) * 1000, 1)
    if proc.returncode != 0 or report is None:
        detail = (proc.stderr or proc.stdout or "Bandwidth Test failed").strip().splitlines()
        return _result(test, started_at, "fail", [_stage("transfer", started, "fail")], _speed_metrics(None, None, None, None, total), detail[-1][:240] if detail else "Bandwidth Test failed")
    down, up, ping, jitter = report
    status = "ok" if down and up else "fail"
    return _result(
        test,
        started_at,
        status,
        [_stage("transfer", started, status)],
        _speed_metrics(down, up, ping, jitter, total),
        f"{host}:{LIBRESPEED_PORT}",
    )


def parse_librespeed_report(payload: str) -> tuple[float, float, float, float] | None:
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return None
    row = body[0] if isinstance(body, list) and body else body
    if not isinstance(row, dict):
        return None
    down = _mbps_value(row.get("download"))
    up = _mbps_value(row.get("upload"))
    ping = _float_value(row.get("ping"))
    jitter = _float_value(row.get("jitter"))
    if down is None and up is None:
        return None
    return down or 0.0, up or 0.0, ping or 0.0, jitter or 0.0


def _speed_metrics(down: float | None, up: float | None, ping: float | None, jitter: float | None, total: float) -> dict:
    return {
        "down_mbps": None if down is None else round(down, 2),
        "up_mbps": None if up is None else round(up, 2),
        "rtt_ms": None if ping is None else round(ping, 2),
        "jitter_ms": None if jitter is None else round(jitter, 2),
        "loss_pct": 0.0,
        "total_ms": total,
    }


def _mbps_value(value: object) -> float | None:
    number = _float_value(value)
    if number is None or number < 0:
        return None
    return number


def _float_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
