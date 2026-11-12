"""The Pi's Cloudflare speed test client, run against a small local server that behaves like speed.cloudflare.com."""

import http.client
import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

MONITOR = Path(__file__).resolve().parents[2] / "monitor"
pytestmark = pytest.mark.skipif(not (MONITOR / "cloudflare_speed.py").is_file(), reason="the agent code is not mounted")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"monitor_{name}", MONITOR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(MONITOR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(MONITOR))
    return module


class FakeCloudflare(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    refuse = 0
    refuse_from = 0
    retry_after = ""
    l4 = ""
    seen: list[str] = []

    def log_message(self, *_args):
        return

    def _refused(self) -> bool:
        if not type(self).refuse:
            return False
        self.send_response(type(self).refuse)
        if type(self).retry_after:
            self.send_header("Retry-After", type(self).retry_after)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        type(self).seen.append(self.path)
        if url.path != "/__down":
            self.send_error(404)
            return
        left = int(parse_qs(url.query).get("bytes", ["0"])[0])
        if left >= type(self).refuse_from and self._refused():
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(left))
        if type(self).l4:
            self.send_header("Server-Timing", type(self).l4)
        self.end_headers()
        block = b"\0" * 65536
        try:
            while left > 0:
                self.wfile.write(block[: min(len(block), left)])
                left -= min(len(block), left)
        except OSError:
            return

    def do_POST(self):
        type(self).seen.append(self.path)
        left = int(self.headers.get("Content-Length") or 0)
        try:
            while left > 0:
                data = self.rfile.read(min(65536, left))
                if not data:
                    return
                left -= len(data)
        except OSError:
            return
        if self.path != "/__up":
            self.send_error(404)
            return
        if self._refused():
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def lab(monkeypatch):
    FakeCloudflare.refuse = 0
    FakeCloudflare.refuse_from = 0
    FakeCloudflare.retry_after = ""
    FakeCloudflare.l4 = ""
    FakeCloudflare.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloudflare)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    module = _load("cloudflare_speed")
    # Small transfers and no pause, so a whole run takes a moment.
    monkeypatch.setattr(module, "DOWN_PLAN", ((100_000, 1), (400_000, 2), (2_000_000, 3)))
    monkeypatch.setattr(module, "UP_PLAN", ((100_000, 1), (1_000_000, 3)))
    monkeypatch.setattr(module, "PAUSE_SECONDS", 0.0)
    port = server.server_address[1]

    def connect(timeout: float) -> http.client.HTTPConnection:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        conn.connect()
        return conn

    yield module, connect
    server.shutdown()


def test_ping_reports_a_round_trip_and_jitter(lab):
    module, connect = lab
    ping, jitter = module.measure_ping(connect, count=5)
    assert ping is not None and 0 <= ping < 500 and jitter is not None and jitter >= 0
    assert FakeCloudflare.seen.count("/__down?bytes=0") == 6


def test_ping_uses_the_round_trip_cloudflare_measured(lab):
    module, connect = lab
    # Cloudflare takes tens of milliseconds to build an answer, so timing the request overstates the line.
    FakeCloudflare.l4 = 'cfSpeedEdge;dur=10, cfSpeedWorker;dur=32, cfL4;desc="?proto=TCP&rtt=8678&min_rtt=6186&rtt_var=4099&sent=8"'
    ping, jitter = module.measure_ping(connect, count=4)
    assert ping == pytest.approx(8.678)
    assert jitter == pytest.approx(4.099)


def test_a_run_makes_a_few_transfers_one_at_a_time(lab):
    module, connect = lab
    down = module.run_plan(connect, module.DOWN_PLAN, module.download_once)
    up = module.run_plan(connect, module.UP_PLAN, module.upload_once)
    assert down is not None and down > 0
    assert up is not None and up > 0
    # Exactly the planned transfers, small ones first: nothing is repeated to fill a time window.
    downloads = [path for path in FakeCloudflare.seen if path.startswith("/__down")]
    assert downloads == ["/__down?bytes=100000"] + ["/__down?bytes=400000"] * 2 + ["/__down?bytes=2000000"] * 3
    assert FakeCloudflare.seen.count("/__up") == 4


def test_the_reading_is_the_typical_speed_of_the_largest_transfers(lab):
    module, connect = lab
    speeds = {100: [900.0], 400: [50.0, 60.0], 2000: [300.0, 100.0, 200.0]}
    plan = ((100, 1), (400, 2), (2000, 3))
    assert module.run_plan(connect, plan, lambda _connect, size: speeds[size].pop(0)) == 200.0
    # When the largest size never completes, the next one down is used.
    speeds = {100: [900.0], 400: [50.0, 60.0], 2000: [None, None, None]}
    assert module.run_plan(connect, plan, lambda _connect, size: speeds[size].pop(0)) == 55.0
    assert module.run_plan(connect, plan, lambda _connect, _size: None) is None


def test_transfers_are_spaced_out(lab, monkeypatch):
    module, connect = lab
    monkeypatch.setattr(module, "PAUSE_SECONDS", 0.5)
    naps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", naps.append)
    module.run_plan(connect, ((100, 1), (200, 2)), lambda _connect, _size: 1.0)
    assert naps == [0.5, 0.5]


def test_a_whole_test_reports_latency_and_both_directions(lab):
    module, connect = lab
    report = module.measure(connect)
    assert report is not None
    assert report["down_mbps"] > 0 and report["up_mbps"] > 0
    assert report["ping_ms"] >= 0 and report["jitter_ms"] >= 0


def test_a_rate_limit_is_a_refusal_not_a_slow_line(lab):
    module, connect = lab
    FakeCloudflare.refuse = 429
    with pytest.raises(module.Refused) as raised:
        module.measure(connect)
    assert "429" in str(raised.value)
    with pytest.raises(module.Refused):
        module.download_once(connect, 100_000)
    with pytest.raises(module.Refused):
        module.upload_once(connect, 100_000)


def test_a_refusal_carries_the_wait_cloudflare_asked_for(lab):
    module, connect = lab
    # Cloudflare limits large downloads only: latency and the small transfers still work.
    FakeCloudflare.refuse = 429
    FakeCloudflare.refuse_from = 2_000_000
    FakeCloudflare.retry_after = "1154"
    with pytest.raises(module.Refused) as raised:
        module.measure(connect)
    assert raised.value.retry_after == 1154
    FakeCloudflare.retry_after = ""
    with pytest.raises(module.Refused) as raised:
        module.download_once(connect, 2_000_000)
    assert raised.value.retry_after is None


def test_a_transfer_that_breaks_off_gives_no_speed(lab):
    module, _connect = lab

    def nowhere(_timeout: float) -> http.client.HTTPConnection:
        raise ConnectionRefusedError("no route")

    assert module.download_once(nowhere, 100_000) is None
    assert module.upload_once(nowhere, 100_000) is None
    assert module.measure(nowhere) is None


def test_the_agent_runs_the_test_only_when_the_server_names_it(lab, monkeypatch):
    module, connect = lab
    checks = _load("checks")
    monkeypatch.setattr(checks.cloudflare_speed, "DOWN_PLAN", ((100_000, 1), (2_000_000, 2)))
    monkeypatch.setattr(checks.cloudflare_speed, "UP_PLAN", ((100_000, 1), (1_000_000, 2)))
    monkeypatch.setattr(checks.cloudflare_speed, "PAUSE_SECONDS", 0.0)
    bound: list[tuple] = []

    def connector(iface, host):
        bound.append((iface, host))
        return connect

    monkeypatch.setattr(checks.cloudflare_speed, "connector", connector)
    monkeypatch.setattr(checks, "_link_interface", lambda link: "eth0" if link == "wired" else "wlan0")
    test = {"slug": "cloudflare-speed", "kind": "cloudflare", "target": "speed.cloudflare.com", "network_slug": "wired", "link": "wired"}
    result = checks.run_check(test)
    assert result is not None
    assert result["test_slug"] == "cloudflare-speed" and result["network_slug"] == "wired"
    assert result["status"] == "ok"
    assert result["metrics"]["down_mbps"] > 0 and result["metrics"]["up_mbps"] > 0
    assert result["message"] == "speed.cloudflare.com"
    # The wired link, not the tunnel and not Wi-Fi.
    assert bound == [("eth0", "speed.cloudflare.com")]

    # A refusal raises, so the scheduler notes the error and sends no failed result.
    FakeCloudflare.refuse = 429
    with pytest.raises(OSError):
        checks.run_check(test)
