"""Signing out, a new password or `app.sessions revoke` ends every session of the account, copied cookies included."""

from __future__ import annotations

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select

from app import keys, sessions
from app.auth import hash_password
from app.models import User, UserRole
from app.seed import _same_password
from tests.conftest import login
from tests.test_guest_login import _ensure_guest


def _copy_of(client: TestClient) -> TestClient:
    """Another browser holding the same cookie, as a thief with a copied cookie would."""
    other = TestClient(client.app, base_url="https://testserver")
    other.cookies.set("hns_session", client.cookies.get("hns_session"))
    return other


def _account(db, username: str, role: UserRole) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, password_hash=hash_password(username), role=role, is_active=True)
        db.add(user)
        db.commit()
    return user


def test_signing_out_ends_a_copied_session(client: TestClient, db):
    _account(db, "revoke-viewer", UserRole.viewer)
    login(client, "revoke-viewer", "revoke-viewer")
    stolen = _copy_of(client)
    assert stolen.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert stolen.get("/api/v1/auth/me").status_code == 401
    login(client, "revoke-viewer", "revoke-viewer")
    assert client.get("/api/v1/auth/me").status_code == 200


def test_the_revoke_command_signs_an_account_out(client: TestClient, db):
    _account(db, "revoke-cli", UserRole.viewer)
    login(client, "revoke-cli", "revoke-cli")
    assert sessions.main(["sessions", "revoke", "revoke-cli"]) == 0
    assert client.get("/api/v1/auth/me").status_code == 401
    assert sessions.main(["sessions", "revoke", "nobody-here"]) == 1


def test_one_guest_signing_out_leaves_other_visitors_signed_in(client: TestClient, db):
    _ensure_guest(db)
    login(client, "guest", "guest")
    other_visitor = TestClient(client.app, base_url="https://testserver")
    login(other_visitor, "guest", "guest")
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert other_visitor.get("/api/v1/auth/me").status_code == 200


def test_a_cookie_from_before_session_versions_still_works_until_revoked(client: TestClient, db):
    user = _account(db, "revoke-legacy", UserRole.viewer)
    db.refresh(user)
    old = URLSafeTimedSerializer(keys.key(keys.SESSION), salt="hns-session").dumps({"uid": str(user.id)})
    client.cookies.set("hns_session", old)
    expected = 200 if user.session_version == 0 else 401
    assert client.get("/api/v1/auth/me").status_code == expected


def test_seed_compares_passwords_without_failing_on_an_odd_hash():
    assert _same_password("x", hash_password("x"))
    assert not _same_password("x", hash_password("y"))
    assert not _same_password("x", "not-a-hash")
