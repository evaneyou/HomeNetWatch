"""Stress tests: start, change while running, stop, and read live numbers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stress
from app.auth import get_current_user, require_admin_csrf
from app.db import get_db
from app.models import Monitor, StressRun, SyntheticRun, User
from app.synthetic import normalize_links

router = APIRouter(prefix="/api/v1/stress")


class SessionIn(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    count: int = Field(default=1, ge=1, le=stress.MAX_COUNT)


class StartIn(BaseModel):
    monitor_id: uuid.UUID
    links: list[str]
    duration_seconds: int = Field(ge=10, le=stress.MAX_SECONDS)
    sessions: list[SessionIn] = Field(min_length=1)


def _stress(db: Session, stress_id: uuid.UUID) -> StressRun:
    row = db.get(StressRun, stress_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Stress test not found")
    stress.expire(db, row)
    return row


def _add(db: Session, row: StressRun, item: SessionIn) -> None:
    try:
        options, label = stress.build_session(item.kind, item.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    options["label"] = label
    for _ in range(item.count):
        db.add(
            SyntheticRun(
                monitor_id=row.monitor_id,
                profile_slug=f"stress-{item.kind}",
                mode="stress",
                links=list(row.links),
                options=dict(options),
                status="running",
                finished_links=[],
                stress_id=row.id,
            )
        )


def _running_count(db: Session, row: StressRun) -> int:
    return sum(1 for run in stress.sessions_of(db, row) if run.status in ("running", "pending"))


@router.get("/catalog")
def get_catalog(_user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
    return stress.catalog()


@router.get("/active")
def active(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: uuid.UUID,
) -> dict[str, Any]:
    row = db.scalar(select(StressRun).where(StressRun.monitor_id == monitor_id, StressRun.status == "running"))
    if row is not None:
        stress.expire(db, row)
        db.commit()
    if row is None or row.status != "running":
        return {"stress_id": None}
    return {"stress_id": row.id}


@router.post("")
def start(
    body: StartIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    monitor = db.get(Monitor, body.monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    if monitor.platform == "android":
        # The Android agent runs the voice stream and the Speed Test only; it never picks up a stress job.
        raise HTTPException(status_code=400, detail="Network Test is not available on an Android agent.")
    try:
        links = normalize_links(body.links)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    open_row = db.scalar(select(StressRun).where(StressRun.monitor_id == body.monitor_id, StressRun.status == "running"))
    if open_row is not None:
        stress.expire(db, open_row)
        if open_row.status == "running":
            raise HTTPException(status_code=409, detail="A stress test is already running on this agent")
    if sum(item.count for item in body.sessions) > stress.MAX_SESSIONS:
        raise HTTPException(status_code=400, detail=f"At most {stress.MAX_SESSIONS} sessions")
    started = stress.now()
    row = StressRun(
        monitor_id=body.monitor_id,
        links=links,
        status="running",
        started_by=admin.username,
        started_at=started,
        ends_at=stress.end_time(started, body.duration_seconds),
    )
    db.add(row)
    db.flush()
    for item in body.sessions:
        _add(db, row, item)
    db.commit()
    return {"stress_id": row.id}


@router.post("/{stress_id}/sessions")
def add_sessions(
    stress_id: uuid.UUID,
    body: SessionIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, str]:
    row = _stress(db, stress_id)
    if row.status != "running":
        raise HTTPException(status_code=409, detail="This stress test has ended")
    if _running_count(db, row) + body.count > stress.MAX_SESSIONS:
        raise HTTPException(status_code=400, detail=f"At most {stress.MAX_SESSIONS} sessions at once")
    _add(db, row, body)
    db.commit()
    return {"status": "ok"}


@router.delete("/{stress_id}/sessions/{run_id}")
def remove_session(
    stress_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, str]:
    row = _stress(db, stress_id)
    run = db.get(SyntheticRun, run_id)
    if run is None or run.stress_id != row.id:
        raise HTTPException(status_code=404, detail="Session not found")
    stress.stop_session(run)
    db.commit()
    return {"status": run.status}


@router.post("/{stress_id}/stop")
def stop(
    stress_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, str]:
    row = _stress(db, stress_id)
    stress.stop_all(db, row)
    db.commit()
    return {"status": row.status}


@router.get("/{stress_id}/live")
def get_live(
    stress_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    since: Optional[datetime] = None,
) -> dict[str, Any]:
    row = _stress(db, stress_id)
    db.commit()
    return stress.live(db, row, since)
