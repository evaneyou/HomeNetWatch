"""A link row raised only by its web or app checks says so. No database."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.readings_routes import _attribute_root_causes
from app.schemas import HealthCell


def _setup(wifi: dict[str, str], wired: dict[str, str]):
    group = uuid4()
    now = datetime.now(timezone.utc)
    # Both tunnels are set up and shook hands a moment ago, so nothing here reads as a dead tunnel.
    monitor = SimpleNamespace(
        id=uuid4(),
        group_id=group,
        last_seen_at=now,
        status=SimpleNamespace(value="online"),
        health_interval_seconds=60,
        wg_address="10.7.0.2",
        wg_wireless_address="10.7.1.2",
        last_handshake_at=now,
        last_wireless_handshake_at=now,
    )
    networks = [SimpleNamespace(id=uuid4(), group_id=group, slug=slug) for slug in ("wifi", "wired")]
    cells = {}
    for network, statuses in zip(networks, (wifi, wired)):
        for column, status in statuses.items():
            cells[(monitor.id, network.id, column)] = HealthCell(
                monitor_id=monitor.id,
                monitor_slug="pi",
                monitor_name="Pi",
                network_id=network.id,
                network_slug=network.slug,
                network_name=network.slug,
                column=column,
                status=status,
            )
    diagnoses = _attribute_root_causes([monitor], networks, cells, now)
    wifi_net = networks[0]
    return cells[(monitor.id, wifi_net.id, "internet")], diagnoses[str(monitor.id)]


OK = {"internet": "ok", "web": "ok", "apps": "ok"}


def test_wifi_raised_only_by_its_web_checks_is_marked_promoted():
    cell, diagnosis = _setup(dict(OK, web="degraded"), OK)
    assert (cell.status, cell.cause, cell.promoted) == ("degraded", "wifi", True)
    assert "path to the server itself is passing" in diagnosis["summary"]
    assert "packet loss" not in diagnosis["summary"]


def test_wifi_whose_own_path_is_bad_is_not_promoted():
    cell, diagnosis = _setup(dict(OK, internet="degraded"), OK)
    assert (cell.status, cell.cause, cell.promoted) == ("degraded", "wifi", False)
    assert "packet loss" in diagnosis["summary"]
