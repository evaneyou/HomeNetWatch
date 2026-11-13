"""Speed test against Cloudflare (speed.cloudflare.com), outside the tunnel.

The Bandwidth Test measures the path to the HomeNetWatch server, so it can read no higher than that
server's own bandwidth. This test measures the home line against Cloudflare's nearest site instead:
the same service as the speed test page at speed.cloudflare.com. Download reads /__down, upload posts
to /__up, and latency reads the round trip Cloudflare reports for an empty /__down.

The agent runs it on the clock (every 15 minutes means on the hour and each quarter after it), no more often
than every 15 minutes.

It is a free public service that limits how much one address may download, so the test is gentle, the
way Cloudflare's own page is: a few transfers, one at a time, small ones first, with a pause between
them. Filling the line with parallel streams for several seconds moved about 500 MB a run on a fast
line and was refused (HTTP 429) for hours afterwards. A run now moves about 130 MB down and 40 MB up.

One transfer at a time does not fill a fast line the way parallel streams do, so the reading is a
little conservative. Cloudflare refuses a single download above 25 MB.
"""

from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
import statistics
import time
from typing import Callable, Optional

HOST = "speed.cloudflare.com"
PORT = 443
# Each step is (bytes, how many transfers). The reading is the typical speed of the largest size that worked.
DOWN_PLAN: tuple[tuple[int, int], ...] = ((1_000_000, 2), (10_000_000, 3), (25_000_000, 4))
UP_PLAN: tuple[tuple[int, int], ...] = ((1_000_000, 2), (10_000_000, 4))
PAUSE_SECONDS = 1.0
# A connection starts slowly and speeds up, so the first part of each transfer is left out of its speed.
RAMP_SHARE = 0.2
TRANSFER_TIMEOUT = 20.0
READ_BUFFER = 256 * 1024
UP_CHUNK = os.urandom(64 * 1024)
HEADERS = {"User-Agent": "HomeNetWatch-agent", "Accept": "*/*"}

Connect = Callable[[float], http.client.HTTPConnection]


class Refused(OSError):
    """Cloudflare answered but would not serve the test: a rate limit or a block.

    `retry_after` is how many seconds Cloudflare asked the caller to wait, when it said.
    """

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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


def _refused(response: http.client.HTTPResponse) -> Refused:
    wait: Optional[float] = None
    try:
        wait = float(response.getheader("Retry-After") or "")
    except ValueError:
        wait = None
    if wait is not None and wait < 0:
        wait = None
    return Refused(f"Cloudflare refused the speed test (HTTP {response.status})", wait)


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
                raise _refused(response)
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


def _mbps(moved: int, seconds: float) -> Optional[float]:
    if moved <= 0 or seconds <= 0:
        return None
    return moved * 8 / 1e6 / seconds


def download_once(connect: Connect, size: int) -> Optional[float]:
    """Download `size` bytes on a new connection. Mbit/s of the part after the ramp, or None when it broke off."""
    conn: Optional[http.client.HTTPConnection] = None
    try:
        conn = connect(TRANSFER_TIMEOUT)
        conn.request("GET", f"/__down?bytes={size}", headers=HEADERS)
        response = conn.getresponse()
        if response.status != 200:
            raise _refused(response)
        buffer = bytearray(READ_BUFFER)
        ramp = int(size * RAMP_SHARE)
        got = 0
        mark_bytes, mark_at = 0, time.perf_counter()
        marked = False
        while True:
            count = response.readinto(buffer)
            if not count:
                break
            got += count
            if not marked and got >= ramp:
                mark_bytes, mark_at, marked = got, time.perf_counter(), True
        ended = time.perf_counter()
        if got < size:
            return None
        return _mbps(got - mark_bytes, ended - mark_at)
    except Refused:
        raise
    except (OSError, http.client.HTTPException):
        return None
    finally:
        if conn is not None:
            conn.close()


def upload_once(connect: Connect, size: int) -> Optional[float]:
    """Upload `size` bytes on a new connection. Mbit/s of the part after the ramp, or None when it broke off.

    The clock stops when Cloudflare answers, which is when it has the whole body: bytes handed to the
    operating system are not yet bytes that crossed the line.
    """
    conn: Optional[http.client.HTTPConnection] = None
    try:
        conn = connect(TRANSFER_TIMEOUT)
        conn.putrequest("POST", "/__up")
        for name, value in HEADERS.items():
            conn.putheader(name, value)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        ramp = int(size * RAMP_SHARE)
        sent = 0
        mark_bytes, mark_at = 0, time.perf_counter()
        marked = False
        while sent < size:
            chunk = UP_CHUNK[: size - sent]
            conn.send(chunk)
            sent += len(chunk)
            if not marked and sent >= ramp:
                mark_bytes, mark_at, marked = sent, time.perf_counter(), True
        response = conn.getresponse()
        response.read()
        ended = time.perf_counter()
        if response.status != 200:
            raise _refused(response)
        return _mbps(sent - mark_bytes, ended - mark_at)
    except Refused:
        raise
    except (OSError, http.client.HTTPException):
        return None
    finally:
        if conn is not None:
            conn.close()


def run_plan(connect: Connect, plan: tuple[tuple[int, int], ...], once: Callable[[Connect, int], Optional[float]]) -> Optional[float]:
    """Run each step of `plan`, one transfer at a time with a pause between, and return the typical speed.

    The typical speed is the median of the largest size that completed at least once: a small transfer
    is over before a fast line reaches full speed, so it only warms the path up.
    """
    speeds: dict[int, list[float]] = {}
    first = True
    for size, count in plan:
        for _ in range(count):
            if not first and PAUSE_SECONDS > 0:
                time.sleep(PAUSE_SECONDS)
            first = False
            speed = once(connect, size)
            if speed is not None:
                speeds.setdefault(size, []).append(speed)
    if not speeds:
        return None
    return statistics.median(speeds[max(speeds)])


def measure(connect: Connect) -> Optional[dict]:
    """Latency, then download, then upload. None when Cloudflare could not be reached at all."""
    ping, jitter = measure_ping(connect)
    if ping is None:
        return None
    down = run_plan(connect, DOWN_PLAN, download_once)
    if PAUSE_SECONDS > 0:
        time.sleep(PAUSE_SECONDS)
    up = run_plan(connect, UP_PLAN, upload_once)
    return {"down_mbps": down, "up_mbps": up, "ping_ms": ping, "jitter_ms": jitter or 0.0}
