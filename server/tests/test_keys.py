"""Per-purpose keys, and rotating SECRET_KEY without losing sessions or stored secrets."""

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import keys, telegram
from app.config import get_settings
from app.enrollment import installer_code_hash, installer_code_hashes
from conftest import login


@pytest.fixture
def rotated(monkeypatch):
    """SECRET_KEY changes to a new value; the old one moves to SECRET_KEY_PREVIOUS."""
    settings = get_settings()
    old = settings.secret_key

    def rotate():
        monkeypatch.setattr(settings, "secret_key_previous", old)
        monkeypatch.setattr(settings, "secret_key", old + "-rotated")

    return rotate


def test_each_purpose_gets_its_own_key():
    derived = {purpose: keys.key(purpose) for purpose in (keys.SESSION, keys.CSRF, keys.INSTALLER_CODE, keys.STORED_SECRETS)}
    assert len(set(derived.values())) == 4
    assert all(len(value) == 32 for value in derived.values())
    assert get_settings().secret_key.encode() not in derived.values()


def test_a_session_survives_rotation_while_the_old_key_is_kept(client: TestClient, rotated, monkeypatch):
    csrf = login(client)
    rotated()
    assert client.get("/api/v1/auth/me").status_code == 200
    # The CSRF token made under the old key is still accepted.
    assert client.delete("/api/v1/settings/gemini", headers=csrf).status_code == 200
    monkeypatch.setattr(get_settings(), "secret_key_previous", "")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_an_installer_code_made_before_rotation_still_matches(rotated):
    before = installer_code_hash("K7QF2MXD9PLA")
    rotated()
    assert installer_code_hash("K7QF2MXD9PLA") != before
    assert before in installer_code_hashes("K7QF2MXD9PLA")


def test_a_secret_stored_before_per_purpose_keys_opens_and_is_re_encrypted():
    legacy = Fernet(base64.urlsafe_b64encode(hashlib.sha256(get_settings().secret_key.encode()).digest()))
    stored = legacy.encrypt(b"123456789:old-token").decode()
    assert telegram.decrypt(stored) == "123456789:old-token"
    fresh = telegram._rotated(stored)
    assert fresh != stored
    current = Fernet(base64.urlsafe_b64encode(keys.key(keys.STORED_SECRETS)))
    assert current.decrypt(fresh.encode()) == b"123456789:old-token"
    assert telegram._rotated(fresh) == fresh


def test_a_stored_secret_survives_rotation(rotated):
    stored = telegram.encrypt("123456789:token")
    rotated()
    assert telegram.decrypt(stored) == "123456789:token"
    fresh = telegram._rotated(stored)
    assert fresh != stored and telegram.decrypt(fresh) == "123456789:token"
