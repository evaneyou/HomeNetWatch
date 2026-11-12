"""Speed test against Cloudflare (speed.cloudflare.com), outside the tunnel.

The Bandwidth Test measures the path to the HomeNetWatch server, so it can read no higher than that
server's own bandwidth. This test measures the home line against Cloudflare's nearest site instead:
the same service as the speed test page at speed.cloudflare.com. Download reads /__down, upload posts
to /__up, and latency times an empty /__down.

It is a free public service, so the server schedules this test no more often than every 30 minutes.
Cloudflare refuses a single download above 25 MB, so each stream asks for 25 MB again and again.
"""

from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
import threading
import time
from typing import Callable, Optional

HOST = "speed.cloudflare.com"
PORT = 443
DOWN_BYTES = 25_000_000
UP_BYTES = 10_000_000
DOWN_STREAMS = 4
UP_STREAMS = 3
SECONDS = 8.0
# The first seconds are the ramp to full speed, so they are left out of the reading.
WARM_UP_SECONDS = 2.0
READ_BUFFER = 256 * 1024
UP_CHUNK = os.urandom(64 * 1024)
HEADERS = {"User-Agent": "HomeNetWatch-agent", "Accept": "*/*"}

Connect = Callable[[float], http.client.HTTPConnection]


class Refused(OSError):
    """Cloudflare answered but would not serve the test: a rate limit or a block."""


def connector(iface: Optional[str], host: str = HOST) -> Connect:
    """Connections to Cloudflare over IPv4 that leave through `iface`, so the reading is that link's."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    def connect(timeout: float) -> http.client.HTTPConnection:
        address = socket.getaddrinfo(host, PORT, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            if iface:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BINDTODEVICE", 25), iface.encode() + b"\0")
                except OSError:
                    pass
            sock.connect((address, PORT))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn = http.client.HTTPSConnection(host, PORT, timeout=timeout, context=context)
            conn.sock = context.wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise
        return conn

    return connect


def _refusal(status: int) -> str:
    return f"Cloudflare refused the speed test (HTTP {status})"


_L4 = re.compile(r"[?&]rtt=(\d+).*?[?&]rtt_var=(\d+)")


def _l4_timing(header: Optional[str]) -> Optional[tuple[float, float]]:
    """Round trip and its variation in ms, as Cloudflare's own TCP stack measured them for this connection.

    Each answer carries them in Server-Timing as `cfL4;desc="?proto=TCP&rtt=8678&...&rtt_var=4099..."`, in
    microseconds. Timing the request instead would add the time Cloudflare takes to build the answer, which
    is tens of milliseconds and now and then a few hundred.
    """
    if not header or "cfL4" not in header:
        return None
    found = _L4.search(header)
    if found is None:
        return None
    return int(found.group(1)) / 1000, int(found.group(2)) / 1000


def measure_ping(connect: Connect, count: int = 8) -> tuple[Optional[float], Optional[float]]:
    """Round trip and jitter in ms over one connection. The first request, which connects, is not counted.

    Cloudflare's own measurement of the connection is used when its answers carry one. Otherwise the
    figure is the time each request took.
    """
    times: list[float] = []
    measured: list[tuple[float, float]] = []
    conn: Optional[http.client.HTTPConnection] = None
    try:
        conn = connect(5.0)
        for index in range(count + 1):
            started = time.perf_counter()
            conn.request("GET", "/__down?bytes=0", headers=HEADERS)
            response = conn.getresponse()
            response.read()
            if response.status != 200:
                raise Refused(_refusal(response.status))
            if index:
                times.append((time.perf_counter() - started) * 1000)
                l4 = _l4_timing(response.getheader("Server-Timing"))
                if l4 is not None:
                    measured.append(l4)
    except Refused:
        raise
    except (OSError, http.client.HTTPException):
        pass
    finally:
        if conn is not None:
            conn.close()
    if not times:
        return None, None
    if len(measured) == len(times):
        return sum(rtt for rtt, _ in measured) / len(measured), sum(var for _, var in measured) / len(measured)
    gaps = [abs(b - a) for a, b in zip(times, times[1:])]
    return sum(times) / len(times), (sum(gaps) / len(gaps) if gaps else 0.0)


def _download_worker(connect: Connect, index: int, counters: list[int], stop: threading.Event, refused: list[str]) -> None:
    buffer = bytearray(READ_BUFFER)
    conn: Optional[http.client.HTTPConnection] = None
    while not stop.is_set():
        try:
            if conn is None:
                conn = connect(8.0)
            conn.request("GET", f"/__down?bytes={DOWN_BYTES}", headers=HEADERS)
            response = conn.getresponse()
            if response.status != 200:
                refused.append(_refusal(response.status))
                break
            while not stop.is_set():
                got = response.readinto(buffer)
                if not got:
                    break
                counters[index] += got
        except (OSError, http.client.HTTPException):
            if conn is not None:
                conn.close()
                conn = None
            if stop.wait(0.2):
                break
    if conn is not None:
        conn.close()


def _upload_worker(connect: Connect, index: int, counters: list[int], stop: threading.Event, refused: list[str]) -> None:
    conn: Optional[http.client.HTTPConnection] = None
    while not stop.is_set():
        try:
            if conn is None:
                conn = connect(8.0)
            conn.putrequest("POST", "/__up")
            for name, value in HEADERS.items():
                conn.putheader(name, value)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(UP_BYTES))
            conn.endheaders()
            sent = 0
            while sent < UP_BYTES and not stop.is_set():
                chunk = UP_CHUNK[: UP_BYTES - sent]
                conn.send(chunk)
                sent += len(chunk)
                counters[index] += len(chunk)
            if sent < UP_BYTES:
                break
            response = conn.getresponse()
            response.read()
            if response.status != 200:
                refused.append(_refusal(response.status))
                break
        except (OSError, http.client.HTTPException):
            if conn is not None:
                conn.close()
                conn = None
            if stop.wait(0.2):
                break
    if conn is not None:
        conn.close()


def run_phase(connect: Connect, phase: str, seconds: float = SECONDS, warm_up: float = WARM_UP_SECONDS) -> Optional[float]:
    """Run one direction for `seconds` and return its steady speed in Mbit/s, or None when no data moved."""
    worker = _download_worker if phase == "download" else _upload_worker
    streams = DOWN_STREAMS if phase == "download" else UP_STREAMS
    counters = [0] * streams
    stop = threading.Event()
    refused: list[str] = []
    threads = [threading.Thread(target=worker, args=(connect, index, counters, stop, refused), daemon=True) for index in range(streams)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    warm_up = warm_up if seconds > warm_up else 0.0
    warm_total, warm_at = 0, started
    try:
        if warm_up:
            time.sleep(warm_up)
            warm_total, warm_at = sum(counters), time.perf_counter()
        while not refused and time.perf_counter() - started < seconds:
            time.sleep(0.05)
        total, ended = sum(counters), time.perf_counter()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2.0)
    if refused:
        raise Refused(refused[0])
    moved = total - warm_total
    if moved <= 0:
        return None
    return moved * 8 / 1e6 / max(ended - warm_at, 1e-6)


def measure(connect: Connect, seconds: float = SECONDS) -> Optional[dict]:
    """Latency, then download, then upload. None when Cloudflare could not be reached at all."""
    ping, jitter = measure_ping(connect)
    if ping is None:
        return None
    down = run_phase(connect, "download", seconds)
    up = run_phase(connect, "upload", seconds)
    return {"down_mbps": down, "up_mbps": up, "ping_ms": ping, "jitter_ms": jitter or 0.0}
