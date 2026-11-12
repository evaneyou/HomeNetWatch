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


def test_a_new_agent_runs_every_test_at_once(agent):
    assert _due(agent()) == {"cloudflare-speed", "icmp-path"}


def test_a_restart_does_not_run_the_cloudflare_test_again_early(agent, tmp_path):
    first = agent()
    first._mark_ran(CLOUDFLARE)
    first._mark_ran(PING)
    saved = json.loads((tmp_path / "schedule.json").read_text())
    # Only the slow test is written down: a check that runs every minute may as well run again.
    assert list(saved) == [KEY]

    restarted = agent()
    assert _due(restarted) == {"icmp-path"}
    left = restarted.state._next_run[KEY] - time.monotonic()
    assert 3500 < left <= 3600


def test_the_cloudflare_test_runs_once_its_interval_has_passed(agent, tmp_path):
    (tmp_path / "schedule.json").write_text(json.dumps({KEY: time.time() - 3700}))
    assert "cloudflare-speed" in _due(agent())


def test_a_clock_that_moved_back_or_a_damaged_file_does_not_hold_the_test_forever(agent, tmp_path):
    (tmp_path / "schedule.json").write_text(json.dumps({KEY: time.time() + 86400}))
    assert "cloudflare-speed" in _due(agent())
    (tmp_path / "schedule.json").write_text("not json")
    assert "cloudflare-speed" in _due(agent())


def test_a_schedule_file_that_cannot_be_written_does_not_stop_the_test(agent, tmp_path, monkeypatch):
    module = agent()
    monkeypatch.setattr(module, "SCHEDULE_PATH", tmp_path / "missing-folder" / "schedule.json")
    module._mark_ran(CLOUDFLARE)
    assert KEY in module.state._next_run
