"""The Pi agent's test schedule: a slow test keeps the time of its last run across an agent restart."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

MONITOR = Path(__file__).resolve().parents[2] / "monitor"
if not (MONITOR / "scheduler.py").is_file():
    pytest.skip("monitor package not present", allow_module_level=True)

CLOUDFLARE = {"slug": "cloudflare-speed", "kind": "cloudflare", "target": "speed.cloudflare.com", "network_slug": "wired", "link": "wired", "interval_seconds": 3600}
PING = {"slug": "icmp-path", "kind": "ping", "target": "10.7.0.1", "network_slug": "wired", "link": "wired", "interval_seconds": 60}
KEY = "wired:cloudflare-speed"


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """A freshly loaded scheduler, as after an agent restart: nothing in memory, the schedule file in a temp folder."""

    def start():
        sys.path.insert(0, str(MONITOR))
        try:
            for name in ("scheduler", "agent_state"):
                sys.modules.pop(name, None)
            spec = importlib.util.spec_from_file_location("scheduler", MONITOR / "scheduler.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["scheduler"] = module
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(MONITOR))
        monkeypatch.setattr(module, "SCHEDULE_PATH", tmp_path / "schedule.json")
        module.remember_tests([CLOUDFLARE, PING])
        return module

    yield start
    for name in ("scheduler", "agent_state"):
        sys.modules.pop(name, None)


def _due(module) -> set[str]:
    return {test["slug"] for test in module.due_tests(time.monotonic())}


def _left(module, key: str = KEY) -> float:
    return module.state._next_run[key] - time.monotonic()


def test_a_new_agent_runs_its_checks_at_once_and_the_cloudflare_test_on_the_clock(agent):
    module = agent()
    # A ping has nothing to line up with. The Cloudflare test waits for the next mark: it never runs "this second".
    assert _due(module) == {"icmp-path"}
    assert 0 < _left(module) <= 3600


def test_marks_are_whole_intervals_past_the_hour(agent):
    module = agent()
    nine_twenty = 1_794_560_400 + 9 * 3600 + 20 * 60  # 09:20:00 on a day that starts at a whole hour
    assert module._next_mark(900, nine_twenty) == nine_twenty + 600  # 09:30
    assert module._next_mark(900, nine_twenty + 600) == nine_twenty + 600  # already on a mark
    assert module._next_mark(900, nine_twenty + 601) == nine_twenty + 1500  # 09:45
    assert module._next_mark(3600, nine_twenty) == nine_twenty + 2400  # 10:00


def test_each_run_is_followed_by_the_next_mark_not_a_set_time_later(agent, monkeypatch):
    module = agent()
    quarter = dict(CLOUDFLARE, interval_seconds=900)
    module.remember_tests([quarter, PING])
    # A run that ended 40 seconds after 09:30: the next is at 09:45, 860 seconds on, not 900.
    half_past = 1_794_560_400 + 9 * 3600 + 30 * 60
    monkeypatch.setattr(module.time, "time", lambda: half_past + 40.0)
    module._mark_ran(quarter)
    assert _left(module) == pytest.approx(860, abs=2)
    # A run that ends exactly on a mark waits for the one after it.
    monkeypatch.setattr(module.time, "time", lambda: half_past + 0.0)
    module._mark_ran(quarter)
    assert _left(module) == pytest.approx(900, abs=2)


def test_a_restart_does_not_run_the_cloudflare_test_again_early(agent, tmp_path):
    first = agent()
    first._mark_ran(CLOUDFLARE)
    first._mark_ran(PING)
    saved = json.loads((tmp_path / "schedule.json").read_text())
    # Only the slow test is written down: a check that runs every minute may as well run again.
    assert list(saved) == [KEY]
    wait = _left(first)

    restarted = agent()
    assert _due(restarted) == {"icmp-path"}
    assert _left(restarted) == pytest.approx(wait, abs=5)


def test_a_run_long_overdue_waits_for_the_next_mark(agent, tmp_path):
    (tmp_path / "schedule.json").write_text(json.dumps({KEY: time.time() - 3 * 3600}))
    module = agent()
    assert "cloudflare-speed" not in _due(module)
    assert 0 < _left(module) <= 3600


def test_a_clock_that_moved_back_or_a_damaged_file_does_not_hold_the_test_for_long(agent, tmp_path):
    (tmp_path / "schedule.json").write_text(json.dumps({KEY: time.time() + 86400}))
    module = agent()
    _due(module)
    assert 0 < _left(module) <= 3600
    (tmp_path / "schedule.json").write_text("not json")
    module = agent()
    _due(module)
    assert 0 < _left(module) <= 3600


def test_a_schedule_file_that_cannot_be_written_does_not_stop_the_test(agent, tmp_path, monkeypatch):
    module = agent()
    monkeypatch.setattr(module, "SCHEDULE_PATH", tmp_path / "missing-folder" / "schedule.json")
    module._mark_ran(CLOUDFLARE)
    assert KEY in module.state._next_run


def test_a_refusal_with_a_wait_pushes_the_next_run_past_it(agent, monkeypatch, tmp_path):
    module = agent()

    class Refused(OSError):
        retry_after = 3550.0

    def refuse(_test):
        raise Refused("Cloudflare refused the speed test (HTTP 429)")

    monkeypatch.setattr(module, "run_check", refuse)
    assert module._run_one(CLOUDFLARE) is None
    # The wait plus five minutes is 3850 seconds: the next run is at the first mark after that.
    wait = _left(module)
    assert 3849 <= wait <= 3850 + 3600
    assert "429" in module.state._last_error

    # A restart keeps the longer wait.
    restarted = agent()
    assert "cloudflare-speed" not in _due(restarted)
    assert _left(restarted) == pytest.approx(wait, abs=5)


def test_a_wait_is_five_minutes_longer_than_asked_and_never_endless(agent):
    module = agent()
    assert module._hold(CLOUDFLARE, 600) == 900
    assert module._hold(CLOUDFLARE, None) == 0
    assert module._hold(CLOUDFLARE, "soon") == 0
    # A very long wait is capped, so one bad answer cannot park the test for days.
    assert module._hold(CLOUDFLARE, 10**9) == 3600 + module.MAX_EXTRA_WAIT


def test_a_check_that_is_not_on_the_clock_keeps_its_plain_interval(agent):
    module = agent()
    module._mark_ran(PING)
    assert _left(module, "wired:icmp-path") == pytest.approx(60, abs=2)


SERVER = {"server_url": "http://10.7.0.1:8847", "token": "t", "name": "pi"}


def _results(module, monkeypatch, ids):
    made = iter({"id": item} for item in ids)
    monkeypatch.setattr(module, "run_check", lambda _test: next(made))


def test_results_that_could_not_be_uploaded_go_with_the_next_batch(agent, monkeypatch):
    module = agent()
    _results(module, monkeypatch, ["during-outage", "after"])

    def down(*_args, **_kwargs):
        raise OSError("tunnel down")

    monkeypatch.setattr(module, "post_json", down)
    with pytest.raises(OSError):
        module._run_due_tests(SERVER, [PING])
    assert [item["id"] for item in module._unsent] == ["during-outage"]

    sent: list[dict] = []
    monkeypatch.setattr(module, "post_json", lambda _url, body, *_rest: sent.append(body) or {})
    module._run_due_tests(SERVER, [PING])
    # The failed check from the outage arrives first, so the dashboard shows it instead of a gap.
    assert [item["id"] for item in sent[0]["results"]] == ["during-outage", "after"]
    assert module._unsent == []


def test_a_batch_the_server_rejects_is_not_kept(agent, monkeypatch):
    import urllib.error

    module = agent()
    _results(module, monkeypatch, ["bad"])

    def rejected(url, *_args, **_kwargs):
        raise urllib.error.HTTPError(url, 422, "Unprocessable", {}, None)

    monkeypatch.setattr(module, "post_json", rejected)
    with pytest.raises(urllib.error.HTTPError):
        module._run_due_tests(SERVER, [PING])
    # Kept, it would be sent and refused again with every later batch, and nothing new would get through.
    assert module._unsent == []


def test_a_server_error_keeps_the_batch(agent, monkeypatch):
    import urllib.error

    module = agent()
    _results(module, monkeypatch, ["kept"])

    def broken(url, *_args, **_kwargs):
        raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

    monkeypatch.setattr(module, "post_json", broken)
    with pytest.raises(urllib.error.HTTPError):
        module._run_due_tests(SERVER, [PING])
    assert [item["id"] for item in module._unsent] == ["kept"]


def test_unsent_results_are_capped_keeping_the_newest(agent, monkeypatch):
    module = agent()
    module._unsent[:] = [{"id": f"old-{index}"} for index in range(module.MAX_UNSENT + 100)]
    _results(module, monkeypatch, ["newest"])

    def down(*_args, **_kwargs):
        raise OSError("tunnel down")

    monkeypatch.setattr(module, "post_json", down)
    with pytest.raises(OSError):
        module._run_due_tests(SERVER, [PING])
    assert len(module._unsent) == module.MAX_UNSENT
    assert module._unsent[-1]["id"] == "newest"
    assert module._unsent[0]["id"] == "old-101"


def test_an_interval_is_counted_from_when_the_check_began(agent):
    module = agent()
    # A check that took 4 seconds: the next is 56 seconds on, so runs stay 60 seconds apart.
    module._mark_ran(PING, 0.0, time.monotonic() - 4.0)
    assert _left(module, "wired:icmp-path") == pytest.approx(56, abs=1)
    # One that took longer than its interval is due again at once, never in the past by more than that.
    module._mark_ran(PING, 0.0, time.monotonic() - 90.0)
    assert _left(module, "wired:icmp-path") == pytest.approx(0, abs=1)


TRACE = {"slug": "mtr-path", "kind": "mtr", "target": "203.0.113.5", "network_slug": "wired", "link": "wired", "interval_seconds": 900}
WIFI_TRACE = dict(TRACE, network_slug="wifi", link="wireless")


def test_a_failed_path_check_runs_the_trace_for_that_link_at_once(agent):
    module = agent()
    module.remember_tests([PING, TRACE, WIFI_TRACE])
    failed = [{"test_slug": "icmp-path", "network_slug": "wired", "status": "fail"}]
    assert module._with_failure_traces([], failed, 1000.0) == [TRACE]
    # Still down a minute later: no second trace until five minutes have gone by.
    assert module._with_failure_traces([], failed, 1060.0) == []
    assert module._with_failure_traces([], failed, 1000.0 + module.FAILURE_TRACE_GAP + 1) == [TRACE]


def test_a_trace_is_not_added_when_it_is_not_wanted(agent):
    module = agent()
    module.remember_tests([PING, TRACE, WIFI_TRACE])
    # A slow path, a failed web check, or a path that is fine: no trace.
    assert module._with_failure_traces([], [{"test_slug": "icmp-path", "network_slug": "wired", "status": "degraded"}], 5000.0) == []
    assert module._with_failure_traces([], [{"test_slug": "http-google", "network_slug": "wired", "status": "fail"}], 5000.0) == []
    # Already in this batch: not run twice.
    failed = [{"test_slug": "http-path", "network_slug": "wired", "status": "fail"}]
    assert module._with_failure_traces([TRACE], failed, 5000.0) == [TRACE]


def test_the_command_answer_can_say_a_speed_test_is_waiting(agent, monkeypatch):
    module = agent()
    module.state.speedtest_wake.clear()
    monkeypatch.setattr(module, "get_json", lambda *_args: {"restart": False, "speedtest": True})
    monkeypatch.setattr(module, "remember_trusted", lambda _body: None)
    module.poll_restart({"server_url": "http://10.7.0.1:8847", "token": "t", "name": "pi"})
    assert module.state.speedtest_wake.is_set()
    module.state.speedtest_wake.clear()
