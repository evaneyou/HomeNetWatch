"""KPI reporting: exact availability and loss from known data, per period, and kept after raw rollups."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import kpi
from app.main import app
from app.models import (
    KpiDaily,
    KpiHourly,
    Monitor,
    Network,
    NetworkKind,
    ResultStatus,
    SyntheticRun,
    SyntheticSample,
    Test,
    TestKind,
    TestResult,
)
from tests.conftest import login
from tests.test_guest_login import _ensure_guest
from tests.test_ingest import _make_monitor

NOW = datetime(2026, 10, 16, 12, 30, tzinfo=timezone.utc)
HOUR = NOW.replace(minute=0)


def _pi(db) -> Monitor:
    monitor, _token = _make_monitor(db, f"KPI Pi {uuid.uuid4().hex[:6]}")
    return monitor


def _link(db, monitor: Monitor, kind: NetworkKind) -> Test:
    network = db.scalar(select(Network).where(Network.group_id == db.info["group_id"], Network.kind == kind))
    if network is None:
        network = Network(group_id=db.info["group_id"], slug=f"{kind.value}-{uuid.uuid4().hex[:4]}", name=kind.value, kind=kind)
        db.add(network)
        db.flush()
    test = Test(monitor_id=monitor.id, network_id=network.id, slug="icmp-path", name="ICMP", kind=TestKind.ping, target="x")
    db.add(test)
    db.flush()
    return test


def _ping(db, test: Test, minute: int, status: ResultStatus, loss: float = 0.0, rtt: float = 20.0, when: datetime = HOUR) -> None:
    db.add(
        TestResult(
            external_id=f"kpi-{uuid.uuid4().hex}",
            test_id=test.id,
            started_at=when + timedelta(minutes=minute),
            finished_at=when + timedelta(minutes=minute, seconds=5),
            status=status,
            metrics={"rtt_ms": rtt, "loss_pct": loss, "jitter_ms": 2.0},
        )
    )


def _run(db, monitor: Monitor, slug: str, links=("wired",)) -> SyntheticRun:
    run = SyntheticRun(monitor_id=monitor.id, profile_slug=slug, mode="continuous", links=list(links), options={}, status="running", finished_links=[])
    db.add(run)
    db.flush()
    return run


def _sample(db, run: SyntheticRun, minute: int, verdict: str, loss: float, link="wired", when: datetime = HOUR) -> None:
    db.add(
        SyntheticSample(
            run_id=run.id,
            link=link,
            sample_key=f"{minute}:{uuid.uuid4().hex[:6]}",
            recorded_at=when + timedelta(minutes=minute),
            rtt_ms=30.0,
            jitter_ms=3.0,
            loss_pct=loss,
            verdict=verdict,
            metrics={},
        )
    )


@pytest.fixture(autouse=True)
def _clean(db):
    db.execute(delete(KpiHourly))
    db.execute(delete(KpiDaily))
    db.commit()
    yield
    db.rollback()
    db.execute(delete(KpiHourly))
    db.execute(delete(KpiDaily))
    db.commit()


def test_internet_availability_outage_minutes_and_incidents(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    # 20 pings in the hour: 2 consecutive failures, then 1 later failure. Two incidents.
    plan = ["ok"] * 5 + ["fail", "fail"] + ["ok"] * 8 + ["fail"] + ["ok"] * 4
    for minute, state in enumerate(plan):
        _ping(db, wired, minute, ResultStatus.fail if state == "fail" else ResultStatus.ok, loss=100.0 if state == "fail" else 0.0)
    db.commit()
    assert kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1)) >= 1
    db.commit()

    row = db.scalar(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id, KpiHourly.kind == "internet"))
    assert (row.n, row.failed, row.incidents, row.link) == (20, 3, 2, "wired")

    result = kpi.report(db, "day", now=NOW, monitor_id=monitor.id)
    internet = result["summary"]["internet"]
    assert internet["availability"] == pytest.approx(85.0)
    assert internet["incidents"] == 2
    assert internet["outage_minutes"] == pytest.approx(9.0)  # 3 of 20 checks failed in a 60 minute hour
    assert internet["mttr_minutes"] == pytest.approx(4.5)
    assert result["links"]["internet"]["wired"]["availability"] == pytest.approx(85.0)
    assert "wireless" not in result["links"]["internet"]


def test_voice_and_video_availability_and_packet_loss(db):
    monitor = _pi(db)
    voice = _run(db, monitor, "voip-g711a", ("wired", "wireless"))
    video = _run(db, monitor, "video-conference")
    for minute in range(10):
        _sample(db, voice, minute, "fail" if minute == 0 else "pass", loss=1.0 if minute == 0 else 0.0)
        _sample(db, video, minute, "pass", loss=0.2)
    _sample(db, voice, 11, "pass", loss=0.0, link="wireless")
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    db.commit()

    result = kpi.report(db, "day", now=NOW, monitor_id=monitor.id)
    assert result["summary"]["voice"]["availability"] == pytest.approx(100.0 * (1 - 1 / 11))
    assert result["summary"]["voice"]["loss_avg"] == pytest.approx(1.0 / 11)
    assert result["summary"]["voice"]["loss_max"] == 1.0
    assert result["summary"]["video"]["availability"] == pytest.approx(100.0)
    assert result["summary"]["video"]["loss_avg"] == pytest.approx(0.2)
    assert result["links"]["voice"]["wireless"]["availability"] == pytest.approx(100.0)
    assert result["app_loss_avg"] is not None and 0 < result["app_loss_avg"] < 1


def test_manual_and_stress_runs_are_not_counted(db):
    monitor = _pi(db)
    run = _run(db, monitor, "stress-voice")
    run.mode = "stress"
    manual = _run(db, monitor, "voip-g711a")
    manual.mode = "manual"
    for target in (run, manual):
        _sample(db, target, 1, "fail", loss=50.0)
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    db.commit()
    assert db.scalar(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id)) is None


def test_rebuild_is_repeatable_and_backfill_fills_older_hours(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for day in range(3):
        for minute in range(5):
            _ping(db, wired, minute, ResultStatus.ok, when=HOUR - timedelta(days=day))
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    db.commit()
    assert len(db.scalars(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id)).all()) == 1

    kpi.backfill(db, now=NOW)
    db.commit()
    mine = db.scalars(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id, KpiHourly.kind == "internet")).all()
    assert len(mine) == 3 and all(row.n == 5 for row in mine)
    assert kpi.backfill(db, now=NOW) == 0, "nothing left to fill"


def test_the_figures_survive_the_raw_rows_being_averaged(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for minute in range(10):
        _ping(db, wired, minute, ResultStatus.fail if minute < 2 else ResultStatus.ok)
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    db.commit()
    before = kpi.report(db, "day", now=NOW, monitor_id=monitor.id)["summary"]["internet"]["availability"]
    # What the six month rollup does: many results become one averaged row.
    db.execute(delete(TestResult).where(TestResult.test_id == wired.id))
    db.add(
        TestResult(
            external_id=f"rollup-{uuid.uuid4().hex}",
            test_id=wired.id,
            started_at=HOUR,
            finished_at=HOUR,
            status=ResultStatus.ok,
            metrics={"rtt_ms": 20},
            message="Hourly average",
        )
    )
    db.commit()
    assert kpi.report(db, "day", now=NOW, monitor_id=monitor.id)["summary"]["internet"]["availability"] == pytest.approx(before) == pytest.approx(80.0)


def test_periods_have_the_right_buckets_and_a_previous_period(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for days_ago, fails in ((0, 0), (1, 1), (8, 2)):
        when = HOUR - timedelta(days=days_ago)
        for minute in range(10):
            _ping(db, wired, minute, ResultStatus.fail if minute < fails else ResultStatus.ok, when=when)
    db.commit()
    kpi.backfill(db, now=NOW)
    db.commit()
    shapes = {name: kpi.report(db, name, now=NOW, monitor_id=monitor.id) for name in ("day", "week", "month", "year")}
    assert [len(shapes[name]["series"]) for name in ("day", "week", "month", "year")] == [24, 7, 30, 12]
    assert [shapes[name]["bucket"] for name in ("day", "week", "month", "year")] == ["hour", "day", "day", "month"]
    week = shapes["week"]
    assert week["summary"]["internet"]["samples"] == 20
    assert week["summary"]["internet"]["availability"] == pytest.approx(95.0)
    assert week["previous"]["internet"]["samples"] == 10 and week["previous"]["internet"]["availability"] == pytest.approx(80.0)
    assert week["series"][-1]["internet"] == pytest.approx(100.0) and week["series"][-2]["internet"] == pytest.approx(90.0)
    assert week["series"][0]["internet"] is None, "a day with no data is a gap, not 100%"
    assert shapes["year"]["series"][-1]["t"].month == NOW.month
    with pytest.raises(ValueError):
        kpi.report(db, "decade", now=NOW)


def test_incidents_group_consecutive_failures(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for minute in (3, 4, 5, 20):
        _ping(db, wired, minute, ResultStatus.fail)
    db.commit()
    found = kpi.incidents(db, HOUR, HOUR + timedelta(hours=1), monitor.id)
    assert [(item["minutes"], item["checks"]) for item in found] == [(1, 1), (3, 3)]
    assert found[0]["start"] > found[1]["start"], "newest first"


def test_the_report_endpoint_is_for_signed_in_users_and_validates_the_period(client: TestClient, db):
    _ensure_guest(db)
    assert client.get("/api/v1/kpi").status_code in (401, 403)
    guest = TestClient(app, base_url="https://testserver")
    login(guest, "guest", "guest")
    body = guest.get("/api/v1/kpi?period=week")
    assert body.status_code == 200, body.text
    assert set(body.json()) >= {"summary", "series", "previous", "targets", "incidents", "links", "app_loss_avg"}
    assert guest.get("/api/v1/kpi?period=century").status_code == 422
    admin = login(client)
    assert admin and client.get("/api/v1/kpi?period=year").status_code == 200


def _days_of_pings(db, monitor, fails_by_days_ago):
    wired = _link(db, monitor, NetworkKind.wired)
    for days_ago, fails in fails_by_days_ago.items():
        when = HOUR - timedelta(days=days_ago)
        for minute in range(10):
            _ping(db, wired, minute, ResultStatus.fail if minute < fails else ResultStatus.ok, when=when)
    db.commit()


def test_a_daily_record_is_stored_and_weeks_months_and_years_are_built_from_it(db):
    monitor = _pi(db)
    _days_of_pings(db, monitor, {0: 0, 1: 1, 2: 2})
    kpi.backfill(db, now=NOW)
    db.commit()

    days = db.scalars(select(KpiDaily).where(KpiDaily.monitor_id == monitor.id, KpiDaily.kind == "internet").order_by(KpiDaily.day)).all()
    assert [(row.day, row.n, row.failed) for row in days] == [
        (date(2026, 10, 14), 10, 2),
        (date(2026, 10, 15), 10, 1),
        (date(2026, 10, 16), 10, 0),
    ]
    assert days[0].outage_min == pytest.approx(12.0)

    before = {name: kpi.report(db, name, now=NOW, monitor_id=monitor.id) for name in ("week", "month", "year")}
    # Remove the hourly rows: weeks, months and years must still come out the same, from the daily records.
    db.execute(delete(KpiHourly).where(KpiHourly.monitor_id == monitor.id))
    db.commit()
    for name, report in before.items():
        again = kpi.report(db, name, now=NOW, monitor_id=monitor.id)
        assert again["summary"]["internet"]["availability"] == pytest.approx(report["summary"]["internet"]["availability"]) == pytest.approx(90.0)
        assert again["summary"]["internet"]["outage_minutes"] == pytest.approx(18.0)
    assert kpi.report(db, "day", now=NOW, monitor_id=monitor.id)["summary"]["internet"]["availability"] is None


def test_refresh_keeps_today_up_to_date(db):
    monitor = _pi(db)
    _days_of_pings(db, monitor, {0: 1})
    kpi.refresh(db, now=NOW)
    db.commit()
    today = db.scalar(select(KpiDaily).where(KpiDaily.monitor_id == monitor.id, KpiDaily.kind == "internet", KpiDaily.day == NOW.date()))
    assert today is not None and (today.n, today.failed) == (10, 1)
    wired = db.scalar(select(Test).where(Test.monitor_id == monitor.id))
    _ping(db, wired, 20, ResultStatus.fail)
    db.commit()
    kpi.refresh(db, now=NOW)
    db.commit()
    db.expire_all()
    today = db.scalar(select(KpiDaily).where(KpiDaily.monitor_id == monitor.id, KpiDaily.kind == "internet", KpiDaily.day == NOW.date()))
    assert (today.n, today.failed) == (11, 2)


def test_a_chosen_day_is_a_calendar_day_by_hour(db):
    monitor = _pi(db)
    _days_of_pings(db, monitor, {0: 0, 3: 4})
    kpi.backfill(db, now=NOW)
    db.commit()
    chosen = date(2026, 10, 13)
    report = kpi.report(db, "day", now=NOW, monitor_id=monitor.id, day=chosen)
    assert report["day"] == chosen and report["bucket"] == "hour" and len(report["series"]) == 24
    assert report["series"][0]["t"] == datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert report["summary"]["internet"]["availability"] == pytest.approx(60.0)
    assert report["summary"]["internet"]["incidents"] == 1
    assert report["series"][12]["internet"] == pytest.approx(60.0), "the hour with the checks"
    assert report["series"][11]["internet"] is None

    week = kpi.report(db, "week", now=NOW, monitor_id=monitor.id, day=chosen)
    assert week["series"][-1]["t"] == datetime(2026, 10, 13, tzinfo=timezone.utc) and len(week["series"]) == 7
    assert week["summary"]["internet"]["availability"] == pytest.approx(60.0)

    future = kpi.report(db, "day", now=NOW, monitor_id=monitor.id, day=date(2030, 1, 1))
    assert future["day"] == NOW.date(), "a day in the future is the latest day"


def test_the_daily_history_lists_days_newest_first_and_can_be_filtered(db):
    monitor = _pi(db)
    _days_of_pings(db, monitor, {0: 0, 1: 1, 2: 2, 5: 0})
    voice = _run(db, monitor, "voip-g711a")
    for minute in range(4):
        _sample(db, voice, minute, "fail" if minute == 0 else "pass", loss=2.0 if minute == 0 else 0.0, when=HOUR - timedelta(days=1))
    db.commit()
    kpi.backfill(db, now=NOW)
    db.commit()
    rows = kpi.daily_history(db, date(2026, 10, 1), date(2026, 10, 16), monitor.id)
    assert [row["day"] for row in rows] == [date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 14), date(2026, 10, 11)]
    yesterday = rows[1]
    assert yesterday["internet"] == pytest.approx(90.0) and yesterday["offline_minutes"] == pytest.approx(6.0)
    assert yesterday["voice"] == pytest.approx(75.0) and yesterday["loss"] == pytest.approx(0.5)
    assert rows[0]["voice"] is None, "a day with no voice checks has no voice figure"
    assert [row["day"] for row in kpi.daily_history(db, date(2026, 10, 14), date(2026, 10, 15), monitor.id)] == [date(2026, 10, 15), date(2026, 10, 14)]
    assert len(kpi.daily_history(db, date(2026, 10, 1), date(2026, 10, 16), monitor.id, limit=2)) == 2


def test_the_new_endpoints(client: TestClient, db):
    _ensure_guest(db)
    assert client.get("/api/v1/kpi/daily").status_code in (401, 403)
    guest = TestClient(app, base_url="https://testserver")
    login(guest, "guest", "guest")
    assert guest.get("/api/v1/kpi/daily").status_code == 200
    assert guest.get("/api/v1/kpi/daily?from=2026-01-01&to=2026-01-31&limit=5").status_code == 200
    assert guest.get("/api/v1/kpi?period=day&day=2026-10-13").json()["day"] == "2026-10-13"
    assert guest.get("/api/v1/kpi?period=day&day=not-a-date").status_code == 422
    assert guest.get("/api/v1/kpi/daily?limit=5000").status_code == 422


def test_the_targets_are_strict_and_reach_the_report(db):
    assert kpi.TARGETS == {"internet": 99.99, "voice": 99.9, "video": 99.9, "loss_pct": 0.1}
    assert kpi.report(db, "week", now=NOW)["targets"] == kpi.TARGETS


def _two_links(db, monitor):
    return _link(db, monitor, NetworkKind.wired), _link(db, monitor, NetworkKind.wifi)


def test_reporting_gaps_are_counted_once_even_when_both_links_stop(db):
    monitor = _pi(db)
    wired, wifi = _two_links(db, monitor)
    for minute in (*range(0, 6), *range(20, 26)):
        _ping(db, wired, minute, ResultStatus.ok)
    for minute in (*range(0, 6), *range(21, 27)):
        _ping(db, wifi, minute, ResultStatus.ok)
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))
    kpi.rebuild_days(db, HOUR.date(), HOUR.date())
    db.commit()

    rows = {row.link: row for row in db.scalars(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id, KpiHourly.kind == "internet")).all()}
    assert (rows["wired"].gaps, round(rows["wired"].gap_min, 1)) == (1, 14.0)
    assert (rows["wireless"].gaps, round(rows["wireless"].gap_min, 1)) == (1, 15.0)
    day = db.scalar(select(KpiDaily).where(KpiDaily.monitor_id == monitor.id, KpiDaily.kind == "internet", KpiDaily.link == "wired"))
    assert (day.gaps, round(day.gap_min, 1)) == (1, 14.0)

    soon = HOUR + timedelta(minutes=28)  # two minutes after the last result, so nothing is still open
    internet = kpi.report(db, "day", now=soon, monitor_id=monitor.id)["summary"]["internet"]
    # The same stop seen on both links is one stretch of about 14.5 minutes, not 29.
    assert internet["gaps"] == 1 and internet["gap_minutes"] == pytest.approx(14.5)
    assert internet["availability"] == pytest.approx(100.0), "a gap is not downtime"
    week = kpi.report(db, "week", now=soon, monitor_id=monitor.id)["summary"]["internet"]
    assert week["gap_minutes"] == pytest.approx(14.5)


def test_a_short_pause_is_not_a_gap_and_one_across_the_hour_counts_where_it_ends(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for minute in (0, 1, 2, 4, 6):  # two minute waits are normal jitter
        _ping(db, wired, minute, ResultStatus.ok)
    _ping(db, wired, 55, ResultStatus.ok, when=HOUR - timedelta(hours=1))  # 11:55
    db.commit()
    kpi.rebuild(db, HOUR, HOUR + timedelta(hours=1))  # only the later hour is rebuilt
    db.commit()
    row = db.scalar(select(KpiHourly).where(KpiHourly.monitor_id == monitor.id, KpiHourly.kind == "internet"))
    assert row.gaps == 1 and round(row.gap_min, 1) == 4.0, "11:55 to 12:00 is a five minute wait, so four minutes missing"


def test_the_gap_log_merges_both_links_and_shows_a_stretch_that_is_still_open(db):
    monitor = _pi(db)
    wired, wifi = _two_links(db, monitor)
    for minute in (*range(0, 6), *range(20, 26)):
        _ping(db, wired, minute, ResultStatus.ok)
    for minute in (*range(0, 6), *range(21, 27)):
        _ping(db, wifi, minute, ResultStatus.ok)
    for minute in range(28, 32):  # Wi-Fi alone stops later, at 12:31 until the end of the hour
        _ping(db, wired, minute, ResultStatus.ok)
    for minute in (28,):
        _ping(db, wifi, minute, ResultStatus.ok)
    _ping(db, wifi, 40, ResultStatus.ok)
    db.commit()
    log = kpi.gap_log(db, HOUR, HOUR + timedelta(hours=1), monitor.id)
    assert [(item["links"], item["minutes"]) for item in log] == [("wireless", 11), ("both", 15)]
    assert log[0]["start"] > log[1]["start"], "newest first"

    # The last result was at 12:40 and it is now 13:30: that stretch has not ended yet.
    later = HOUR + timedelta(minutes=90)
    report = kpi.report(db, "day", now=later, monitor_id=monitor.id)
    assert report["gaps"][0]["ongoing"] is True and report["gaps"][0]["minutes"] == 49
    assert report["summary"]["internet"]["gaps"] >= 1
    past = kpi.report(db, "day", now=later, monitor_id=monitor.id, day=date(2026, 10, 15))
    assert all(not item["ongoing"] for item in past["gaps"]), "a past day has no stretch still open"


def test_daily_history_carries_the_gap_minutes(db):
    monitor = _pi(db)
    wired = _link(db, monitor, NetworkKind.wired)
    for minute in (*range(0, 6), *range(20, 26)):
        _ping(db, wired, minute, ResultStatus.ok)
    db.commit()
    kpi.backfill(db, now=NOW)
    db.commit()
    rows = kpi.daily_history(db, NOW.date(), NOW.date(), monitor.id)
    assert rows and rows[0]["gap_minutes"] == pytest.approx(14.0)
