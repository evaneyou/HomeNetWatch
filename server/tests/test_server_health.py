"""Server Health: sampling math, the disk forecast, the Sunday cache, and stuck processes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.auth import hash_password
from app.models import ServerAdvisory, ServerDbDaily, ServerSample, ServerStuck, User, UserRole
from app.server_health import (
    advisories_from_osv,
    build_advisory_payload,
    cvss3_score,
    byte_rates,
    check_due,
    choose_iface,
    cpu_counters,
    cpu_percent,
    forecast,
    judge_stuck,
    maybe_refresh_advisories,
    parse_net,
    prune,
    record_sample,
    refresh_advisories,
    risk_of,
    support_row,
    version_is_newer,
)
from tests.conftest import login


def test_cpu_percent_from_proc_stat():
    previous = cpu_counters("cpu  100 0 0 800 100 0 0 0\ncpu0 1 0 0 1 0 0 0 0\n")
    current = cpu_counters("cpu  190 0 0 800 110 0 0 0\n")
    assert previous == (100, 1000)
    assert cpu_percent(previous, current) == 90
    assert cpu_percent(None, current) is None


def test_interface_rate_skips_virtual_nics():
    text = """
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets
    lo: 1000 0 0 0 0 0 0 0 1000 0
docker0: 9000 0 0 0 0 0 0 0 9000 0
  eth0: 5000 0 0 0 0 0 0 0 2000 0
    wg0: 8000 0 0 0 0 0 0 0 8000 0
"""
    name, rx, tx = choose_iface(parse_net(text))
    assert (name, rx, tx) == ("eth0", 5000, 2000)
    start = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rates = byte_rates((start, rx, tx), (start + timedelta(seconds=2), rx + 1000, tx + 40))
    assert rates == (500.0, 20.0)


def test_forecast_dates_a_rising_database():
    start = date(2026, 10, 1)
    daily = [(start + timedelta(days=offset), 100 + 10 * offset) for offset in range(7)]
    cast = forecast(daily, free_bytes=70)
    assert cast["ready"] is True
    assert cast["growing"] is True
    assert cast["full_on"] == "2026-10-14"
    assert cast["bytes_per_day"] == 10
    assert cast["far"] is False
    flat = forecast([(start + timedelta(days=offset), 100) for offset in range(7)], 70)
    assert flat["growing"] is False
    assert flat["full_on"] is None
    assert forecast(daily[:6], 70)["ready"] is False


def test_sunday_cache_does_not_fetch_again_the_same_week(db, monkeypatch):
    calls = {"n": 0}

    def fake(session, now, **_kwargs):
        calls["n"] += 1
        refresh_advisories(
            session,
            now,
            installed={"bash": "1.0"},
            newest={"bash": "1.1"},
            vulns={"bash": [{"id": "USN-1", "risk": "high", "summary": ""}]},
            os_name="Ubuntu",
            kernel="6.8.0",
        )

    monkeypatch.setattr("app.server_health.refresh_advisories_live", fake)
    sunday = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)
    db.execute(delete(ServerAdvisory))
    db.commit()
    maybe_refresh_advisories(db, sunday)
    db.commit()
    maybe_refresh_advisories(db, sunday + timedelta(days=2))
    db.commit()
    assert calls["n"] == 1
    assert check_due(sunday + timedelta(days=2), sunday) is False
    maybe_refresh_advisories(db, sunday + timedelta(days=7))
    db.commit()
    assert calls["n"] == 2
    payload = build_advisory_payload(
        os_name="Ubuntu",
        kernel="6.8.0",
        installed={"bash": "1.0", "coreutils": "9.0"},
        newest={"bash": "1.1", "coreutils": "9.0"},
        vulns={},
    )
    assert [row["name"] for row in payload["packages"]] == ["bash"]
    db.execute(delete(ServerAdvisory))
    db.commit()


def test_versions_compare_in_debian_order():
    assert version_is_newer("3.0.13-0ubuntu3.6", "3.0.13-0ubuntu3.4")
    assert version_is_newer("1:1.0", "2.0")
    assert version_is_newer("2.10", "2.9")
    assert not version_is_newer("1.0~rc1", "1.0")
    assert not version_is_newer("6.8.0-85.85", "6.8.0-85.85")


def test_risk_comes_from_the_full_record():
    assert cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert risk_of({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}) == "critical"
    assert risk_of({"severity": [{"type": "Ubuntu", "score": "medium"}]}) == "medium"
    both = {"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}, {"type": "Ubuntu", "score": "low"}]}
    assert risk_of(both) == "low"
    assert risk_of({"database_specific": {"severity": "MODERATE"}}) == "medium"
    assert risk_of({"id": "bare"}) == "low"
    rows = advisories_from_osv(
        [{"vulns": [{"id": "A"}, {"id": "B"}]}],
        ["curl"],
        {"A": {"severity": [{"type": "Ubuntu", "score": "low"}], "summary": "minor"}, "B": {"severity": [{"type": "Ubuntu", "score": "high"}]}},
    )
    assert [row["risk"] for row in rows["curl"]] == ["high", "low"]


def test_support_calendar_flags_old_and_ended_versions():
    today = date(2026, 10, 20)
    behind, note = support_row("PostgreSQL 16", "16.4", {"latest": "16.10", "eol": "2028-11-09"}, today)
    assert behind["newest"] == "16.10" and behind["risk"] == "low"
    assert "runs until 2028-11-09" in note
    ended, _ = support_row("Python 3.8", "3.8.1", {"latest": "3.8.1", "eol": "2024-10-07"}, today)
    assert ended["advisories"][0]["id"] == "End of life" and ended["risk"] == "high"
    current, _ = support_row("PostgreSQL 16", "16.10", {"latest": "16.10", "eol": False}, today)
    assert current is None


def test_cache_in_an_older_shape_is_rebuilt(db, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr("app.server_health.refresh_advisories_live", lambda *_args, **_kwargs: calls.update(n=calls["n"] + 1))
    sunday = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)
    db.execute(delete(ServerAdvisory))
    db.add(ServerAdvisory(id=1, checked_at=sunday, payload={"packages": []}))
    db.commit()
    maybe_refresh_advisories(db, sunday + timedelta(days=1))
    assert calls["n"] == 1
    db.execute(delete(ServerAdvisory))
    db.commit()


def test_stuck_process_detection():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    zombie = judge_stuck({}, [{"pid": 5, "name": "gone", "state": "Z", "ticks": 0, "userland": True}], now)
    assert zombie[0]["reason"] == "zombie"
    assert judge_stuck({}, [{"pid": 2, "name": "kthread", "state": "D", "ticks": 0, "userland": False}], now) == []

    row = None
    for _minute in range(4):
        previous = {row["pid"]: row} if row else {}
        row = judge_stuck(previous, [{"pid": 9, "name": "nfs", "state": "D", "ticks": 0, "userland": True}], now)[0]
    assert row["reason"] is None
    row = judge_stuck({row["pid"]: row}, [{"pid": 9, "name": "nfs", "state": "D", "ticks": 0, "userland": True}], now)[0]
    assert row["reason"] == "sleep"
    assert row["d_minutes"] == 5

    row = None
    ticks = 0
    for _minute in range(16):
        ticks += 5400
        previous = {row["pid"]: row} if row else {}
        row = judge_stuck(
            previous,
            [{"pid": 3, "name": "busy", "state": "R", "ticks": ticks, "userland": True}],
            now,
            clk_tck=100,
        )[0]
    assert row["reason"] == "cpu"
    assert row["hot_minutes"] == 15
    assert judge_stuck({}, [], now) == []


def _user(db, username: str, role: UserRole) -> None:
    if db.scalar(select(User).where(User.username == username)) is None:
        db.add(User(username=username, password_hash=hash_password(username if role != UserRole.admin else "admin-change-me"), role=role, is_active=True))
        db.commit()


def test_viewer_is_refused(client, db):
    _user(db, "viewer-health", UserRole.viewer)
    login(client, "viewer-health", "viewer-health")
    response = client.get("/api/v1/server-health")
    assert response.status_code == 403


def test_samples_are_stored_and_pruned(client, db):
    _user(db, "admin", UserRole.admin)
    now = datetime.now(timezone.utc)
    old = ServerSample(
        recorded_at=now - timedelta(days=8),
        mem_used_mb=1,
        mem_total_mb=2,
        disk_used_mb=1,
        disk_total_mb=2,
        db_bytes=10,
        connections=1,
    )
    old_day = ServerDbDaily(day=date(now.year - 3, 1, 1), db_bytes=10, free_bytes=10)
    db.add_all([old, old_day])
    db.commit()
    sample = record_sample(db, now)
    db.commit()
    assert sample.db_bytes > 0
    assert db.get(ServerDbDaily, now.date()) is not None
    removed = prune(db, now)
    db.commit()
    db.expire_all()
    assert removed >= 2
    assert db.get(ServerSample, old.id) is None
    assert db.get(ServerDbDaily, old_day.day) is None
    login(client)
    response = client.get("/api/v1/server-health?range=1h")
    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["db_bytes"] == sample.db_bytes
    assert [group["kept"] for group in body["retention"]] == [
        "5 years",
        "2 years",
        "1 year",
        "90 days",
        "30 days",
        "7 days",
    ]
    db.execute(delete(ServerStuck))
    db.execute(delete(ServerSample).where(ServerSample.id == sample.id))
    db.execute(delete(ServerDbDaily).where(ServerDbDaily.day == now.date()))
    db.commit()
