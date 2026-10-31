from __future__ import annotations

import base64
import os
import secrets
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://hns:hns-dev-password@127.0.0.1:55432/hns",
)
os.environ.setdefault("SECRET_KEY", "test-secret")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from app.api.enrollment_routes import CHECK_IN_PATH, NONCE_PATH, POLL_PATH  # noqa: E402
from app.auth import hash_password, login_failures  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.enrollment import limiter, nonces, signed_message  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Group,
    Monitor,
    MonitorStatus,
    Network,
    NetworkKind,
    Test,
    TestKind,
)
from app.seed import _monitor_token  # noqa: E402

AGENT_BASE_URL = "https://testserver"


@pytest.fixture(scope="session")
def engine():
    url = os.environ["DATABASE_URL"]
    eng = create_engine(url, pool_pre_ping=True)
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield eng


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    group = Group(slug=f"test-home-{suffix}", name=f"Test Home {suffix}")
    session.add(group)
    session.flush()
    monitor = Monitor(
        group_id=group.id,
        slug=f"pi-test-{suffix}",
        name="Pi Test",
        token_hash=hash_password(_monitor_token(f"pi-test-{suffix}")),
        status=MonitorStatus.online,
    )
    network = Network(
        group_id=group.id,
        slug="wan",
        name="WAN",
        kind=NetworkKind.wired,
    )
    session.add_all([monitor, network])
    session.flush()
    test = Test(
        monitor_id=monitor.id,
        network_id=network.id,
        slug="http-cloudflare",
        name="HTTP Cloudflare",
        kind=TestKind.http,
        target="https://1.1.1.1",
    )
    session.add(test)
    session.commit()

    session.info["monitor_slug"] = monitor.slug
    session.info["group_id"] = group.id

    def _override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.clear()

    session.rollback()
    session.execute(text("DELETE FROM synthetic_samples WHERE run_id IN (SELECT id FROM synthetic_runs WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid))"), {"gid": group.id})
    session.execute(text("DELETE FROM synthetic_runs WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid)"), {"gid": group.id})
    session.execute(text("DELETE FROM system_logs WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid)"), {"gid": group.id})
    session.execute(text("DELETE FROM monitor_health WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid)"), {"gid": group.id})
    session.execute(text("DELETE FROM test_results WHERE test_id IN (SELECT id FROM tests WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid))"), {"gid": group.id})
    session.execute(text("DELETE FROM tests WHERE monitor_id IN (SELECT id FROM monitors WHERE group_id = :gid)"), {"gid": group.id})
    session.execute(text("DELETE FROM monitors WHERE group_id = :gid"), {"gid": group.id})
    session.execute(text("DELETE FROM networks WHERE group_id = :gid"), {"gid": group.id})
    session.execute(text("DELETE FROM groups WHERE id = :gid"), {"gid": group.id})
    session.execute(
        text("DELETE FROM audit_events WHERE action = 'guest_login' AND detail IN ('testclient', '203.0.113.5')")
    )
    session.execute(
        text("DELETE FROM guest_visits WHERE address IN ('testclient', '203.0.113.5') OR address LIKE '198.51.100.%'")
    )
    session.commit()
    session.close()


@pytest.fixture
def client(db) -> TestClient:
    return TestClient(app, base_url=AGENT_BASE_URL)


@pytest.fixture
def monitor_token(db) -> str:
    return _monitor_token(db.info["monitor_slug"])


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """A WireGuard state directory with a hub public key, as the hub container leaves it."""
    (tmp_path / "server.pub").write_text(_b64(X25519PrivateKey.generate().public_key().public_bytes_raw()) + "\n")
    monkeypatch.setattr(get_settings(), "wireguard_state_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "wireguard_endpoint", "192.0.2.10:51820")
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_agent_limits():
    nonces.clear()
    limiter.clear()
    login_failures.limit = 8
    login_failures.clear()
    yield
    nonces.clear()
    limiter.clear()
    login_failures.limit = 8
    login_failures.clear()


def login(client: TestClient, username: str = "admin", password: str = "admin-change-me") -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def new_installer_code() -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class FakeAgent:
    """Signs requests the way the remote agent on the Pi does."""

    def __init__(self, client: TestClient, installer_code: str, hostname: str = "remote-agent-a1b2") -> None:
        self.client = client
        self.installer_code = installer_code
        self.hostname = hostname
        self.lan_ip = "192.168.1.40"
        self.identity = Ed25519PrivateKey.generate()
        self.identity_public_key = _b64(
            self.identity.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        self.wg_public_key = _b64(X25519PrivateKey.generate().public_key().public_bytes_raw())
        self.wg_wireless_public_key = _b64(X25519PrivateKey.generate().public_key().public_bytes_raw())
        self.token: str | None = None

    def nonce(self):
        return self.client.post(NONCE_PATH, json={"identity_public_key": self.identity_public_key})

    def sign(self, path: str, nonce: str, fields: list[str]) -> str:
        return _b64(self.identity.sign(signed_message(path, nonce, [self.identity_public_key, *fields])))

    def check_in_body(self, nonce: str, signed: dict | None = None, tampered: dict | None = None) -> dict:
        """`signed` fields are covered by the signature; `tampered` fields are changed after signing."""
        body = {
            "identity_public_key": self.identity_public_key,
            "wg_public_key": self.wg_public_key,
            "wg_wireless_public_key": self.wg_wireless_public_key,
            "installer_code": self.installer_code,
            "hostname": self.hostname,
            "lan_ip": self.lan_ip,
            "nonce": nonce,
            **(signed or {}),
        }
        body["signature"] = self.sign(
            CHECK_IN_PATH,
            nonce,
            [
                body["wg_public_key"],
                *([body["wg_wireless_public_key"]] if body.get("wg_wireless_public_key") else []),
                body["installer_code"],
                body["hostname"],
                body["lan_ip"],
                *([body["platform"]] if body.get("platform") else []),
                *([body["device_name"]] if body.get("device_name") else []),
            ],
        )
        body.update(tampered or {})
        return body

    def check_in(self, signed: dict | None = None, tampered: dict | None = None):
        response = self.nonce()
        assert response.status_code == 200, response.text
        body = self.check_in_body(response.json()["nonce"], signed, tampered)
        return self.client.post(CHECK_IN_PATH, json=body)

    def poll(self, have_token: bool | None = None):
        response = self.nonce()
        assert response.status_code == 200, response.text
        nonce = response.json()["nonce"]
        has = self.token is not None if have_token is None else have_token
        body = {
            "identity_public_key": self.identity_public_key,
            "have_token": has,
            "nonce": nonce,
            "signature": self.sign(POLL_PATH, nonce, ["1" if has else "0"]),
        }
        result = self.client.post(POLL_PATH, json=body)
        if result.status_code == 200 and result.json().get("token"):
            self.token = result.json()["token"]
        return result
