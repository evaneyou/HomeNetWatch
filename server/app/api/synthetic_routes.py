"""Dashboard and agent endpoints for synthetic traffic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import _resolve_monitor_identity
from app.auth import get_current_user, require_admin_csrf
from app.config import get_settings
from app.db import get_db
from app.models import Monitor, StressRun, SyntheticProfile, SyntheticRun, SyntheticSample, User
from app.synthetic import (
    apply_options,
    build_report,
    ensure_key,
    ensure_profiles,
    metrics_from_interval,
    normalize_links,
    profile_category,
    public_host,
    range_start,
)
from app import stress as stress_module
from app.redact import redact
from app.wireguard_tunnel import ensure_srtp_key

router = APIRouter(prefix="/api/v1/synthetic")


class OptionsIn(BaseModel):
    report_interval_s: Optional[int] = None
    max_rtt_ms: Optional[int] = None
    max_jitter_ms: Optional[int] = None
    max_loss_pct: Optional[float] = None
    ptime_ms: Optional[int] = None


class ContinuousIn(BaseModel):
    monitor_id: uuid.UUID
    profile_slug: str
    links: list[str] = Field(default_factory=list)
    options: OptionsIn = Field(default_factory=OptionsIn)


class ManualIn(BaseModel):
    monitor_id: uuid.UUID
    profile_slug: str
    links: list[str]
    duration_seconds: int = Field(ge=5, le=3600)
    options: OptionsIn = Field(default_factory=OptionsIn)


class SampleIn(BaseModel):
    run_id: uuid.UUID
    link: str
    interval_index: int = Field(ge=0)
    recorded_at: datetime
    metrics: dict[str, Any]


class FinishIn(BaseModel):
    link: str
    status: str = "finished"
    message: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile(db: Session, slug: str) -> SyntheticProfile:
    ensure_profiles(db)
    row = db.get(SyntheticProfile, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown traffic type")
    return row


def _monitor(db: Session, monitor_id: uuid.UUID) -> Monitor:
    row = db.get(Monitor, monitor_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    return row


def _options(profile: SyntheticProfile, incoming: OptionsIn) -> dict[str, Any]:
    try:
        return apply_options(profile, incoming.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_out(run: SyntheticRun, name: str) -> dict[str, Any]:
    return {
        "id": run.id,
        "monitor_id": run.monitor_id,
        "profile_slug": run.profile_slug,
        "name": name,
        "mode": run.mode,
        "links": run.links,
        "options": run.options,
        "duration_seconds": run.duration_seconds,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.get("/profiles")
def list_profiles(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    profiles = ensure_profiles(db)
    db.commit()
    runs = []
    if monitor_id is not None:
        _monitor(db, monitor_id)
        runs = db.scalars(
            select(SyntheticRun).where(
                SyntheticRun.monitor_id == monitor_id,
                SyntheticRun.mode == "continuous",
                SyntheticRun.status == "running",
            )
        ).all()
    running = {run.profile_slug: run for run in runs}
    names = {row.slug: row.name for row in profiles}
    return {
        "profiles": [
            {
                "slug": row.slug,
                "name": row.name,
                "category": profile_category(row.slug),
                "options": row.options,
                "continuous": _run_out(running[row.slug], names[row.slug]) if row.slug in running else None,
            }
            for row in profiles
        ]
    }


@router.put("/continuous")
def save_continuous(
    body: ContinuousIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    _monitor(db, body.monitor_id)
    profile = _profile(db, body.profile_slug)
    options = _options(profile, body.options)
    try:
        links = normalize_links(body.links) if body.links else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = db.scalar(
        select(SyntheticRun).where(
            SyntheticRun.monitor_id == body.monitor_id,
            SyntheticRun.profile_slug == body.profile_slug,
            SyntheticRun.mode == "continuous",
            SyntheticRun.status == "running",
        )
    )
    if not links:
        if run is not None:
            run.status = "stopped"
            run.finished_at = _now()
        db.commit()
        return {"status": "stopped"}
    if run is None:
        run = SyntheticRun(
            monitor_id=body.monitor_id,
            profile_slug=profile.slug,
            mode="continuous",
            links=links,
            options=options,
            status="running",
            finished_links=[],
        )
        db.add(run)
    else:
        run.links = links
        run.options = options
    db.commit()
    return _run_out(run, profile.name)


@router.post("/manual")
def start_manual(
    body: ManualIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    _monitor(db, body.monitor_id)
    profile = _profile(db, body.profile_slug)
    options = _options(profile, body.options)
    try:
        links = normalize_links(body.links)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = SyntheticRun(
        monitor_id=body.monitor_id,
        profile_slug=profile.slug,
        mode="manual",
        links=links,
        options=options,
        duration_seconds=body.duration_seconds,
        status="pending",
        finished_links=[],
    )
    db.add(run)
    db.commit()
    return _run_out(run, profile.name)


@router.get("/runs")
def list_runs(
    monitor_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    _monitor(db, monitor_id)
    ensure_profiles(db)
    names = {row.slug: row.name for row in db.scalars(select(SyntheticProfile)).all()}
    runs = db.scalars(
        select(SyntheticRun)
        .where(SyntheticRun.monitor_id == monitor_id, SyntheticRun.mode == "manual")
        .order_by(SyntheticRun.started_at.desc())
    ).all()
    return [_run_out(run, names.get(run.profile_slug, run.profile_slug)) for run in runs]


@router.get("/runs/{run_id}/report")
def run_report(
    run_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    run = db.get(SyntheticRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    profile = db.get(SyntheticProfile, run.profile_slug)
    samples = db.scalars(select(SyntheticSample).where(SyntheticSample.run_id == run.id)).all()
    report = build_report(run, list(samples), profile.name if profile else run.profile_slug)
    return redact(report, _user.role)


@router.get("/health")
def health(
    monitor_id: uuid.UUID,
    link: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    range: str = "1h",
) -> dict[str, Any]:
    if link not in {"wired", "wireless"}:
        raise HTTPException(status_code=400, detail="Choose Wired or Wi-Fi")
    _monitor(db, monitor_id)
    profiles = ensure_profiles(db)
    since = range_start(range, _now())
    runs = [
        run
        for run in db.scalars(
            select(SyntheticRun).where(
                SyntheticRun.monitor_id == monitor_id,
                SyntheticRun.mode == "continuous",
                SyntheticRun.status == "running",
            )
        ).all()
        if link in (run.links or [])
    ]
    run_ids = [run.id for run in runs]
    samples = []
    if run_ids:
        samples = db.scalars(
            select(SyntheticSample).where(
                SyntheticSample.run_id.in_(run_ids),
                SyntheticSample.link == link,
                SyntheticSample.recorded_at >= since,
            )
        ).all()
    by_slug: dict[str, list[SyntheticSample]] = {}
    slug_of = {run.id: run.profile_slug for run in runs}
    for sample in samples:
        by_slug.setdefault(slug_of[sample.run_id], []).append(sample)
    running_slugs = {run.profile_slug for run in runs}
    types = []
    for profile in profiles:
        if profile.slug not in running_slugs:
            continue
        rows = sorted(by_slug.get(profile.slug, []), key=lambda sample: sample.recorded_at)
        latest = rows[-1] if rows else None
        types.append(
            {
                "slug": profile.slug,
                "name": profile.name,
                "latest": {
                    "rtt_ms": latest.rtt_ms if latest else None,
                    "jitter_ms": latest.jitter_ms if latest else None,
                    "loss_pct": latest.loss_pct if latest else None,
                    "verdict": latest.verdict if latest else None,
                },
                "points": [
                    {
                        "t": row.recorded_at,
                        "rtt_ms": row.rtt_ms,
                        "jitter_ms": row.jitter_ms,
                        "loss_pct": row.loss_pct,
                        "verdict": row.verdict,
                    }
                    for row in rows
                ],
            }
        )
    return redact({"types": types}, _user.role)


@router.get("/assignment")
def assignment(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    try:
        host = public_host(get_settings().wireguard_endpoint)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    key = ensure_key(db)
    srtp = ensure_srtp_key(monitor.id)
    runs = db.scalars(
        select(SyntheticRun).where(
            SyntheticRun.monitor_id == monitor.id,
            SyntheticRun.status.in_(("running", "pending")),
        )
    ).all()
    # The five-minute cap is enforced here too, so a stale test stops being assigned.
    ends: dict[uuid.UUID, datetime] = {}
    for stress_run in db.scalars(select(StressRun).where(StressRun.monitor_id == monitor.id, StressRun.status == "running")).all():
        stress_module.expire(db, stress_run)
        if stress_run.status == "running":
            ends[stress_run.id] = stress_run.ends_at
    jobs = []
    for run in runs:
        if run.mode == "stress" and (run.stress_id not in ends or run.status not in ("running", "pending")):
            continue
        for link in run.links:
            job = {
                "run_id": run.id,
                "link": link,
                "mode": run.mode,
                "profile_slug": run.profile_slug,
                "options": run.options,
                "duration_seconds": run.duration_seconds,
            }
            if run.mode == "stress":
                job["ends_in_s"] = max(1, int((ends[run.stress_id] - _now()).total_seconds()))
            jobs.append(job)
    db.commit()
    return {
        "fast": bool(ends),
        "target_host": host,
        "udp_port": get_settings().rtnp_udp_port,
        "control_port": get_settings().rtnp_control_port,
        "key": key,
        "srtp_key": srtp.hex() if srtp else "",
        "jobs": jobs,
    }


@router.post("/samples")
def post_sample(
    body: SampleIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, str]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    run = db.get(SyntheticRun, body.run_id)
    if run is None or run.monitor_id != monitor.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if body.link not in run.links:
        raise HTTPException(status_code=400, detail="This run does not include that link")
    extracted = metrics_from_interval(body.metrics)
    # A restarted sender begins its interval count again. The time keeps each report.
    key = f"{body.interval_index}:{int(body.recorded_at.timestamp())}"
    existing = db.scalar(
        select(SyntheticSample).where(
            SyntheticSample.run_id == run.id,
            SyntheticSample.link == body.link,
            SyntheticSample.sample_key == key,
        )
    )
    if existing is None:
        db.add(
            SyntheticSample(
                run_id=run.id,
                link=body.link,
                sample_key=key,
                interval_index=body.interval_index,
                recorded_at=body.recorded_at,
                metrics=body.metrics if run.mode == "stress" else {},
                **extracted,
            )
        )
    if run.status == "pending":
        run.status = "running"
    if (
        run.mode == "continuous"
        and extracted["loss_pct"] is None
        and body.interval_index >= 3
        and not ((body.metrics.get("flows") or [{}])[0].get("rx"))
    ):
        # If rtnp-recv restarted on the server, a long-running rtnp-send on the Pi
        # loses its TCP control channel and reports rx=None until restarted.
        if monitor.restart_requested_at is None:
            monitor.restart_requested_at = _now()
    db.commit()
    return {"status": "ok"}


@router.post("/runs/{run_id}/finish")
def finish_run(
    run_id: uuid.UUID,
    body: FinishIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, str]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    run = db.get(SyntheticRun, run_id)
    if run is None or run.monitor_id != monitor.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.mode == "continuous":
        return {"status": run.status}
    if run.mode == "stress":
        if body.status == "failed" and run.status in ("running", "pending"):
            run.status = "failed"
            run.finished_at = _now()
            run.options = {**(run.options or {}), "error": (body.message or "The session stopped")[:200]}
            db.commit()
        return {"status": run.status}
    done = list(run.finished_links or [])
    if body.link not in done:
        done.append(body.link)
    run.finished_links = done
    if body.status == "failed":
        run.status = "failed"
        run.finished_at = _now()
    elif set(run.links).issubset(set(done)):
        run.status = "finished"
        run.finished_at = _now()
    db.commit()
    return {"status": run.status}
