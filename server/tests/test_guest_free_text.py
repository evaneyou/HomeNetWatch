"""A guest never receives an SSID, BSSID or home address, even inside a sentence such as an issue title."""

import json
import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import redact as redact_module
from app.models import Issue, IssueSeverity, IssueStatus, Monitor, UserRole, WifiObservation
from app.redact import Known, redact, scrub_text
from tests.test_guest_login import _ensure_guest

SSID = "Quokka-Home-5G"
BSSID = "a4:2b:b0:11:22:33"


def test_text_hides_macs_private_and_known_addresses_and_known_names():
    known = Known(names=re.compile(r"(?<![A-Za-z0-9])(?:Quokka\-Home\-5G)(?![A-Za-z0-9])"), addresses=frozenset({"203.0.113.40"}))
    text = f"Possible evil twin {SSID} on {BSSID}, gateway 192.168.1.1 via 100.72.0.9, home 203.0.113.40, dns 8.8.8.8"
    hidden = scrub_text(text, known)
    for secret in (SSID, BSSID, "192.168.1.1", "100.72.0.9", "203.0.113.40"):
        assert secret not in hidden
    assert hidden.startswith("Possible evil twin ") and "8.8.8.8" in hidden
    assert "##:##:##:##:##:##" in hidden
    # A known name only matches as a whole word, so "Quokka-Home-5Gx" is left alone.
    assert scrub_text(f"{SSID}x", known) == f"{SSID}x"


def test_redact_scrubs_free_text_for_guests_only():
    payload = {"title": f"Airwave deauth on {BSSID}", "items": [{"message": "Joined 10.0.0.7"}]}
    hidden = redact(payload, UserRole.guest)
    assert BSSID not in json.dumps(hidden) and "10.0.0.7" not in json.dumps(hidden)
    assert redact(payload, UserRole.admin) == payload


def test_guest_issue_titles_and_logs_hide_wifi_names_and_bssids(client: TestClient, db, monkeypatch):
    _ensure_guest(db)
    monitor = db.scalar(select(Monitor).where(Monitor.slug == db.info["monitor_slug"]))
    assert monitor is not None
    now = datetime.now(timezone.utc)
    db.add(
        WifiObservation(
            monitor_id=monitor.id,
            scanned_at=now - timedelta(minutes=5),
            ssid=SSID,
            bssid=BSSID,
            vendor="Test",
            band="5",
            channel=36,
            channel_width=80,
            rssi_dbm=-50.0,
            security="WPA2",
            phy="ax",
            max_link_rate_mbps=1200,
        )
    )
    db.add(
        Issue(
            group_id=monitor.group_id,
            monitor_id=monitor.id,
            title=f"Possible evil twin {SSID} on {BSSID}",
            summary=f"An access point named {SSID} at {BSSID} is not one you trust.",
            severity=IssueSeverity.warning,
            status=IssueStatus.open,
            root_cause_stage="airwave",
            opened_at=now - timedelta(minutes=1),
        )
    )
    db.commit()
    monkeypatch.setattr(redact_module, "_known_cache", (float("-inf"), Known()))

    guest = TestClient(client.app, base_url="https://testserver")
    signed_in = guest.post("/api/v1/auth/login", json={"username": "guest", "password": "guest"})
    assert signed_in.status_code == 200, signed_in.text
    for path in ("/api/v1/issues", "/api/v1/logs/unified?limit=500"):
        response = guest.get(path)
        assert response.status_code == 200, response.text
        body = response.text
        assert SSID not in body, path
        assert BSSID not in body and BSSID.upper() not in body, path
    # The sentence is still there, with the names hidden.
    titles = [item["title"] for item in guest.get("/api/v1/issues").json() if "evil twin" in item["title"]]
    assert titles and all(title.startswith("Possible evil twin ") for title in titles)
