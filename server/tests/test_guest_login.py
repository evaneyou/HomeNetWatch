"""Guest sign-in records the visitor address and expires after three idle minutes."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import hash_password
from app.models import AuditEvent, User, UserRole
from conftest import login

FORWARDED = "198.51.100.9, 203.0.113.5"


def _ensure_guest(db) -> None:
    if db.scalar(select(User).where(User.username == "guest")) is None:
        db.add(User(username="guest", password_hash=hash_password("guest"), role=UserRole.guest, is_active=True))
        db.commit()


def test_guest_entry_link_signs_in_and_opens_the_dashboard(client, db):
    _ensure_guest(db)
    response = client.get("/api/v1/auth/guest", follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "guest"


def test_guest_login_records_the_proxy_address_and_hides_the_list(client, db):
    _ensure_guest(db)
    before = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "guest_login"))
    guest = TestClient(client.app, base_url="https://testserver")
    response = guest.post(
        "/api/v1/auth/login",
        json={"username": "guest", "password": "guest"},
        headers={"X-Forwarded-For": FORWARDED},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    after = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "guest_login"))
    assert after == before + 1

    denied = guest.get("/api/v1/settings/guest-visits")
    assert denied.status_code == 403

    login(client)
    listed = client.get("/api/v1/settings/guest-visits")
    assert listed.status_code == 200, listed.text
    assert any(row["address"] == "203.0.113.5" for row in listed.json())

    admin_before = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "guest_login"))
    login(client)
    db.expire_all()
    admin_after = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "guest_login"))
    assert admin_after == admin_before


def test_two_guest_browsers_stay_signed_in(client, db):
    _ensure_guest(db)
    other = TestClient(client.app, base_url="https://testserver")
    login(client, "guest", "guest")
    login(other, "guest", "guest")
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 200


def test_guest_session_ends_after_three_idle_minutes(client, db, monkeypatch):
    import itsdangerous.timed as timed

    clock = {"now": 1_700_000_000}
    monkeypatch.setattr(timed.time, "time", lambda: clock["now"])
    _ensure_guest(db)
    login(client, "guest", "guest")

    clock["now"] += 179
    assert client.get("/api/v1/auth/me").status_code == 200

    clock["now"] += 181
    assert client.get("/api/v1/auth/me").status_code == 401

    clock["now"] = 1_700_000_000
    login(client, "guest", "guest")
    clock["now"] += 100
    assert client.get("/api/v1/auth/me").status_code == 200
    clock["now"] += 100
    assert client.get("/api/v1/auth/me").status_code == 200
    clock["now"] += 181
    assert client.get("/api/v1/auth/me").status_code == 401


def test_admin_session_stays_for_the_long_window(client, monkeypatch):
    import itsdangerous.timed as timed

    clock = {"now": 1_700_000_000}
    monkeypatch.setattr(timed.time, "time", lambda: clock["now"])
    login(client)
    clock["now"] += 181
    assert client.get("/api/v1/auth/me").status_code == 200
