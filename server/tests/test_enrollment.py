from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app import tls
from app.api.enrollment_routes import NONCE_PATH
from app.config import get_settings
from app.auth import hash_password
from app.enrollment import RATE_LIMIT, nonces
from app.main import app
from app.models import AuditEvent, EnrollmentCheckin, InstallerCode, Monitor, Network, Test, User, UserRole
from conftest import FakeAgent, login, new_installer_code

WAITING = "Waiting for this code to be registered"


@pytest.fixture
def room(db):
    """A unique device-name prefix. Everything enrolled under it is removed afterwards."""
    prefix = f"T{uuid.uuid4().hex[:6]} "
    networks_before = set(db.scalars(select(Network.id)).all())
    yield prefix
    db.rollback()
    like = {"p": f"{prefix}%"}
    owned = "SELECT id FROM monitors WHERE name LIKE :p"
    db.execute(text(f"DELETE FROM monitor_health WHERE monitor_id IN ({owned})"), like)
    db.execute(text(f"DELETE FROM test_results WHERE test_id IN (SELECT id FROM tests WHERE monitor_id IN ({owned}))"), like)
    db.execute(text(f"DELETE FROM tests WHERE monitor_id IN ({owned})"), like)
    db.execute(text("DELETE FROM monitors WHERE name LIKE :p"), like)
    # An enrolled Pi that reports gets Wired and Wi-Fi networks in the shared home group. Left behind, they give every
    # later monitor a second "Wi-Fi" cell in /summary, and tests that read the first one passed or failed by chance.
    for network_id in set(db.scalars(select(Network.id)).all()) - networks_before:
        if db.scalar(select(func.count(Test.id)).where(Test.network_id == network_id)) == 0:
            db.execute(text("DELETE FROM networks WHERE id = :id"), {"id": network_id})
    db.execute(text("DELETE FROM installer_codes WHERE device_name LIKE :p"), like)
    db.execute(text("DELETE FROM audit_events WHERE detail LIKE :p"), like)
    db.commit()


def _register(client: TestClient, csrf: dict, code: str, device_name: str, location: str | None = None):
    return client.post(
        "/api/v1/enrollment/codes",
        json={"installer_code": code, "device_name": device_name, "location": location},
        headers=csrf,
    )


def _enroll(
    client: TestClient, csrf: dict, device_name: str, replace: str | None = None, location: str | None = None
) -> tuple[FakeAgent, dict]:
    code = new_installer_code()
    registered = _register(client, csrf, code, device_name, location)
    assert registered.status_code == 201, registered.text
    agent = FakeAgent(client, code)
    assert agent.check_in().status_code == 200
    approved = client.post(
        f"/api/v1/enrollment/codes/{registered.json()['id']}/approve",
        json={"replace_monitor_id": replace},
        headers=csrf,
    )
    assert approved.status_code == 200, approved.text
    return agent, approved.json()


def _health(client: TestClient, token: str):
    return client.post(
        "/api/v1/monitors/health",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hostname": "remote-agent-a1b2",
            "agent_version": "0.2.0",
            "uptime_seconds": 1,
            "cpu_percent": 1,
            "load_1": 0,
            "mem_used_mb": 1,
            "mem_total_mb": 2,
            "disk_used_mb": 1,
            "disk_total_mb": 2,
        },
    )


def test_unknown_expired_and_consumed_codes_get_the_same_rejection(client, db, room, state_dir):
    csrf = login(client)
    unknown = FakeAgent(client, new_installer_code())
    first = unknown.check_in()
    assert first.status_code == 403
    assert first.json()["detail"] == WAITING
    malformed = FakeAgent(client, "not a code")
    assert malformed.check_in().json() == first.json()

    expired_code = new_installer_code()
    registered = _register(client, csrf, expired_code, f"{room}Expired")
    assert registered.status_code == 201
    db.execute(
        text("UPDATE installer_codes SET expires_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=1), "id": uuid.UUID(registered.json()["id"])},
    )
    db.commit()
    expired = FakeAgent(client, expired_code).check_in()
    assert expired.status_code == 403
    assert expired.json() == first.json()

    agent, _ = _enroll(client, csrf, f"{room}Kitchen")
    intruder = FakeAgent(client, agent.installer_code).check_in()
    assert intruder.status_code == 403
    assert intruder.json() == first.json()

    assert db.scalar(
        select(EnrollmentCheckin).where(
            EnrollmentCheckin.identity_public_key.in_([unknown.identity_public_key, malformed.identity_public_key])
        )
    ) is None


def test_registered_code_shows_a_waiting_card(client, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    registered = _register(client, csrf, code.lower().replace("-", " "), f"{room}PiPocono", "Pocono House")
    assert registered.status_code == 201, registered.text
    agent = FakeAgent(client, code)
    checked = agent.check_in()
    assert checked.status_code == 200, checked.text
    assert checked.json()["state"] == "waiting_for_approval"
    assert checked.json()["conflict"] is False
    polled = agent.poll()
    assert polled.status_code == 200
    assert polled.json()["state"] == "waiting_for_approval"

    cards = client.get("/api/v1/enrollment").json()
    card = next(item for item in cards if item["id"] == registered.json()["id"])
    assert card["device_name"] == f"{room}PiPocono"
    assert card["location"] == "Pocono House"
    assert [row["hostname"] for row in card["checkins"]] == ["remote-agent-a1b2"]
    assert card["checkins"][0]["lan_ip"] == "192.168.1.40"
    assert card["checkins"][0]["observed_ip"] == "testclient"
    assert "code_hash" not in card


def test_second_device_with_the_same_code_blocks_approval_until_start_over(client, db, room, state_dir):
    # Login does not depend on seed or on another test having created the admin.
    if db.scalar(select(User).where(User.username == "admin")) is None:
        db.add(User(username="admin", password_hash=hash_password("admin-change-me"), role=UserRole.admin, is_active=True))
        db.commit()
    csrf = login(client)
    code = new_installer_code()
    registered = _register(client, csrf, code, f"{room}Hall").json()
    real = FakeAgent(client, code)
    copycat = FakeAgent(client, code, hostname="laptop")
    assert real.check_in().status_code == 200
    second = copycat.check_in()
    assert second.status_code == 200
    assert second.json()["conflict"] is True

    approve_url = f"/api/v1/enrollment/codes/{registered['id']}/approve"
    blocked = client.post(approve_url, json={}, headers=csrf)
    assert blocked.status_code == 409
    assert "More than one device" in blocked.json()["detail"]

    cleared = client.post(f"/api/v1/enrollment/codes/{registered['id']}/start-over", headers=csrf)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["checkins"] == []
    assert real.check_in().status_code == 200
    approved = client.post(approve_url, json={}, headers=csrf)
    assert approved.status_code == 200, approved.text


def test_approve_as_new_and_replace(client, db, room, state_dir):
    csrf = login(client)
    old, created = _enroll(client, csrf, f"{room}PiPocono", location="Pocono House")
    assert created["name"] == f"{room}PiPocono"
    assert created["location_label"] == "Pocono House"
    assert created["tunnel_address"].startswith("10.7.0.")
    assert created["tunnel_state"] == "never_connected"
    assert old.poll().status_code == 200

    same_name_code = new_installer_code()
    same_name = _register(client, csrf, same_name_code, f"{room}PiPocono").json()
    assert FakeAgent(client, same_name_code).check_in().status_code == 200
    refused = client.post(f"/api/v1/enrollment/codes/{same_name['id']}/approve", json={}, headers=csrf)
    assert refused.status_code == 409
    assert "device name" in refused.json()["detail"]

    new, replaced = _enroll(client, csrf, f"{room}PiPocono 2", replace=created["id"])
    assert replaced["id"] == created["id"]
    assert replaced["tunnel_address"] == created["tunnel_address"]
    assert replaced["name"] == f"{room}PiPocono 2"
    assert replaced["location_label"] == "Pocono House"
    assert replaced["config_version"] == created["config_version"] + 1

    assert old.poll().status_code == 404
    assert old.token is not None
    assert _health(client, old.token).status_code == 401
    fresh = new.poll()
    assert fresh.status_code == 200
    assert fresh.json()["monitor_id"] == created["id"]
    assert _health(client, new.token).status_code == 200

    old_code_hash = db.scalars(select(InstallerCode).where(InstallerCode.monitor_id == uuid.UUID(created["id"]))).all()
    assert len(old_code_hash) == 1


def test_delete_releases_the_code_and_removes_the_preshared_key(client, db, room, state_dir):
    csrf = login(client)
    agent, created = _enroll(client, csrf, f"{room}Garage")
    psk_file = state_dir / "psk" / f"{created['id']}.key"
    assert psk_file.is_file()
    assert agent.poll().status_code == 200

    assert client.delete(f"/api/v1/monitors/{created['id']}", headers=csrf).status_code == 204
    assert not psk_file.exists()
    assert agent.poll().status_code == 404
    assert agent.check_in().json()["detail"] == WAITING

    again = _register(client, csrf, agent.installer_code, f"{room}Garage")
    assert again.status_code == 201, again.text
    assert agent.check_in().status_code == 200
    approved = client.post(f"/api/v1/enrollment/codes/{again.json()['id']}/approve", json={}, headers=csrf)
    assert approved.status_code == 200, approved.text


def test_poll_serves_the_preshared_key_until_a_handshake_and_rotates_tokens(client, db, room, state_dir):
    csrf = login(client)
    agent, created = _enroll(client, csrf, f"{room}Attic")
    first = agent.poll()
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["state"] == "approved"
    assert body["token"]
    tunnel = body["tunnel"]
    assert tunnel["address"] == created["tunnel_address"]
    assert tunnel["link"] == "wired"
    assert tunnel["allowed_ips"] == "10.7.0.1/32"
    wireless = next(item for item in body["tunnels"] if item["link"] == "wireless")
    assert wireless["address"] == created["wireless_tunnel_address"]
    assert wireless["allowed_ips"] == "10.7.1.1/32"
    assert wireless["preshared_key"]
    assert tunnel["persistent_keepalive"] == 25
    assert tunnel["endpoint"] == "192.0.2.10:51820"
    assert tunnel["preshared_key"]
    peers = (state_dir / "peers.conf").read_text()
    assert f"PresharedKey = {tunnel['preshared_key']}" in peers
    assert f"PublicKey = {agent.wg_public_key}" in peers
    assert f"PublicKey = {agent.wg_wireless_public_key}" in peers
    assert f"AllowedIPs = {wireless['address']}/32" in peers

    token = agent.token
    assert agent.poll().json()["token"] is None
    assert _health(client, token).status_code == 200

    (state_dir / "handshakes").write_text(f"{agent.wg_public_key}\t{int(time.time())}\n")
    connected = agent.poll().json()
    assert connected["tunnel"]["preshared_key"] is None
    listed = next(m for m in client.get("/api/v1/monitors").json() if m["id"] == created["id"])
    assert listed["tunnel_state"] == "connected"

    stale = int(time.time()) - 11 * 60
    db.execute(
        text("UPDATE monitors SET last_handshake_at = :t WHERE id = :id"),
        {"t": datetime.fromtimestamp(stale, tz=timezone.utc), "id": uuid.UUID(created["id"])},
    )
    db.commit()
    db.expire_all()
    (state_dir / "handshakes").write_text(f"{agent.wg_public_key}\t{stale}\n")
    assert agent.poll().json()["tunnel"]["preshared_key"]

    recovered = agent.poll(have_token=False)
    assert recovered.json()["token"]
    assert recovered.json()["token"] != token
    assert _health(client, token).status_code == 401
    assert _health(client, agent.token).status_code == 200

    stored = db.scalar(select(Monitor.token_hash).where(Monitor.id == uuid.UUID(created["id"])))
    assert stored.startswith("sha256:")
    assert agent.token not in stored


def test_signatures_and_nonces(client, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    _register(client, csrf, code, f"{room}Porch")
    agent = FakeAgent(client, code)

    nonce = agent.nonce().json()["nonce"]
    body = agent.check_in_body(nonce)
    assert client.post("/api/v1/agents/check-in", json=body).status_code == 200
    assert client.post("/api/v1/agents/check-in", json=body).status_code == 401

    swapped = FakeAgent(client, code).wg_public_key
    tampered = agent.check_in(tampered={"wg_public_key": swapped})
    assert tampered.status_code == 401

    stranger = FakeAgent(client, code)
    borrowed = agent.nonce().json()["nonce"]
    assert client.post("/api/v1/agents/check-in", json=stranger.check_in_body(borrowed)).status_code == 401

    issued = agent.nonce().json()["nonce"]
    nonces.clear()
    assert client.post("/api/v1/agents/check-in", json=agent.check_in_body(issued)).status_code == 401
    assert agent.check_in().status_code == 200


def test_rate_limit_spares_approved_agents(client, room, state_dir):
    csrf = login(client)
    approved, _ = _enroll(client, csrf, f"{room}Basement")
    stranger = FakeAgent(client, new_installer_code())
    nonces.clear()
    from app.enrollment import limiter

    limiter.clear()
    for _ in range(RATE_LIMIT):
        assert stranger.nonce().status_code == 200
    assert stranger.nonce().status_code == 429
    assert approved.nonce().status_code == 200
    assert approved.poll().status_code == 200


def test_agent_endpoints_need_https(db, state_dir):
    plain = TestClient(app, base_url="http://testserver")
    agent = FakeAgent(plain, new_installer_code())
    response = plain.post(NONCE_PATH, json={"identity_public_key": agent.identity_public_key})
    assert response.status_code == 403


def test_invalid_hostname_or_address_is_rejected_and_not_stored(client, db, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    _register(client, csrf, code, f"{room}Shed")
    agent = FakeAgent(client, code)
    assert agent.check_in(signed={"hostname": "<script>alert(1)</script>"}).status_code == 400
    assert agent.check_in(signed={"lan_ip": "192.168.1.400"}).status_code == 400
    assert db.scalar(
        select(EnrollmentCheckin).where(EnrollmentCheckin.identity_public_key == agent.identity_public_key)
    ) is None


def test_viewer_and_missing_csrf_are_rejected_and_admin_actions_are_audited(client, db, room, state_dir):
    csrf = login(client)
    agent, created = _enroll(client, csrf, f"{room}Loft")
    code = new_installer_code()
    pending = _register(client, csrf, code, f"{room}Nook").json()
    FakeAgent(client, code).check_in()

    viewer = TestClient(app, base_url="https://testserver")
    viewer_csrf = login(viewer, "viewer", "viewer-change-me")
    assert viewer.get("/api/v1/enrollment").status_code == 200
    actions = [
        ("post", "/api/v1/enrollment/codes", {"installer_code": new_installer_code(), "device_name": f"{room}V"}),
        ("post", f"/api/v1/enrollment/codes/{pending['id']}/approve", {}),
        ("post", f"/api/v1/enrollment/codes/{pending['id']}/approve", {"replace_monitor_id": created["id"]}),
        ("post", f"/api/v1/enrollment/codes/{pending['id']}/start-over", None),
        ("delete", f"/api/v1/enrollment/codes/{pending['id']}", None),
        ("delete", f"/api/v1/monitors/{created['id']}", None),
    ]
    for method, url, body in actions:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(viewer, method)(url, headers=viewer_csrf, **kwargs).status_code == 403, url
        assert getattr(client, method)(url, **kwargs).status_code == 403, url
        bad = {"X-CSRF-Token": "0" * 64}
        assert getattr(client, method)(url, headers=bad, **kwargs).status_code == 403, url

    assert client.post(f"/api/v1/enrollment/codes/{pending['id']}/start-over", headers=csrf).status_code == 200
    assert client.delete(f"/api/v1/enrollment/codes/{pending['id']}", headers=csrf).status_code == 204
    assert client.delete(f"/api/v1/monitors/{created['id']}", headers=csrf).status_code == 204

    recorded = db.scalars(
        select(AuditEvent.action).where(AuditEvent.detail.like(f"{room}%")).order_by(AuditEvent.created_at)
    ).all()
    assert recorded == ["register_code", "approve", "register_code", "start_over", "remove_code", "delete"]
    assert not any(agent.installer_code in row for row in db.scalars(select(AuditEvent.detail)).all())


def test_waiting_cards_are_capped(client, db, room, state_dir):
    csrf = login(client)
    db.execute(text("DELETE FROM enrollment_checkins"))
    db.commit()
    agents = []
    for index in range(11):
        code = new_installer_code()
        assert _register(client, csrf, code, f"{room}Room {index}").status_code == 201
        agents.append(FakeAgent(client, code))
    for agent in agents[:10]:
        assert agent.check_in().status_code == 200
    assert agents[10].check_in().status_code == 429
    assert agents[0].check_in().status_code == 200


def test_issue_adds_the_missing_wireless_tunnel_without_replacing_wired(client, db, room, state_dir):
    csrf = login(client)
    _agent, created = _enroll(client, csrf, f"{room}WiredOnly")
    monitor_id = uuid.UUID(created["id"])
    wired_key = db.scalar(select(Monitor.wg_public_key).where(Monitor.id == monitor_id))
    db.execute(
        text(
            "UPDATE monitors SET wg_wireless_address = NULL, wg_wireless_public_key = NULL, "
            "last_wireless_handshake_at = NULL WHERE id = :id"
        ),
        {"id": monitor_id},
    )
    db.commit()
    (state_dir / "psk" / f"{monitor_id}-wireless.key").unlink(missing_ok=True)

    issued = client.post(f"/api/v1/monitors/{created['id']}/tunnel", headers=csrf)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["config"] == ""
    assert body["address"] == created["tunnel_address"]
    assert body["wireless_address"].startswith("10.7.1.")
    assert "AllowedIPs = 10.7.1.1/32" in body["wireless_config"]
    assert "/sys/class/net" in body["wireless_config"]
    db.expire_all()
    assert db.scalar(select(Monitor.wg_public_key).where(Monitor.id == monitor_id)) == wired_key
    peers = (state_dir / "peers.conf").read_text()
    assert wired_key in peers
    assert f"AllowedIPs = {body['address']}/32" in peers
    assert f"AllowedIPs = {body['wireless_address']}/32" in peers


def test_onboarding_issues_a_wired_and_a_wireless_tunnel(client, room, state_dir):
    csrf = login(client)
    _agent, created = _enroll(client, csrf, f"{room}Dual")
    issued = client.post(f"/api/v1/monitors/{created['id']}/tunnel", headers=csrf)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["address"].startswith("10.7.0.")
    assert body["wireless_address"].startswith("10.7.1.")
    assert "AllowedIPs = 10.7.0.1/32" in body["config"]
    assert "FwMark = 51820" in body["config"]
    assert "ip rule add fwmark 51820 lookup 100 priority 100" in body["config"]
    assert "AllowedIPs = 10.7.1.1/32" in body["wireless_config"]
    assert "FwMark = 51821" in body["wireless_config"]
    assert "ip rule add fwmark 51821 lookup 101 priority 101" in body["wireless_config"]
    assert "/sys/class/net" in body["wireless_config"]
    assert "carrier" in body["wireless_config"]
    assert "builtin" in body["wireless_config"]
    assert "scope link" in body["wireless_config"]
    assert "iw dev" not in body["wireless_config"]
    assert "#" not in body["wireless_config"]
    assert "PrivateKey" in body["config"]
    peers = (state_dir / "peers.conf").read_text()
    assert f"AllowedIPs = {body['address']}/32" in peers
    assert f"AllowedIPs = {body['wireless_address']}/32" in peers
    listed = next(item for item in client.get("/api/v1/monitors").json() if item["id"] == created["id"])
    assert listed["tunnel_address"] == body["address"]
    assert listed["wireless_tunnel_address"] == body["wireless_address"]


def _android_agent(client, csrf, device_name: str):
    code = new_installer_code()
    registered = _register(client, csrf, code, device_name)
    assert registered.status_code == 201, registered.text
    agent = FakeAgent(client, code)
    # An Android device has one link, so it sends one WireGuard key and says what it is. Both are signed.
    checked = agent.check_in(signed={"wg_wireless_public_key": None, "platform": "android"})
    assert checked.status_code == 200, checked.text
    approved = client.post(
        f"/api/v1/enrollment/codes/{registered.json()['id']}/approve", json={"replace_monitor_id": None}, headers=csrf
    )
    assert approved.status_code == 200, approved.text
    return agent, approved.json()


def test_android_agent_enrolls_with_one_tunnel_and_no_pi_checks(client, db, room, state_dir):
    csrf = login(client)
    agent, created = _android_agent(client, csrf, f"{room}Shield")
    monitor = db.get(Monitor, uuid.UUID(created["id"]))
    assert monitor.platform == "android"
    assert monitor.wg_address.startswith("10.7.0.")
    assert monitor.wg_wireless_address is None

    polled = agent.poll()
    assert polled.status_code == 200, polled.text
    assert [item["link"] for item in polled.json()["tunnels"]] == ["wired"]
    assert agent.token
    assert _health(client, agent.token).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count(Test.id)).where(Test.monitor_id == monitor.id)) == 0


def test_pi_agent_still_gets_the_internet_checks(client, db, room, state_dir):
    csrf = login(client)
    agent, created = _enroll(client, csrf, f"{room}Pi")
    assert agent.poll().status_code == 200
    assert _health(client, agent.token).status_code == 200
    db.expire_all()
    monitor = db.get(Monitor, uuid.UUID(created["id"]))
    assert monitor.platform == "pi"
    assert db.scalar(select(func.count(Test.id)).where(Test.monitor_id == monitor.id)) > 0


def test_platform_is_covered_by_the_signature(client, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    _register(client, csrf, code, f"{room}Tamper")
    agent = FakeAgent(client, code)
    forged = agent.check_in(signed={"wg_wireless_public_key": None, "platform": "android"}, tampered={"platform": "pi"})
    assert forged.status_code == 401
    unknown = agent.check_in(signed={"platform": "toaster"})
    assert unknown.status_code == 422


def test_name_typed_on_the_android_device_becomes_the_sensor_name(client, db, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    registered = _register(client, csrf, code, f"{room}Placeholder")
    assert registered.status_code == 201, registered.text
    agent = FakeAgent(client, code)
    chosen = f"{room}Living Room Shield"
    checked = agent.check_in(signed={"wg_wireless_public_key": None, "platform": "android", "device_name": chosen})
    assert checked.status_code == 200, checked.text
    card = next(item for item in client.get("/api/v1/enrollment").json() if item["id"] == registered.json()["id"])
    assert card["checkins"][0]["device_name"] == chosen
    approved = client.post(
        f"/api/v1/enrollment/codes/{registered.json()['id']}/approve", json={"replace_monitor_id": None}, headers=csrf
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["name"] == chosen


def test_registration_name_is_used_when_the_device_sends_none(client, room, state_dir):
    csrf = login(client)
    _agent, created = _enroll(client, csrf, f"{room}FromDashboard")
    assert created["name"] == f"{room}FromDashboard"


def test_device_name_is_covered_by_the_signature(client, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    _register(client, csrf, code, f"{room}Sig")
    agent = FakeAgent(client, code)
    forged = agent.check_in(
        signed={"wg_wireless_public_key": None, "platform": "android", "device_name": "Real"},
        tampered={"device_name": "Forged"},
    )
    assert forged.status_code == 401


def _decode_setup_key(value: str) -> dict:
    assert value.startswith("hnw1.")
    raw = value[len("hnw1.") :]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def test_registration_carries_a_setup_key_with_the_server_address_and_pins(client, room, state_dir, tmp_path, monkeypatch):
    tls_dir = tmp_path / "tls"
    tls.ensure(tls_dir)
    monkeypatch.setattr(get_settings(), "tls_dir", str(tls_dir))
    csrf = login(client)
    registered = _register(client, csrf, new_installer_code(), f"{room}Shield")
    assert registered.status_code == 201, registered.text
    key = registered.json()["setup_key"]
    assert _decode_setup_key(key) == {"u": "https://testserver", "p": tls.pin_list(tls_dir)}
    listed = client.get("/api/v1/enrollment").json()
    assert any(item["setup_key"] == key for item in listed if item["id"] == registered.json()["id"])


def test_registration_has_no_setup_key_without_a_server_key(client, room, state_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "tls_dir", str(tmp_path / "missing"))
    csrf = login(client)
    registered = _register(client, csrf, new_installer_code(), f"{room}NoKey")
    assert registered.status_code == 201, registered.text
    assert registered.json()["setup_key"] is None


def test_dashboard_is_told_the_device_type(client, room, state_dir):
    csrf = login(client)
    code = new_installer_code()
    registered = _register(client, csrf, code, f"{room}Kind")
    agent = FakeAgent(client, code)
    assert agent.check_in(signed={"wg_wireless_public_key": None, "platform": "android"}).status_code == 200
    # The type comes from the device's signed check-in, so the admin sees it before approving.
    waiting = next(item for item in client.get("/api/v1/enrollment").json() if item["id"] == registered.json()["id"])
    assert [row["platform"] for row in waiting["checkins"]] == ["android"]
    approved = client.post(
        f"/api/v1/enrollment/codes/{registered.json()['id']}/approve", json={"replace_monitor_id": None}, headers=csrf
    )
    assert approved.json()["platform"] == "android"
    listed = next(item for item in client.get("/api/v1/monitors").json() if item["id"] == approved.json()["id"])
    assert listed["platform"] == "android"
    assert client.get(f"/api/v1/monitors/{approved.json()['id']}").json()["platform"] == "android"
    # The Home page reads the type from the summary, so the card is right from the first paint.
    summary = client.get("/api/v1/summary").json()
    assert next(item for item in summary["monitors"] if item["id"] == approved.json()["id"])["platform"] == "android"


def test_a_pi_reports_its_type_too(client, room, state_dir):
    csrf = login(client)
    _agent, created = _enroll(client, csrf, f"{room}PiKind")
    assert created["platform"] == "pi"
    assert client.get(f"/api/v1/monitors/{created['id']}").json()["platform"] == "pi"


def test_an_android_agent_is_tested_over_wifi_only(client, room, state_dir):
    csrf = login(client)
    agent, created = _android_agent(client, csrf, f"{room}WifiOnly")
    assert agent.poll().status_code == 200
    assert _health(client, agent.token).status_code == 200
    monitor_id = created["id"]

    # The server refuses the Wired link for an Android agent, whatever the dashboard shows.
    wired = client.post("/api/v1/speedtest", json={"monitor_id": monitor_id, "link": "wired"}, headers=csrf)
    assert wired.status_code == 400
    assert "Wi-Fi only" in wired.json()["detail"]
    assert client.post("/api/v1/speedtest", json={"monitor_id": monitor_id, "link": "wireless"}, headers=csrf).status_code == 200

    both = {"monitor_id": monitor_id, "profile_slug": "voip-g711a", "links": ["wired", "wireless"]}
    assert client.put("/api/v1/synthetic/continuous", json=both, headers=csrf).status_code == 400
    wifi = {**both, "links": ["wireless"]}
    assert client.put("/api/v1/synthetic/continuous", json=wifi, headers=csrf).status_code == 200

    stress = client.post(
        "/api/v1/stress",
        json={"monitor_id": monitor_id, "links": ["wireless"], "duration_seconds": 30, "sessions": [{"kind": "voice"}]},
        headers=csrf,
    )
    assert stress.status_code == 400


def test_an_android_device_collects_its_setup_key_with_its_pairing_token(client, room, state_dir, tmp_path, monkeypatch):
    tls_dir = tmp_path / "tls"
    tls.ensure(tls_dir)
    monkeypatch.setattr(get_settings(), "tls_dir", str(tls_dir))
    token = "pair_" + uuid.uuid4().hex
    # Before anyone registers it, the device keeps waiting. Nothing says whether the token is known.
    assert client.post("/api/v1/agents/pairing", json={"token": token}).json() == {
        "state": "waiting",
        "setup_key": None,
        "name": None,
    }
    csrf = login(client)
    body = {"installer_code": new_installer_code(), "device_name": f"{room}Den", "pairing_token": token}
    registered = client.post("/api/v1/enrollment/codes", json=body, headers=csrf)
    assert registered.status_code == 201, registered.text
    # No session is needed: the token is the proof, and it was only ever on the device's screen.
    client.cookies.clear()
    answer = client.post("/api/v1/agents/pairing", json={"token": token}).json()
    assert answer["state"] == "registered"
    assert answer["name"] == f"{room}Den"
    assert _decode_setup_key(answer["setup_key"]) == {"u": "https://testserver", "p": tls.pin_list(tls_dir)}
    # Another token learns nothing, and a malformed one is refused.
    assert client.post("/api/v1/agents/pairing", json={"token": "pair_" + uuid.uuid4().hex}).json()["state"] == "waiting"
    assert client.post("/api/v1/agents/pairing", json={"token": "x"}).status_code == 422

