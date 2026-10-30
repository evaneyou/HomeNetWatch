"""Dashboard and agent endpoints for synthetic traffic."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import _resolve_monitor_identity, require_agent_links
from app.auth import get_current_user, require_admin_csrf
from app.config import get_settings
from app.db import get_db
from app.models import Monitor, Network, NetworkKind, ResultStatus, StressRun, SyntheticProfile, SyntheticRun, SyntheticSample, Test, TestResult, User
from app.synthetic import (
    SOFTWARE,
    apply_options,
    build_report,
    classify_software,
    ensure_key,
    ensure_profiles,
    metrics_from_interval,
    normalize_links,
    profile_category,
    public_host,
    range_start,
    restart_token_for,
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
    monitor = _monitor(db, body.monitor_id)
    profile = _profile(db, body.profile_slug)
    options = _options(profile, body.options)
    try:
        links = normalize_links(body.links) if body.links else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    require_agent_links(monitor, links)
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
    monitor = _monitor(db, body.monitor_id)
    profile = _profile(db, body.profile_slug)
    options = _options(profile, body.options)
    try:
        links = normalize_links(body.links)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    require_agent_links(monitor, links)
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


def _limit(options: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(options.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _diagnose_bandwidth(
    db: Session,
    monitor_id: uuid.UUID,
    link: str,
    latest_fail_at: datetime,
    both_links_failing: bool,
) -> Optional[dict[str, str]]:
    """Check if LibreSpeed upload/download collapsed around the time of the application failures."""
    from app.internet_tests import _baseline_value, load_internet_service

    setting = load_internet_service(db)
    variance = min(max(setting.librespeed_variance_pct, 0), 50)
    floor = max(0.0, 1.0 - variance / 100.0)
    tests = db.execute(
        select(Test, Network)
        .join(Network, Test.network_id == Network.id)
        .where(
            Test.monitor_id == monitor_id,
            Test.slug == "librespeed",
            Network.slug.in_(("wired", "wifi")),
        )
    ).all()
    if not tests:
        return None
    by_link: dict[str, dict[str, Any]] = {}
    for test, network in tests:
        lnk = "wireless" if network.kind == NetworkKind.wifi else "wired"
        latest = db.scalar(
            select(TestResult)
            .where(
                TestResult.test_id == test.id,
                TestResult.started_at >= latest_fail_at - timedelta(hours=2),
                TestResult.started_at <= latest_fail_at + timedelta(minutes=30),
            )
            .order_by(TestResult.started_at.desc())
            .limit(1)
        )
        if latest is None:
            continue
        metrics = latest.metrics or {}
        down = metrics.get("down_mbps")
        up = metrics.get("up_mbps")
        down_val = float(down) if isinstance(down, (int, float)) and not isinstance(down, bool) else None
        up_val = float(up) if isinstance(up, (int, float)) and not isinstance(up, bool) else None
        base_down = _baseline_value(db, test, "down_mbps")
        base_up = _baseline_value(db, test, "up_mbps")
        up_bad = up_val is not None and (
            (base_up is not None and up_val < base_up * floor) or up_val < 2.0
        )
        down_bad = down_val is not None and (
            (base_down is not None and down_val < base_down * floor) or down_val < 5.0
        )
        is_bad = latest.status in (ResultStatus.degraded, ResultStatus.fail) or up_bad or down_bad
        if is_bad:
            by_link[lnk] = {
                "down": down_val,
                "up": up_val,
                "base_down": base_down,
                "base_up": base_up,
                "up_bad": up_bad,
                "down_bad": down_bad,
                "reason": latest.status_reason or "",
            }
    if not by_link:
        return None
    if both_links_failing or ("wired" in by_link and "wireless" in by_link):
        w = by_link.get("wired")
        f = by_link.get("wireless")
        sample = w or f or {}
        if (w and w["up_bad"]) or (f and f["up_bad"]):
            parts = []
            if w and w["up"] is not None:
                parts.append(f"{w['up']:.1f} Mbps on Wired")
            if f and f["up"] is not None:
                parts.append(f"{f['up']:.1f} Mbps on Wi-Fi")
            norm = sample.get("base_up")
            norm_txt = f" (normal ~{norm:.0f} Mbps)" if norm else ""
            return {
                "cause": "bandwidth",
                "title": "Upstream ISP bandwidth collapse",
                "summary": (
                    f"Upload throughput dropped to {' and '.join(parts)}{norm_txt}, "
                    "starving real-time voice, video, and remote desktop streams across both links."
                ),
            }
        parts = []
        if w and w["down"] is not None:
            parts.append(f"{w['down']:.1f} Mbps down / {w['up'] or 0:.1f} Mbps up on Wired")
        if f and f["down"] is not None:
            parts.append(f"{f['down']:.1f} Mbps down / {f['up'] or 0:.1f} Mbps up on Wi-Fi")
        return {
            "cause": "bandwidth",
            "title": "Upstream ISP bandwidth collapse",
            "summary": (
                f"Bandwidth Test throughput dropped ({'; '.join(parts)}), "
                "degrading real-time voice, video, and remote desktop streams across both links."
            ),
        }
    if link in by_link:
        item = by_link[link]
        label = "Wi-Fi" if link == "wireless" else "Wired"
        if item["up_bad"] and item["up"] is not None:
            norm = item.get("base_up")
            norm_txt = f" (normal ~{norm:.0f} Mbps)" if norm else ""
            return {
                "cause": "bandwidth",
                "title": f"{label} bandwidth drop",
                "summary": (
                    f"Upload throughput on {label} dropped to {item['up']:.1f} Mbps{norm_txt}, "
                    "causing real-time application packet loss and latency on that link."
                ),
            }
        return {
            "cause": "bandwidth",
            "title": f"{label} bandwidth drop",
            "summary": item["reason"] or f"Bandwidth Test throughput on {label} dropped below normal.",
        }
    return None


def _diagnose_health(
    db: Session,
    monitor_id: uuid.UUID,
    link: str,
    all_runs: list[SyntheticRun],
    link_samples: list[SyntheticSample],
    since: datetime,
) -> Optional[dict[str, str]]:
    """Diagnose whether failures in this window are link-specific (wifi/wired), server/agent, or upstream WAN."""
    raw_link = [row for row in link_samples if not row.sample_key.startswith(("hour:", "day:"))]
    failed_samples = [row for row in raw_link if row.verdict == "fail"]
    if not failed_samples:
        return None
    latest_fail_at = max(row.recorded_at for row in failed_samples)
    sibling_link = "wired" if link == "wireless" else "wireless"
    sibling_runs = [run for run in all_runs if sibling_link in (run.links or [])]
    if not sibling_runs:
        return _diagnose_bandwidth(db, monitor_id, link, latest_fail_at, both_links_failing=False)
    sibling_samples = list(
        db.scalars(
            select(SyntheticSample).where(
                SyntheticSample.run_id.in_([run.id for run in sibling_runs]),
                SyntheticSample.link == sibling_link,
                SyntheticSample.recorded_at >= since,
                ~SyntheticSample.sample_key.startswith("hour:"),
                ~SyntheticSample.sample_key.startswith("day:"),
            )
        ).all()
    )
    if not sibling_samples:
        return _diagnose_bandwidth(db, monitor_id, link, latest_fail_at, both_links_failing=False)

    sib_failed = [row for row in sibling_samples if row.verdict == "fail"]
    my_fails = len(failed_samples)
    sib_fails = len(sib_failed)

    # If the sibling link has far more failures across the window (>= 3x), the dominant issue is on the sibling link,
    # not this link. Do not show a misleading banner on the clean link.
    if sib_fails >= max(5, my_fails * 3):
        return None

    # If this link has far more failures than the sibling link (>= 3x, or sibling had 0), attribute carefully.
    if my_fails >= 3 and (sib_fails == 0 or my_fails >= sib_fails * 3):
        bw_diag = _diagnose_bandwidth(db, monitor_id, link, latest_fail_at, both_links_failing=False)
        if bw_diag is not None:
            return bw_diag
        web_ok = db.scalar(
            select(TestResult.id)
            .join(Test, TestResult.test_id == Test.id)
            .join(Network, Test.network_id == Network.id)
            .where(
                Test.monitor_id == monitor_id,
                Network.kind == (NetworkKind.wifi if link == "wireless" else NetworkKind.wired),
                Test.slug.startswith("http-"),
                Test.slug != "http-path",
                TestResult.status == ResultStatus.ok,
                TestResult.started_at >= latest_fail_at - timedelta(minutes=5),
                TestResult.started_at <= latest_fail_at + timedelta(minutes=5),
            )
            .limit(1)
        )
        path_ok = db.scalar(
            select(TestResult.id)
            .join(Test, TestResult.test_id == Test.id)
            .join(Network, Test.network_id == Network.id)
            .where(
                Test.monitor_id == monitor_id,
                Network.kind == (NetworkKind.wifi if link == "wireless" else NetworkKind.wired),
                Test.slug.in_(("icmp-path", "http-path")),
                TestResult.status == ResultStatus.ok,
                TestResult.started_at >= latest_fail_at - timedelta(minutes=5),
                TestResult.started_at <= latest_fail_at + timedelta(minutes=5),
            )
            .limit(1)
        )
        if web_ok is not None or path_ok is not None:
            label = "Wi-Fi" if link == "wireless" else "Wired"
            return {
                "cause": "tunnel",
                "title": f"{label} measurement tunnel or receiver issue",
                "summary": (
                    f"{label} application checks failed ({my_fails} intervals) while path or web checks on the same "
                    "link still succeeded. Home access was up; the WireGuard measurement tunnel or synthetic "
                    "receiver was not."
                ),
            }
        if link == "wireless":
            return {
                "cause": "wifi",
                "title": "Wireless network degradation",
                "summary": (
                    f"Wi-Fi recorded {my_fails} failed intervals in this range compared with {sib_fails} on Wired Ethernet. "
                    "This points to local Wi-Fi interference or signal quality rather than the applications."
                ),
            }
        return {
            "cause": "wired",
            "title": "Wired Ethernet degradation",
            "summary": (
                f"Wired Ethernet recorded {my_fails} failed intervals in this range compared with {sib_fails} on Wi-Fi. "
                "This points to the local wired connection (cable, switch, or port) rather than the applications."
            ),
        }

    # Check if both links had a simultaneous multi-stream failure in the same 2-minute window
    latest_fail_at = max(row.recorded_at for row in failed_samples)
    w_start = latest_fail_at - timedelta(minutes=1)
    w_end = latest_fail_at + timedelta(minutes=1)
    my_window_fails = [row for row in failed_samples if w_start <= row.recorded_at <= w_end]
    sib_window_fails = [row for row in sib_failed if w_start <= row.recorded_at <= w_end]
    my_window_slugs = {run.profile_slug for run in all_runs for row in my_window_fails if row.run_id == run.id}
    sib_window_slugs = {run.profile_slug for run in all_runs for row in sib_window_fails if row.run_id == run.id}

    if len(my_window_slugs) >= 2 and len(sib_window_slugs) >= 2:
        all_blackout = all(
            (row.loss_pct or 0) >= 99.0 and not row.rtt_ms and not row.jitter_ms
            for row in (*my_window_fails, *sib_window_fails)
        )
        web_ok = db.scalar(
            select(TestResult.id)
            .join(Test, TestResult.test_id == Test.id)
            .where(
                Test.monitor_id == monitor_id,
                Test.slug.startswith("http-"),
                Test.slug != "http-path",
                TestResult.status == ResultStatus.ok,
                TestResult.started_at >= latest_fail_at - timedelta(minutes=5),
                TestResult.started_at <= latest_fail_at + timedelta(minutes=5),
            )
            .limit(1)
        )
        if all_blackout and web_ok is not None:
            return {
                "cause": "server",
                "title": "Server or agent synthetic receiver issue",
                "summary": (
                    "Both Wired and Wi-Fi synthetic flows had a simultaneous blackout while external Web checks passed. "
                    "This points to a temporary restart in the HomeNetWatch synthetic receiver or remote agent sender."
                ),
            }
        bw_diag = _diagnose_bandwidth(db, monitor_id, link, latest_fail_at, both_links_failing=True)
        if bw_diag is not None:
            return bw_diag
        return {
            "cause": "wan",
            "title": "Upstream ISP or shared network spike",
            "summary": (
                "Multiple streams experienced packet loss or latency simultaneously across both Wired and Wi-Fi, "
                "indicating an upstream ISP blip, router congestion, or shared path issue."
            ),
        }
    bw_diag = _diagnose_bandwidth(db, monitor_id, link, latest_fail_at, both_links_failing=bool(my_fails >= 3 and sib_fails >= 3))
    if bw_diag is not None:
        return bw_diag
    return None


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
    all_runs = list(
        db.scalars(
            select(SyntheticRun).where(
                SyntheticRun.monitor_id == monitor_id,
                SyntheticRun.mode == "continuous",
                SyntheticRun.status == "running",
            )
        ).all()
    )
    runs = [run for run in all_runs if link in (run.links or [])]
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
        matched = [run for run in runs if run.profile_slug == profile.slug]
        options = {**(profile.options or {}), **((matched[-1].options if matched else None) or {})}
        types.append(
            {
                "slug": profile.slug,
                "name": profile.name,
                "limits": {
                    "max_rtt_ms": _limit(options, "max_rtt_ms", 300),
                    "max_jitter_ms": _limit(options, "max_jitter_ms", 30),
                    "max_loss_pct": _limit(options, "max_loss_pct", 1),
                },
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
    diagnosis = _diagnose_health(db, monitor_id, link, all_runs, list(samples), since)
    return redact({"types": types, "diagnosis": diagnosis}, _user.role)


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
                "restart_token": restart_token_for(run, link),
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
    extracted = classify_software(
        db, monitor, run, body.link, body.recorded_at, metrics_from_interval(body.metrics)
    )
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
                metrics=body.metrics if run.mode == "stress" else ({"impact": SOFTWARE} if extracted.get("verdict") == SOFTWARE else {}),
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
