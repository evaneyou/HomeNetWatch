from fastapi.testclient import TestClient

import pytest

from app.api import auth_routes
from app.auth import ACCOUNT_FAILURE_LIMIT, LOGIN_FAILURE_LIMIT, account_failures, login_failures
from app.config import get_settings
from app.main import create_app


def test_a_missing_name_and_a_wrong_password_look_the_same(client: TestClient):
    missing = client.post("/api/v1/auth/login", json={"username": "nobody-xyz", "password": "wrong-password"})
    wrong = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"] == "Invalid credentials"


def test_failures_from_one_address_stop_being_accepted(client: TestClient):
    login_failures.limit = 3
    for _ in range(2):
        denied = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        assert denied.status_code == 401
    blocked = client.post("/api/v1/auth/login", json={"username": "nobody-xyz", "password": "nope"})
    assert blocked.status_code == 429
    still = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-change-me"})
    assert still.status_code == 429
    login_failures.clear()
    login_failures.limit = LOGIN_FAILURE_LIMIT
    accepted = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-change-me"})
    assert accepted.status_code == 200, accepted.text


@pytest.fixture
def untrusted_peer(monkeypatch):
    """The client is not the proxy, as on the agent port or through the tunnel."""
    monkeypatch.setattr(get_settings(), "trusted_proxies", "caddy-only-in-production")
    monkeypatch.setattr(auth_routes, "_proxy_cache", (float("-inf"), frozenset()))
    yield
    monkeypatch.setattr(auth_routes, "_proxy_cache", (float("-inf"), frozenset()))


def test_a_forged_forwarded_address_does_not_reset_the_limit(client: TestClient, untrusted_peer):
    login_failures.limit = 3
    for index in range(2):
        denied = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "nope"},
            headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
        )
        assert denied.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "nope"},
        headers={"X-Forwarded-For": "198.51.100.200"},
    )
    assert blocked.status_code == 429


def test_the_proxy_forwarded_address_is_used_from_the_proxy(client: TestClient):
    login_failures.limit = 2
    for _ in range(2):
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "nope"},
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
    other = client.post(
        "/api/v1/auth/login",
        json={"username": "nobody-xyz", "password": "nope"},
        headers={"X-Forwarded-For": "198.51.100.8"},
    )
    assert other.status_code == 401


def test_one_account_is_limited_across_addresses(client: TestClient):
    for index in range(ACCOUNT_FAILURE_LIMIT):
        denied = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "nope"},
            headers={"X-Forwarded-For": f"203.0.113.{index % 250 + 1}"},
        )
        assert denied.status_code in (401, 429)
    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "ADMIN", "password": "admin-change-me"},
        headers={"X-Forwarded-For": "192.0.2.77"},
    )
    assert blocked.status_code == 429
    account_failures.clear()
    accepted = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-change-me"},
        headers={"X-Forwarded-For": "192.0.2.77"},
    )
    assert accepted.status_code == 200, accepted.text


def test_the_agent_port_serves_only_enrollment():
    agent = TestClient(create_app(agent_api_only=True), base_url="https://testserver")
    paths = {getattr(route, "path", "") for route in agent.app.routes}
    assert paths and all(path.startswith("/api/v1/agents/") for path in paths)
    assert agent.post("/api/v1/auth/login", json={"username": "admin", "password": "x"}).status_code == 404
    assert agent.get("/api/v1/monitors").status_code == 404
    assert agent.get("/openapi.json").status_code == 404
    assert agent.get("/docs").status_code == 404
    # The enrollment endpoints are still there (a malformed key is refused, not missing).
    assert agent.post("/api/v1/agents/nonce", json={"identity_public_key": "x"}).status_code in (400, 422)
