from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.enrollment import new_token
from app.internet_tests import public_trace_host
from app.main import app
from app.models import Monitor, MonitorHealth, MonitorStatus, ResultStatus, Test, TestResult
from conftest import login


def _payload(result_id: str = "res-1") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "results": [
            {
                "id": result_id,
                "test_slug": "http-cloudflare",
                "network_slug": "wan",
                "started_at": (now - timedelta(seconds=1)).isoformat(),
                "finished_at": now.isoformat(),
                "status": "ok",
                "stages": [
                    {"name": "dns", "duration_ms": 12.0, "status": "ok"},
                    {"name": "tcp", "duration_ms": 8.0, "status": "ok"},
                    {"name": "tls", "duration_ms": 40.0, "status": "ok"},
                ],
                "metrics": {"rtt_ms": 60.0, "loss_pct": 0.0},
            }
        ]
    }


def _device_headers(name: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "X-Monitor-Name": quote(name, safe="")}


def _token_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _make_monitor(db, name: str) -> tuple[Monitor, str]:
    token, hashed = new_token()
    monitor = Monitor(
        group_id=db.info["group_id"],
        slug=f"m-{uuid.uuid4().hex[:8]}",
        name=name,
        token_hash=hashed,
        status=MonitorStatus.unknown,
    )
    db.add(monitor)
    db.commit()
    return monitor, token


def test_ingest_happy_path(client: TestClient, monitor_token: str):
    r = client.post(
        "/api/v1/ingest",
        json=_payload(f"happy-{uuid.uuid4().hex[:8]}"),
        headers=_device_headers("Pi Test", monitor_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["duplicates"] == 0


def test_ingest_duplicate(client: TestClient, monitor_token: str):
    rid = f"dup-{uuid.uuid4().hex[:8]}"
    headers = _device_headers("Pi Test", monitor_token)
    r1 = client.post("/api/v1/ingest", json=_payload(rid), headers=headers)
    assert r1.status_code == 200
    assert r1.json()["accepted"] == 1

    r2 = client.post("/api/v1/ingest", json=_payload(rid), headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 1


def test_healthz(client: TestClient):
    r = client.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def _health_body(config_version: int = 0) -> dict:
    return {
        "hostname": "pi-test",
        "agent_version": "0.1.0",
        "uptime_seconds": 120,
        "cpu_percent": 12.5,
        "load_1": 0.4,
        "mem_used_mb": 400,
        "mem_total_mb": 1024,
        "disk_used_mb": 2000,
        "disk_total_mb": 8000,
        "temp_c": 48.2,
        "lan_ip": "10.0.0.50",
        "config_version": config_version,
    }


def test_issued_token_and_optional_name_must_match(client: TestClient, db):
    name = f"Living Room {uuid.uuid4().hex[:6]}"
    monitor, token = _make_monitor(db, name)

    by_token = client.post("/api/v1/monitors/health", headers=_token_headers(token), json=_health_body())
    assert by_token.status_code == 200, by_token.text
    assert by_token.json()["name"] == name

    with_name = client.post("/api/v1/monitors/health", headers=_device_headers(name, token), json=_health_body())
    assert with_name.status_code == 200, with_name.text

    wrong_case = client.post(
        "/api/v1/monitors/health", headers=_device_headers(name.lower(), token), json=_health_body()
    )
    assert wrong_case.status_code == 401

    wrong_key = client.post("/api/v1/monitors/health", headers=_token_headers("a" * 43), json=_health_body())
    assert wrong_key.status_code == 401

    viewer = TestClient(app, base_url="https://testserver")
    viewer_csrf = login(viewer, "viewer", "viewer-change-me")
    assert viewer.delete(f"/api/v1/monitors/{monitor.id}", headers=viewer_csrf).status_code == 403

    csrf = login(client)
    assert client.delete(f"/api/v1/monitors/{monitor.id}").status_code == 403
    assert client.delete(f"/api/v1/monitors/{monitor.id}", headers=csrf).status_code == 204
    gone = client.post("/api/v1/monitors/health", headers=_token_headers(token), json=_health_body())
    assert gone.status_code == 401


def test_legacy_key_still_works(client: TestClient, monitor_token: str):
    posted = client.post(
        "/api/v1/monitors/health", headers=_device_headers("Pi Test", monitor_token), json=_health_body()
    )
    assert posted.status_code == 200, posted.text


def test_health_assigns_transfer_and_web_services(client: TestClient, db):
    login(client)
    name = f"Internet {uuid.uuid4().hex[:6]}"
    monitor, token = _make_monitor(db, name)
    headers = _token_headers(token)
    posted = client.post("/api/v1/monitors/health", headers=headers, json=_health_body())
    assert posted.status_code == 200, posted.text
    tests = posted.json()["tests"]
    slugs = {item["slug"] for item in tests}
    assert "iperf3" not in slugs
    assert "http-youtube" in slugs
    assert "http-google" in slugs
    assert "http-netflix" in slugs
    assert "http-path" in slugs
    assert "icmp-path" in slugs
    assert "mtr-path" in slugs
    assert "librespeed" in slugs
    assert "icmp-youtube" in slugs
    # Only the ISP path is traced; web services get no MTR test.
    assert not any(slug.startswith("mtr-") and slug != "mtr-path" for slug in slugs)
    stored_transfer = db.scalar(text("SELECT librespeed_interval_seconds FROM internet_service_settings WHERE id = 1"))
    transfer = next(item for item in tests if item["slug"] == "librespeed")
    assert transfer["kind"] == "librespeed"
    assert transfer["target"] == "10.7.0.1"
    assert transfer["network_slug"] == "wired"
    assert transfer["interval_seconds"] == max(stored_transfer, 300)
    http_path = next(item for item in tests if item["slug"] == "http-path")
    assert http_path["kind"] == "http"
    assert http_path["target"] == "http://10.7.0.1:8847/api/v1/healthz"
    icmp = next(item for item in tests if item["slug"] == "icmp-path")
    assert icmp["kind"] == "ping"
    assert icmp["target"] == "10.7.0.1"
    mtr = next(item for item in tests if item["slug"] == "mtr-path")
    assert mtr["kind"] == "mtr"
    assert mtr["target"] == public_trace_host()
    web = next(item for item in tests if item["slug"] == "http-youtube")
    assert web["target"] == "https://www.youtube.com/generate_204"
    stored = db.scalar(text("SELECT interval_seconds FROM web_service_settings WHERE key = 'youtube'"))
    assert web["interval_seconds"] == stored
    identified = client.post(
        "/api/v1/monitors/health",
        headers=headers,
        json={**_health_body(), "public_ip": "1.1.1.1", "isp": "Example Fiber"},
    )
    assert identified.status_code == 200, identified.text
    device = client.get(f"/api/v1/monitors/{monitor.id}/device")
    assert device.status_code == 200, device.text
    assert device.json()["public_ip"] == "1.1.1.1"
    assert device.json()["isp"] == "Example Fiber"
    assert device.json()["latest"]["last_error"] is None
    reported = client.post(
        "/api/v1/monitors/health",
        headers=headers,
        json={**_health_body(), "last_error": "Kismet HTTP 400: invalid field"},
    )
    assert reported.status_code == 200, reported.text
    shown = client.get(f"/api/v1/monitors/{monitor.id}/device")
    assert shown.json()["latest"]["last_error"] == "Kismet HTTP 400: invalid field"
    client.post("/api/v1/monitors/health", headers=headers, json={**_health_body(), "public_ip": "10.7.0.2"})
    kept = client.get(f"/api/v1/monitors/{monitor.id}/device")
    assert kept.json()["public_ip"] == "1.1.1.1"
    assert kept.json()["isp"] == "Example Fiber"
    again = client.post("/api/v1/monitors/health", headers=headers, json=_health_body())
    assert again.status_code == 200, again.text
    assert len(again.json()["tests"]) == len(tests)


def test_admin_can_queue_a_probe_restart(client: TestClient, db):
    name = f"Restart {uuid.uuid4().hex[:6]}"
    monitor, token = _make_monitor(db, name)
    headers = _token_headers(token)

    waiting = client.get("/api/v1/monitors/command", headers=headers)
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["restart"] is False

    viewer = TestClient(app, base_url="https://testserver")
    login(viewer, "viewer", "viewer-change-me")
    assert viewer.post(f"/api/v1/monitors/{monitor.id}/restart").status_code == 403

    admin = login(client)
    refused = client.post(f"/api/v1/monitors/{monitor.id}/restart")
    assert refused.status_code == 403
    queued = client.post(f"/api/v1/monitors/{monitor.id}/restart", headers=admin)
    assert queued.status_code == 200, queued.text
    assert queued.json()["restart_requested_at"]

    health = client.post("/api/v1/monitors/health", headers=headers, json=_health_body())
    assert health.status_code == 200, health.text
    assert health.json()["restart"] is True
    listed = client.get("/api/v1/monitors")
    row = next(item for item in listed.json() if item["id"] == str(monitor.id))
    assert row["restart_requested_at"]

    acked = client.post("/api/v1/monitors/command", headers=headers)
    assert acked.status_code == 200, acked.text
    assert acked.json()["restart"] is False
    cleared = client.get("/api/v1/monitors/command", headers=headers)
    assert cleared.json()["restart"] is False


def test_health_stores_link_interfaces(client: TestClient, db):
    login(client)
    name = f"Ifaces {uuid.uuid4().hex[:6]}"
    monitor, token = _make_monitor(db, name)
    posted = client.post(
        "/api/v1/monitors/health",
        headers=_token_headers(token),
        json={
            **_health_body(),
            "wired_iface": "eth0",
            "wifi_link_iface": "wlan0",
            "wifi_radio_iface": "wlan1",
            "airwave_mode": "kismet",
            "public_ip": "1.1.1.1",
            "isp": "Example Fiber",
        },
    )
    assert posted.status_code == 200, posted.text
    detail = client.get(f"/api/v1/monitors/{monitor.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["wired_iface"] == "eth0"
    assert body["wifi_link_iface"] == "wlan0"
    assert body["wifi_radio_iface"] == "wlan1"
    assert body["airwave_mode"] == "kismet"
    assert body["lan_ip"] == "10.0.0.50"
    assert body["public_ip"] == "1.1.1.1"
    assert body["isp"] == "Example Fiber"

    guest = TestClient(app, base_url="https://testserver")
    login(guest, "guest", "guest")
    hidden = guest.get(f"/api/v1/monitors/{monitor.id}")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["public_ip"] == "#.#.#.#"
    assert hidden.json()["isp"] == "####### #####"
    assert hidden.json()["lan_ip"] == "##.#.#.##"
    assert hidden.json()["wired_iface"] == "####"
    assert hidden.json()["wifi_link_iface"] == "#####"
    assert hidden.json()["wifi_radio_iface"] == "#####"
    assert hidden.json()["airwave_mode"] == "kismet"

    dropped = client.post(
        "/api/v1/monitors/health",
        headers=_token_headers(token),
        json={
            **_health_body(),
            "wired_iface": "../eth0",
            "wifi_link_iface": "wlan0;rm",
            "wifi_radio_iface": "",
            "airwave_mode": "scan",
        },
    )
    assert dropped.status_code == 200, dropped.text
    cleared = client.get(f"/api/v1/monitors/{monitor.id}")
    assert cleared.json()["wired_iface"] is None
    assert cleared.json()["wifi_link_iface"] is None
    assert cleared.json()["wifi_radio_iface"] is None
    assert cleared.json()["airwave_mode"] == "scan"



def test_health_cleanup_only_drops_the_reporting_monitor(client: TestClient, db):
    login(client)
    one, token = _make_monitor(db, f"Keep {uuid.uuid4().hex[:6]}")
    other, _other_token = _make_monitor(db, f"Other {uuid.uuid4().hex[:6]}")
    old = datetime.now(timezone.utc) - timedelta(days=8)
    for monitor in (one, other):
        db.add(
            MonitorHealth(
                monitor_id=monitor.id,
                reported_at=old,
                hostname="old-row",
                uptime_seconds=1,
                cpu_percent=1,
                load_1=0.1,
                mem_used_mb=1,
                mem_total_mb=2,
                disk_used_mb=1,
                disk_total_mb=2,
            )
        )
    db.commit()
    posted = client.post("/api/v1/monitors/health", headers=_token_headers(token), json=_health_body())
    assert posted.status_code == 200, posted.text
    still_there = db.scalar(
        select(func.count()).select_from(MonitorHealth).where(
            MonitorHealth.monitor_id == other.id, MonitorHealth.hostname == "old-row"
        )
    )
    removed = db.scalar(
        select(func.count()).select_from(MonitorHealth).where(
            MonitorHealth.monitor_id == one.id, MonitorHealth.hostname == "old-row"
        )
    )
    assert still_there == 1
    assert removed == 0


def test_a_long_metric_range_returns_a_bounded_series(client: TestClient, db, monkeypatch):
    from app.api import readings_routes

    monkeypatch.setattr(readings_routes, "_MAX_METRIC_POINTS", 3)
    login(client)
    monitor = db.scalar(select(Monitor).where(Monitor.slug == db.info["monitor_slug"]))
    test = db.scalar(select(Test).where(Test.monitor_id == monitor.id))
    now = datetime.now(timezone.utc)
    for index in range(5):
        started = now - timedelta(minutes=40 - index)
        db.add(
            TestResult(
                external_id=f"bound-{uuid.uuid4().hex}",
                test_id=test.id,
                started_at=started,
                finished_at=started + timedelta(seconds=1),
                status=ResultStatus.ok,
                stages=[],
                metrics={"rtt_ms": float(index)},
            )
        )
    db.commit()
    body = client.get(f"/api/v1/metrics?test_id={test.id}&range=1h")
    assert body.status_code == 200, body.text
    points = body.json()["points"]
    assert len(points) == 3
    assert points[0]["rtt_ms"] == 0
    assert points[-1]["rtt_ms"] == 4


def test_agent_logs_and_unified_timeline(client: TestClient, db):
    login(client)
    monitor, token = _make_monitor(db, f"Unified {uuid.uuid4().hex[:6]}")
    payload = {
        **_health_body(),
        "last_error": "wireless: rtnp-send exited with code 1",
        "logs": [
            {
                "subsystem": "synthetic",
                "severity": "info",
                "message": "wireless flow restarted after 3 missing intervals",
            }
        ],
    }
    posted = client.post("/api/v1/monitors/health", headers=_token_headers(token), json=payload)
    assert posted.status_code == 200, posted.text

    syslog_page = client.get(f"/api/v1/monitors/{monitor.id}/log?probe=system")
    assert syslog_page.status_code == 200, syslog_page.text
    messages = [item["message"] for item in syslog_page.json()["items"]]
    assert "wireless flow restarted after 3 missing intervals" in messages
    assert "wireless: rtnp-send exited with code 1" in messages

    unified = client.get(f"/api/v1/logs/unified?monitor_id={monitor.id}&category=system")
    assert unified.status_code == 200, unified.text
    unified_messages = [item["message"] for item in unified.json()["items"]]
    assert "wireless flow restarted after 3 missing intervals" in unified_messages
    assert "wireless: rtnp-send exited with code 1" in unified_messages

