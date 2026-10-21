"""Synthetic traffic stays off the tunnel and reports from stored samples."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from app.auth import hash_password
from app.models import Issue, IssueStatus, Monitor, SyntheticRun, SyntheticSample, User, UserRole
from app.synthetic import build_report, is_blackout, is_tunnel_target, launch_plan, public_host, send_args
from conftest import login


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Monitor-Name": quote("Pi Test", safe="")}


def _monitor(db) -> Monitor:
    monitor = db.scalar(select(Monitor).where(Monitor.slug == db.info["monitor_slug"]))
    assert monitor is not None
    return monitor


def test_tunnel_addresses_are_rejected():
    assert is_tunnel_target("10.7.0.1")
    assert is_tunnel_target("10.7.1.2")
    assert not is_tunnel_target("203.0.113.10")
    try:
        public_host("10.7.0.1:51820")
    except ValueError as exc:
        assert "tunnel" in str(exc)
    else:
        raise AssertionError("a tunnel endpoint was accepted")
    assert public_host("203.0.113.10:51820") == "203.0.113.10"


def test_public_target_uses_the_physical_nic():
    plan = launch_plan("wired", "203.0.113.10", "eth0")
    assert plan["interface"] == "eth0"
    for target in ("10.7.0.1", "10.7.1.1"):
        try:
            launch_plan("wired", target, "eth0")
        except ValueError as exc:
            assert "tunnel" in str(exc)
        else:
            raise AssertionError(target)
    try:
        launch_plan("wireless", "203.0.113.10", None)
    except ValueError as exc:
        assert "physical NIC" in str(exc)
    else:
        raise AssertionError("missing NIC")
    secret = "ab" * 30
    args = send_args(
        "rtnp-send",
        {
            "preset": "voip-g711a",
            "wire": "rtp",
            "rtp": "g711a",
            "ptime_ms": 20,
            "bidir": True,
            "rtcp_interval_s": 5,
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
        "203.0.113.10",
        control_host="10.7.0.1",
        key_file="/tmp/rtnp.key",
        srtp_key_file="/tmp/rtnp-srtp.key",
        forever=True,
    )
    assert "203.0.113.10:8998" in args
    assert "10.7.0.1:8999" in args
    assert "--meter" not in args
    assert secret not in args
    assert "--srtp-key-file" in args
    try:
        send_args(
            "rtnp-send",
            {"preset": "fortnite", "meter": "rtt", "report_interval_s": 10, "max_rtt_ms": 300, "max_jitter_ms": 30, "max_loss_pct": 1},
            "203.0.113.10",
            control_host="10.7.0.1",
            key_file="/tmp/rtnp.key",
            srtp_key_file="/tmp/rtnp-srtp.key",
            forever=True,
        )
    except ValueError as exc:
        assert "reflection" in str(exc)
    else:
        raise AssertionError("meter rtt was accepted")


def test_agent_module_matches_the_path_rule():
    path = Path(__file__).resolve().parents[2] / "monitor" / "synthetic.py"
    if not path.is_file():
        return
    spec = importlib.util.spec_from_file_location("monitor_synthetic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    assert module.launch_plan("wired", "203.0.113.10", "end0")["interface"] == "end0"
    try:
        module.launch_plan("wired", "10.7.0.1", "end0")
    except ValueError:
        pass
    else:
        raise AssertionError("agent accepted a tunnel target")
    assert module.route_is_physical("203.0.113.10 dev end0 src 192.0.2.10", "end0")
    assert not module.route_is_physical("10.7.0.1 dev wg0 src 10.7.0.2", "end0")
    original_is_file = module.Path.is_file

    def deny_wireguard_config(self, *args, **kwargs):
        if "wireguard" in str(self):
            raise PermissionError(13, "Permission denied", str(self))
        return original_is_file(self, *args, **kwargs)

    module.Path.is_file = deny_wireguard_config
    try:
        assert module.tunnel_warning() == ""
        assert "Permission denied" not in module._endpoint_text()
    finally:
        module.Path.is_file = original_is_file
    module._tunnel_text = lambda: "AllowedIPs = 0.0.0.0/0"
    assert "0.0.0.0/0" in module.tunnel_warning()
    module._tunnel_text = lambda: "AllowedIPs = 10.7.0.1/32"
    assert module.tunnel_warning() == ""
    started: list = []
    module.tunnel_warning = lambda: ""
    module._ensure = lambda *args: started.append(args)
    module._over_budget = lambda: False
    module._write_key = lambda *_args: None
    module._write_srtp = lambda *_args: None
    module._running.clear()

    def get_json(*_args):
        return {
            "target_host": "203.0.113.50",
            "jobs": [
                {"run_id": "1", "link": "wired", "options": {}},
                {"run_id": "2", "link": "wireless", "options": {}},
            ]
        }

    module.sync(
        {"server_url": "http://example.test", "token": "t", "name": "n", "measurement_host": "203.0.113.10"},
        get_json,
        lambda *_args: None,
    )
    assert len(started) == 2
    assert {item[1][1] for item in started} == {"wired", "wireless"}
    assert {item[3] for item in started} == {"203.0.113.50"}
    # A link carries every check asked of it, and continuous ones are ordered first.
    chosen = module.jobs_for_links(
        [
            {"run_id": "manual", "link": "wired", "mode": "manual"},
            {"run_id": "continuous", "link": "wired", "mode": "continuous"},
            {"run_id": "wifi", "link": "wireless", "mode": "continuous"},
        ]
    )
    assert set(chosen) == {("manual", "wired"), ("continuous", "wired"), ("wifi", "wireless")}
    assert list(chosen)[0] == ("continuous", "wired")
    # The same run on the same link is still one flow.
    repeated = module.jobs_for_links(
        [
            {"run_id": "continuous", "link": "wired", "mode": "continuous"},
            {"run_id": "continuous", "link": "wired", "mode": "continuous"},
        ]
    )
    assert set(repeated) == {("continuous", "wired")}
    assert module.route_get_args("203.0.113.10", "192.0.2.10") == [
        "ip",
        "route",
        "get",
        "203.0.113.10",
        "from",
        "192.0.2.10",
    ]


def test_agent_key_writes_skip_unchanged_files(tmp_path: Path):
    path = Path(__file__).resolve().parents[2] / "monitor" / "synthetic.py"
    if not path.is_file():
        return
    spec = importlib.util.spec_from_file_location("monitor_synthetic_keys", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    module.KEY_PATH = tmp_path / "rtnp.key"
    module.SRTP_PATH = tmp_path / "rtnp-srtp.key"
    module._write_key("secret-key")
    key_inode = module.KEY_PATH.stat().st_ino
    module._write_key("secret-key")
    assert module.KEY_PATH.stat().st_ino == key_inode
    srtp_hex = "ab" * 30
    module._write_srtp(srtp_hex)
    srtp_inode = module.SRTP_PATH.stat().st_ino
    module._write_srtp(srtp_hex)
    assert module.SRTP_PATH.stat().st_ino == srtp_inode


def test_recommended_profiles_and_comparison_report(client, db, monitor_token):
    if db.scalar(select(User).where(User.username == "admin")) is None:
        db.add(User(username="admin", password_hash=hash_password("admin-change-me"), role=UserRole.admin, is_active=True))
        db.commit()
    monitor = _monitor(db)
    headers = login(client)
    listed = client.get(f"/api/v1/synthetic/profiles?monitor_id={monitor.id}")
    assert listed.status_code == 200, listed.text
    slugs = [item["slug"] for item in listed.json()["profiles"]]
    assert "voip-g711a" in slugs
    assert "fortnite" in slugs
    voice = next(item for item in listed.json()["profiles"] if item["slug"] == "voip-g711a")
    assert voice["category"] == "voice"
    assert voice["options"]["report_interval_s"] == 10
    assert voice["options"]["max_rtt_ms"] == 300

    created = client.post(
        "/api/v1/synthetic/manual",
        headers=headers,
        json={
            "monitor_id": str(monitor.id),
            "profile_slug": "voip-g711a",
            "links": ["wired", "wireless"],
            "duration_seconds": 30,
            "options": {"max_rtt_ms": 300, "max_jitter_ms": 30, "max_loss_pct": 1},
        },
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    for link, rtt in (("wired", 20), ("wireless", 400)):
        sample = client.post(
            "/api/v1/synthetic/samples",
            headers=_headers(monitor_token),
            json={
                "run_id": run_id,
                "link": link,
                "interval_index": 1,
                "recorded_at": datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc).isoformat(),
                "metrics": {
                    "type": "interval",
                    "index": 1,
                    "flows": [
                        {
                            "rtt": {"avg_ms": rtt, "jitter_ms": 2},
                            "loss_pct": 0,
                            "verdict": {"pass": rtt < 50},
                        }
                    ],
                },
            },
        )
        assert sample.status_code == 200, sample.text
    report = client.get(f"/api/v1/synthetic/runs/{run_id}/report")
    assert report.status_code == 200, report.text
    columns = {item["label"]: item for item in report.json()["links"]}
    assert set(columns) == {"Wired", "Wi-Fi"}
    assert columns["Wired"]["verdict"] == "pass"
    assert columns["Wi-Fi"]["verdict"] == "fail"
    stored = db.get(SyntheticRun, report.json()["run_id"])
    assert stored is not None
    rows = db.scalars(select(SyntheticSample).where(SyntheticSample.run_id == stored.id)).all()
    assert len(rows) == 2
    again = build_report(stored, list(rows), "Voice G.711")
    assert [item["label"] for item in again["links"]] == ["Wired", "Wi-Fi"]

    wired_only = client.post(
        "/api/v1/synthetic/manual",
        headers=headers,
        json={
            "monitor_id": str(monitor.id),
            "profile_slug": "fortnite",
            "links": ["wired"],
            "duration_seconds": 20,
            "options": {},
        },
    )
    assert wired_only.status_code == 200, wired_only.text
    wired_id = wired_only.json()["id"]
    ingested = client.post(
        "/api/v1/synthetic/samples",
        headers=_headers(monitor_token),
        json={
            "run_id": wired_id,
            "link": "wired",
            "interval_index": 1,
            "recorded_at": datetime(2026, 10, 13, 15, 1, tzinfo=timezone.utc).isoformat(),
            "metrics": {
                "type": "interval",
                "index": 1,
                "flows": [{"rtt": {"avg_ms": 18, "jitter_ms": 1}, "loss_pct": 0, "verdict": {"pass": True}}],
            },
        },
    )
    assert ingested.status_code == 200, ingested.text
    wired_report = client.get(f"/api/v1/synthetic/runs/{wired_id}/report")
    assert wired_report.status_code == 200, wired_report.text
    assert [item["label"] for item in wired_report.json()["links"]] == ["Wired"]
    assert wired_report.json()["links"][0]["verdict"] == "pass"

    detail = client.get(f"/api/v1/monitors/{monitor.id}")
    assert detail.status_code == 200, detail.text
    assert [item["slug"] for item in detail.json()["probes"]] == ["voip-g711a", "fortnite"]
    voice_log = client.get(f"/api/v1/monitors/{monitor.id}/log?probe=rtnp:voip-g711a")
    assert voice_log.status_code == 200, voice_log.text
    assert voice_log.json()["total"] == 2
    repeated = client.post(
        "/api/v1/synthetic/samples",
        headers=_headers(monitor_token),
        json={
            "run_id": run_id,
            "link": "wireless",
            "interval_index": 1,
            "recorded_at": datetime(2026, 10, 13, 15, 5, tzinfo=timezone.utc).isoformat(),
            "metrics": {
                "type": "interval",
                "index": 1,
                "flows": [
                    {
                        "rtt": {"avg_ms": 40, "jitter_ms": 2},
                        "loss_pct": 0,
                        "verdict": {"pass": True},
                    }
                ],
            },
        },
    )
    assert repeated.status_code == 200, repeated.text
    voice_again = client.get(f"/api/v1/monitors/{monitor.id}/log?probe=rtnp:voip-g711a")
    assert voice_again.json()["total"] == 3
    by_network = {item["network_slug"]: item for item in voice_log.json()["items"]}
    assert set(by_network) == {"wired", "wifi"}
    assert by_network["wifi"]["test_name"] == "Voice G.711"
    assert by_network["wifi"]["status"] == "fail"
    assert by_network["wifi"]["metrics"]["rtt_ms"] == 400
    assert by_network["wired"]["status"] == "ok"
    combined = client.get(f"/api/v1/monitors/{monitor.id}/log?probe=rtnp")
    assert combined.status_code == 200, combined.text
    assert combined.json()["total"] == 4
    assert client.get(f"/api/v1/monitors/{monitor.id}/log?probe=rtnp:not-a-probe").status_code == 400


def test_guest_cannot_start_a_check_or_see_the_home_address(client, db, monitor_token):
    from app.models import UserRole

    if db.scalar(select(User).where(User.username == "guest")) is None:
        db.add(User(username="guest", password_hash=hash_password("guest"), role=UserRole.guest, is_active=True))
        db.commit()
    monitor = _monitor(db)
    monitor.public_ip = "203.0.113.50"
    db.commit()
    guest = login(client, "guest", "guest")
    listed = client.get("/api/v1/monitors", headers=guest)
    assert listed.status_code == 200
    mine = next(item for item in listed.json() if item["id"] == str(monitor.id))
    assert mine["public_ip"] == "###.#.###.##"
    denied = client.put(
        "/api/v1/synthetic/continuous",
        headers=guest,
        json={"monitor_id": str(monitor.id), "profile_slug": "voip-g711a", "links": ["wired"], "options": {}},
    )
    assert denied.status_code == 403
    manual = client.post(
        "/api/v1/synthetic/manual",
        headers=guest,
        json={
            "monitor_id": str(monitor.id),
            "profile_slug": "voip-g711a",
            "links": ["wired"],
            "duration_seconds": 30,
            "options": {},
        },
    )
    assert manual.status_code == 403
    turned_off = client.put(
        "/api/v1/synthetic/continuous",
        headers=guest,
        json={"monitor_id": str(monitor.id), "profile_slug": "voip-g711a", "links": [], "options": {}},
    )
    assert turned_off.status_code == 403
    scan = client.post(f"/api/v1/rf/scan-now?agent_id={monitor.id}", headers=guest)
    assert scan.status_code == 403


def test_presets_group_by_category_and_skip_empty_or_unknown():
    from app.synthetic import PROFILES, group_profiles

    groups = group_profiles(PROFILES)
    assert [group["category"] for group in groups] == ["voice", "video", "enterprise", "games"]
    assert [item["slug"] for item in groups[0]["profiles"]] == ["voip-g711a", "voip-g729", "voip-g723"]
    assert [item["slug"] for item in groups[2]["profiles"]] == ["remote-desktop", "market-data"]
    assert [item["slug"] for item in groups[3]["profiles"]] == ["fortnite", "call-of-duty"]
    without_video = [item for item in PROFILES if item.get("category") != "video"]
    without_video.append({"slug": "other", "category": "satellite"})
    grouped = group_profiles(without_video)
    assert [group["category"] for group in grouped] == ["voice", "enterprise", "games"]
    assert all(item["slug"] != "other" for group in grouped for item in group["profiles"])


def test_out_of_range_settings_are_rejected(client, db):
    if db.scalar(select(User).where(User.username == "admin")) is None:
        db.add(User(username="admin", password_hash=hash_password("admin-change-me"), role=UserRole.admin, is_active=True))
        db.commit()
    monitor = _monitor(db)
    headers = login(client)
    base = {"monitor_id": str(monitor.id), "profile_slug": "voip-g711a", "links": ["wired"]}
    rejected = [
        {"report_interval_s": 0},
        {"report_interval_s": 301},
        {"max_rtt_ms": 0},
        {"max_rtt_ms": 10001},
        {"max_jitter_ms": -1},
        {"max_jitter_ms": 1001},
        {"max_loss_pct": -0.1},
        {"max_loss_pct": 100.1},
        {"ptime_ms": 9},
        {"ptime_ms": 201},
    ]
    for options in rejected:
        response = client.put(
            "/api/v1/synthetic/continuous",
            headers=headers,
            json={**base, "options": options},
        )
        assert response.status_code == 400, options
    for duration in (4, 3601):
        response = client.post(
            "/api/v1/synthetic/manual",
            headers=headers,
            json={**base, "duration_seconds": duration, "options": {}},
        )
        assert response.status_code == 422, duration


def test_srtp_key_is_a_file_and_never_an_argument(tmp_path, monkeypatch):
    from uuid import uuid4

    from app.config import get_settings
    from app.wireguard_tunnel import ensure_srtp_key

    (tmp_path / "wg").mkdir()
    (tmp_path / "rtnp").mkdir()
    monkeypatch.setenv("WIREGUARD_STATE_DIR", str(tmp_path / "wg"))
    monkeypatch.setenv("RTNP_STATE_DIR", str(tmp_path / "rtnp"))
    get_settings.cache_clear()
    try:
        monitor_id = uuid4()
        key = ensure_srtp_key(monitor_id)
        assert key is not None and len(key) == 30
        assert ensure_srtp_key(monitor_id) == key
        stored = (tmp_path / "wg" / "srtp" / f"{monitor_id}.bin").read_bytes()
        assert key not in stored
        published = (tmp_path / "rtnp" / "keys" / str(monitor_id)).read_bytes()
        assert published == key
        args = send_args(
            "rtnp-send",
            {
                "preset": "voip-g711a",
                "wire": "rtp",
                "bidir": True,
                "report_interval_s": 10,
                "max_rtt_ms": 300,
                "max_jitter_ms": 30,
                "max_loss_pct": 1,
            },
            "203.0.113.10",
            control_host="10.7.0.1",
            key_file="/tmp/rtnp.key",
            srtp_key_file="/tmp/rtnp-srtp.key",
            forever=True,
        )
        rendered = " ".join(args)
        assert key.hex() not in rendered
        assert "--srtp-key-file" in args
    finally:
        get_settings.cache_clear()


def test_guest_middleware_hashes_the_streamed_json_body(monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from app import redact as redact_mod
    from app.redact import GuestAnonymizeMiddleware

    monkeypatch.setattr(redact_mod, "_request_is_guest", lambda _request: True)
    mini = FastAPI()
    mini.add_middleware(GuestAnonymizeMiddleware)

    @mini.get("/probe")
    def probe():
        response = JSONResponse({"public_ip": "203.0.113.9", "rtt_ms": 12})
        response.set_cookie("hns_session", "refreshed", max_age=180, path="/")
        return response

    client = TestClient(mini)
    response = client.get("/probe")
    assert response.status_code == 200, response.text
    assert response.json() == {"public_ip": "###.#.###.#", "rtt_ms": 12}
    assert response.cookies.get("hns_session") == "refreshed"


def test_guest_redaction_drops_markings_and_leaves_admin_results():
    from app.redact import redact

    payload = {
        "public_ip": "203.0.113.9",
        "lan_ip": "",
        "latitude": 40.1,
        "hostname": "living-room",
        "metrics": {"tos": 184, "dscp": 46, "rtt_ms": 20},
        "aps": [{"ssid": "Home", "bssid": "aa:bb"}, {"ssid": "Cafe", "bssid": "cc:dd"}],
    }
    hidden = redact(payload, UserRole.guest)
    assert hidden["public_ip"] == "###.#.###.#"
    assert hidden["lan_ip"] == ""
    assert hidden["latitude"] == "##.#"  # no monitor_id, so this is not the probe's own place
    assert hidden["hostname"] == "######-####"
    assert hidden["metrics"] == {"rtt_ms": 20}
    assert hidden["aps"][0]["ssid"] == "####"
    assert hidden["aps"][0]["bssid"] == "##:##"
    assert hidden["aps"][0]["anon_key"] != hidden["aps"][1]["anon_key"]
    probe = {
        "monitor_id": "m1",
        "location_label": "Living room",
        "region": "New York",
        "country": "United States",
        "latitude": 40.75,
        "longitude": -73.98,
        "public_ip": "203.0.113.9",
    }
    shown = redact(probe, UserRole.guest)
    assert shown["location_label"] == "Living room"
    assert shown["region"] == "New York"
    assert shown["latitude"] == 40.75
    assert shown["public_ip"] == "###.#.###.#"
    visitor = redact({"country": "Canada", "region": "Ontario", "city": "Toronto"}, UserRole.guest)
    assert visitor["country"] == "######"
    assert visitor["region"] == "#######"
    visible = redact(payload, UserRole.admin)
    assert visible["metrics"]["tos"] == 184
    assert visible["public_ip"] == "203.0.113.9"


def test_allowlist_keeps_ipv4_peer_endpoints_only():
    from app.rtnp_allowlist import endpoints_from_wg, nft_commands

    text = "\n".join(
        [
            "peerA 203.0.113.10:51820",
            "peerB (none)",
            "peerC [2001:db8::5]:51820",
            "peerD 203.0.113.10:51820",
        ]
    )
    assert endpoints_from_wg(text) == ["203.0.113.10"]
    empty = [" ".join(command) for command in nft_commands([])]
    assert any(command.endswith("drop") for command in empty)
    assert not any("accept" in command for command in empty)
    assert not any("203.0.113.10" in command for command in empty)
    assert any("jump RTNP_IN" in command for command in empty)
    allowed = [" ".join(command) for command in nft_commands(["203.0.113.10"], "jump RTNP_IN", "jump RTNP_IN")]
    assert any("203.0.113.10" in command and "rtnp_sources" in command for command in allowed)
    assert any("@rtnp_sources accept" in command for command in allowed)
    assert any(command.endswith("drop") for command in allowed)
    assert not any("jump RTNP_IN" in command for command in allowed)


def test_agent_restarts_a_flow_that_loses_every_packet():
    path = Path(__file__).resolve().parents[2] / "monitor" / "synthetic.py"
    spec = importlib.util.spec_from_file_location("monitor_synthetic_flow", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    assert module.flow_state({"rx": None, "loss_pct": None}) == "missing"
    assert module.flow_state({"rx": 0, "loss_pct": 100.0}) == "lost"
    assert module.flow_state({"rx": 100, "loss_pct": 0.4}) == ""
    # a few lost packets are not an outage
    assert module.flow_state({"rx": 90, "loss_pct": 10.0}) == ""
    assert module.SILENT_INTERVALS > module.MISSING_INTERVALS
    assert module.restart_pending(1, 2) is True
    assert module.restart_pending(2, 2) is False
    assert module.restart_pending(None, 0) is False


def test_blackout_needs_no_timing_and_a_partial_loss_is_not_one():
    assert is_blackout(0, 0, 100)
    assert is_blackout(None, None, 100)
    assert not is_blackout(80, 0, 100)
    assert not is_blackout(0, 4, 100)
    assert not is_blackout(20, 2, 1)


def _post_sample(client, token, run_id, link, when, rtt, jitter, loss, passed, index):
    return client.post(
        "/api/v1/synthetic/samples",
        headers=_headers(token),
        json={
            "run_id": str(run_id),
            "link": link,
            "interval_index": index,
            "recorded_at": when.isoformat(),
            "metrics": {
                "type": "interval",
                "index": index,
                "flows": [
                    {
                        "rtt": {"avg_ms": rtt, "jitter_ms": jitter},
                        "loss_pct": loss,
                        "verdict": {"pass": passed},
                    }
                ],
            },
        },
    )


def test_a_blackout_beside_a_healthy_check_is_a_software_issue(client, db, monitor_token):
    monitor = _monitor(db)
    voice = SyntheticRun(
        monitor_id=monitor.id, profile_slug="voip-g711a", mode="continuous", links=["wireless"], options={}, status="running", finished_links=[]
    )
    video = SyntheticRun(
        monitor_id=monitor.id,
        profile_slug="video-conference",
        mode="continuous",
        links=["wireless"],
        options={},
        status="running",
        finished_links=[],
    )
    alone = SyntheticRun(
        monitor_id=monitor.id, profile_slug="video-conference", mode="continuous", links=["wired"], options={}, status="running", finished_links=[]
    )
    db.add_all([voice, video, alone])
    db.commit()
    start = datetime(2026, 10, 19, 23, 20, tzinfo=timezone.utc)
    healthy = _post_sample(client, monitor_token, voice.id, "wireless", start, 40, 2, 0, True, 1)
    assert healthy.status_code == 200, healthy.text
    marked = _post_sample(client, monitor_token, video.id, "wireless", start + timedelta(seconds=10), 0, 0, 100, False, 1)
    assert marked.status_code == 200, marked.text
    db.expire_all()
    stored = db.scalar(select(SyntheticSample).where(SyntheticSample.run_id == video.id))
    assert stored is not None
    assert stored.verdict == "software"
    assert stored.metrics.get("impact") == "software"
    issue = db.scalar(select(Issue).where(Issue.monitor_id == monitor.id, Issue.root_cause_stage == "software"))
    assert issue is not None and issue.status == IssueStatus.open
    assert "Wi-Fi" in issue.title
    assert db.get(SyntheticRun, video.id).options["software_restarts"]["wireless"]["token"] == 1

    again = _post_sample(client, monitor_token, video.id, "wireless", start + timedelta(seconds=40), 0, 0, 100, False, 2)
    assert again.status_code == 200, again.text
    db.expire_all()
    assert db.get(SyntheticRun, video.id).options["software_restarts"]["wireless"]["token"] == 1
    later = _post_sample(client, monitor_token, video.id, "wireless", start + timedelta(minutes=3), 0, 0, 100, False, 3)
    assert later.status_code == 200, later.text
    db.expire_all()
    assert db.get(SyntheticRun, video.id).options["software_restarts"]["wireless"]["token"] == 2

    recovered = _post_sample(client, monitor_token, video.id, "wireless", start + timedelta(minutes=4), 90, 4, 0, True, 4)
    assert recovered.status_code == 200, recovered.text
    db.expire_all()
    issue = db.scalar(select(Issue).where(Issue.monitor_id == monitor.id, Issue.root_cause_stage == "software"))
    assert issue.status == IssueStatus.resolved

    real = _post_sample(client, monitor_token, alone.id, "wired", start, 0, 0, 100, False, 1)
    assert real.status_code == 200, real.text
    db.expire_all()
    wired = db.scalar(select(SyntheticSample).where(SyntheticSample.run_id == alone.id))
    assert wired is not None and wired.verdict == "fail"

    if db.scalar(select(User).where(User.username == "admin")) is None:
        db.add(User(username="admin", password_hash=hash_password("admin-change-me"), role=UserRole.admin, is_active=True))
        db.commit()
    log = client.get(f"/api/v1/monitors/{monitor.id}/log?probe=rtnp:video-conference", headers=login(client))
    assert log.status_code == 200, log.text
    items = log.json()["items"]
    assert any(item["network_slug"] == "wifi" and item["status"] == "software" for item in items)
    assert any(item["network_slug"] == "wired" and item["status"] == "fail" for item in items)
    assignment = client.get("/api/v1/synthetic/assignment", headers=_headers(monitor_token))
    assert assignment.status_code == 200, assignment.text
    job = next(item for item in assignment.json()["jobs"] if item["run_id"] == str(video.id))
    assert job["restart_token"] == 2


def test_health_uses_the_running_checks_limits(client, db):
    monitor = _monitor(db)
    if db.scalar(select(User).where(User.username == "admin")) is None:
        db.add(User(username="admin", password_hash=hash_password("admin-change-me"), role=UserRole.admin, is_active=True))
        db.commit()
    db.add(
        SyntheticRun(
            monitor_id=monitor.id,
            profile_slug="voip-g711a",
            mode="continuous",
            links=["wired"],
            options={"max_rtt_ms": 250, "max_jitter_ms": 20, "max_loss_pct": 0.5},
            status="running",
            finished_links=[],
        )
    )
    db.commit()
    body = client.get(f"/api/v1/synthetic/health?monitor_id={monitor.id}&link=wired", headers=login(client))
    assert body.status_code == 200, body.text
    voice = next(item for item in body.json()["types"] if item["slug"] == "voip-g711a")
    assert voice["limits"]["max_rtt_ms"] == 250
    assert voice["limits"]["max_jitter_ms"] == 20
    assert voice["limits"]["max_loss_pct"] == 0.5
