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
