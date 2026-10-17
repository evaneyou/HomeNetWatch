"""Live speed test against the LibreSpeed server, on the link the user picked.

Each link has its own WireGuard tunnel (wired: 10.7.0.1, Wi-Fi: 10.7.1.1) that leaves through that
physical interface, so choosing a tunnel address chooses the interface. The test talks the plain
LibreSpeed protocol: download reads /garbage.php, upload posts to /empty.php, ping times /empty.php.

LibreSpeed's own command line tool only prints a result at the end, and the dashboard wants a reading
every half second, so this is a small client of its own. It runs ping, then download, then upload, and
reports to the server as it goes. The server answers each report with whether the user pressed Stop.
"""

from __future__ import annotations

import http.client
import os
import socket
import threading
import time
import urllib.error
from typing import Any, Callable, Optional

from checks import link_lock

PORT = 8080
HOSTS = {"wired": "10.7.0.1", "wireless": "10.7.1.1"}
LINK_NAMES = {"wired": "Wired", "wireless": "Wi-Fi"}
DOWN_STREAMS = 4
UP_STREAMS = 3
TICK_SECONDS = 0.5
READ_BUFFER = 256 * 1024
UP_CHUNK = os.urandom(256 * 1024)
UP_BODY_BYTES = 24 * 1024 * 1024
WARM_UP_MS = 3000
POLL_SECONDS = 3

Emit = Callable[[int, str, int, float], None]


def _connect(host: str, timeout: float = 8.0) -> http.client.HTTPConnection:
    conn = http.client.HTTPConnection(host, PORT, timeout=timeout)
    conn.connect()
    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return conn


def measure_ping(host: str, count: int = 8) -> tuple[Optional[float], Optional[float]]:
    """Round trip and jitter in ms: a request over one connection, the first (which connects) not counted."""
    times: list[float] = []
    conn: Optional[http.client.HTTPConnection] = None
    try:
        conn = _connect(host, 5.0)
        for index in range(count + 1):
            started = time.perf_counter()
            conn.request("GET", f"/empty.php?r={index}")
            conn.getresponse().read()
            if index:
                times.append((time.perf_counter() - started) * 1000)
    except (OSError, http.client.HTTPException):
        pass
    finally:
        if conn is not None:
            conn.close()
    if not times:
        return None, None
    gaps = [abs(b - a) for a, b in zip(times, times[1:])]
    return sum(times) / len(times), (sum(gaps) / len(gaps) if gaps else 0.0)


def _download_worker(host: str, index: int, counters: list[int], stop: threading.Event) -> None:
    buffer = bytearray(READ_BUFFER)
    while not stop.is_set():
        conn: Optional[http.client.HTTPConnection] = None
        try:
            conn = _connect(host)
            conn.request("GET", f"/garbage.php?ckSize=200&r={time.time_ns()}")
            response = conn.getresponse()
            while not stop.is_set():
                got = response.readinto(buffer)
                if not got:
                    break
                counters[index] += got
        except (OSError, http.client.HTTPException):
            if stop.wait(0.2):
                return
        finally:
            if conn is not None:
                conn.close()


def _upload_worker(host: str, index: int, counters: list[int], stop: threading.Event) -> None:
    while not stop.is_set():
        conn: Optional[http.client.HTTPConnection] = None
        try:
            conn = _connect(host)
            conn.putrequest("POST", f"/empty.php?r={time.time_ns()}")
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(UP_BODY_BYTES))
            conn.endheaders()
            sent = 0
            while sent < UP_BODY_BYTES and not stop.is_set():
                conn.send(UP_CHUNK)
                sent += len(UP_CHUNK)
                counters[index] += len(UP_CHUNK)
            if sent >= UP_BODY_BYTES:
                conn.getresponse().read()
        except (OSError, http.client.HTTPException):
            if stop.wait(0.2):
                return
        finally:
            if conn is not None:
                conn.close()


def run_phase(host: str, phase: str, seconds: float, emit: Emit, cancelled: threading.Event, seq_start: int = 0) -> tuple[Optional[float], int]:
    """Run one direction for `seconds`. Reports a reading each half second. Returns the steady speed and the next number."""
    worker = _download_worker if phase == "download" else _upload_worker
    streams = DOWN_STREAMS if phase == "download" else UP_STREAMS
    counters = [0] * streams
    stop = threading.Event()
    threads = [threading.Thread(target=worker, args=(host, index, counters, stop), daemon=True) for index in range(streams)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    seq = seq_start
    readings: list[tuple[int, float]] = []
    last_total, last_at = 0, started
    previous_rate = 0.0
    try:
        next_tick = started + TICK_SECONDS
        while True:
            now = time.perf_counter()
            if cancelled.is_set() or now - started >= seconds:
                break
            time.sleep(max(0.0, min(next_tick - now, 0.25)))
            now = time.perf_counter()
            if now < next_tick:
                continue
            total = sum(counters)
            rate = (total - last_total) * 8 / 1e6 / max(now - last_at, 1e-6)
            # A reading is the last second, so each bar is steady but still shows a ramp.
            shown = rate if seq == seq_start else (rate + previous_rate) / 2
            previous_rate = rate
            last_total, last_at = total, now
            t_ms = int((now - started) * 1000)
            emit(seq, phase, t_ms, round(shown, 2))
            readings.append((t_ms, shown))
            seq += 1
            next_tick += TICK_SECONDS
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2.0)
    steady = [rate for t_ms, rate in readings if t_ms >= WARM_UP_MS] or [rate for _t, rate in readings]
    return (sum(steady) / len(steady) if steady else None), seq


def run(config: dict, test: dict, post_json: Callable[..., Any]) -> None:
    """Run one test the server handed out, reporting every second, and finish it."""
    base = str(config.get("server_url") or "").rstrip("/")
    token, name = config.get("token"), config.get("name")
    test_id = str(test.get("id") or "")
    link = str(test.get("link") or "")
    host = HOSTS.get(link)
    prefix = f"{base}/api/v1/monitors/speedtest/{test_id}"
    if not test_id or host is None:
        return
    down_seconds = max(1.0, min(float(test.get("down_seconds") or 15), 20.0))
    up_seconds = max(1.0, min(float(test.get("up_seconds") or 15), 20.0))
    cancelled = threading.Event()
    lock = threading.Lock()
    pending: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    done = threading.Event()

    def emit(seq: int, phase: str, t_ms: int, mbps: float) -> None:
        with lock:
            pending.append({"seq": seq, "phase": phase, "t_ms": t_ms, "mbps": mbps})

    def poster() -> None:
        while True:
            finished = done.wait(0.5)
            with lock:
                batch, pending[:] = pending[:20], pending[20:]
                body = dict(extra)
                extra.clear()
            if batch or body or finished:
                try:
                    reply = post_json(f"{prefix}/samples", {"samples": batch, **body}, token, name, 5)
                    if isinstance(reply, dict) and reply.get("cancel"):
                        cancelled.set()
                except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                    pass
            with lock:
                more = bool(pending)
            if finished and not more:
                return

    sender = threading.Thread(target=poster, name="speedtest-post", daemon=True)
    sender.start()
    result: dict[str, Any] = {"status": "failed", "error": ""}
    got_lock = link_lock.acquire(timeout=45)
    try:
        if not got_lock:
            result["error"] = "The Pi was busy with other checks. Try again."
            return
        ping, jitter = measure_ping(host)
        if ping is None:
            result["error"] = f"The speed test server could not be reached over the {LINK_NAMES[link]} link."
            return
        with lock:
            extra.update(ping_ms=round(ping, 2), jitter_ms=round(jitter or 0.0, 2))
        down, seq = run_phase(host, "download", down_seconds, emit, cancelled)
        up: Optional[float] = None
        if not cancelled.is_set():
            up, seq = run_phase(host, "upload", up_seconds, emit, cancelled, seq)
        if cancelled.is_set():
            return
        if not down and not up:
            result["error"] = f"No data came back over the {LINK_NAMES[link]} link."
            return
        result = {
            "status": "finished",
            "down_mbps": round(down or 0.0, 2),
            "up_mbps": round(up or 0.0, 2),
            "ping_ms": round(ping, 2),
            "jitter_ms": round(jitter or 0.0, 2),
            "error": "",
        }
    except Exception as exc:  # the dashboard should hear why, not wait for a timeout
        result = {"status": "failed", "error": f"The speed test stopped: {type(exc).__name__}"}
    finally:
        if got_lock:
            link_lock.release()
        done.set()
        sender.join(timeout=8)
        if not cancelled.is_set():
            try:
                post_json(f"{prefix}/finish", result, token, name, 8)
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                pass


def poll_loop(stop: threading.Event, load_config: Callable[[], dict], get_json: Callable[..., Any], post_json: Callable[..., Any]) -> None:
    """Ask the server every few seconds whether someone started a speed test."""
    while not stop.is_set():
        config = load_config()
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                body = get_json(f"{str(config['server_url']).rstrip('/')}/api/v1/monitors/speedtest", config["token"], config["name"])
                test = body.get("test") if isinstance(body, dict) else None
                if isinstance(test, dict):
                    run(dict(config), test, post_json)
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError):
                pass
        stop.wait(POLL_SECONDS)
