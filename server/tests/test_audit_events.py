"""Security events land in audit_events: sign-ins with their address, failures, blocks and settings changes."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import audit
from app.auth import LOGIN_FAILURE_LIMIT, login_failures
from app.models import AuditEvent
from conftest import login


def _events(db, action: str, username: str | None = None) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.action == action)
    if username is not None:
        query = query.where(AuditEvent.username == username)
    db.expire_all()
    return list(db.scalars(query.order_by(AuditEvent.created_at)).all())


def test_a_sign_in_is_recorded_with_its_address(client: TestClient, db):
    before = len(_events(db, "login", "admin"))
    login(client)
    rows = _events(db, "login", "admin")
    assert len(rows) == before + 1
    assert rows[-1].detail == "testclient"


def test_a_failed_sign_in_is_recorded(client: TestClient, db):
    name = f"nobody-{uuid.uuid4().hex[:8]}"
    assert client.post("/api/v1/auth/login", json={"username": name, "password": "wrong"}).status_code == 401
    rows = _events(db, "login_failed", name)
    assert len(rows) == 1 and rows[0].detail == "testclient"


def test_a_block_is_recorded_once_and_blocked_attempts_are_not(client: TestClient, db):
    name = f"flood-{uuid.uuid4().hex[:8]}"
    login_failures.clear()
    login_failures.limit = 3
    try:
        codes = [client.post("/api/v1/auth/login", json={"username": name, "password": "x"}).status_code for _ in range(6)]
    finally:
        login_failures.clear()
        login_failures.limit = LOGIN_FAILURE_LIMIT
    assert codes == [401, 401, 429, 429, 429, 429]
    assert len(_events(db, "login_failed", name)) == 3
    assert len(_events(db, "login_blocked", name)) == 1


def test_settings_changes_are_recorded(client: TestClient, db):
    csrf = login(client)
    before = len(_events(db, "gemini_key_removed", "admin"))
    assert client.delete("/api/v1/settings/gemini", headers=csrf).status_code == 200
    assert len(_events(db, "gemini_key_removed", "admin")) == before + 1
    before = len(_events(db, "telegram_disconnected", "admin"))
    assert client.delete("/api/v1/settings/telegram", headers=csrf).status_code == 200
    assert len(_events(db, "telegram_disconnected", "admin")) == before + 1


def test_a_new_admin_address_is_sent_to_telegram_once(client: TestClient, db, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(audit, "alert_new_address", lambda username, address: sent.append(address))
    seen = audit.new_address(db, "admin", "testclient")
    login(client)
    login(client)
    assert sent == (["testclient"] if seen else [])
    assert not audit.new_address(db, "admin", "testclient")
