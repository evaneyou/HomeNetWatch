"""Live speed test: the dashboard starts it and watches, the Pi runs it and reports every half second."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.common import _resolve_monitor_identity, require_agent_links
from app.auth import get_current_user, require_admin_csrf
from app.db import get_db
from app.models import Monitor, SpeedSample, SpeedTest, User

router = APIRouter(prefix="/api/v1")

DOWN_SECONDS = 15
UP_SECONDS = 15
PENDING_TIMEOUT = timedelta(seconds=60)
RUN_TIMEOUT = timedelta(seconds=DOWN_SECONDS + UP_SECONDS + 60)
MAX_SAMPLES = 200
MAX_MBPS = 20000.0
MAX_PER_HOUR = 20
LINKS = ("wired", "wireless")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StartIn(BaseModel):
    monitor_id: uuid.UUID
    link: str = "wired"


class SampleIn(BaseModel):
    seq: int = Field(ge=0, le=MAX_SAMPLES)
    phase: str = Field(pattern="^(download|upload)$")
    t_ms: int = Field(ge=0, le=600_000)
    mbps: float = Field(ge=0, le=MAX_MBPS)


class SamplesIn(BaseModel):
    samples: list[SampleIn] = Field(default_factory=list, max_length=20)
    ping_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    jitter_ms: Optional[float] = Field(default=None, ge=0, le=60_000)


class FinishIn(BaseModel):
    status: str = Field(pattern="^(finished|failed)$")
    down_mbps: Optional[float] = Field(default=None, ge=0, le=MAX_MBPS)
    up_mbps: Optional[float] = Field(default=None, ge=0, le=MAX_MBPS)
    ping_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    jitter_ms: Optional[float] = Field(default=None, ge=0, le=60_000)
    error: str = ""


def expire(db: Session, monitor_id: uuid.UUID) -> None:
    """A test the Pi never took, or never finished, is marked failed so it cannot block the next one."""
    now = _now()
    rows = db.scalars(select(SpeedTest).where(SpeedTest.monitor_id == monitor_id, SpeedTest.status.in_(("pending", "running")))).all()
    for row in rows:
        if row.status == "pending" and now - row.requested_at > PENDING_TIMEOUT:
            row.status, row.finished_at = "failed", now
            row.error = "The Pi did not pick up the test. It may be offline."
        elif row.status == "running" and row.started_at and now - row.started_at > RUN_TIMEOUT:
            row.status, row.finished_at = "failed", now
            row.error = row.error or "The test did not finish."
    db.flush()


def _out(row: SpeedTest) -> dict[str, Any]:
    return {
        "id": row.id,
        "monitor_id": row.monitor_id,
        "link": row.link,
        "status": row.status,
        "phase": row.phase,
        "requested_at": row.requested_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "down_seconds": row.down_seconds,
        "up_seconds": row.up_seconds,
        "down_mbps": row.down_mbps,
        "up_mbps": row.up_mbps,
        "ping_ms": row.ping_ms,
        "jitter_ms": row.jitter_ms,
        "error": row.error,
        "requested_by": row.requested_by,
    }


# ---- the dashboard

@router.post("/speedtest")
def start(
    body: StartIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    if body.link not in LINKS:
        raise HTTPException(status_code=400, detail="Choose the Wired or Wi-Fi link.")
    monitor = db.get(Monitor, body.monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    require_agent_links(monitor, [body.link])
    expire(db, monitor.id)
    open_test = db.scalar(select(SpeedTest).where(SpeedTest.monitor_id == monitor.id, SpeedTest.status.in_(("pending", "running"))))
    if open_test is not None:
        raise HTTPException(status_code=409, detail="A speed test is already running on this agent.")
    if monitor.last_seen_at is None or _now() - monitor.last_seen_at > timedelta(minutes=5):
        raise HTTPException(status_code=409, detail="The agent is offline, so it cannot run a speed test.")
    recent = db.scalar(select(func.count()).select_from(SpeedTest).where(SpeedTest.monitor_id == monitor.id, SpeedTest.requested_at >= _now() - timedelta(hours=1))) or 0
    if recent >= MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many speed tests in the last hour. Try again later.")
    row = SpeedTest(monitor_id=monitor.id, link=body.link, status="pending", requested_by=admin.username, down_seconds=DOWN_SECONDS, up_seconds=UP_SECONDS)
    db.add(row)
    db.commit()
    return _out(row)


@router.get("/speedtest/history")
def history(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: Optional[uuid.UUID] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict[str, Any]]:
    query = select(SpeedTest).where(SpeedTest.status == "finished")
    if monitor_id is not None:
        query = query.where(SpeedTest.monitor_id == monitor_id)
    return [_out(row) for row in db.scalars(query.order_by(desc(SpeedTest.requested_at)).limit(limit)).all()]


@router.get("/speedtest/active")
def active(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: uuid.UUID,
) -> dict[str, Any]:
    expire(db, monitor_id)
    db.commit()
    row = db.scalar(select(SpeedTest).where(SpeedTest.monitor_id == monitor_id, SpeedTest.status.in_(("pending", "running"))))
    return {"test": _out(row) if row is not None else None}


@router.get("/speedtest/{test_id}")
def read(
    test_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    since: Annotated[int, Query(ge=-1)] = -1,
) -> dict[str, Any]:
    row = db.get(SpeedTest, test_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Speed test not found")
    expire(db, row.monitor_id)
    db.commit()
    samples = db.scalars(select(SpeedSample).where(SpeedSample.test_id == row.id, SpeedSample.seq > since).order_by(SpeedSample.seq)).all()
    return {**_out(row), "samples": [{"seq": item.seq, "phase": item.phase, "t_ms": item.t_ms, "mbps": item.mbps} for item in samples]}


@router.post("/speedtest/{test_id}/cancel")
def cancel(
    test_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    row = db.get(SpeedTest, test_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Speed test not found")
    if row.status in ("pending", "running"):
        row.status, row.finished_at = "cancelled", _now()
        db.commit()
    return _out(row)


# ---- the Pi

def _mine(db: Session, test_id: uuid.UUID, monitor: Monitor) -> SpeedTest:
    row = db.get(SpeedTest, test_id)
    if row is None or row.monitor_id != monitor.id:
        raise HTTPException(status_code=404, detail="Speed test not found")
    return row


@router.get("/monitors/speedtest")
def next_test(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """The Pi asks every few seconds. A waiting test is handed out once, and marked running."""
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    expire(db, monitor.id)
    row = db.scalar(select(SpeedTest).where(SpeedTest.monitor_id == monitor.id, SpeedTest.status == "pending").order_by(SpeedTest.requested_at).limit(1))
    if row is None:
        db.commit()
        return {"test": None}
    row.status, row.started_at = "running", _now()
    db.commit()
    return {"test": {"id": str(row.id), "link": row.link, "down_seconds": row.down_seconds, "up_seconds": row.up_seconds}}


@router.post("/monitors/speedtest/{test_id}/samples")
def post_samples(
    test_id: uuid.UUID,
    body: SamplesIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, bool]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    row = _mine(db, test_id, monitor)
    if row.status == "cancelled":
        return {"cancel": True}
    if row.status != "running":
        raise HTTPException(status_code=409, detail="This speed test is not running.")
    if body.ping_ms is not None:
        row.ping_ms = body.ping_ms
        row.jitter_ms = body.jitter_ms
        if not row.phase:
            row.phase = "ping"
    for item in body.samples:
        if db.get(SpeedSample, (row.id, item.seq)) is None:
            db.add(SpeedSample(test_id=row.id, seq=item.seq, phase=item.phase, t_ms=item.t_ms, mbps=item.mbps))
        row.phase = item.phase
    db.commit()
    return {"cancel": False}


@router.post("/monitors/speedtest/{test_id}/finish")
def finish(
    test_id: uuid.UUID,
    body: FinishIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, str]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    row = _mine(db, test_id, monitor)
    if row.status in ("pending", "running"):
        failed = body.status == "failed" or (body.down_mbps is None and body.up_mbps is None)
        row.status = "failed" if failed else "finished"
        row.finished_at = _now()
        row.phase = "done"
        row.down_mbps, row.up_mbps = body.down_mbps, body.up_mbps
        if body.ping_ms is not None:
            row.ping_ms, row.jitter_ms = body.ping_ms, body.jitter_ms
        row.error = (body.error or ("The test produced no result." if failed and not body.error else ""))[:300]
        db.commit()
    return {"status": row.status}
