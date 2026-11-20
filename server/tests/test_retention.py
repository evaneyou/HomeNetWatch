import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    Group,
    Network,
    NetworkKind,
    ResultStatus,
    Monitor,
    MonitorStatus,
    Test,
    TestKind,
    TestResult,
    WifiObservation,
)
from app.services.retention import apply_retention


def test_retention_averages_old_samples_and_drops_anything_past_two_years():
    engine = create_engine(
        os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://hns:hns-dev-password@127.0.0.1:55432/hns",
        ),
        pool_pre_ping=True,
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    suffix = uuid.uuid4().hex[:8]
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    group = Group(slug=f"retain-{suffix}", name="Retain")
    session.add(group)
    session.flush()
    monitor = Monitor(
        group_id=group.id,
        slug=f"pi-retain-{suffix}",
        name="Pi Retain",
        token_hash="test",
        status=MonitorStatus.online,
    )
    network = Network(group_id=group.id, slug="wan", name="WAN", kind=NetworkKind.wired)
    session.add_all([monitor, network])
    session.flush()
    test = Test(
        monitor_id=monitor.id,
        network_id=network.id,
        slug="ping-gateway",
        name="Ping",
        kind=TestKind.ping,
        target="1.1.1.1",
    )
    session.add(test)
    session.flush()

    def result(when: datetime, rtt: float, status: ResultStatus, external_id: str) -> TestResult:
        return TestResult(
            external_id=external_id,
            test_id=test.id,
            started_at=when,
            finished_at=when + timedelta(seconds=1),
            status=status,
            stages=[{"name": "icmp", "duration_ms": rtt, "status": status.value}],
            metrics={"rtt_ms": rtt, "loss_pct": 0.0},
        )

    session.add_all(
        [
            result(now - timedelta(days=1), 11, ResultStatus.ok, f"recent-{suffix}"),
            result(now - timedelta(days=30), 12, ResultStatus.ok, f"within-{suffix}"),
            result(now - timedelta(days=70), 13, ResultStatus.ok, f"past-a-{suffix}"),
            result(now - timedelta(days=70) + timedelta(minutes=10), 15, ResultStatus.ok, f"past-b-{suffix}"),
            result(datetime(2025, 10, 15, 8, 10, tzinfo=timezone.utc), 10, ResultStatus.ok, f"h1-{suffix}"),
            result(datetime(2025, 10, 15, 8, 20, tzinfo=timezone.utc), 20, ResultStatus.ok, f"h2-{suffix}"),
            result(datetime(2025, 10, 15, 8, 40, tzinfo=timezone.utc), 30, ResultStatus.fail, f"h3-{suffix}"),
            result(datetime(2025, 4, 15, 1, 0, tzinfo=timezone.utc), 40, ResultStatus.ok, f"d1-{suffix}"),
            result(datetime(2025, 4, 15, 5, 0, tzinfo=timezone.utc), 50, ResultStatus.degraded, f"d2-{suffix}"),
            result(datetime(2025, 4, 15, 9, 0, tzinfo=timezone.utc), 60, ResultStatus.ok, f"d3-{suffix}"),
            result(datetime(2023, 12, 15, 12, 0, tzinfo=timezone.utc), 99, ResultStatus.ok, f"old-{suffix}"),
        ]
    )
    session.add_all(
        [
            WifiObservation(
                monitor_id=monitor.id,
                scanned_at=datetime(2025, 10, 15, 8, 10, tzinfo=timezone.utc),
                ssid="Home",
                bssid="aa:bb:cc:dd:ee:01",
                vendor="Test",
                band="5",
                channel=36,
                channel_width=80,
                rssi_dbm=-40,
                noise_dbm=-95,
                security="WPA2",
                phy="ac",
                max_link_rate_mbps=400,
            ),
            WifiObservation(
                monitor_id=monitor.id,
                scanned_at=datetime(2025, 10, 15, 8, 40, tzinfo=timezone.utc),
                ssid="Home",
                bssid="aa:bb:cc:dd:ee:01",
                vendor="Test",
                band="5",
                channel=36,
                channel_width=80,
                rssi_dbm=-60,
                noise_dbm=-91,
                security="WPA2",
                phy="ac",
                max_link_rate_mbps=200,
            ),
            WifiObservation(
                monitor_id=monitor.id,
                scanned_at=datetime(2023, 12, 15, 12, 0, tzinfo=timezone.utc),
                ssid="Home",
                bssid="aa:bb:cc:dd:ee:01",
                vendor="Test",
                band="5",
                channel=36,
                channel_width=80,
                rssi_dbm=-70,
                noise_dbm=-90,
                security="WPA2",
                phy="ac",
                max_link_rate_mbps=100,
            ),
        ]
    )
    session.commit()

    try:
        summary = apply_retention(session, now)
        session.commit()
        assert summary["hourly_results"] >= 1
        assert summary["daily_results"] >= 1
        assert summary["deleted_results"] >= 1
        assert summary["hourly_wifi"] >= 1
        assert summary["deleted_wifi"] >= 1

        rows = session.scalars(select(TestResult).where(TestResult.test_id == test.id)).all()
        assert len(rows) == 5
        raw = [row for row in rows if row.message is None]
        assert {row.external_id for row in raw} == {f"recent-{suffix}", f"within-{suffix}"}
        assert next(row for row in raw if row.external_id == f"recent-{suffix}").metrics["rtt_ms"] == 11

        hourlies = [row for row in rows if row.message == "Hourly average"]
        assert len(hourlies) == 2
        hourly = next(row for row in hourlies if row.started_at == datetime(2025, 10, 15, 8, 0, tzinfo=timezone.utc))
        aged = next(row for row in hourlies if row.started_at == datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc))
        assert aged.metrics["rtt_ms"] == 14
        assert hourly.started_at == datetime(2025, 10, 15, 8, 0, tzinfo=timezone.utc)
        assert hourly.status == ResultStatus.fail
        assert hourly.metrics["rtt_ms"] == 20
        assert hourly.stages[0]["duration_ms"] == 20
        assert hourly.stages[0]["status"] == "fail"

        daily = next(row for row in rows if row.message == "Daily average")
        assert daily.started_at == datetime(2025, 4, 15, 0, 0, tzinfo=timezone.utc)
        assert daily.status == ResultStatus.degraded
        assert daily.metrics["rtt_ms"] == 50

        scans = session.scalars(
            select(WifiObservation).where(WifiObservation.monitor_id == monitor.id)
        ).all()
        assert len(scans) == 1
        assert scans[0].rssi_dbm == -50
        assert scans[0].noise_dbm == -93
        assert scans[0].max_link_rate_mbps == 300
        assert scans[0].scanned_at == datetime(2025, 10, 15, 8, 0, tzinfo=timezone.utc)

        again = apply_retention(session, now)
        session.commit()
        assert again["hourly_results"] == 0
        assert again["daily_results"] == 0
        assert again["hourly_wifi"] == 0
        kept = session.scalars(select(TestResult).where(TestResult.test_id == test.id)).all()
        assert len(kept) == 5
    finally:
        session.execute(
            text("DELETE FROM test_results WHERE test_id = :tid"),
            {"tid": test.id},
        )
        session.execute(text("DELETE FROM tests WHERE id = :tid"), {"tid": test.id})
        session.execute(
            text("DELETE FROM wifi_observations WHERE monitor_id = :sid"),
            {"sid": monitor.id},
        )
        session.execute(text("DELETE FROM monitors WHERE id = :sid"), {"sid": monitor.id})
        session.execute(text("DELETE FROM networks WHERE group_id = :gid"), {"gid": group.id})
        session.execute(text("DELETE FROM groups WHERE id = :gid"), {"gid": group.id})
        session.commit()
        session.close()
