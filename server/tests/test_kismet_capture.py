"""Kismet mapping, the airwave spool, and rf-capture ingest. No live Kismet."""

import importlib.util
import io
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import func, select

from app.models import ApInventory, Issue, IssueStatus, RfAlert, RfClient, WifiObservation


def _load(name: str, filename: str):
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "monitor" / filename, Path("/monitor") / filename]
    path = next(item for item in candidates if item.is_file())
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


AP = "11:22:33:44:55:66"
CLIENT = "02:aa:bb:cc:dd:01"
OTHER = "aa:bb:cc:dd:ee:10"
CLIENT_BSSID = "11:22:33:44:55:66"

DEVICES = [
    {
        "kismet.device.base.macaddr": AP,
        "kismet.device.base.name": "Home",
        "kismet.device.base.type": "Wi-Fi AP",
        "kismet.device.base.channel": "6",
        "kismet.device.base.frequency": 2_437_000,
        "kismet.device.base.signal": {"kismet.common.signal.last_signal": -42},
        "dot11.device": {"dot11.device.num_associated_clients": 2},
    },
    {
        "kismet.device.base.macaddr": CLIENT,
        "kismet.device.base.manuf": "Apple",
        "kismet.device.base.type": "Wi-Fi Client",
        "kismet.device.base.frequency": 2_437_000,
        "kismet.device.base.channel": "6",
        "kismet.device.base.signal": {
            "kismet.common.signal.last_signal": -60,
            "kismet.common.signal.min_signal": -68,
            "kismet.common.signal.max_signal": -55,
        },
        "kismet.device.base.packets.total": 12,
        "kismet.device.base.datasize": 400,
        "kismet.device.base.first_time": 1_699_999_000,
        "kismet.device.base.last_time": 1_700_000_000,
        "dot11.device": {
            "dot11.device.last_bssid": AP,
            "dot11.device.num_retries": 3,
            "dot11.device.probed_ssid_map": [{"dot11.probedssid.ssid": "Cafe"}, {"dot11.probedssid.ssid": ""}],
        },
    },
    {
        "kismet.device.base.macaddr": OTHER,
        "kismet.device.base.type": "Wi-Fi Client",
        "kismet.device.base.frequency": 5_180_000,
        "kismet.device.base.channel": "36",
        "kismet.device.base.signal": -70,
        "dot11.device": {"dot11.device.last_bssid": "aa:bb:cc:dd:ee:99"},
    },
    {
        "kismet.device.base.macaddr": "10:20:30:40:50:61",
        "kismet.device.base.type": "Wi-Fi Client",
        "kismet.device.base.frequency": 2_437_000,
        "kismet.device.base.channel": "6",
        "dot11.device": {"dot11.device.last_bssid": AP},
    },
    {
        "kismet.device.base.macaddr": "10:20:30:40:50:60",
        "kismet.device.base.type": "Wi-Fi AP",
        "kismet.device.base.frequency": 5_955_000,
        "kismet.device.base.channel": "1",
        "kismet.device.base.signal": -40,
    },
]


def test_kismet_json_maps_aps_clients_channels_and_alerts():
    view = _load("kismet_view", "kismet_view.py")
    mapped = view.map_devices(DEVICES)
    assert mapped["aps"] == [
        {"bssid": AP.lower(), "ssid": "Home", "band": "2.4", "channel": 6, "signal": -42.0, "client_count": 2}
    ]
    assert len(mapped["clients"]) == 3
    home_client = mapped["clients"][0]
    assert home_client["randomized"] is True
    assert home_client["vendor"] == "Apple"
    assert home_client["channel"] == 6
    assert home_client["packets"] == 12
    assert home_client["data_bytes"] == 400
    assert home_client["signal_min"] == -68
    assert home_client["probed_ssids"] == ["Cafe"]
    assert home_client["retries"] == 3
    kept = view.visible_clients(mapped["clients"], {AP})
    assert [item["mac"] for item in kept] == [CLIENT.lower(), "10:20:30:40:50:61"]
    channels = view.map_channels(
        view.channel_rows(
            {
                "kismet.channeltracker.frequency_map": {
                    "2437000": {
                        "kismet.channelrec.channel": "6",
                        "kismet.channelrec.frequency": 2_437_000,
                        "kismet.channelrec.packets_rrd": {"kismet.common.rrd.minute_vec": [1, 40]},
                        "kismet.channelrec.device_rrd": {"kismet.common.rrd.minute_vec": [2]},
                    }
                }
            }
        )
    )
    assert channels[0]["airtime_pct"] is None
    assert channels[0]["packets"] == 41
    assert channels[0]["devices"] == 2
    quiet = ""
    link = "Survey data from wlan0\n\tfrequency:\t2437 MHz\n\tchannel active time:\t200 ms\n\tchannel busy time:\t50 ms\n"
    filled = view.with_busy(channels, view.survey_channels(view.prefer_survey(quiet, link)))
    assert filled[0]["busy_pct"] == 25.0
    alerts = view.map_alerts(
        [
            {
                "kismet.alert.header": "DEAUTHFLOOD",
                "kismet.alert.text": "flood",
                "kismet.alert.source_mac": AP,
                "kismet.alert.timestamp": 10.5,
                "kismet.alert.hash": 42,
                "kismet.alert.frequency": 2_437_000,
            }
        ]
    )
    assert alerts[0]["type"] == "DEAUTHFLOOD"
    assert alerts[0]["bssid"] == AP.lower()
    assert alerts[0]["ts"] == 10
    assert alerts[0]["hash"] == "42"
    other = view.map_alerts([{"kismet.alert.header": "APSPOOF", "kismet.alert.other_mac": OTHER}])
    assert other[0]["bssid"] == OTHER.lower()
    assert view.iw_scan_skipped("kismet", "hnwradio0") is True
    assert view.iw_scan_skipped("kismet", None) is False
    assert view.iw_scan_skipped("scan", "hnwradio0") is False
    assert view.iw_scan_skipped("off", None) is False


def test_device_fields_are_a_string_or_a_pair():
    view = _load("kismet_view_fields", "kismet_view.py")
    assert view.DEVICE_FIELDS
    for entry in view.DEVICE_FIELDS:
        assert isinstance(entry, str) or (isinstance(entry, list) and len(entry) == 2)


def test_rrd_minute_vec_sums_packets_wherever_the_bucket_sits():
    view = _load("kismet_view_rrd", "kismet_view.py")
    packets = [0] * 60
    devices = [0] * 60
    packets[7] = 15
    devices[7] = 3
    channels = view.map_channels(
        view.channel_rows(
            {
                "kismet.channeltracker.frequency_map": {
                    "2437000": {
                        "kismet.channelrec.channel": "6",
                        "kismet.channelrec.frequency": 2_437_000,
                        "kismet.channelrec.packets_rrd": {"kismet.common.rrd.minute_vec": packets},
                        "kismet.channelrec.device_rrd": {"kismet.common.rrd.minute_vec": devices},
                    }
                }
            }
        )
    )
    assert channels[0]["packets"] == 15
    assert channels[0]["devices"] == 3
    assert packets[-1] == 0


def test_kismet_http_failure_is_kept_on_the_agent(tmp_path: Path):
    here = Path(__file__).resolve()
    path = next(item for item in (here.parents[2] / "monitor" / "agent.py", Path("/monitor/agent.py")) if item.is_file())
    sys.path.insert(0, str(path.parent))
    for name in ("agent_state", "enrollment", "health", "radio", "scheduler"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("monitor_agent_kismet_error", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    creds = tmp_path / "kismet.json"
    creds.write_text('{"url":"http://127.0.0.1:2501","username":"k","password":"p"}', encoding="utf-8")
    import agent_state
    import radio

    agent_state.KISMET_PATH = creds
    agent_state._last_error = ""

    def reject(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:2501/devices.json",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"invalid field"}'),
        )

    radio.kismet_get = reject
    radio.kismet_once({"server_url": "http://127.0.0.1", "token": "t", "name": "Pi"})
    assert agent_state._last_error.startswith("Kismet HTTP 400:")
    assert "invalid field" in agent_state._last_error
    assert agent_state._airwave_mode == "off"


def test_real_kismet_json_maps_an_ap_a_channel_and_an_alert():
    view = _load("kismet_view_capture", "kismet_view.py")
    devices = [
        {
            "kismet.device.base.macaddr": AP,
            "kismet.device.base.name": "Home",
            "kismet.device.base.type": "Wi-Fi AP",
            "kismet.device.base.channel": "6",
            "kismet.device.base.frequency": 2_437_000,
            "kismet.device.base.signal": {"kismet.common.signal.last_signal": -42},
            "dot11.device": {"dot11.device.num_associated_clients": 1},
        }
    ]
    channels = {
        "kismet.channeltracker.frequency_map": {
            "5180000": {
                "kismet.channelrec.channel": "36",
                "kismet.channelrec.frequency": 5_180_000,
                "kismet.channelrec.packets_rrd": {"kismet.common.rrd.minute_vec": [0, 9]},
                "kismet.channelrec.device_rrd": {"kismet.common.rrd.minute_vec": [1, 4]},
            }
        }
    }
    alerts = [
        {
            "kismet.alert.header": "DEAUTHFLOOD",
            "kismet.alert.text": "flood",
            "kismet.alert.transmitter_mac": AP,
            "kismet.alert.timestamp": 1_700_000_200.125,
            "kismet.alert.hash": "abc123",
            "kismet.alert.frequency": 2_437_000,
        }
    ]
    mapped = view.map_kismet_capture(devices, channels, alerts)
    assert mapped["aps"][0]["bssid"] == AP.lower()
    assert mapped["aps"][0]["channel"] == 6
    assert mapped["channels"] == [
        {"band": "5", "channel": 36, "airtime_pct": None, "packets": 9, "devices": 4, "busy_pct": None}
    ]
    assert mapped["alerts"][0]["bssid"] == AP.lower()
    assert mapped["alerts"][0]["hash"] == "abc123"


def test_spool_keeps_five_days_and_caps_size(tmp_path: Path):
    spool = _load("rf_spool", "rf_spool.py")
    path = tmp_path / "rf-capture.sqlite"
    now = time.time()
    spool.MAX_BYTES = 40
    spool.push(path, {"old": True}, now=now - spool.MAX_AGE_SECONDS - 5)
    spool.push(path, {"n": 1}, now=now)
    assert [row[1] for row in spool.pending(path)] == [{"n": 1}]
    spool.MAX_BYTES = 10
    spool.push(path, {"n": 2}, now=now)
    assert [row[1] for row in spool.pending(path)] == [{"n": 2}]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Monitor-Name": quote("Pi Test", safe="")}


def _body(captured: datetime) -> dict:
    return {
        "captured_at": captured.isoformat(),
        "aps": [
            {"bssid": AP, "ssid": "Home", "band": "2.4", "channel": 6, "signal": -40, "client_count": 2},
        ],
        "clients": [
            {
                "mac": CLIENT,
                "bssid": AP,
                "signal": -55,
                "signal_min": -68,
                "signal_max": -50,
                "last_seen": 1_700_000_000,
                "first_seen": 1_699_999_000,
                "randomized": True,
                "vendor": "Apple",
                "band": "2.4",
                "channel": 6,
                "packets": 12,
                "data_bytes": 400,
                "retries": 3,
                "probed_ssids": ["Cafe"],
            },
            {"mac": OTHER, "bssid": "aa:bb:cc:dd:ee:99", "signal": -70, "last_seen": 1_700_000_100, "randomized": False},
        ],
        "channels": [{"band": "2.4", "channel": 6, "airtime_pct": 18.0, "packets": 90}],
        "alerts": [
            {"type": "DEAUTHFLOOD", "bssid": AP, "text": "Deauth flood on Home", "ts": 1_700_000_200},
            {"type": "DEAUTHFLOOD", "bssid": "aa:bb:cc:dd:ee:99", "text": "ignored", "ts": 1_700_000_200},
            {"type": "NOCLIENTMFP", "bssid": AP, "text": "Client does not support management frame protection", "ts": 1_700_000_201},
        ],
    }


def test_rf_capture_is_idempotent_and_keeps_trusted_clients(client, db, monitor_token):
    captured = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)
    from app.models import Monitor

    monitor = db.scalar(select(Monitor).where(Monitor.slug == db.info["monitor_slug"]))
    assert monitor is not None
    monitor_id = monitor.id
    db.add(
        ApInventory(
            monitor_id=monitor_id,
            bssid=AP.lower(),
            trusted=True,
            label="Home",
            first_seen=captured,
            last_seen=captured,
        )
    )
    db.commit()
    first = client.post("/api/v1/monitors/rf-capture", headers=_headers(monitor_token), json=_body(captured))
    assert first.status_code == 200, first.text
    again = client.post("/api/v1/monitors/rf-capture", headers=_headers(monitor_token), json=_body(captured))
    assert again.status_code == 200, again.text
    db.expire_all()
    clients = db.scalars(select(RfClient).where(RfClient.monitor_id == monitor_id)).all()
    assert [row.mac for row in clients] == [CLIENT.lower()]
    assert clients[0].vendor == "Apple"
    assert clients[0].channel == 6
    assert clients[0].packets == 12
    assert clients[0].probed_ssids == '["Cafe"]'
    obs = db.scalar(select(WifiObservation).where(WifiObservation.monitor_id == monitor_id, WifiObservation.bssid == AP.lower()))
    assert obs is not None
    assert obs.client_count == 2
    assert db.scalar(select(func.count()).select_from(RfAlert).where(RfAlert.monitor_id == monitor_id)) == 3
    issues = db.scalars(select(Issue).where(Issue.monitor_id == monitor_id, Issue.status == IssueStatus.open)).all()
    assert len(issues) == 1
    assert issues[0].root_cause_stage == "airwave"
    assert issues[0].severity.value == "critical"
    assert "DEAUTHFLOOD" in issues[0].title
    later = captured.replace(minute=5)
    third = client.post("/api/v1/monitors/rf-capture", headers=_headers(monitor_token), json=_body(later))
    assert third.status_code == 200, third.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Issue).where(Issue.monitor_id == monitor_id)) == 1
