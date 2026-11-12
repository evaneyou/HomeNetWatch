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
    seen: list[str] = []

    def log_message(self, *_args):
        return

    def _refused(self) -> bool:
        if not type(self).refuse:
            return False
        self.send_response(type(self).refuse)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        type(self).seen.append(self.path)
        if url.path != "/__down":
            self.send_error(404)
            return
        if self._refused():
            return
        left = int(parse_qs(url.query).get("bytes", ["0"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(left))
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
    FakeCloudflare.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloudflare)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    module = _load("cloudflare_speed")
    # Small bodies, so each stream finishes several requests inside a short test.
    monkeypatch.setattr(module, "DOWN_BYTES", 2_000_000)
    monkeypatch.setattr(module, "UP_BYTES", 1_000_000)
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


def test_each_direction_reports_a_speed(lab):
    module, connect = lab
    down = module.run_phase(connect, "download", seconds=1.2, warm_up=0.3)
    up = module.run_phase(connect, "upload", seconds=1.2, warm_up=0.3)
    assert down is not None and down > 0
    assert up is not None and up > 0
    # Cloudflare refuses one large download, so a stream asks for the same small size again and again.
    assert FakeCloudflare.seen.count("/__down?bytes=2000000") > module.DOWN_STREAMS
    assert FakeCloudflare.seen.count("/__up") > module.UP_STREAMS


def test_a_whole_test_reports_latency_and_both_directions(lab):
    module, connect = lab
    report = module.measure(connect, seconds=1.0)
    assert report is not None
    assert report["down_mbps"] > 0 and report["up_mbps"] > 0
    assert report["ping_ms"] >= 0 and report["jitter_ms"] >= 0


def test_a_rate_limit_is_a_refusal_not_a_slow_line(lab):
    module, connect = lab
    FakeCloudflare.refuse = 429
    with pytest.raises(module.Refused) as raised:
        module.measure(connect, seconds=1.0)
    assert "429" in str(raised.value)
    with pytest.raises(module.Refused):
        module.run_phase(connect, "download", seconds=1.0, warm_up=0.2)
    with pytest.raises(module.Refused):
        module.run_phase(connect, "upload", seconds=1.0, warm_up=0.2)


def test_an_unreachable_server_gives_no_report(lab):
    module, _connect = lab

    def nowhere(_timeout: float) -> http.client.HTTPConnection:
        raise ConnectionRefusedError("no route")

    assert module.measure(nowhere, seconds=0.5) is None


def test_the_agent_runs_the_test_only_when_the_server_names_it(lab, monkeypatch):
    module, connect = lab
    checks = _load("checks")
    monkeypatch.setattr(checks.cloudflare_speed, "DOWN_BYTES", 2_000_000)
    monkeypatch.setattr(checks.cloudflare_speed, "UP_BYTES", 1_000_000)
    monkeypatch.setattr(checks.cloudflare_speed, "SECONDS", 1.0)
    monkeypatch.setattr(checks.cloudflare_speed, "WARM_UP_SECONDS", 0.2)
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
