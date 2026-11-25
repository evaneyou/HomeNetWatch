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
    tw._last_restore_at["wg1"] = None

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
    tw._last_restore_at["wg1"] = None

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


def test_configured_works_without_read_access_to_etc_wireguard(monkeypatch):
    """The agent runs as the monitor user; /etc/wireguard is root-only, so the file check raises."""
    tw = _load()
    import subprocess

    def unreadable(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(tw.Path, "is_file", unreadable)
    calls: list[list[str]] = []

    def systemctl(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0 if args[-1] == "wg-quick@wg0" else 1)

    monkeypatch.setattr(tw.subprocess, "run", systemctl)
    assert tw._configured("wg0") is True
    assert tw._configured("wg1") is False
    assert calls[0][:3] == ["systemctl", "is-enabled", "--quiet"]


def _helper(tw, tmp_path, monkeypatch, outcome):
    """Stand in for the root systemd unit: on the agent's next wait, write the outcome and remove the request."""
    requests = tmp_path / "run-monitor"
    results = tmp_path / "run"
    requests.mkdir()
    results.mkdir()
    monkeypatch.setattr(tw, "REQUEST_DIR", requests)
    monkeypatch.setattr(tw, "RESULT_DIR", results)
    seen: list[str] = []

    def answer(_seconds):
        for request in requests.iterdir():
            seen.append(request.name)
            if outcome is not None:
                (results / f"hnw-{request.name}.result").write_text(outcome + "\n")
                request.unlink()

    monkeypatch.setattr(tw.time, "sleep", answer)
    return requests, seen


def test_restore_asks_the_root_helper_and_never_runs_sudo(monkeypatch, tmp_path):
    tw = _load()
    requests, seen = _helper(tw, tmp_path, monkeypatch, "success")

    def no_commands(*_args, **_kwargs):
        raise AssertionError("the agent's service sets NoNewPrivileges: it must not run sudo or any other command")

    monkeypatch.setattr(tw.subprocess, "run", no_commands)
    assert tw._restore("wg1") is True
    assert seen == ["restore-wg1"]
    assert list(requests.iterdir()) == []


def test_restore_reports_a_helper_that_failed(monkeypatch, tmp_path):
    tw = _load()
    _helper(tw, tmp_path, monkeypatch, "exit-code")
    assert tw._restore("wg0") is False


def test_restore_gives_up_and_withdraws_the_request_when_nothing_answers(monkeypatch, tmp_path):
    tw = _load()
    requests, seen = _helper(tw, tmp_path, monkeypatch, None)
    assert tw._restore("wg0") is False
    assert len(seen) == int(tw.RESTORE_WAIT_S / tw.RESTORE_POLL_S)
    # A request left behind would restart the tunnel later, when nobody is waiting for it.
    assert list(requests.iterdir()) == []


def test_restore_without_the_runtime_folder_fails_cleanly(monkeypatch, tmp_path):
    tw = _load()
    monkeypatch.setattr(tw, "REQUEST_DIR", tmp_path / "missing")
    monkeypatch.setattr(tw.time, "sleep", lambda _s: None)
    assert tw._restore("wg0") is False


def test_restore_takes_only_the_two_tunnels(monkeypatch, tmp_path):
    tw = _load()
    requests, seen = _helper(tw, tmp_path, monkeypatch, "success")
    assert tw._restore("../../etc/passwd") is False
    assert tw._restore("eth0") is False
    assert seen == []


def test_the_service_and_the_helper_units_agree():
    """sudo cannot work under NoNewPrivileges, so the pieces of the replacement must line up."""
    service = (MONITOR / "monitor.service").read_text(encoding="utf-8")
    assert "NoNewPrivileges=true" in service
    assert "RuntimeDirectory=monitor" in service
    assert "RuntimeDirectoryPreserve=yes" in service
    path_unit = (MONITOR / "hnw-restore-tunnel@.path").read_text(encoding="utf-8")
    helper = (MONITOR / "hnw-restore-tunnel@.service").read_text(encoding="utf-8")
    assert "PathExists=/run/monitor/restore-%i" in path_unit
    assert "ExecStart=/opt/monitor/restore_tunnel.sh %i" in helper
    assert "/run/hnw-restore-%i.result" in helper and "/run/monitor/restore-%i" in helper
    for script in ("install.sh", "update.sh"):
        text = (MONITOR / script).read_text(encoding="utf-8")
        assert "hnw-restore-tunnel@wg0.path hnw-restore-tunnel@wg1.path" in text, script
        assert "NOPASSWD" not in text, script
    packed = (MONITOR / "pack.sh").read_text(encoding="utf-8")
    assert "hnw-restore-tunnel@.path hnw-restore-tunnel@.service" in packed and "monitor.service" in packed
    for source in ("tunnel_watch.py", "scheduler.py"):
        assert '"sudo"' not in (MONITOR / source).read_text(encoding="utf-8"), source


def _adapter(sys_net, name, *, carrier="1", kind="1", wireless=True):
    path = sys_net / name
    path.mkdir(parents=True)
    if wireless:
        (path / "wireless").mkdir()
    (path / "type").write_text(kind + "\n")
    if carrier is not None:
        (path / "carrier").write_text(carrier + "\n")
    return path


def _wifi_setup(tw, tmp_path, monkeypatch, outcome="success"):
    sys_net = tmp_path / "sys-net"
    sys_net.mkdir()
    monkeypatch.setattr(tw, "SYS_NET", sys_net)
    monkeypatch.setattr(tw, "_configured", lambda iface: True)
    _requests, seen = _helper(tw, tmp_path, monkeypatch, outcome)
    return sys_net, seen


def test_wifi_link_down_names_only_a_real_station_adapter(monkeypatch, tmp_path):
    tw = _load()
    sys_net, _seen = _wifi_setup(tw, tmp_path, monkeypatch)
    _adapter(sys_net, "eth0", wireless=False, carrier="1")
    _adapter(sys_net, "hnwradio0", carrier="0")
    _adapter(sys_net, "hnwradio0mon", carrier="1", kind="803")
    _adapter(sys_net, "p2p-dev-wlan0", carrier="0")
    assert tw._wifi_link_down() is None  # no station adapter at all

    wlan = _adapter(sys_net, "wlan0", carrier="0")
    assert tw._wifi_link_down() == "wlan0"
    (wlan / "carrier").write_text("1\n")
    assert tw._wifi_link_down() is None
    # An adapter that is switched off cannot be asked for its carrier: that is "down" too.
    (wlan / "carrier").unlink()
    assert tw._wifi_link_down() == "wlan0"


def test_a_capture_radio_in_monitor_mode_is_never_taken_for_the_link(monkeypatch, tmp_path):
    tw = _load()
    sys_net, _seen = _wifi_setup(tw, tmp_path, monkeypatch)
    _adapter(sys_net, "wlan1", carrier="1", kind="803")
    _adapter(sys_net, "wlan0", carrier="0")
    assert tw._wifi_link_down() == "wlan0"


def test_wifi_rejoin_is_asked_after_four_checks_and_then_every_five_minutes(monkeypatch, tmp_path):
    tw = _load()
    sys_net, seen = _wifi_setup(tw, tmp_path, monkeypatch)
    _adapter(sys_net, "wlan0", carrier="0")

    for step in range(3):
        tw.check_wifi(now=100.0 + 30 * step)
    assert seen == []  # NetworkManager is still given time to retry by itself
    tw.check_wifi(now=190.0)
    assert seen == ["restore-wifi"]

    # Still down: no second request inside the cooldown, one after it.
    tw.check_wifi(now=220.0)
    tw.check_wifi(now=190.0 + tw.WIFI_REJOIN_COOLDOWN_S - 1)
    assert seen == ["restore-wifi"]
    tw.check_wifi(now=190.0 + tw.WIFI_REJOIN_COOLDOWN_S + 1)
    assert seen == ["restore-wifi", "restore-wifi"]


def test_wifi_that_comes_back_resets_the_count(monkeypatch, tmp_path):
    tw = _load()
    sys_net, seen = _wifi_setup(tw, tmp_path, monkeypatch)
    wlan = _adapter(sys_net, "wlan0", carrier="0")
    for step in range(3):
        tw.check_wifi(now=100.0 + 30 * step)
    (wlan / "carrier").write_text("1\n")
    tw.check_wifi(now=190.0)
    (wlan / "carrier").write_text("0\n")
    for step in range(3):
        tw.check_wifi(now=220.0 + 30 * step)
    assert seen == []


def test_wifi_is_left_alone_on_a_pi_without_a_wifi_tunnel(monkeypatch, tmp_path):
    tw = _load()
    sys_net, seen = _wifi_setup(tw, tmp_path, monkeypatch)
    monkeypatch.setattr(tw, "_configured", lambda iface: iface == "wg0")
    _adapter(sys_net, "wlan0", carrier="0")
    for step in range(10):
        tw.check_wifi(now=100.0 + 30 * step)
    assert seen == []


def test_a_failed_wifi_rejoin_does_not_stop_the_tunnel_checks(monkeypatch, tmp_path):
    tw = _load()
    sys_net, seen = _wifi_setup(tw, tmp_path, monkeypatch, outcome="exit-code")
    _adapter(sys_net, "wlan0", carrier="0")
    probed: list[str] = []
    monkeypatch.setattr(tw, "_peer_ok", lambda host, iface: probed.append(iface) or True)
    for step in range(4):
        tw.check_once(now=100.0 + 30 * step)
    assert seen == ["restore-wifi"]
    assert probed.count("wg0") == 4 and probed.count("wg1") == 4


def test_the_wifi_helper_takes_no_input_and_is_installed_everywhere():
    script = (MONITOR / "restore_wifi.sh").read_text(encoding="utf-8")
    # The adapters come from NetworkManager: nothing the agent (or the server) says reaches a root command.
    assert '"$1"' not in script and "${1" not in script and "$@" not in script and "$*" not in script
    assert 'nmcli --wait 45 device connect "$dev"' in script
    helper = (MONITOR / "hnw-restore-wifi.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/monitor/restore_wifi.sh\n" in helper
    assert "/run/hnw-restore-wifi.result" in helper and "/run/monitor/restore-wifi" in helper
    assert "PathExists=/run/monitor/restore-wifi" in (MONITOR / "hnw-restore-wifi.path").read_text(encoding="utf-8")
    for name in ("install.sh", "update.sh"):
        text = (MONITOR / name).read_text(encoding="utf-8")
        assert "restore_wifi.sh /opt/monitor/restore_wifi.sh" in text, name
        assert "hnw-restore-wifi.path hnw-restore-wifi.service /etc/systemd/system/" in text, name
        assert "hnw-restore-tunnel@wg1.path hnw-restore-wifi.path" in text, name
    packed = (MONITOR / "pack.sh").read_text(encoding="utf-8")
    assert "restore_wifi.sh" in packed and "hnw-restore-wifi.path hnw-restore-wifi.service" in packed
