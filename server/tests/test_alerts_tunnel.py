"""Tunnel-down alerts and access-plane correlation."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import alerts
from app.models import Network, NetworkKind, ResultStatus, Test, TestKind, TestResult
from tests.test_ingest import _make_monitor

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def test_stale_wireless_handshake_opens_tunnel_down_condition(db):
    monitor, _token = _make_monitor(db, "Tunnel Pi")
    monitor.last_seen_at = NOW
    monitor.wg_address = "10.7.0.2"
    monitor.wg_wireless_address = "10.7.1.2"
    monitor.last_handshake_at = NOW - timedelta(minutes=1)
    monitor.last_wireless_handshake_at = NOW - timedelta(minutes=30)
    db.commit()

    conditions = alerts.evaluate(db, NOW)
    keys = {c.key for c in conditions}
    assert f"tunnel-down:{monitor.id}:wireless" in keys
    assert f"tunnel-down:{monitor.id}:wired" not in keys
    tunnel = next(c for c in conditions if c.key == f"tunnel-down:{monitor.id}:wireless")
    assert tunnel.category == "pi"
    assert "tunnel" in tunnel.title.lower()


def test_net_down_skipped_when_web_still_ok_on_link(db):
    monitor, _token = _make_monitor(db, "Access Ok Pi")
    monitor.last_seen_at = NOW
    monitor.wg_wireless_address = "10.7.1.2"
    monitor.last_wireless_handshake_at = NOW - timedelta(minutes=40)
    group_id = monitor.group_id
    network = db.scalar(select(Network).where(Network.group_id == group_id, Network.kind == NetworkKind.wifi))
    if network is None:
        network = Network(group_id=group_id, slug="wifi-t", name="Wi-Fi", kind=NetworkKind.wifi)
        db.add(network)
        db.flush()
    path = Test(
        monitor_id=monitor.id,
        network_id=network.id,
        slug="icmp-path",
        name="ICMP",
        kind=TestKind.ping,
        target="x",
        enabled=True,
        stable_status=ResultStatus.fail,
        state_since=NOW - timedelta(minutes=5),
    )
    web = Test(
        monitor_id=monitor.id,
        network_id=network.id,
        slug="http-youtube",
        name="YouTube",
        kind=TestKind.http,
        target="https://youtube.com",
        enabled=True,
        stable_status=ResultStatus.ok,
    )
    db.add_all([path, web])
    db.flush()
    db.add(
        TestResult(
            external_id=f"web-ok-{monitor.id.hex[:8]}",
            test_id=web.id,
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(seconds=50),
            status=ResultStatus.ok,
            metrics={},
        )
    )
    db.commit()

    conditions = alerts.evaluate(db, NOW)
    keys = {c.key for c in conditions}
    assert f"net-down:{monitor.id}:wifi" not in keys
    assert f"tunnel-down:{monitor.id}:wireless" in keys
