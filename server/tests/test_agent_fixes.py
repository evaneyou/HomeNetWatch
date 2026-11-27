"""Pi agent fixes from the code review: stalled senders, lost results, Wi-Fi checks on the wrong link."""

from __future__ import annotations

import importlib
import json
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

MONITOR = Path(__file__).resolve().parents[2] / "monitor"
if not (MONITOR / "synthetic.py").is_file():
    pytest.skip("monitor package not present", allow_module_level=True)


def _module(name: str):
    sys.path.insert(0, str(MONITOR))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(MONITOR))


# ---- the sender's error output


def test_a_sender_that_writes_a_lot_of_errors_is_not_blocked():
    synthetic = _module("synthetic")
    # Far more than a pipe holds (about 64 KB). Unread, the child would block before it printed "done".
    code = (
        "import sys\n"
        "for index in range(4000):\n"
        "    sys.stderr.write('warning %05d ' % index + 'x' * 80 + '\\n')\n"
        "sys.stderr.write('last words\\n')\n"
        "print('done')\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    tail: list[str] = []
    drain = threading.Thread(target=synthetic._drain_stderr, args=(proc, tail), daemon=True)
    drain.start()
    try:
        assert proc.wait(timeout=30) == 0
    finally:
        proc.kill()
    drain.join(timeout=10)
    assert proc.stdout is not None and proc.stdout.read().strip() == "done"
    assert tail == ["last words"]
    proc.stdout.close()
    if proc.stderr is not None:
        proc.stderr.close()


# ---- interval reports are sent from their own thread


def test_a_full_queue_of_reports_loses_the_oldest():
    synthetic = _module("synthetic")
    outbox: queue.Queue = queue.Queue(maxsize=3)
    for index in range(5):
        synthetic._queue_sample(outbox, {"interval_index": index})
    assert [outbox.get_nowait()["interval_index"] for _ in range(3)] == [2, 3, 4]


def _batch_setup(synthetic, monkeypatch):
    monkeypatch.setattr(synthetic, "BATCH_LINGER_S", 0)
    monkeypatch.setattr(synthetic, "_batch_ok", True)
    monkeypatch.setattr(synthetic, "_post_config", {})
    outbox: queue.Queue = queue.Queue()
    for index in range(3):
        outbox.put({"interval_index": index})
    outbox.put(None)
    return outbox


CONFIG = {"server_url": "http://10.7.0.1:8847", "token": "t", "name": "pi"}


def test_the_reports_of_all_flows_go_in_one_request(monkeypatch):
    synthetic = _module("synthetic")
    outbox = _batch_setup(synthetic, monkeypatch)
    calls: list[tuple[str, dict]] = []
    synthetic._post_samples(dict(CONFIG, post_json=lambda url, body, *_rest: calls.append((url, body)) or {}), outbox)
    assert [url for url, _body in calls] == ["http://10.7.0.1:8847/api/v1/synthetic/samples/batch"]
    assert [item["interval_index"] for item in calls[0][1]["samples"]] == [0, 1, 2]


def test_an_older_server_gets_the_reports_one_at_a_time(monkeypatch):
    synthetic = _module("synthetic")
    outbox = _batch_setup(synthetic, monkeypatch)
    single: list[int] = []
    batches = 0

    def post(url, body, *_rest):
        nonlocal batches
        if url.endswith("/batch"):
            batches += 1
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if body["interval_index"] == 0:
            raise OSError("server unreachable")
        single.append(body["interval_index"])
        return {}

    config = dict(CONFIG, post_json=post)
    synthetic._post_samples(config, outbox)
    # One failed report does not stop the ones behind it, and the batch address is not tried again.
    assert single == [1, 2]
    synthetic._send_batch(config, [{"interval_index": 7}])
    assert single == [1, 2, 7] and batches == 1


def test_a_server_error_does_not_fall_back_to_single_reports(monkeypatch):
    synthetic = _module("synthetic")
    outbox = _batch_setup(synthetic, monkeypatch)
    urls: list[str] = []

    def post(url, _body, *_rest):
        urls.append(url)
        raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

    synthetic._post_samples(dict(CONFIG, post_json=post), outbox)
    assert len(urls) == 1 and synthetic._batch_ok is True


def test_the_batch_answer_can_say_a_speed_test_is_waiting(monkeypatch):
    state = _module("agent_state")
    synthetic = _module("synthetic")
    outbox = _batch_setup(synthetic, monkeypatch)
    state.speedtest_wake.clear()
    synthetic._post_samples(dict(CONFIG, post_json=lambda *_args: {"status": "ok", "speedtest": True}), outbox)
    assert state.speedtest_wake.is_set()
    state.speedtest_wake.clear()


def test_the_speed_test_loop_waits_for_the_hint_instead_of_asking_every_few_seconds():
    speedtest = _module("speedtest")
    stop = threading.Event()
    asked: list[str] = []
    waits: list[float] = []

    class Wake:
        def wait(self, seconds):
            waits.append(seconds)
            stop.set()

        def clear(self):
            return None

    def get_json(url, *_rest):
        asked.append(url)
        return {"test": None}

    speedtest.poll_loop(stop, lambda: dict(CONFIG), get_json, lambda *_args: {}, Wake())
    assert asked == ["http://10.7.0.1:8847/api/v1/monitors/speedtest"]
    assert waits == [speedtest.IDLE_POLL_SECONDS] and speedtest.IDLE_POLL_SECONDS >= 30


def test_reading_a_sender_reports_every_interval_and_keeps_its_last_error(monkeypatch):
    synthetic = _module("synthetic")
    lines = [
        {"type": "interval", "index": index, "interval_s": 10, "flows": [{"sent": 500, "rx": 500, "loss_pct": 0.0}]}
        for index in range(3)
    ]
    code = (
        "import sys\n"
        f"for line in {[json.dumps(line) for line in lines]!r}:\n"
        "    print(line, flush=True)\n"
        "sys.stderr.write('Error: gave up\\n')\n"
        "sys.exit(3)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    item = ("run-1", "wired")
    monkeypatch.setitem(synthetic._running, item, {"args": [], "proc": proc, "mode": "continuous", "restart_token": 0})
    # The reports are queued for the one sender thread, which is not started here.
    outbox: queue.Queue = queue.Queue()
    monkeypatch.setattr(synthetic, "_outbox", outbox)
    monkeypatch.setattr(synthetic, "_ensure_poster", lambda _config: None)
    tail: list[str] = []
    drain = threading.Thread(target=synthetic._drain_stderr, args=(proc, tail), daemon=True)
    drain.start()
    synthetic._read_output(dict(CONFIG), item, proc, tail, drain)
    queued = [outbox.get_nowait() for _ in range(outbox.qsize())]
    assert [(body["run_id"], body["link"], body["interval_index"]) for body in queued] == [("run-1", "wired", 0), ("run-1", "wired", 1), ("run-1", "wired", 2)]
    assert synthetic.last_failure() == "wired: Error: gave up"
    monkeypatch.setattr(synthetic, "_last_failure", "")


def test_a_job_with_a_missing_option_fails_alone(monkeypatch):
    synthetic = _module("synthetic")
    finished: list[tuple] = []
    monkeypatch.setattr(synthetic, "_link_interface", lambda _link: "eth0")

    def broken(*_args, **_kwargs):
        raise TypeError("int() argument must be a string, a bytes-like object or a real number, not 'NoneType'")

    monkeypatch.setattr(synthetic, "launch_plan", broken)
    monkeypatch.setattr(synthetic, "_finish", lambda _config, run_id, link, status, message: finished.append((run_id, link, status)))
    # Before, the TypeError left sync() and no later job was started on that pass or any after it.
    synthetic._ensure({}, ("run-9", "wired"), {"mode": "continuous", "options": {}}, "203.0.113.5")
    assert finished == [("run-9", "wired", "failed")]


# ---- which adapter is the Wi-Fi link


def _entry(root: Path, name: str) -> Path:
    path = root / name
    (path / "wireless").mkdir(parents=True)
    return path


def test_with_wifi_down_the_link_is_the_real_adapter_never_the_capture_radio(tmp_path):
    checks = _module("checks")
    capture = [_entry(tmp_path, "hnwradio0"), _entry(tmp_path, "hnwradio0mon")]
    assert checks._fallback_link(capture) is None
    assert checks._fallback_link([*capture, _entry(tmp_path, "wlan0")]) == "wlan0"
    assert checks._capture_name("hnwradio0mon") and checks._capture_name("p2p-dev-wlan0")
    assert not checks._capture_name("wlan0")


def test_a_wifi_check_with_no_wifi_adapter_fails_instead_of_leaving_by_ethernet(monkeypatch):
    checks = _module("checks")
    monkeypatch.setattr(checks, "_link_interface", lambda _link: None)
    ran: list[str] = []
    monkeypatch.setattr(checks, "check_http", lambda test: ran.append("http") or {"status": "ok"})
    monkeypatch.setattr(checks, "check_ping", lambda test: ran.append("ping") or {"status": "ok"})

    web = {"slug": "http-google", "kind": "http", "target": "https://www.google.com/", "link": "wireless", "network_slug": "wifi"}
    result = checks.run_check(web)
    assert result["status"] == "fail" and result["message"] == "No Wi-Fi adapter to run this check on"
    assert result["metrics"]["loss_pct"] == 100.0
    ping = checks.run_check({"slug": "icmp-dns", "kind": "ping", "target": "1.1.1.1", "link": "wireless", "network_slug": "wifi"})
    assert ping["status"] == "fail"
    assert ran == []

    # The tunnel peer follows the tunnel's own route, and a wired check is not this case.
    checks.run_check({"slug": "icmp-path", "kind": "ping", "target": "10.7.1.1", "link": "wireless", "network_slug": "wifi"})
    checks.run_check(dict(web, link="wired", network_slug="wired"))
    assert ran == ["ping", "http"]

    # With an adapter the check runs as before.
    monkeypatch.setattr(checks, "_link_interface", lambda _link: "wlan0")
    checks.run_check(web)
    assert ran == ["ping", "http", "http"]


# ---- the airwave upload queue


def test_a_capture_the_server_rejects_does_not_block_the_ones_behind_it(monkeypatch):
    radio = _module("radio")
    rows = [(1, {"n": 1}), (2, {"n": 2}), (3, {"n": 3})]
    dropped: list[int] = []
    posted: list[int] = []
    monkeypatch.setattr(radio.rf_spool, "pending", lambda _path: list(rows))
    monkeypatch.setattr(radio.rf_spool, "drop", lambda _path, row_id: dropped.append(row_id))

    def post(_config, payload):
        if payload["n"] == 1:
            raise urllib.error.HTTPError("http://10.7.0.1:8847", 422, "Unprocessable", {}, None)
        posted.append(payload["n"])

    monkeypatch.setattr(radio, "_post_capture", post)
    radio._drain_spool({})
    assert posted == [2, 3]
    assert dropped == [1, 2, 3]


def test_a_server_error_leaves_the_queue_as_it_is(monkeypatch):
    radio = _module("radio")
    dropped: list[int] = []
    monkeypatch.setattr(radio.rf_spool, "pending", lambda _path: [(1, {"n": 1}), (2, {"n": 2})])
    monkeypatch.setattr(radio.rf_spool, "drop", lambda _path, row_id: dropped.append(row_id))

    def post(_config, _payload):
        raise urllib.error.HTTPError("http://10.7.0.1:8847", 502, "Bad Gateway", {}, None)

    monkeypatch.setattr(radio, "_post_capture", post)
    with pytest.raises(urllib.error.HTTPError):
        radio._drain_spool({})
    assert dropped == []


# ---- the setup page


def test_the_setup_page_answers_400_to_a_body_that_is_not_an_object():
    agent = _module("agent")
    server = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for body in (b"[]", b"5", b'"text"'):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/setup", data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            assert caught.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


# ---- a gentle start for video


def _video_args(synthetic, options, forever=True):
    return synthetic.send_args(
        "/opt/monitor/rtnp-send",
        {"preset": "video-conference", "wire": "rtp", "video": True, "report_interval_s": 10, "max_rtt_ms": 300, "max_jitter_ms": 30, "max_loss_pct": 1.0, **options},
        "203.0.113.5",
        control_host="10.7.1.1",
        key_file="/etc/monitor/rtnp.key",
        srtp_key_file="/etc/monitor/rtnp-srtp.key",
        forever=forever,
    )


def test_a_video_flow_starts_gently_when_the_sender_can(monkeypatch):
    synthetic = _module("synthetic")
    monkeypatch.setattr(synthetic, "_sender_has", lambda _binary, flag: flag == "--video-ease-in")
    args = _video_args(synthetic, {"video_ease_in_ms": 250})
    assert args[args.index("--video") + 1 : args.index("--video") + 3] == ["--video-ease-in", "250ms"]
    # Nothing is added when the server did not ask, and a value out of range is refused.
    assert "--video-ease-in" not in _video_args(synthetic, {})
    with pytest.raises(ValueError):
        _video_args(synthetic, {"video_ease_in_ms": 60000})


def test_an_older_sender_is_not_given_an_option_it_would_stop_at(monkeypatch):
    synthetic = _module("synthetic")
    monkeypatch.setattr(synthetic, "_sender_has", lambda _binary, _flag: False)
    assert "--video-ease-in" not in _video_args(synthetic, {"video_ease_in_ms": 250})


def test_the_sender_is_asked_once_what_it_knows(monkeypatch):
    synthetic = _module("synthetic")
    monkeypatch.setattr(synthetic, "_sender_flags", {})
    calls: list[list[str]] = []

    def run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="      --video-ease-in <VIDEO_EASE_IN>  Spread the first video frame", stderr="")

    monkeypatch.setattr(synthetic.subprocess, "run", run)
    assert synthetic._sender_has("/opt/monitor/rtnp-send", "--video-ease-in") is True
    assert synthetic._sender_has("/opt/monitor/rtnp-send", "--video-ease-in") is True
    assert synthetic._sender_has("/opt/monitor/rtnp-send", "--made-up") is False
    assert calls == [["/opt/monitor/rtnp-send", "flow", "--help"], ["/opt/monitor/rtnp-send", "flow", "--help"]]

    def missing(args, **_kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(synthetic.subprocess, "run", missing)
    assert synthetic._sender_has("/nowhere/rtnp-send", "--video-ease-in") is False
