import socket
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.path_view import clean_trace_target, resolved_public_target
from tests.conftest import login
from tests.test_ingest import _make_monitor, _token_headers


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Example.com", "example.com"),
        ("https://www.example.com/a/b?x=1", "www.example.com"),
        ("example.com:8443/path", "example.com"),
        ("8.8.8.8", "8.8.8.8"),
        ("2001:4860:4860::8888", "2001:4860:4860::8888"),
        ("192.168.1.1", None),
        ("10.0.0.5", None),
        ("127.0.0.1", None),
        ("169.254.1.1", None),
        ("224.0.0.1", None),
        ("localhost", None),
        ("not a host", None),
        ("", None),
        ("-bad.example.com", None),
    ],
)
def test_trace_target_is_a_public_host(raw, expected):
    assert clean_trace_target(raw) == expected


def _answer(ip: str):
    """Answer every name with `ip`, except the database host: this patch is process-wide, so a
    new database connection made during the test would otherwise be sent to the fake address."""
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host in ("db", "localhost"):
            return real(host, *args, **kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake


def test_a_name_that_resolves_inside_is_refused():
    assert resolved_public_target("192.168.1.1") is None
    assert resolved_public_target("8.8.8.8") == ("8.8.8.8", "8.8.8.8")


def test_a_name_is_resolved_once_to_its_public_ipv4_address(monkeypatch):
    def answers(host, *args, **kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr("app.path_view.socket.getaddrinfo", answers)
    assert resolved_public_target("example.com") == ("example.com", "93.184.216.34")


def test_a_name_with_only_ipv6_addresses_is_refused(monkeypatch):
    def answers(host, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::1", 0, 0, 0))]

    monkeypatch.setattr("app.path_view.socket.getaddrinfo", answers)
    assert resolved_public_target("v6only.example.com") is None


def test_trace_on_demand_round_trip(client: TestClient, db, monkeypatch):
    monkeypatch.setattr("app.path_view.socket.getaddrinfo", _answer("93.184.216.34"))
    monitor, token = _make_monitor(db, f"Trace {uuid.uuid4().hex[:6]}")
    headers = _token_headers(token)
    admin = login(client)

    refused = client.post(f"/api/v1/monitors/{monitor.id}/traces", json={"link": "wired", "target": "192.168.1.1"}, headers=admin)
    assert refused.status_code == 422

    viewer = TestClient(app, base_url="https://testserver")
    login(viewer, "viewer", "viewer-change-me")
    assert viewer.post(f"/api/v1/monitors/{monitor.id}/traces", json={"link": "wired", "target": "example.com"}).status_code == 403

    queued = client.post(f"/api/v1/monitors/{monitor.id}/traces", json={"link": "wireless", "target": "https://Example.com/x"}, headers=admin)
    assert queued.status_code == 200, queued.text
    trace_id = queued.json()["id"]
    assert queued.json()["target"] == "example.com"

    again = client.post(f"/api/v1/monitors/{monitor.id}/traces", json={"link": "wired", "target": "example.org"}, headers=admin)
    assert again.status_code == 409

    waiting = client.get(f"/api/v1/monitors/{monitor.id}/traces/{trace_id}")
    assert waiting.json()["state"] == "pending"

    handed = client.get("/api/v1/monitors/command", headers=headers).json()["traces"]
    # The agent gets the address the server checked, so a name can't answer differently the second time.
    assert handed == [{"id": trace_id, "target": "93.184.216.34", "link": "wireless"}]
    assert client.get("/api/v1/monitors/command", headers=headers).json()["traces"] == []

    hops = [
        {"hop": 1, "host": "192.168.1.1", "loss_pct": 0, "avg_ms": 1.0, "best_ms": 0.8, "worst_ms": 1.4},
        {"hop": 2, "host": "93.184.216.34", "loss_pct": 0, "avg_ms": 12.0, "best_ms": 11.0, "worst_ms": 13.0},
    ]
    done = client.post(f"/api/v1/monitors/traces/{trace_id}/result", json={"hops": hops}, headers=headers)
    assert done.status_code == 200, done.text
    body = client.get(f"/api/v1/monitors/{monitor.id}/traces/{trace_id}").json()
    assert body["state"] == "done"
    assert len(body["hops"]) == 2
    assert body["target"]["name"] == "example.com"


def test_trace_refuses_a_hostname_that_resolves_inside(client: TestClient, db, monkeypatch):
    monkeypatch.setattr("app.path_view.socket.getaddrinfo", _answer("10.1.2.3"))
    monitor, _token = _make_monitor(db, f"Inside {uuid.uuid4().hex[:6]}")
    admin = login(client)
    refused = client.post(
        f"/api/v1/monitors/{monitor.id}/traces",
        json={"link": "wired", "target": "printer.example.com"},
        headers=admin,
    )
    assert refused.status_code == 422


def test_trace_result_needs_the_agent_that_was_asked(client: TestClient, db, monkeypatch):
    monkeypatch.setattr("app.path_view.socket.getaddrinfo", _answer("93.184.216.34"))
    monitor, _token = _make_monitor(db, f"Own {uuid.uuid4().hex[:6]}")
    other, other_token = _make_monitor(db, f"Other {uuid.uuid4().hex[:6]}")
    admin = login(client)
    queued = client.post(f"/api/v1/monitors/{monitor.id}/traces", json={"link": "wired", "target": "example.com"}, headers=admin)
    trace_id = queued.json()["id"]
    wrong = client.post(f"/api/v1/monitors/traces/{trace_id}/result", json={"hops": []}, headers=_token_headers(other_token))
    assert wrong.status_code == 404
