"""Server Health: sampling math, the disk forecast, the Sunday cache, and stuck processes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.auth import hash_password
from app.models import ServerAdvisory, ServerDbDaily, ServerSample, ServerStorage, ServerStuck, User, UserRole
from app.server_health import (
    advisories_from_osv,
    build_advisory_payload,
    cvss3_score,
    byte_rates,
    counter_increase,
    category_usage,
    cpu_cores,
    check_due,
    choose_iface,
    read_iface,
    cpu_counters,
    cpu_percent,
    disk_projection,
    forecast,
    busiest_processes,
    largest_processes,
    judge_stuck,
    list_backups,
    maybe_refresh_advisories,
    measure_storage,
    parse_net,
    prune,
    record_sample,
    refresh_advisories,
    risk_of,
    support_row,
    traffic_totals,
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
    assert read_iface(" enp0s6: 100 0 0 0 0 0 0 0 40 0\n") == ("enp0s6", 100, 40)
    assert read_iface("") == ("", None, None)


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


def _sample(when: datetime, **extra) -> ServerSample:
    return ServerSample(
        recorded_at=when,
        mem_used_mb=1,
        mem_total_mb=2,
        disk_used_mb=1,
        disk_total_mb=2,
        db_bytes=10,
        connections=1,
        **extra,
    )


def test_report_shows_the_machine_and_the_app_interface(client, db):
    _user(db, "admin", UserRole.admin)
    db.execute(delete(ServerSample))
    db.commit()
    start = datetime.now(timezone.utc).replace(microsecond=0)
    db.add_all(
        [
            _sample(start, iface="eth0", rx_bytes=1000, tx_bytes=100, host_iface="enp0s6", host_rx_bytes=5000, host_tx_bytes=200),
            _sample(
                start + timedelta(seconds=60),
                iface="eth0",
                rx_bytes=7000,
                tx_bytes=100,
                host_iface="enp0s6",
                host_rx_bytes=65000,
                host_tx_bytes=200,
            ),
            _sample(start + timedelta(seconds=120), iface="eth0", rx_bytes=1, tx_bytes=1, host_iface="eth0", host_rx_bytes=9, host_tx_bytes=9),
        ]
    )
    db.commit()
    login(client)
    points = client.get("/api/v1/server-health?range=1h").json()["points"]
    assert len(points) == 3
    both = points[1]
    assert both["iface"] == "eth0"
    assert both["rx_bps"] == 100
    assert both["host_iface"] == "enp0s6"
    assert both["host_rx_bps"] == 1000
    assert both["host_tx_bps"] == 0
    same = points[-1]
    assert same["host_iface"] == ""
    assert same["host_rx_bps"] is None
    db.execute(delete(ServerSample))
    db.commit()


def test_backup_listing_reports_time_and_size_only(tmp_path):
    now = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    (tmp_path / "db-20261020T155300Z.sql.gz.age").write_bytes(b"db")
    (tmp_path / "db-20261019T031500Z.sql.gz").write_bytes(b"old")
    (tmp_path / "wireguard-20261020T155300Z.tar.gz").write_bytes(b"wg-keys")
    (tmp_path / "tls-20261019T031500Z.tar.gz").write_bytes(b"tls")
    (tmp_path / "db-not-a-stamp.sql.gz").write_bytes(b"skip")
    (tmp_path / "notes.txt").write_bytes(b"skip")
    found = list_backups(tmp_path, now)
    assert found["available"] is True
    assert found["fresh"] is False
    database, wireguard, tls = found["kinds"]
    assert database["count"] == 2
    assert database["copies"][0] == {"taken_at": "2026-10-20T15:53:00+00:00", "bytes": 2, "encrypted": True}
    assert database["copies"][1]["encrypted"] is False
    assert wireguard["copies"][0]["bytes"] == 7
    assert wireguard["copies"][0]["encrypted"] is False
    assert tls["copies"][0]["taken_at"].startswith("2026-10-19T03:15:00")
    assert "name" not in database["copies"][0]
    (tmp_path / "tls-20261020T031500Z.tar.gz").write_bytes(b"tls")
    assert list_backups(tmp_path, datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc))["fresh"] is True
    assert list_backups(tmp_path / "missing", now)["available"] is False


def test_busiest_processes_are_a_share_of_the_machine():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    previous = {7: {"name": "postgres", "cpu_ticks": 100, "seen_at": now - timedelta(seconds=60)}}
    processes = [
        {"pid": 7, "name": "postgres", "command": "postgres: checkpointer", "ticks": 220, "userland": True},
        {"pid": 8, "name": "idle", "command": "idle", "ticks": 5, "userland": True},
        {"pid": 3, "name": "ksoftirqd", "command": "", "ticks": 9000, "userland": False},
    ]
    rows = busiest_processes(previous, processes, now, clk_tck=100, cores=2)
    assert rows == [{"pid": 7, "name": "postgres: checkpointer", "cpu": 1.0}]
    assert cpu_cores("cpu  1 0 0 1\ncpu0 1 0 0 1\ncpu1 0 0 0 0\n") == 2


def test_largest_processes_are_a_share_of_memory():
    processes = [
        {"pid": 1, "name": "postgres", "command": "postgres: checkpointer", "userland": True, "rss_bytes": 400 * 1024 * 1024},
        {"pid": 2, "name": "tiny", "command": "tiny", "userland": True, "rss_bytes": 1000},
        {"pid": 3, "name": "kthread", "command": "", "userland": False, "rss_bytes": 9 * 1024 * 1024 * 1024},
    ]
    rows = largest_processes(processes, 8 * 1024 * 1024 * 1024)
    assert rows == [{"pid": 1, "name": "postgres: checkpointer", "bytes": 400 * 1024 * 1024, "share": 4.9}]


def test_viewer_is_refused_and_a_guest_can_read(client, db):
    _user(db, "viewer-health", UserRole.viewer)
    login(client, "viewer-health", "viewer-health")
    assert client.get("/api/v1/server-health").status_code == 403
    _user(db, "guest-health", UserRole.guest)
    login(client, "guest-health", "guest-health")
    assert client.get("/api/v1/server-health").status_code == 200


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
    db.execute(delete(ServerDbDaily).where(ServerDbDaily.day == old_day.day))
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


def test_package_details_have_every_advisory_but_the_report_only_a_preview(client, db):
    _user(db, "admin", UserRole.admin)
    many = [{"id": f"UBUNTU-CVE-2026-{i}", "risk": "high", "summary": "x"} for i in range(40)]
    payload = {
        "version": 2,
        "os": "Ubuntu 24.04",
        "kernel": "7.0.0",
        "error": "",
        "packages": [
            {"name": "linux-oracle", "installed": "1", "newest": "1", "behind": False, "advisories": many, "risk": "high"}
        ],
        "groups": [
            {
                "title": "Server container",
                "packages": [
                    {"name": "tar", "installed": "1", "newest": "2", "behind": True, "advisories": [], "risk": "low"}
                ],
            }
        ],
    }
    db.execute(delete(ServerAdvisory))
    db.add(ServerAdvisory(id=1, checked_at=datetime.now(timezone.utc), payload=payload))
    db.commit()
    login(client)

    body = client.get("/api/v1/server-health?range=1h").json()
    row = body["advisories"]["packages"][0]
    assert len(row["advisories"]) == 3
    assert row["advisory_count"] == 40
    assert body["advisories"]["groups"][0]["key"] == "server-container"

    full = client.get("/api/v1/server-health/software", params={"source": "host", "name": "linux-oracle"})
    assert full.status_code == 200
    assert len(full.json()["package"]["advisories"]) == 40
    other = client.get("/api/v1/server-health/software", params={"source": "server-container", "name": "tar"})
    assert other.json()["source_title"] == "Server container"
    assert client.get("/api/v1/server-health/software", params={"source": "host", "name": "nope"}).status_code == 404
    assert client.get("/api/v1/server-health/software", params={"source": "x", "name": "tar"}).status_code == 404

    db.execute(delete(ServerAdvisory))
    db.commit()

def test_disk_projection_needs_a_week_then_dates_the_full_disk():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    gb = 1024**3
    short = [ServerDbDaily(day=date(2026, 10, 14) + timedelta(days=i), db_bytes=1, free_bytes=1, disk_used_bytes=10 * gb, disk_total_bytes=40 * gb) for i in range(3)]
    assert disk_projection(short, now)["ready"] is False

    rows = [
        ServerDbDaily(day=date(2026, 10, 14) + timedelta(days=i), db_bytes=1, free_bytes=1, disk_used_bytes=(10 + i) * gb, disk_total_bytes=40 * gb)
        for i in range(7)
    ]
    cast = disk_projection(rows, now)
    assert cast["ready"] and cast["growing"]
    assert abs(cast["bytes_per_day"] - gb) < gb * 0.01
    assert cast["full_on"] == (now + timedelta(days=24)).date().isoformat()  # 16 GB used, 24 GB free, 1 GB a day
    assert cast["far"] is False
    assert cast["line"][0]["bytes"] == 16 * gb

    flat = [ServerDbDaily(day=date(2026, 10, 14) + timedelta(days=i), db_bytes=1, free_bytes=1, disk_used_bytes=10 * gb, disk_total_bytes=40 * gb) for i in range(7)]
    assert disk_projection(flat, now)["growing"] is False


def test_storage_breakdown_groups_what_uses_the_disk(tmp_path):
    (tmp_path / "usr" / "lib").mkdir(parents=True)
    (tmp_path / "usr" / "lib" / "big").write_bytes(b"x" * 50_000)
    (tmp_path / "home" / "me" / ".cache").mkdir(parents=True)
    (tmp_path / "home" / "me" / ".cache" / "blob").write_bytes(b"x" * 80_000)
    (tmp_path / "home" / "me" / "project").mkdir()
    (tmp_path / "home" / "me" / "project" / "file").write_bytes(b"x" * 20_000)
    (tmp_path / "var" / "lib" / "containerd").mkdir(parents=True)
    (tmp_path / "var" / "lib" / "containerd" / "layer").write_bytes(b"x" * 200_000)
    (tmp_path / "var" / "lib" / "docker" / "volumes").mkdir(parents=True)
    (tmp_path / "var" / "lib" / "docker" / "volumes" / "pg").write_bytes(b"x" * 30_000)

    result = measure_storage(tmp_path, used_bytes=2_000_000, total_bytes=4_000_000, db_bytes=10_000)
    groups = {group["key"]: group for group in result["groups"]}
    assert set(groups) == {"docker", "home", "system", "volumes", "other"}
    assert groups["docker"]["bytes"] >= 200_000
    assert groups["home"]["children"][0]["name"] == ".cache"
    assert groups["volumes"]["children"][0] == {"name": "Database", "bytes": 10_000}
    assert groups["other"]["bytes"] == 2_000_000 - sum(g["bytes"] for k, g in groups.items() if k != "other")
    assert [g["bytes"] for g in result["groups"]] == sorted((g["bytes"] for g in result["groups"]), reverse=True)


def test_report_has_the_storage_projection_and_breakdown(client, db):
    _user(db, "admin", UserRole.admin)
    db.execute(delete(ServerStorage))
    db.add(ServerStorage(id=1, checked_at=datetime.now(timezone.utc), payload={"version": 1, "used_bytes": 1, "total_bytes": 2, "groups": []}))
    db.commit()
    login(client)
    body = client.get("/api/v1/server-health?range=7d").json()
    assert body["storage"]["breakdown"]["total_bytes"] == 2
    assert body["storage"]["projection"]["ready"] in (True, False)
    db.execute(delete(ServerStorage))
    db.commit()

def test_database_categories_sum_to_the_tables():
    tables = [
        {"name": "test_results", "bytes": 200},
        {"name": "wifi_observations", "bytes": 100},
        {"name": "synthetic_samples", "bytes": 90},
        {"name": "synthetic_runs", "bytes": 10},
        {"name": "rf_channel_stats", "bytes": 50},
        {"name": "users", "bytes": 5},
        {"name": "brand_new_table", "bytes": 3},
    ]
    groups = {group["key"]: group for group in category_usage(tables)}
    assert groups["tests"]["bytes"] == 200
    assert groups["synthetic"]["bytes"] == 100
    assert groups["synthetic"]["children"][0]["name"] == "synthetic_samples"
    assert groups["settings"]["bytes"] == 8  # users plus the unlisted table
    assert sum(group["bytes"] for group in groups.values()) == sum(table["bytes"] for table in tables)

def test_traffic_totals_add_up_counter_increases_and_survive_a_reset():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    rows = [
        (now - timedelta(days=7, minutes=1), 100, 1000),
        (now - timedelta(hours=25), 200, 2000),
        (now - timedelta(hours=24, minutes=1), 300, 3000),
        (now - timedelta(hours=23), 400, 4000),
        # The machine rebooted: the counters start again from a small number.
        (now - timedelta(hours=2), 50, 500),
        (now - timedelta(minutes=30), 150, 800),
        (now, 250, 1300),
    ]
    totals = traffic_totals(rows, now)
    assert totals is not None
    by_key = {item["key"]: item for item in totals["periods"]}
    assert (by_key["1h"]["rx"], by_key["1h"]["tx"]) == (200, 800)
    assert (by_key["6h"]["rx"], by_key["6h"]["tx"]) == (250, 1300)
    # 24h: the reading 23h ago is the first one inside the period, so it counts from the one before it.
    assert (by_key["24h"]["rx"], by_key["24h"]["tx"]) == (100 + 50 + 100 + 100, 1000 + 500 + 300 + 500)
    assert (by_key["7d"]["rx"], by_key["7d"]["tx"]) == (100 + 100 + 100 + 50 + 100 + 100, 1000 + 1000 + 1000 + 500 + 300 + 500)
    assert not any(item["partial"] for item in totals["periods"])
    assert totals["since_start"] == {"rx": 250, "tx": 1300}
    assert totals["month_tx"] == by_key["24h"]["tx"] * 30
    assert counter_increase(10, 4) == 4


def test_traffic_totals_mark_short_history_and_hold_back_the_monthly_pace():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    rows = [(now - timedelta(hours=3), 0, 0), (now, 3000, 600)]
    totals = traffic_totals(rows, now)
    assert totals is not None
    by_key = {item["key"]: item for item in totals["periods"]}
    assert by_key["1h"]["partial"] is False
    assert by_key["6h"]["partial"] is True
    assert by_key["7d"]["partial"] is True
    assert by_key["24h"]["tx"] == 600
    assert totals["month_tx"] is None
    assert traffic_totals([], now) is None
    single = traffic_totals([(now, 5, 6)], now)
    assert single is not None
    assert single["periods"][0]["rx"] is None
    assert single["since_start"] == {"rx": 5, "tx": 6}


def test_report_has_machine_totals_and_the_app_container(client, db):
    _user(db, "admin", UserRole.admin)
    db.execute(delete(ServerSample))
    db.commit()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db.add_all(
        [
            _sample(now - timedelta(minutes=30), iface="eth0", rx_bytes=500, tx_bytes=50, host_iface="enp0s6", host_rx_bytes=10_000, host_tx_bytes=40_000),
            _sample(now - timedelta(minutes=1), iface="eth0", rx_bytes=900, tx_bytes=90, host_iface="enp0s6", host_rx_bytes=12_000, host_tx_bytes=49_000),
        ]
    )
    db.commit()
    login(client)
    traffic = client.get("/api/v1/server-health?range=1h").json()["traffic"]
    assert traffic["iface"] == "enp0s6"
    hour = next(item for item in traffic["periods"] if item["key"] == "1h")
    assert (hour["rx"], hour["tx"]) == (2000, 9000)
    assert traffic["since_start"] == {"rx": 12_000, "tx": 49_000}
    assert traffic["app"] == {"iface": "eth0", "rx": 900, "tx": 90}
    assert traffic["month_tx"] is None
    db.execute(delete(ServerSample))
    db.commit()
    assert client.get("/api/v1/server-health?range=1h").json()["traffic"] is None


def test_fault_simulation_viewer_refused_missing_key_and_mocked_success(client, db, monkeypatch):
    from app import gemini_rca
    from app.models import GeminiSetting

    _user(db, "viewer-fault", UserRole.viewer)
    login(client, "viewer-fault", "viewer-fault")
    assert (
        client.post(
            "/api/v1/server-health/fault-simulation",
            json={"prompt": "Wi-Fi apps fail but web is fine"},
        ).status_code
        == 403
    )

    _user(db, "admin", UserRole.admin)
    login(client)
    db.execute(delete(GeminiSetting))
    db.commit()
    missing = client.post(
        "/api/v1/server-health/fault-simulation",
        json={"prompt": "Wi-Fi apps fail but web is fine"},
    )
    assert missing.status_code == 400
    assert "Gemini API key" in missing.json()["detail"]

    fake_key = "AQ." + "C" * 40
    monkeypatch.setattr(gemini_rca, "verify_key", lambda key, model="gemini-3.8-flash": None)
    headers = login(client)
    saved = client.put(
        "/api/v1/settings/gemini",
        headers=headers,
        json={"api_key": fake_key, "model": "gemini-3.8-flash"},
    )
    assert saved.status_code == 200, saved.text

    def fake_call_gemini(api_key, model, system_prompt, user_payload, json_mode=True):
        assert api_key == fake_key
        assert "fault_simulation" in user_payload or "Wi-Fi" in user_payload
        assert "tunnel-down" in system_prompt or "BEHAVIOR" in system_prompt or "Access plane" in system_prompt
        return {
            "assumptions": ["Single home sensor with wired and wifi"],
            "interpretation": "Public web OK on Wi-Fi; tunnel-backed apps fail against 10.7.1.1.",
            "detection": ["stale last_wireless_handshake_at", "synthetic wireless fail"],
            "overview": {
                "matrix": "Wi-Fi web green; internet/apps red or amber",
                "cause": "tunnel",
                "detail": "Access plane healthy; measurement tunnel dead",
            },
            "alerts": {
                "fires": ["tunnel-down:{id}:wireless"],
                "suppresses": ["net-down on wifi because access_ok_recent"],
            },
            "logs": ["tunnel_watch: wg1 restore"],
            "self_heal": "tunnel_watch restores wg1 on ~60s recovery cooldown once access is ok",
            "will_not_happen": ["net-down:wan blaming the ISP"],
        }

    monkeypatch.setattr(gemini_rca, "call_gemini", fake_call_gemini)
    ok = client.post(
        "/api/v1/server-health/fault-simulation",
        headers=headers,
        json={"prompt": "Wi-Fi public web is fine but apps time out to 10.7.1.1"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["overview"]["cause"] == "tunnel"
    assert "tunnel-down:{id}:wireless" in body["alerts"]["fires"]
    assert "ISP" in body["will_not_happen"][0] or "net-down:wan" in body["will_not_happen"][0]
