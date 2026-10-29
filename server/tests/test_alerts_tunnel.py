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


def test_variance_clamped_to_50_and_ingest_refreshes_librespeed_stable_status(db):
    from app.internet_tests import classify_transfer, ensure_internet_tests, load_internet_service, save_internet_service
    from app.schemas import IngestResult
    from app.services.ingest import ingest_results

    monitor, _token = _make_monitor(db, "Speed Clamp Pi")
    ensure_internet_tests(db, monitor)
    row = save_internet_service(
        db,
        {
            "interval_seconds": 60,
            "http_enabled": True,
            "icmp_enabled": True,
            "mtr_enabled": True,
            "http_variance_pct": 25,
            "icmp_variance_pct": 25,
            "mtr_variance_pct": 25,
            "librespeed_enabled": True,
            "librespeed_interval_seconds": 900,
            "librespeed_variance_pct": 100,
        },
    )
    assert row.librespeed_variance_pct == 50
    loaded = load_internet_service(db)
    assert loaded.librespeed_variance_pct == 50

    speed_test = db.scalar(
        select(Test)
        .join(Network, Test.network_id == Network.id)
        .where(Test.monitor_id == monitor.id, Network.slug == "wired", Test.slug == "librespeed")
    )
    assert speed_test is not None
    # Seed 4 healthy baseline samples (300 down / 30 up)
    for i in range(4):
        t = NOW - timedelta(hours=4 - i)
        db.add(
            TestResult(
                external_id=f"base-{monitor.id.hex[:6]}-{i}",
                test_id=speed_test.id,
                started_at=t,
                finished_at=t + timedelta(seconds=15),
                status=ResultStatus.ok,
                metrics={"down_mbps": 300.0, "up_mbps": 30.0},
            )
        )
    db.flush()

    # Even if someone passes 100 into classify_transfer, it clamps to 50% and catches a 0.6 Mbps upload collapse
    st, reason = classify_transfer(db, speed_test, {"down_mbps": 280.0, "up_mbps": 0.6}, 100)
    assert st == ResultStatus.degraded
    assert "Upload 0.6 Mbps" in reason

    # Ingesting degraded librespeed samples updates Test.stable_status
    samples = [
        IngestResult(
            id=f"ing-{monitor.id.hex[:6]}-{idx}",
            test_slug="librespeed",
            network_slug="wired",
            started_at=NOW - timedelta(minutes=30 - idx * 10),
            finished_at=NOW - timedelta(minutes=30 - idx * 10) + timedelta(seconds=10),
            status="ok",
            metrics={"down_mbps": 260.0, "up_mbps": 0.5},
        )
        for idx in range(3)
    ]
    ingest_results(db, monitor, samples)
    db.refresh(speed_test)
    assert speed_test.stable_status == ResultStatus.degraded


def test_alert_hierarchy_collapses_dual_link_bandwidth_and_suppresses_apps_and_path(db):
    from app.internet_tests import ensure_internet_tests
    from app.models import SyntheticRun, SyntheticSample

    monitor, _token = _make_monitor(db, "Hierarchy Pi")
    monitor.last_seen_at = NOW
    ensure_internet_tests(db, monitor)
    db.flush()

    for slug in ("wired", "wifi"):
        net = db.scalar(select(Network).where(Network.group_id == monitor.group_id, Network.slug == slug))
        speed = db.scalar(select(Test).where(Test.monitor_id == monitor.id, Test.network_id == net.id, Test.slug == "librespeed"))
        speed.stable_status = ResultStatus.degraded
        speed.state_since = NOW - timedelta(minutes=20)
        db.add(
            TestResult(
                external_id=f"spd-{monitor.id.hex[:6]}-{slug}",
                test_id=speed.id,
                started_at=NOW - timedelta(minutes=5),
                finished_at=NOW - timedelta(minutes=4),
                status=ResultStatus.degraded,
                status_reason="Upload 0.6 Mbps is more than 25% below the recent normal of 31.0 Mbps.",
                metrics={"down_mbps": 270.0, "up_mbps": 0.6},
            )
        )
        # Also mark path degraded on both links
        icmp = db.scalar(select(Test).where(Test.monitor_id == monitor.id, Test.network_id == net.id, Test.slug == "icmp-path"))
        icmp.stable_status = ResultStatus.degraded
        icmp.state_since = NOW - timedelta(minutes=10)

    # Also add failing continuous synthetic runs across both links
    for profile in ("voip-g711a", "video-conference", "remote-desktop"):
        run = SyntheticRun(
            monitor_id=monitor.id,
            profile_slug=profile,
            mode="continuous",
            links=["wired", "wireless"],
            options={"preset": profile},
            status="running",
            finished_links=[],
        )
        db.add(run)
        db.flush()
        for link in ("wired", "wireless"):
            for idx in range(3):
                db.add(
                    SyntheticSample(
                        run_id=run.id,
                        link=link,
                        sample_key=f"s-{idx}",
                        interval_index=idx,
                        recorded_at=NOW - timedelta(minutes=3 - idx),
                        rtt_ms=180.0,
                        jitter_ms=35.0,
                        loss_pct=12.5,
                        verdict="fail",
                        metrics={},
                    )
                )
    db.commit()

    conditions = [c for c in alerts.evaluate(db, NOW) if str(monitor.id) in c.key]
    keys = [c.key for c in conditions]
    assert keys == [f"net-bandwidth:{monitor.id}:wan"]
    assert "ISP bandwidth drop" in conditions[0].title
    assert "0.6 Mbps" in conditions[0].detail

