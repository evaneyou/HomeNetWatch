"""The Cloudflare Speed Test: hourly, on the wired link of a Pi, graded without a variance."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enrollment import new_token
from app.internet_tests import CLOUDFLARE_SLUG, ensure_internet_tests, matrix_column, scheduled_tests
from app.models import Monitor, MonitorStatus, ResultStatus, Test, TestResult
from conftest import login


def _make_monitor(db, platform: str = "pi") -> tuple[Monitor, str]:
    token, hashed = new_token()
    monitor = Monitor(
        group_id=db.info["group_id"],
        slug=f"cf-{uuid.uuid4().hex[:8]}",
        name=f"Cloudflare {uuid.uuid4().hex[:6]}",
        token_hash=hashed,
        status=MonitorStatus.unknown,
        platform=platform,
    )
    db.add(monitor)
    db.commit()
    return monitor, token


def _assigned(db, monitor: Monitor) -> list[dict]:
    ensure_internet_tests(db, monitor)
    db.commit()
    return [item for item in scheduled_tests(db, monitor) if item["slug"] == CLOUDFLARE_SLUG]


def _result(down: float | None, up: float | None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": f"cf-{uuid.uuid4().hex}",
        "test_slug": CLOUDFLARE_SLUG,
        "network_slug": "wired",
        "started_at": (now - timedelta(seconds=20)).isoformat(),
        "finished_at": now.isoformat(),
        "status": "ok",
        "stages": [{"name": "transfer", "duration_ms": 19000.0, "status": "ok"}],
        "metrics": {"down_mbps": down, "up_mbps": up, "rtt_ms": 14.0, "jitter_ms": 1.0, "loss_pct": 0.0},
        "message": "speed.cloudflare.com",
    }


def test_a_pi_gets_the_test_on_its_wired_link_only(db):
    monitor, _ = _make_monitor(db)
    assigned = _assigned(db, monitor)
    assert len(assigned) == 1
    item = assigned[0]
    # Agents from before this test skip a kind they do not know, so it is not sent as "librespeed".
    assert item["kind"] == "cloudflare"
    assert item["target"] == "speed.cloudflare.com"
    assert item["network_slug"] == "wired"
    assert item["link"] == "wired"
    assert item["interval_seconds"] == 3600
    networks = {
        test.network.slug
        for test in db.scalars(select(Test).where(Test.monitor_id == monitor.id, Test.slug == CLOUDFLARE_SLUG))
    }
    assert networks == {"wired"}


def test_an_android_agent_does_not_get_the_test(db):
    monitor, _ = _make_monitor(db, platform="android")
    assert _assigned(db, monitor) == []


def test_the_test_stays_out_of_the_overview_and_its_alerts():
    assert matrix_column("wired", CLOUDFLARE_SLUG) is None


def test_results_are_graded_by_whether_each_direction_answered(client: TestClient, db):
    monitor, token = _make_monitor(db)
    _assigned(db, monitor)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"results": [_result(381.2, 37.4), _result(0.0, 36.0), _result(None, None)]}
    posted = client.post("/api/v1/ingest", json=body, headers=headers)
    assert posted.status_code == 200, posted.text
    assert posted.json()["accepted"] == 3
    rows = {
        row.external_id: row
        for row in db.scalars(select(TestResult).join(Test).where(Test.monitor_id == monitor.id, Test.slug == CLOUDFLARE_SLUG))
    }
    good, one_way, none = (rows[item["id"]] for item in body["results"])
    assert good.status == ResultStatus.ok
    assert "381.2" in (good.status_reason or "") and "Cloudflare" in (good.status_reason or "")
    assert one_way.status == ResultStatus.degraded
    assert none.status == ResultStatus.fail


def test_admin_can_turn_it_off_and_change_how_often_it_runs(client: TestClient, db):
    monitor, _ = _make_monitor(db)
    csrf = login(client)
    current = client.get("/api/v1/settings/internet-service").json()
    assert current["cloudflare_enabled"] is True
    assert current["cloudflare_interval_seconds"] == 3600
    try:
        # Free public service: nothing faster than every 15 minutes.
        too_often = client.put("/api/v1/settings/internet-service", json={**current, "cloudflare_interval_seconds": 600}, headers=csrf)
        assert too_often.status_code == 422

        quarter = client.put("/api/v1/settings/internet-service", json={**current, "cloudflare_interval_seconds": 900}, headers=csrf)
        assert quarter.status_code == 200, quarter.text
        assert _assigned(db, monitor)[0]["interval_seconds"] == 900

        slower = client.put("/api/v1/settings/internet-service", json={**current, "cloudflare_interval_seconds": 7200}, headers=csrf)
        assert slower.status_code == 200, slower.text
        assert _assigned(db, monitor)[0]["interval_seconds"] == 7200

        # A dashboard from before this setting sends neither field. What is saved stays.
        old = {key: value for key, value in current.items() if not key.startswith("cloudflare_")}
        kept = client.put("/api/v1/settings/internet-service", json=old, headers=csrf)
        assert kept.status_code == 200, kept.text
        assert kept.json()["cloudflare_interval_seconds"] == 7200

        off = client.put("/api/v1/settings/internet-service", json={**current, "cloudflare_enabled": False}, headers=csrf)
        assert off.status_code == 200, off.text
        db.expire_all()
        assert _assigned(db, monitor) == []
    finally:
        client.put("/api/v1/settings/internet-service", json=current, headers=csrf)
