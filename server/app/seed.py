from __future__ import annotations

import argparse
import hashlib
import math
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.auth import hash_password, revoke_sessions, verify_password
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import (
    Base,
    Group,
    Issue,
    IssueSeverity,
    IssueStatus,
    Network,
    NetworkKind,
    ResultStatus,
    Monitor,
    MonitorHealth,
    MonitorStatus,
    Test,
    TestKind,
    TestResult,
    User,
    UserRole,
    WifiObservation,
)

DEMO_GROUP_SLUG = "demo-home"

# Stable UUIDs so re-seed is idempotent via upsert-by-id
IDS = {
    "group": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    "monitor_lr": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
    "monitor_of": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"),
    "net_wan": uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc1"),
    "net_lan": uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc2"),
    "net_wifi": uuid.UUID("cccccccc-cccc-cccc-cccc-ccccccccccc3"),
    "admin": uuid.UUID("dddddddd-dddd-dddd-dddd-ddddddddddd1"),
    "viewer": uuid.UUID("dddddddd-dddd-dddd-dddd-ddddddddddd2"),
    "guest": uuid.UUID("dddddddd-dddd-dddd-dddd-ddddddddddd3"),
}


def _same_password(password: str, password_hash: str) -> bool:
    try:
        return verify_password(password, password_hash)
    except ValueError:
        return False


def _stable_uuid(key: str) -> uuid.UUID:
    return uuid.UUID(hashlib.md5(key.encode()).hexdigest())


def _monitor_token(slug: str) -> str:
    return f"monitor-token-{slug}"


STAGE_TEMPLATES = {
    TestKind.http: ["dns", "tcp", "tls", "ttfb", "download"],
    TestKind.dns: ["resolve"],
    TestKind.ping: ["icmp"],
    TestKind.wifi: ["associate", "dhcp", "dns", "http"],
    TestKind.mtr: ["trace"],
}


def _gen_stages(
    kind: TestKind,
    status: ResultStatus,
    rng: random.Random,
    names: list[str] | None = None,
) -> list[dict]:
    names = names or STAGE_TEMPLATES[kind]
    stages = []
    fail_at = None
    if status == ResultStatus.fail:
        fail_at = rng.choice(names)
    elif status == ResultStatus.degraded:
        fail_at = names[-1] if len(names) > 1 else names[0]

    for name in names:
        base = {
            "dns": rng.uniform(8, 40),
            "tcp": rng.uniform(5, 30),
            "tls": rng.uniform(20, 80),
            "ttfb": rng.uniform(30, 150),
            "download": rng.uniform(40, 200),
            "resolve": rng.uniform(5, 35),
            "icmp": rng.uniform(2, 25),
            "associate": rng.uniform(50, 200),
            "dhcp": rng.uniform(30, 120),
            "http": rng.uniform(40, 180),
            "trace": rng.uniform(20, 80),
            "transfer": rng.uniform(3000, 8000),
        }.get(name, rng.uniform(10, 50))
        if status == ResultStatus.degraded:
            base *= rng.uniform(1.8, 3.0)
        st = "ok"
        if fail_at and name == fail_at:
            st = "fail" if status == ResultStatus.fail else "degraded"
            if status == ResultStatus.fail:
                base *= rng.uniform(2.0, 5.0)
        stages.append({"name": name, "duration_ms": round(base, 1), "status": st})
        if st == "fail":
            break
    return stages


def _gen_hops(target: str, status: ResultStatus, rng: random.Random) -> list[dict]:
    routers = ["192.168.1.1", "10.0.0.1", "100.64.0.1", "203.0.113.12", target]
    hops = []
    for index, host in enumerate(routers, start=1):
        avg = 2 + index * rng.uniform(3, 12)
        loss = 0.0
        if status == ResultStatus.degraded:
            avg *= rng.uniform(1.8, 3.0)
            if index >= len(routers) - 1:
                loss = rng.uniform(2, 8)
        elif status == ResultStatus.fail:
            avg *= rng.uniform(4.0, 8.0)
            if index >= len(routers) - 1:
                loss = rng.uniform(20, 80)
        hops.append(
            {
                "hop": index,
                "host": host,
                "loss_pct": round(loss, 2),
                "best_ms": round(avg * 0.7, 2),
                "avg_ms": round(avg, 2),
                "worst_ms": round(avg * rng.uniform(1.3, 2.2), 2),
            }
        )
    return hops


def _gen_metrics(
    kind: TestKind,
    status: ResultStatus,
    stages: list[dict],
    rng: random.Random,
    target: str = "",
) -> dict:
    total = sum(s["duration_ms"] for s in stages)
    rtt = total if kind in (TestKind.ping, TestKind.dns, TestKind.http, TestKind.mtr) else rng.uniform(8, 30)
    if status == ResultStatus.degraded and kind != TestKind.mtr:
        rtt *= rng.uniform(2.0, 4.0)
    elif status == ResultStatus.fail and kind != TestKind.mtr:
        rtt *= rng.uniform(5.0, 10.0)
    loss = 0.0
    if status == ResultStatus.degraded and kind != TestKind.mtr:
        loss = rng.uniform(2, 8)
    elif status == ResultStatus.fail and kind != TestKind.mtr:
        loss = rng.uniform(20, 100)
    metrics: dict = {"rtt_ms": round(rtt, 2), "loss_pct": round(loss, 2), "total_ms": round(total, 1)}
    if kind == TestKind.mtr:
        hops = _gen_hops(target or "1.1.1.1", status, rng)
        metrics["hops"] = hops
        metrics["rtt_ms"] = hops[-1]["avg_ms"]
        metrics["loss_pct"] = max(hop["loss_pct"] for hop in hops)
    return metrics


def wipe_demo(db: Session) -> None:
    group = db.scalar(select(Group).where(Group.slug == DEMO_GROUP_SLUG))
    if group:
        # Cascades handle children when FK ondelete=CASCADE; delete issues/tests/results explicitly for safety
        monitor_ids = [s.id for s in db.scalars(select(Monitor).where(Monitor.group_id == group.id)).all()]
        network_ids = [n.id for n in db.scalars(select(Network).where(Network.group_id == group.id)).all()]
        test_ids = []
        if monitor_ids:
            test_ids = [t.id for t in db.scalars(select(Test).where(Test.monitor_id.in_(monitor_ids))).all()]
        if test_ids:
            db.execute(delete(TestResult).where(TestResult.test_id.in_(test_ids)))
            db.execute(delete(Test).where(Test.id.in_(test_ids)))
        db.execute(delete(Issue).where(Issue.group_id == group.id))
        if monitor_ids:
            db.execute(delete(WifiObservation).where(WifiObservation.monitor_id.in_(monitor_ids)))
            db.execute(delete(MonitorHealth).where(MonitorHealth.monitor_id.in_(monitor_ids)))
            db.execute(delete(Monitor).where(Monitor.id.in_(monitor_ids)))
        if network_ids:
            db.execute(delete(Network).where(Network.id.in_(network_ids)))
        db.execute(delete(Group).where(Group.id == group.id))
        db.flush()


_WIFI_APS = [
    {
        "ssid": "HomeMain",
        "bssid": "A4:2B:B0:11:22:01",
        "vendor": "Ubiquiti",
        "band": "5",
        "channel": 36,
        "width": 80,
        "security": "WPA3",
        "phy": "ax",
        "link": 1201,
        "rssi": -46,
        "noise": -94,
    },
    {
        "ssid": "HomeMain",
        "bssid": "A4:2B:B0:11:22:02",
        "vendor": "Ubiquiti",
        "band": "2.4",
        "channel": 6,
        "width": 20,
        "security": "WPA3",
        "phy": "ax",
        "link": 286,
        "rssi": -54,
        "noise": -91,
    },
    {
        "ssid": "HomeMain",
        "bssid": "A4:2B:B0:11:22:03",
        "vendor": "Ubiquiti",
        "band": "6",
        "channel": 37,
        "width": 160,
        "security": "WPA3",
        "phy": "ax",
        "link": 2402,
        "rssi": -61,
        "noise": -96,
    },
    {
        "ssid": "NeighborNet",
        "bssid": "B4:FB:E4:44:55:10",
        "vendor": "TP-Link",
        "band": "2.4",
        "channel": 11,
        "width": 40,
        "security": "WPA2",
        "phy": "n",
        "link": 300,
        "rssi": -71,
        "noise": -89,
    },
    {
        "ssid": "xfinitywifi",
        "bssid": "00:1A:2B:77:88:99",
        "vendor": "CommScope",
        "band": "5",
        "channel": 149,
        "width": 80,
        "security": "Open",
        "phy": "ac",
        "link": 867,
        "rssi": -79,
        "noise": -93,
    },
    {
        "ssid": "Office-IoT",
        "bssid": "F0:9F:C2:AA:10:20",
        "vendor": "Intel",
        "band": "5",
        "channel": 44,
        "width": 40,
        "security": "WPA2",
        "phy": "ac",
        "link": 433,
        "rssi": -68,
        "noise": -92,
    },
]


def _seed_wifi_scans(monitors: list[Monitor]) -> list[WifiObservation]:
    """Hourly-ish nearby-network scans for the last 7 days."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    slots = 24 * 7 * 2  # every 30 minutes
    rows: list[WifiObservation] = []
    for monitor_i, monitor in enumerate(monitors):
        farther = 8 if monitor_i else 0
        for slot in range(slots):
            scanned = now - timedelta(minutes=30 * (slots - 1 - slot))
            rng = random.Random(f"{monitor.slug}:{slot}")
            for ap in _WIFI_APS:
                channel = ap["channel"]
                width = ap["width"]
                if ap["ssid"] == "NeighborNet" and slot > slots // 2:
                    channel = 1
                    width = 20
                rssi = ap["rssi"] - farther + 3.5 * math.sin(slot / 8) + rng.uniform(-1.8, 1.8)
                noise = ap["noise"] + rng.uniform(-1.2, 1.2)
                rows.append(
                    WifiObservation(
                        monitor_id=monitor.id,
                        scanned_at=scanned,
                        ssid=ap["ssid"],
                        bssid=ap["bssid"],
                        vendor=ap["vendor"],
                        band=ap["band"],
                        channel=channel,
                        channel_width=width,
                        rssi_dbm=round(rssi, 1),
                        noise_dbm=round(noise, 1),
                        security=ap["security"],
                        phy=ap["phy"],
                        max_link_rate_mbps=ap["link"],
                    )
                )
    return rows


def ensure_users(db: Session) -> None:
    """Create or update the bootstrap admin, viewer and guest accounts from the environment."""
    settings = get_settings()
    # Upsert by username
    for uid, username, password, role in [
        (IDS["admin"], settings.bootstrap_admin_username, settings.bootstrap_admin_password, UserRole.admin),
        (IDS["viewer"], settings.bootstrap_viewer_username, settings.bootstrap_viewer_password, UserRole.viewer),
        (IDS["guest"], settings.bootstrap_guest_username, settings.bootstrap_guest_password, UserRole.guest),
    ]:
        existing = db.scalar(select(User).where(User.username == username)) or db.get(User, uid)
        if existing:
            existing.username = username
            if not _same_password(password, existing.password_hash):
                # A new password ends the sessions signed in with the old one.
                existing.password_hash = hash_password(password)
                revoke_sessions(existing)
            existing.role = role
            existing.is_active = True
        else:
            db.add(
                User(
                    id=uid,
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                )
            )


def seed(db: Session) -> None:
    settings = get_settings()
    wipe_demo(db)

    ensure_users(db)

    group = Group(id=IDS["group"], slug=DEMO_GROUP_SLUG, name="Demo Home")
    db.add(group)

    monitors = [
        Monitor(
            id=IDS["monitor_lr"],
            group_id=group.id,
            slug="pi-living-room",
            name="Pi Living Room",
            token_hash=hash_password(_monitor_token("pi-living-room")),
            status=MonitorStatus.online,
            last_seen_at=datetime.now(timezone.utc),
            latitude=40.7580,
            longitude=-73.9855,
            location_label="Living room",
        ),
        Monitor(
            id=IDS["monitor_of"],
            group_id=group.id,
            slug="pi-office",
            name="Pi Office",
            token_hash=hash_password(_monitor_token("pi-office")),
            status=MonitorStatus.online,
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            latitude=40.7484,
            longitude=-73.9857,
            location_label="Office",
        ),
    ]
    db.add_all(monitors)

    networks = [
        Network(id=IDS["net_wan"], group_id=group.id, slug="wan", name="Internet", kind=NetworkKind.wired),
        Network(id=IDS["net_lan"], group_id=group.id, slug="lan", name="LAN Wired", kind=NetworkKind.wired),
        Network(id=IDS["net_wifi"], group_id=group.id, slug="wifi-main", name="Wi-Fi", kind=NetworkKind.wifi),
    ]
    db.add_all(networks)
    db.flush()

    # Internet path plus one HTTP, ICMP, and MTR test for each app probe.
    app_probes = [
        ("youtube", "YouTube", "https://www.youtube.com/generate_204", "www.youtube.com"),
        ("google", "Google", "https://www.google.com/generate_204", "www.google.com"),
        ("microsoft", "Microsoft", "http://www.msftconnecttest.com/connecttest.txt", "www.msftconnecttest.com"),
        ("hulu", "Hulu", "https://www.hulu.com", "www.hulu.com"),
        ("zoom", "Zoom", "https://zoom.us", "zoom.us"),
        ("netflix", "Netflix", "https://www.netflix.com", "www.netflix.com"),
        ("teams", "Microsoft Teams", "https://teams.microsoft.com", "teams.microsoft.com"),
        ("facebook", "Facebook", "https://www.facebook.com", "www.facebook.com"),
    ]
    test_defs: list[tuple[Monitor, Network, str, str, TestKind, str]] = []
    for monitor in monitors:
        test_defs.extend(
            [
                (monitor, networks[0], "http-path", "HTTP", TestKind.http, "https://1.1.1.1"),
                (monitor, networks[0], "icmp-path", "ICMP", TestKind.ping, "1.1.1.1"),
                (monitor, networks[0], "mtr-path", "MTR", TestKind.mtr, "1.1.1.1"),
                (monitor, networks[1], "ping-nas", "Ping NAS", TestKind.ping, "192.168.1.50"),
                (monitor, networks[2], "wifi-assoc", "Wi-Fi Association", TestKind.wifi, "HomeMain"),
                (monitor, networks[2], "http-local", "HTTP Router UI", TestKind.http, "http://192.168.1.1"),
            ]
        )
        for key, label, url, host in app_probes:
            test_defs.extend(
                [
                    (monitor, networks[0], f"http-{key}", f"{label} HTTP", TestKind.http, url),
                    (monitor, networks[0], f"icmp-{key}", f"{label} ICMP", TestKind.ping, host),
                    (monitor, networks[0], f"mtr-{key}", f"{label} MTR", TestKind.mtr, host),
                ]
            )

    tests: list[Test] = []
    for monitor, network, slug, name, kind, target in test_defs:
        t = Test(
            id=_stable_uuid(f"test:{monitor.slug}:{network.slug}:{slug}"),
            monitor_id=monitor.id,
            network_id=network.id,
            slug=slug,
            name=name,
            kind=kind,
            target=target,
            interval_seconds=60 if kind in (TestKind.http, TestKind.ping) else 300,
        )
        tests.append(t)
    db.add_all(tests)
    db.flush()

    now = datetime.now(timezone.utc)
    rng = random.Random(42)
    results: list[TestResult] = []
    # Older history stays every 2 hours. The last 6 hours fill the 1h and 6h
    # charts: every 5 minutes, and every 60 seconds for HTTP and ICMP.
    days = 30
    start = now - timedelta(days=days)
    recent_start = (now - timedelta(hours=6)).replace(second=0, microsecond=0)

    fail_windows = [
        (24 * 3, 24 * 3 + 6, ResultStatus.fail, "http-path", "pi-living-room", "wan"),
        (24 * 10, 24 * 10 + 4, ResultStatus.degraded, "http-google", "pi-office", "wan"),
        (24 * 20, 24 * 20 + 8, ResultStatus.fail, "wifi-assoc", "pi-living-room", "wifi-main"),
        # Still in progress, so the latest Office / Internet cell stays degraded.
        (24 * 27, 10**9, ResultStatus.degraded, "icmp-path", "pi-office", "wan"),
        # Closed bursts in the last day so the living-room ISP strip shows amber and red.
        (days * 24 - 5.45, days * 24 - 5.05, ResultStatus.fail, "mtr-path", "pi-living-room", "wan"),
        (days * 24 - 2.65, days * 24 - 2.25, ResultStatus.degraded, "icmp-path", "pi-living-room", "wan"),
        (days * 24 - 0.85, days * 24 - 0.55, ResultStatus.fail, "http-path", "pi-living-room", "wan"),
    ]

    tests_loaded = db.scalars(
        select(Test).options(joinedload(Test.monitor), joinedload(Test.network))
    ).unique().all()
    for test in tests_loaded:
        recent_step = (
            timedelta(seconds=60) if test.kind in (TestKind.http, TestKind.ping) else timedelta(minutes=5)
        )
        idx = 0
        for span_start, span_end, step in (
            (start, recent_start, timedelta(hours=2)),
            (recent_start, now, recent_step),
        ):
            t = span_start
            while t < span_end:
                status = ResultStatus.ok
                for wh_start, wh_end, wh_status, slug, monitor_slug, net_slug in fail_windows:
                    hours = (t - start).total_seconds() / 3600
                    if (
                        wh_start <= hours <= wh_end
                        and test.slug == slug
                        and test.monitor.slug == monitor_slug
                        and test.network.slug == net_slug
                    ):
                        status = wh_status
                        break
                stage_names = ["dns", "tcp", "tls"] if test.slug == "http-zoom" else None
                stages = _gen_stages(test.kind, status, rng, stage_names)
                metrics = _gen_metrics(test.kind, status, stages, rng, test.target)
                finished = t + timedelta(milliseconds=metrics["total_ms"])
                ext = f"seed-{test.id.hex[:8]}-{idx}"
                results.append(
                    TestResult(
                        id=_stable_uuid(ext),
                        external_id=ext,
                        test_id=test.id,
                        started_at=t,
                        finished_at=finished,
                        status=status,
                        stages=stages,
                        metrics=metrics,
                        message=None if status == ResultStatus.ok else f"{status.value} during staged run",
                    )
                )
                t += step
                idx += 1

    db.add_all(results)

    # Sample issues tied to failure windows
    issue_defs = [
        (
            "Critical WAN HTTP failures on living-room Pi",
            "HTTP checks to 1.1.1.1 failed for ~6 hours; TLS stage elevated then timed out. Recovered.",
            IssueSeverity.critical,
            IssueStatus.resolved,
            "tls",
            monitors[0],
            networks[0],
            next(t for t in tests_loaded if t.slug == "http-path" and t.monitor_id == monitors[0].id),
            now - timedelta(days=27),
            now - timedelta(days=26, hours=18),
        ),
        (
            "Wi-Fi association flapping",
            "Association stage failing on Main SSID from living-room monitor. Recovered.",
            IssueSeverity.warning,
            IssueStatus.resolved,
            "associate",
            monitors[0],
            networks[2],
            next(t for t in tests_loaded if t.slug == "wifi-assoc" and t.monitor_id == monitors[0].id),
            now - timedelta(days=10),
            now - timedelta(days=9, hours=16),
        ),
        (
            "Google HTTP elevated latency",
            "Office Pi Google HTTP checks degraded for several hours; recovered.",
            IssueSeverity.warning,
            IssueStatus.resolved,
            "ttfb",
            monitors[1],
            networks[0],
            next(t for t in tests_loaded if t.slug == "http-google" and t.monitor_id == monitors[1].id),
            now - timedelta(days=20),
            now - timedelta(days=19, hours=20),
        ),
        (
            "Internet path ping degradation",
            "ICMP round-trip to 1.1.1.1 is still elevated from the office monitor.",
            IssueSeverity.warning,
            IssueStatus.open,
            "icmp",
            monitors[1],
            networks[0],
            next(t for t in tests_loaded if t.slug == "icmp-path" and t.monitor_id == monitors[1].id),
            now - timedelta(days=3),
            None,
        ),
    ]
    for title, summary, sev, st, root, monitor, network, test, opened, resolved in issue_defs:
        db.add(
            Issue(
                id=_stable_uuid(f"issue:{title}"),
                group_id=group.id,
                monitor_id=monitor.id,
                network_id=network.id,
                test_id=test.id,
                title=title,
                summary=summary,
                severity=sev,
                status=st,
                root_cause_stage=root,
                opened_at=opened,
                resolved_at=resolved,
            )
        )

    scans = _seed_wifi_scans(monitors)
    db.add_all(scans)

    db.commit()
    print(
        f"Seeded demo group '{DEMO_GROUP_SLUG}': "
        f"{len(monitors)} monitors, {len(networks)} networks, {len(tests_loaded)} tests, "
        f"{len(results)} results, {len(issue_defs)} issues, {len(scans)} Wi-Fi scans."
    )
    print(f"Admin login: {settings.bootstrap_admin_username} / {settings.bootstrap_admin_password}")
    print(f"Monitor tokens: monitor-token-<slug> e.g. {_monitor_token('pi-living-room')}")


DEFAULT_PASSWORDS = {"admin-change-me", "viewer-change-me"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the database.")
    parser.add_argument(
        "--users-only",
        action="store_true",
        help="Create only the bootstrap admin, viewer and guest accounts, with no demo data. Use this in production.",
    )
    args = parser.parse_args()

    if args.users_only:
        settings = get_settings()
        if {settings.bootstrap_admin_password, settings.bootstrap_viewer_password} & DEFAULT_PASSWORDS:
            raise SystemExit(
                "Refusing to create accounts with the default passwords. "
                "Set BOOTSTRAP_ADMIN_PASSWORD and BOOTSTRAP_VIEWER_PASSWORD in .env first."
            )

    # Ensure tables exist if migrations not run (dev convenience)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.users_only:
            ensure_users(db)
            db.commit()
            print(f"Created accounts: {get_settings().bootstrap_admin_username} (admin), "
                  f"{get_settings().bootstrap_viewer_username} (viewer), "
                  f"{get_settings().bootstrap_guest_username} (guest).")
        else:
            seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
