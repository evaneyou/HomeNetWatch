"""Old results are not shown as the current state, and a quiet device reads as offline sooner. No database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.readings_routes import OFFLINE_AFTER_SECONDS, mark_stale, went_quiet
from app.schemas import HealthCell

NOW = datetime(2026, 11, 27, 12, 30, tzinfo=timezone.utc)


def _cell(monitor, network, column: str, status: str, age_seconds: float | None) -> HealthCell:
    return HealthCell(
        monitor_id=monitor,
        monitor_slug="pi",
        monitor_name="Pi",
        network_id=network,
        network_slug="wifi",
        network_name="Wi-Fi",
        column=column,
        status=status,
        last_test_at=None if age_seconds is None else NOW - timedelta(seconds=age_seconds),
        failing_tests=1 if status in ("fail", "degraded") else 0,
    )


def _test(monitor, network, slug: str, interval: int, enabled: bool = True):
    return SimpleNamespace(monitor_id=monitor, network_id=network, slug=slug, interval_seconds=interval, enabled=enabled, network=SimpleNamespace(slug="wifi"))


def _run(internet_age: float | None, web_age: float | None, internet_status: str = "ok"):
    monitor, network = uuid4(), uuid4()
    tests = [
        _test(monitor, network, "icmp-path", 60),
        _test(monitor, network, "http-path", 60),
        _test(monitor, network, "mtr-path", 900),
        _test(monitor, network, "http-google", 300),
        _test(monitor, network, "librespeed", 900, enabled=False),
    ]
    cells = {
        (monitor, network, "internet"): _cell(monitor, network, "internet", internet_status, internet_age),
        (monitor, network, "web"): _cell(monitor, network, "web", "ok", web_age),
        (monitor, network, "apps"): _cell(monitor, network, "apps", "ok", 600),
    }
    mark_stale(tests, cells, NOW)
    return cells[(monitor, network, "internet")], cells[(monitor, network, "web")], cells[(monitor, network, "apps")]


def test_a_fresh_result_is_shown_as_it_is():
    internet, web, _apps = _run(90, 400)
    assert (internet.status, internet.stale) == ("ok", False)
    assert (web.status, web.stale) == ("ok", False)


def test_an_old_pass_is_no_longer_shown_as_passing():
    # Checks every 60 seconds: after three minutes with nothing new, the row has no data.
    internet, web, _apps = _run(181, 400)
    assert (internet.status, internet.stale) == ("unknown", True)
    # Web checks run every five minutes, so the same age is still current there.
    assert (web.status, web.stale) == ("ok", False)
    internet, web, _apps = _run(4000, 901)
    assert (web.status, web.stale) == ("unknown", True)


def test_an_old_failure_is_cleared_too():
    internet, _web, _apps = _run(4000, 4000, internet_status="fail")
    assert (internet.status, internet.stale, internet.failing_tests, internet.cause) == ("unknown", True, 0, None)


def test_a_cell_with_no_results_or_no_checks_is_left_alone():
    internet, _web, apps = _run(None, None)
    assert (internet.status, internet.stale) == ("ok", False)  # nothing to compare: last_test_at is empty
    # The apps cell has no checks of this kind; it has its own 15 minute rule.
    assert (apps.status, apps.stale) == ("ok", False)


def test_a_device_reads_as_offline_after_two_and_a_half_minutes():
    def monitor(seconds_ago: float | None, interval: int = 60):
        seen = None if seconds_ago is None else NOW - timedelta(seconds=seconds_ago)
        return SimpleNamespace(last_seen_at=seen, health_interval_seconds=interval)

    assert OFFLINE_AFTER_SECONDS == 150
    assert not went_quiet(monitor(120), NOW)
    assert went_quiet(monitor(151), NOW)
    assert not went_quiet(monitor(None), NOW)
    # A device told to check in less often gets a wait to match.
    assert not went_quiet(monitor(400, interval=300), NOW)
    assert went_quiet(monitor(751, interval=300), NOW)
