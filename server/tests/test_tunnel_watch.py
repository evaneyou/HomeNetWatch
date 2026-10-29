"""Agent tunnel_watch self-heal behaviour (loaded from ../monitor)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MONITOR = Path(__file__).resolve().parents[2] / "monitor"
if not (MONITOR / "tunnel_watch.py").is_file():
    pytest.skip("monitor package not present", allow_module_level=True)


def _load():
    sys.path.insert(0, str(MONITOR))
    try:
        # Ensure a fresh module so monkeypatches do not leak across tests.
        for name in ("tunnel_watch", "agent_state"):
            sys.modules.pop(name, None)
        state_spec = importlib.util.spec_from_file_location("agent_state", MONITOR / "agent_state.py")
        assert state_spec and state_spec.loader
        state = importlib.util.module_from_spec(state_spec)
        sys.modules["agent_state"] = state
        state_spec.loader.exec_module(state)
        tw_spec = importlib.util.spec_from_file_location("tunnel_watch", MONITOR / "tunnel_watch.py")
        assert tw_spec and tw_spec.loader
        tw = importlib.util.module_from_spec(tw_spec)
        sys.modules["tunnel_watch"] = tw
        tw_spec.loader.exec_module(tw)
        return tw
    finally:
        pass


def test_recovery_retries_within_a_minute_when_access_is_up(monkeypatch):
    tw = _load()
    restores: list[str] = []

    monkeypatch.setattr(tw, "_configured", lambda iface: iface == "wg1")
    monkeypatch.setattr(tw, "_peer_ok", lambda host, iface: False)
    monkeypatch.setattr(tw, "_access_ok", lambda link: link == "wireless")
    monkeypatch.setattr(tw, "_restore", lambda iface: restores.append(iface) or True)
    monkeypatch.setattr(tw.time, "sleep", lambda _s: None)
    tw._fail_counts["wg1"] = 0
    tw._last_restore_at["wg1"] = 0.0

    tw.check_once(now=100.0)
    tw.check_once(now=101.0)
    assert restores == ["wg1"]

    # Still within the 15-minute outage cooldown, but recovery cooldown is only 60s.
    restores.clear()
    tw.check_once(now=130.0)
    tw.check_once(now=162.0)
    assert restores == ["wg1"]


def test_outage_keeps_long_cooldown_when_access_is_down(monkeypatch):
    tw = _load()
    restores: list[str] = []

    monkeypatch.setattr(tw, "_configured", lambda iface: iface == "wg1")
    monkeypatch.setattr(tw, "_peer_ok", lambda host, iface: False)
    monkeypatch.setattr(tw, "_access_ok", lambda link: False)
    monkeypatch.setattr(tw, "_restore", lambda iface: restores.append(iface) or True)
    monkeypatch.setattr(tw.time, "sleep", lambda _s: None)
    tw._fail_counts["wg1"] = 0
    tw._last_restore_at["wg1"] = 0.0

    tw.check_once(now=100.0)
    tw.check_once(now=101.0)
    assert restores == ["wg1"]

    restores.clear()
    tw.check_once(now=200.0)
    tw.check_once(now=201.0)
    assert restores == []

    tw.check_once(now=100.0 + tw.COOLDOWN_OUTAGE_S + 2)
    tw.check_once(now=100.0 + tw.COOLDOWN_OUTAGE_S + 3)
    assert restores == ["wg1"]
