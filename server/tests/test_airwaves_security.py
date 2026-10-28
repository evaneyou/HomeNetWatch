"""Airwaves Security: alert filters, LAN scan request/ingest, tunnel rejection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import select

from app.auth import hash_password
from app.models import ApInventory, LanScanRun, Monitor, RfAlert, User, UserRole
from tests.conftest import login


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Monitor-Name": quote("Pi Test", safe="")}


def _monitor(db) -> Monitor:
    monitor = db.scalar(select(Monitor).where(Monitor.slug == db.info["monitor_slug"]))
    assert monitor is not None
    return monitor


def _user(db, username: str, role: UserRole) -> None:
    if db.scalar(select(User).where(User.username == username)) is None:
        password = "admin-change-me" if role == UserRole.admin else username
        db.add(User(username=username, password_hash=hash_password(password), role=role, is_active=True))
        db.commit()


def test_rf_alerts_filter_by_severity_trusted_and_search(client, db):
    monitor = _monitor(db)
    _user(db, "admin", UserRole.admin)
    now = datetime.now(timezone.utc)
    db.add(
        ApInventory(
            monitor_id=monitor.id,
            bssid="aa:bb:cc:dd:ee:01",
            trusted=True,
            trust_source="manual",
            label="",
            first_seen=now,
            last_seen=now,
        )
    )
    db.add_all(
        [
            RfAlert(
                monitor_id=monitor.id,
                observed_at=now - timedelta(minutes=5),
                dedupe_key="1",
                alert_type="DEAUTHFLOOD",
                bssid="aa:bb:cc:dd:ee:01",
                text="trusted deauth",
            ),
            RfAlert(
                monitor_id=monitor.id,
                observed_at=now - timedelta(minutes=4),
                dedupe_key="2",
                alert_type="PROBE",
                bssid="11:22:33:44:55:66",
                text="info noise",
            ),
            RfAlert(
                monitor_id=monitor.id,
                observed_at=now - timedelta(minutes=3),
                dedupe_key="3",
                alert_type="WPA3DOWNGRADE",
                bssid="11:22:33:44:55:66",
                text="warning untrusted",
            ),
        ]
    )
    db.commit()
    headers = login(client)
    critical = client.get(f"/api/v1/rf/alerts?agent_id={monitor.id}&range=1h&severity=critical", headers=headers)
    assert critical.status_code == 200
    assert [row["alert_type"] for row in critical.json()] == ["DEAUTHFLOOD"]
    trusted = client.get(f"/api/v1/rf/alerts?agent_id={monitor.id}&range=1h&trusted=true", headers=headers)
    assert [row["bssid"] for row in trusted.json()] == ["aa:bb:cc:dd:ee:01"]
    search = client.get(f"/api/v1/rf/alerts?agent_id={monitor.id}&range=1h&q=downgrade", headers=headers)
    assert [row["alert_type"] for row in search.json()] == ["WPA3DOWNGRADE"]


def test_lan_scan_request_command_ingest_and_rejects_tunnel(client, db, monitor_token):
    monitor = _monitor(db)
    _user(db, "admin", UserRole.admin)
    headers = login(client)
    first = client.post(f"/api/v1/rf/lan-scan?agent_id={monitor.id}", headers=headers)
    assert first.status_code == 200, first.text
    run_id = first.json()["run_id"]
    assert first.json()["lan_scan_requested_at"]
    second = client.post(f"/api/v1/rf/lan-scan?agent_id={monitor.id}", headers=headers)
    assert second.status_code == 409

    command = client.get("/api/v1/monitors/command", headers=_headers(monitor_token))
    assert command.status_code == 200
    assert command.json()["lan_scan"] is True

    bad = client.post(
        "/api/v1/monitors/lan-scan",
        headers=_headers(monitor_token),
        json={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "finished",
            "subnet": "10.7.0.0/24",
            "hosts": [],
        },
    )
    assert bad.status_code == 400

    ok = client.post(
        "/api/v1/monitors/lan-scan",
        headers=_headers(monitor_token),
        json={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "finished",
            "subnet": "192.168.1.0/24",
            "hosts": [
                {"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "Router", "hostname": "gateway.local.", "ports": [80, 443]},
                {"ip": "192.168.1.20", "mac": "aa:bb:cc:00:11:22", "ports": []},  # older agent: no hostname field
            ],
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["run_id"] == run_id
    db.refresh(monitor)
    assert monitor.lan_scan_requested_at is None
    run = db.get(LanScanRun, run_id)
    assert run is not None
    assert run.status == "finished"
    assert run.subnet == "192.168.1.0/24"
    assert len(run.hosts) == 2
    by_ip = {host.ip: host for host in run.hosts}
    assert by_ip["192.168.1.1"].ports == [80, 443]
    assert by_ip["192.168.1.1"].hostname == "gateway.local"  # trailing dot trimmed
    assert by_ip["192.168.1.20"].hostname == ""

    listed = client.get(f"/api/v1/rf/lan-scans?agent_id={monitor.id}", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run_id
    hosts_out = {host["ip"]: host for host in listed.json()[0]["hosts"]}
    assert hosts_out["192.168.1.1"]["hostname"] == "gateway.local"
    assert hosts_out["192.168.1.1"]["vendor"] == "Router"
    summary = client.get(f"/api/v1/rf/security?agent_id={monitor.id}", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["last_lan_scan"]["id"] == run_id
