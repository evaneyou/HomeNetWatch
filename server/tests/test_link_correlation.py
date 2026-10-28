"""Access plane vs measurement-tunnel correlation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.link_correlation import (
    access_met_target,
    correlate_link,
    diagnose_from_links,
    tunnel_backed_only_missed,
    tunnel_is_stale,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def _monitor(**kwargs):
    defaults = {
        "wg_address": "10.7.0.2",
        "wg_wireless_address": "10.7.1.2",
        "last_handshake_at": NOW - timedelta(minutes=1),
        "last_wireless_handshake_at": NOW - timedelta(minutes=1),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_tunnel_stale_when_handshake_old():
    mon = _monitor(last_wireless_handshake_at=NOW - timedelta(minutes=20))
    assert tunnel_is_stale(mon, "wireless", NOW) is True
    assert tunnel_is_stale(mon, "wired", NOW) is False


def test_correlate_tunnel_down_when_access_ok():
    mon = _monitor(last_wireless_handshake_at=NOW - timedelta(minutes=30))
    corr = correlate_link(mon, "wireless", access_ok=True, now=NOW)
    assert corr.status == "tunnel_down"
    assert "tunnel" in corr.detail.lower() or "WireGuard" in corr.detail or "measurement" in corr.detail.lower()


def test_correlate_link_down_when_access_bad():
    mon = _monitor(last_wireless_handshake_at=NOW - timedelta(minutes=30))
    corr = correlate_link(mon, "wireless", access_ok=False, now=NOW)
    assert corr.status == "link_down"


def test_kpi_diagnosis_tunnel_when_web_ok_voice_bad():
    targets = {"internet": 99.95, "voice": 99.9, "video": 99.9, "web": 99.9, "loss_pct": 0.1}
    links = {
        "internet": {
            "wired": {"availability": 100.0},
            "wireless": {"availability": 100.0},
        },
        "web": {
            "wired": {"availability": 100.0},
            "wireless": {"availability": 100.0},
        },
        "voice": {
            "wired": {"availability": 100.0, "loss_avg": 0.0},
            "wireless": {"availability": 10.0, "loss_avg": 90.0},
        },
        "video": {
            "wired": {"availability": 100.0, "loss_avg": 0.0},
            "wireless": {"availability": 5.0, "loss_avg": 95.0},
        },
    }
    assert tunnel_backed_only_missed(["voice", "video", "loss"]) is True
    assert access_met_target(links, "wireless", targets) is True
    diag = diagnose_from_links(links, targets)
    assert diag is not None and diag["cause"] == "tunnel"


def test_kpi_diagnosis_still_wifi_when_web_also_misses():
    targets = {"internet": 99.95, "voice": 99.9, "video": 99.9, "web": 99.9, "loss_pct": 0.1}
    links = {
        "internet": {
            "wired": {"availability": 100.0},
            "wireless": {"availability": 100.0},
        },
        "web": {
            "wired": {"availability": 100.0},
            "wireless": {"availability": 90.0},
        },
        "voice": {
            "wired": {"availability": 100.0, "loss_avg": 0.0},
            "wireless": {"availability": 80.0, "loss_avg": 2.5},
        },
        "video": {},
    }
    diag = diagnose_from_links(links, targets)
    assert diag is not None and diag["cause"] == "wifi"
