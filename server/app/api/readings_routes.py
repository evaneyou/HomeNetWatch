"""Dashboard reads: the summary, results, logs, metrics, issues, and Wi-Fi scans."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, and_, desc, func, literal, or_, select, text, union_all
from sqlalchemy.orm import Session, joinedload

from app.api.rf_routes import rf_alert_severity
from app.auth import get_current_user
from app.db import get_db
from app.internet_tests import (
    explain_result,
    is_web_check,
    load_web_stability,
    log_slug_filter,
    matrix_column,
    stability_config,
)
from app.models import (
    Issue,
    IssueSeverity,
    IssueStatus,
    Monitor,
    Network,
    ResultStatus,
    RfAlert,
    SyntheticProfile,
    SyntheticRun,
    SyntheticSample,
    SystemLog,
    Test,
    TestResult,
    User,
    WifiObservation,
)
from app.schemas import (
    HealthCell,
    HopMetric,
    IssueOut,
    IssueSummary,
    MetricsPoint,
    MetricsResponse,
    ResultOut,
    ResultPage,
    SummaryResponse,
    ThroughputMbps,
    UnifiedEventOut,
    UnifiedEventPage,
    WifiObservationOut,
)
from app.services.stability import cell_quorum, service_status
from app.synthetic import PRESETS

router = APIRouter(prefix="/api/v1")

def _result_to_out(r: TestResult) -> ResultOut:
    test = r.test
    return ResultOut(
        id=r.id,
        external_id=r.external_id,
        test_id=r.test_id,
        test_slug=test.slug,
        test_name=test.name,
        test_kind=test.kind.value,
        monitor_id=test.monitor_id,
        monitor_slug=test.monitor.slug,
        network_id=test.network_id,
        network_slug=test.network.slug,
        network_name=test.network.name,
        started_at=r.started_at,
        finished_at=r.finished_at,
        status=r.status.value,
        stages=r.stages or [],
        metrics=r.metrics or {},
        message=r.message,
        status_reason=r.status_reason
        or explain_result(test.kind.value, r.status.value, r.metrics, r.stages, r.message),
    )


def _fill_web_quorum(tests, latest_by_test, cell_map, cfg) -> None:
    """Web Services cell color comes from how many services are steadily bad, not one sample."""
    groups: dict[tuple, dict[str, dict[str, Test]]] = {}
    for test in tests:
        if not test.enabled:
            continue
        column = matrix_column(test.network.slug, test.slug)
        if column != "web" or not is_web_check(test.slug):
            continue
        kind, _, key = test.slug.partition("-")
        groups.setdefault((test.monitor_id, test.network_id, column), {}).setdefault(key, {})[kind] = test

    now = datetime.now(timezone.utc)
    for cell_key, services in groups.items():
        cell = cell_map.get(cell_key)
        if cell is None:
            continue
        statuses = []
        sustained_fail = False
        last_at = None
        for kinds in services.values():
            http = kinds.get("http")
            icmp = kinds.get("icmp")
            for probe in (http, icmp):
                if probe is None:
                    continue
                result = latest_by_test.get(probe.id)
                if result is not None and (last_at is None or result.started_at > last_at):
                    last_at = result.started_at
            http_status = http.stable_status if http is not None and latest_by_test.get(http.id) else None
            icmp_status = icmp.stable_status if icmp is not None and latest_by_test.get(icmp.id) else None
            stable = service_status(http_status, icmp_status)
            if stable is None:
                continue
            statuses.append(stable)
            if (
                http is not None
                and http.stable_status == ResultStatus.fail
                and http.state_since is not None
                and now - http.state_since >= timedelta(minutes=15)
            ):
                sustained_fail = True
        if not statuses:
            continue
        status, bad, total = cell_quorum(statuses, cfg, sustained_fail)
        cell.status = status
        cell.failing_tests = bad
        cell.last_test_at = last_at
        cell.quorum = {"bad": bad, "total": total}


def _fill_app_cells(db: Session, monitors: list[Monitor], networks: list[Network], cell_map: dict) -> None:
    """Real-time application health (voice, video, etc.) per link over recent samples."""
    runs = db.scalars(
        select(SyntheticRun).where(SyntheticRun.mode == "continuous", SyntheticRun.status == "running")
    ).all()
    if not runs:
        return
    net_by_slug = {(n.group_id, n.slug): n for n in networks}
    mon_by_id = {m.id: m for m in monitors}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=15)
    rank = {"ok": 0, "degraded": 1, "fail": 2, "unknown": -1}

    for run in runs:
        monitor = mon_by_id.get(run.monitor_id)
        if monitor is None:
            continue
        for link in run.links or []:
            net_slug = "wifi" if link == "wireless" else "wired"
            network = net_by_slug.get((monitor.group_id, net_slug))
            if network is None:
                continue
            cell = cell_map.get((monitor.id, network.id, "apps"))
            if cell is None:
                continue
            rows = db.scalars(
                select(SyntheticSample)
                .where(
                    SyntheticSample.run_id == run.id,
                    SyntheticSample.link == link,
                    ~SyntheticSample.sample_key.startswith("hour:"),
                    ~SyntheticSample.sample_key.startswith("day:"),
                )
                .order_by(desc(SyntheticSample.recorded_at))
                .limit(3)
            ).all()
            if not rows or rows[0].recorded_at < cutoff:
                continue
            if cell.last_test_at is None or rows[0].recorded_at > cell.last_test_at:
                cell.last_test_at = rows[0].recorded_at
            fails = sum(1 for row in rows if row.verdict == "fail")
            if len(rows) >= 3 and fails == 3:
                flow_status = "fail"
            elif fails >= 2 or rows[0].verdict == "fail":
                flow_status = "degraded"
            else:
                flow_status = "ok"
            if rank[flow_status] > rank[cell.status]:
                cell.status = flow_status
            if flow_status in ("fail", "degraded"):
                cell.failing_tests += 1


@router.get("/summary", response_model=SummaryResponse)
def summary(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SummaryResponse:
    monitors = db.scalars(select(Monitor).order_by(Monitor.name)).all()
    networks = db.scalars(select(Network).order_by(Network.name)).all()
    tests = db.scalars(
        select(Test).options(joinedload(Test.monitor), joinedload(Test.network))
    ).all()

    # Latest result per test
    latest_subq = (
        select(TestResult.test_id, func.max(TestResult.started_at).label("max_started"))
        .group_by(TestResult.test_id)
        .subquery()
    )
    latest_results = db.scalars(
        select(TestResult)
        .join(
            latest_subq,
            and_(
                TestResult.test_id == latest_subq.c.test_id,
                TestResult.started_at == latest_subq.c.max_started,
            ),
        )
    ).all()
    latest_by_test = {r.test_id: r for r in latest_results}

    # Build matrix cells keyed by monitor × network × column.
    # The Internet network splits into Internet Service and Web Services.
    cell_map: dict[tuple[UUID, UUID, str], HealthCell] = {}
    for monitor in monitors:
        for network in networks:
            columns = ["internet", "web", "apps"] if network.slug in {"wan", "wired", "wifi"} else [network.slug]
            for column in columns:
                cell_map[(monitor.id, network.id, column)] = HealthCell(
                    monitor_id=monitor.id,
                    monitor_slug=monitor.slug,
                    monitor_name=monitor.name,
                    network_id=network.id,
                    network_slug=network.slug,
                    network_name=network.name,
                    column=column,
                    status="unknown",
                    last_test_at=None,
                    failing_tests=0,
                )

    rank = {"ok": 0, "degraded": 1, "fail": 2, "unknown": -1}
    for test in tests:
        if not test.enabled:
            continue
        column = matrix_column(test.network.slug, test.slug)
        if column is None:
            continue
        cell = cell_map.get((test.monitor_id, test.network_id, column))
        if cell is None or column == "web":
            continue
        result = latest_by_test.get(test.id)
        if result is None:
            continue
        status_val = (
            test.stable_status.value
            if test.slug in ("http-path", "icmp-path") and test.stable_status is not None
            else result.status.value
        )
        if cell.last_test_at is None or result.started_at > cell.last_test_at:
            cell.last_test_at = result.started_at
        if test.slug == "mtr-path":
            # The trace is detail for the Path View. Many hosts drop trace probes,
            # so it must not turn the Wired, Wi-Fi or Internet tile red by itself.
            continue
        if rank[status_val] > rank[cell.status]:
            cell.status = status_val
        if status_val in ("fail", "degraded"):
            cell.failing_tests += 1

    _fill_web_quorum(tests, latest_by_test, cell_map, stability_config(load_web_stability(db)))
    _fill_app_cells(db, monitors, networks, cell_map)

    open_issues = db.scalars(
        select(Issue)
        .where(Issue.status == IssueStatus.open, Issue.severity != IssueSeverity.info)
        .order_by(desc(Issue.opened_at))
        .limit(20)
    ).all()
    monitor_by_id = {s.id: s for s in monitors}
    network_by_id = {n.id: n for n in networks}
    test_by_id = {t.id: t for t in tests}
    issue_summaries = []
    for i in open_issues:
        latest = latest_by_test.get(i.test_id) if i.test_id else None
        if latest is not None and latest.status.value == "ok":
            continue
        issue_summaries.append(
            IssueSummary(
                id=i.id,
                title=i.title,
                severity=i.severity.value,
                status=i.status.value,
                opened_at=i.opened_at,
                root_cause_stage=i.root_cause_stage,
                monitor_slug=monitor_by_id[i.monitor_id].slug if i.monitor_id and i.monitor_id in monitor_by_id else None,
                monitor_name=monitor_by_id[i.monitor_id].name if i.monitor_id and i.monitor_id in monitor_by_id else None,
                network_slug=network_by_id[i.network_id].slug
                if i.network_id and i.network_id in network_by_id
                else None,
                network_name=network_by_id[i.network_id].name
                if i.network_id and i.network_id in network_by_id
                else None,
                test_name=test_by_id[i.test_id].name if i.test_id and i.test_id in test_by_id else None,
            )
        )

    now_utc = datetime.now(timezone.utc)
    return SummaryResponse(
        generated_at=now_utc,
        matrix=list(cell_map.values()),
        open_issues=issue_summaries,
        monitors=[
            {
                "id": str(s.id),
                "slug": s.slug,
                "name": s.name,
                "status": (
                    "offline"
                    if s.last_seen_at is not None
                    and now_utc - (
                        s.last_seen_at
                        if s.last_seen_at.tzinfo
                        else s.last_seen_at.replace(tzinfo=timezone.utc)
                    )
                    > timedelta(seconds=max(300, 3 * int(s.health_interval_seconds or 60)))
                    else s.status.value
                ),
                "location_label": s.location_label,
                "region": s.region,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in monitors
        ],
        networks=[
            {"id": str(n.id), "slug": n.slug, "name": n.name, "kind": n.kind.value} for n in networks
        ],
    )


@router.get("/results", response_model=list[ResultOut])
def results(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: Optional[UUID] = None,
    network_id: Optional[UUID] = None,
    test_id: Optional[UUID] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[ResultOut]:
    stmt: Select[tuple[TestResult]] = (
        select(TestResult)
        .join(Test)
        .options(
            joinedload(TestResult.test).joinedload(Test.monitor),
            joinedload(TestResult.test).joinedload(Test.network),
        )
        .order_by(desc(TestResult.started_at))
        .limit(limit)
    )
    if monitor_id:
        stmt = stmt.where(Test.monitor_id == monitor_id)
    if network_id:
        stmt = stmt.where(Test.network_id == network_id)
    if test_id:
        stmt = stmt.where(TestResult.test_id == test_id)
    if status_filter:
        try:
            stmt = stmt.where(TestResult.status == ResultStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
    if since:
        stmt = stmt.where(TestResult.started_at >= since)
    if until:
        stmt = stmt.where(TestResult.started_at <= until)

    rows = db.scalars(stmt).unique().all()
    return [_result_to_out(r) for r in rows]


_LOG_LINK = {"wired": ("wired", "Wired"), "wireless": ("wifi", "Wi-Fi")}


def _synthetic_slug(probe: str) -> Optional[str]:
    """'' for every application probe, a slug for one, or None when this is not that filter."""
    if probe == "rtnp":
        return ""
    if not probe.startswith("rtnp:"):
        return None
    slug = probe[5:]
    if slug not in PRESETS:
        raise ValueError("Invalid probe filter")
    return slug


def _sample_rows(monitor_id: UUID, slug: str):
    stmt = (
        select(SyntheticSample)
        .join(SyntheticRun, SyntheticSample.run_id == SyntheticRun.id)
        .where(
            SyntheticRun.monitor_id == monitor_id,
            SyntheticRun.mode != "stress",
            ~SyntheticSample.sample_key.startswith("hour:"),
            ~SyntheticSample.sample_key.startswith("day:"),
        )
        .options(joinedload(SyntheticSample.run))
    )
    if slug:
        stmt = stmt.where(SyntheticRun.profile_slug == slug)
    return stmt


def _profile_names(db: Session, slugs: set[str]) -> dict[str, str]:
    if not slugs:
        return {}
    rows = db.scalars(select(SyntheticProfile).where(SyntheticProfile.slug.in_(slugs))).all()
    return {row.slug: row.name for row in rows}


def _synthetic_out(sample: SyntheticSample, monitor: Monitor, names: dict[str, str]) -> ResultOut:
    network_slug, network_name = _LOG_LINK.get(sample.link, (sample.link, sample.link))
    if sample.verdict == "pass":
        status, reason = "ok", "Within the round-trip, jitter, and loss limits."
    elif sample.verdict == "fail":
        status, reason = "fail", "Outside the round-trip, jitter, or loss limit."
    else:
        status, reason = "unknown", None
    metrics = {
        key: value
        for key, value in (
            ("rtt_ms", sample.rtt_ms),
            ("jitter_ms", sample.jitter_ms),
            ("loss_pct", sample.loss_pct),
        )
        if value is not None
    }
    slug = sample.run.profile_slug
    return ResultOut(
        id=sample.id,
        external_id=sample.sample_key,
        test_id=sample.run_id,
        test_slug=slug,
        test_name=names.get(slug, slug),
        test_kind="rtnp",
        monitor_id=monitor.id,
        monitor_slug=monitor.slug,
        network_id=monitor.id,
        network_slug=network_slug,
        network_name=network_name,
        started_at=sample.recorded_at,
        finished_at=sample.recorded_at,
        status=status,
        stages=[],
        metrics=metrics,
        status_reason=reason,
    )


def _synthetic_page(db: Session, monitor: Monitor, slug: str, offset: int, limit: int) -> ResultPage:
    filters = [
        SyntheticRun.monitor_id == monitor.id,
        SyntheticRun.mode != "stress",
        ~SyntheticSample.sample_key.startswith("hour:"),
        ~SyntheticSample.sample_key.startswith("day:"),
    ]
    if slug:
        filters.append(SyntheticRun.profile_slug == slug)
    total = db.scalar(
        select(func.count(SyntheticSample.id))
        .join(SyntheticRun, SyntheticSample.run_id == SyntheticRun.id)
        .where(*filters)
    ) or 0
    rows = db.scalars(
        _sample_rows(monitor.id, slug)
        .order_by(desc(SyntheticSample.recorded_at), desc(SyntheticSample.id))
        .offset(offset)
        .limit(limit)
    ).unique().all()
    names = _profile_names(db, {row.run.profile_slug for row in rows})
    return ResultPage(
        total=total,
        offset=offset,
        limit=limit,
        items=[_synthetic_out(row, monitor, names) for row in rows],
    )


def _rf_alert_out(alert: RfAlert, monitor: Monitor) -> ResultOut:
    sev = rf_alert_severity(alert.alert_type)
    if sev == "critical":
        status = "fail"
    elif sev == "warning":
        status = "degraded"
    else:
        status = "info"
    label = f"Airwave {alert.alert_type}" + (f" ({alert.bssid})" if alert.bssid else "")
    return ResultOut(
        id=alert.id,
        external_id=alert.dedupe_key,
        test_id=monitor.id,
        test_slug=f"airwave:{alert.alert_type.lower()}",
        test_name=label,
        test_kind="airwave",
        monitor_id=monitor.id,
        monitor_slug=monitor.slug,
        network_id=monitor.id,
        network_slug="wifi",
        network_name="Wi-Fi",
        started_at=alert.observed_at,
        finished_at=alert.observed_at,
        status=status,
        stages=[],
        metrics={},
        message=alert.text,
        status_reason=alert.text,
    )


def _airwave_page(db: Session, monitor: Monitor, offset: int, limit: int) -> ResultPage:
    total = db.scalar(select(func.count(RfAlert.id)).where(RfAlert.monitor_id == monitor.id)) or 0
    rows = db.scalars(
        select(RfAlert)
        .where(RfAlert.monitor_id == monitor.id)
        .order_by(desc(RfAlert.observed_at), desc(RfAlert.id))
        .offset(offset)
        .limit(limit)
    ).all()
    return ResultPage(
        total=total,
        offset=offset,
        limit=limit,
        items=[_rf_alert_out(row, monitor) for row in rows],
    )


def _system_log_out(row: SystemLog, monitor: Monitor) -> ResultOut:
    sev = (row.severity or "info").lower()
    if sev == "critical":
        status = "fail"
    elif sev == "warning":
        status = "degraded"
    else:
        status = "info"
    source_label = "Agent" if row.source == "agent" else "Server"
    return ResultOut(
        id=row.id,
        external_id=f"syslog-{row.id}",
        test_id=monitor.id,
        test_slug=f"system:{row.subsystem}",
        test_name=f"{source_label} {row.subsystem}",
        test_kind="system",
        monitor_id=monitor.id,
        monitor_slug=monitor.slug,
        network_id=monitor.id,
        network_slug="wan",
        network_name=source_label,
        started_at=row.recorded_at,
        finished_at=row.recorded_at,
        status=status,
        stages=[],
        metrics={},
        message=row.message,
        status_reason=row.message,
    )


def _system_log_page(db: Session, monitor: Monitor, offset: int, limit: int) -> ResultPage:
    cond = or_(SystemLog.monitor_id == monitor.id, SystemLog.monitor_id.is_(None))
    total = db.scalar(select(func.count(SystemLog.id)).where(cond)) or 0
    rows = db.scalars(
        select(SystemLog)
        .where(cond)
        .order_by(desc(SystemLog.recorded_at), desc(SystemLog.id))
        .offset(offset)
        .limit(limit)
    ).all()
    return ResultPage(
        total=total,
        offset=offset,
        limit=limit,
        items=[_system_log_out(row, monitor) for row in rows],
    )


def _merged_page(db: Session, monitor: Monitor, offset: int, limit: int) -> ResultPage:
    tests = (
        select(
            TestResult.started_at.label("at"),
            TestResult.id.label("row_id"),
            literal("test").label("src"),
        )
        .join(Test, TestResult.test_id == Test.id)
        .where(Test.monitor_id == monitor.id)
    )
    samples = (
        select(
            SyntheticSample.recorded_at.label("at"),
            SyntheticSample.id.label("row_id"),
            literal("sample").label("src"),
        )
        .join(SyntheticRun, SyntheticSample.run_id == SyntheticRun.id)
        .where(
            SyntheticRun.monitor_id == monitor.id,
            SyntheticRun.mode != "stress",
            ~SyntheticSample.sample_key.startswith("hour:"),
            ~SyntheticSample.sample_key.startswith("day:"),
        )
    )
    airwaves = (
        select(
            RfAlert.observed_at.label("at"),
            RfAlert.id.label("row_id"),
            literal("airwave").label("src"),
        )
        .where(RfAlert.monitor_id == monitor.id)
    )
    syslogs = (
        select(
            SystemLog.recorded_at.label("at"),
            SystemLog.id.label("row_id"),
            literal("syslog").label("src"),
        )
        .where(or_(SystemLog.monitor_id == monitor.id, SystemLog.monitor_id.is_(None)))
    )
    merged = union_all(tests, samples, airwaves, syslogs).subquery()
    total = db.scalar(select(func.count()).select_from(merged)) or 0
    picked = db.execute(
        select(merged.c.row_id, merged.c.src)
        .order_by(desc(merged.c.at), desc(merged.c.row_id))
        .offset(offset)
        .limit(limit)
    ).all()
    test_ids = [row_id for row_id, src in picked if src == "test"]
    sample_ids = [row_id for row_id, src in picked if src == "sample"]
    airwave_ids = [row_id for row_id, src in picked if src == "airwave"]
    syslog_ids = [row_id for row_id, src in picked if src == "syslog"]
    test_rows = {}
    if test_ids:
        test_rows = {
            row.id: row
            for row in db.scalars(
                select(TestResult)
                .where(TestResult.id.in_(test_ids))
                .options(
                    joinedload(TestResult.test).joinedload(Test.monitor),
                    joinedload(TestResult.test).joinedload(Test.network),
                )
            ).unique().all()
        }
    sample_rows = {}
    if sample_ids:
        sample_rows = {
            row.id: row
            for row in db.scalars(
                select(SyntheticSample)
                .where(SyntheticSample.id.in_(sample_ids))
                .options(joinedload(SyntheticSample.run))
            ).unique().all()
        }
    airwave_rows = {}
    if airwave_ids:
        airwave_rows = {
            row.id: row
            for row in db.scalars(
                select(RfAlert).where(RfAlert.id.in_(airwave_ids))
            ).all()
        }
    syslog_rows = {}
    if syslog_ids:
        syslog_rows = {
            row.id: row
            for row in db.scalars(
                select(SystemLog).where(SystemLog.id.in_(syslog_ids))
            ).all()
        }
    names = _profile_names(db, {row.run.profile_slug for row in sample_rows.values()})
    items = []
    for row_id, src in picked:
        if src == "test" and row_id in test_rows:
            items.append(_result_to_out(test_rows[row_id]))
        elif src == "sample" and row_id in sample_rows:
            items.append(_synthetic_out(sample_rows[row_id], monitor, names))
        elif src == "airwave" and row_id in airwave_rows:
            items.append(_rf_alert_out(airwave_rows[row_id], monitor))
        elif src == "syslog" and row_id in syslog_rows:
            items.append(_system_log_out(syslog_rows[row_id], monitor))
    return ResultPage(total=total, offset=offset, limit=limit, items=items)


@router.get("/monitors/{monitor_id}/log", response_model=ResultPage)
def monitor_log(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    probe: str = Query("all"),
) -> ResultPage:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    if probe == "airwave":
        return _airwave_page(db, monitor, offset, limit)
    if probe == "system":
        return _system_log_page(db, monitor, offset, limit)
    try:
        synthetic = _synthetic_slug(probe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid probe filter") from exc
    if synthetic is not None:
        return _synthetic_page(db, monitor, synthetic, offset, limit)
    if probe in ("", "all"):
        return _merged_page(db, monitor, offset, limit)
    try:
        probe_filter = log_slug_filter(probe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid probe filter") from exc
    count_stmt = (
        select(func.count(TestResult.id))
        .select_from(TestResult)
        .join(Test)
        .where(Test.monitor_id == monitor_id)
    )
    rows_stmt = (
        select(TestResult)
        .join(Test)
        .where(Test.monitor_id == monitor_id)
        .options(
            joinedload(TestResult.test).joinedload(Test.monitor),
            joinedload(TestResult.test).joinedload(Test.network),
        )
        .order_by(desc(TestResult.started_at), desc(TestResult.id))
    )
    if probe_filter is not None:
        count_stmt = count_stmt.where(probe_filter)
        rows_stmt = rows_stmt.where(probe_filter)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(rows_stmt.offset(offset).limit(limit)).unique().all()
    return ResultPage(total=total, offset=offset, limit=limit, items=[_result_to_out(r) for r in rows])


@router.get("/logs/unified", response_model=UnifiedEventPage)
def unified_logs(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: Optional[UUID] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> UnifiedEventPage:
    """Query the unified timeline view across all telemetry, alerts, issues, and system logs."""
    clauses: list[str] = []
    params: dict[str, object] = {"offset": offset, "limit": limit}
    if monitor_id is not None:
        clauses.append("(monitor_id = :monitor_id OR monitor_id IS NULL)")
        params["monitor_id"] = monitor_id
    if category:
        clauses.append("category = :category")
        params["category"] = category
    if severity:
        clauses.append("severity = :severity")
        params["severity"] = severity
    if since is not None:
        clauses.append("recorded_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("recorded_at <= :until")
        params["until"] = until
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = int(db.execute(text(f"SELECT count(*) FROM v_unified_events {where_sql}"), params).scalar() or 0)
    rows = db.execute(
        text(
            f"""
            SELECT recorded_at, event_id, monitor_id, monitor_slug, monitor_name,
                   category, subsystem, network, severity, title, message,
                   rtt_ms, jitter_ms, loss_pct, down_mbps, up_mbps, metadata
            FROM v_unified_events
            {where_sql}
            ORDER BY recorded_at DESC, event_id DESC
            OFFSET :offset LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return UnifiedEventPage(
        total=total,
        offset=offset,
        limit=limit,
        items=[UnifiedEventOut(**dict(row)) for row in rows],
    )


def _metric_float(metrics: Optional[dict], key: str) -> Optional[float]:
    value = (metrics or {}).get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metric_hops(metrics: Optional[dict]) -> list[HopMetric]:
    hops: list[HopMetric] = []
    for item in (metrics or {}).get("hops") or []:
        if not isinstance(item, dict):
            continue
        try:
            hops.append(
                HopMetric(
                    hop=int(item["hop"]),
                    host=str(item["host"]),
                    loss_pct=float(item["loss_pct"]),
                    best_ms=float(item["best_ms"]),
                    avg_ms=float(item["avg_ms"]),
                    worst_ms=float(item["worst_ms"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return hops


def _metric_throughput(metrics: Optional[dict]) -> Optional[ThroughputMbps]:
    raw = metrics or {}
    nested = raw.get("throughput_mbps") if isinstance(raw.get("throughput_mbps"), dict) else {}
    upload = nested.get("upload", raw.get("up_mbps"))
    download = nested.get("download", raw.get("down_mbps"))
    if not isinstance(upload, (int, float)) and not isinstance(download, (int, float)):
        return None
    return ThroughputMbps(
        upload=float(upload) if isinstance(upload, (int, float)) else None,
        download=float(download) if isinstance(download, (int, float)) else None,
    )


_MAX_METRIC_POINTS = 720


def _bounded_metric_rows(db: Session, test_id: UUID, since: datetime) -> list[TestResult]:
    """Evenly spaced samples once a range would pull in more than the chart can show.

    A month of checks is tens of thousands of rows, and each row carries its hop list.
    """
    window = (TestResult.test_id == test_id, TestResult.started_at >= since)
    total = db.scalar(select(func.count()).select_from(TestResult).where(*window)) or 0
    if total <= _MAX_METRIC_POINTS:
        return list(
            db.scalars(select(TestResult).where(*window).order_by(TestResult.started_at)).all()
        )
    step = max(1, math.ceil(total / _MAX_METRIC_POINTS))
    numbered = (
        select(
            TestResult.id.label("id"),
            func.row_number().over(order_by=TestResult.started_at).label("n"),
        )
        .where(*window)
        .subquery()
    )
    chosen = select(numbered.c.id).where(or_((numbered.c.n - 1) % step == 0, numbered.c.n == total))
    return list(
        db.scalars(select(TestResult).where(TestResult.id.in_(chosen)).order_by(TestResult.started_at)).all()
    )


@router.get("/metrics", response_model=MetricsResponse)
def metrics(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    test_id: UUID = Query(...),
    range: str = Query("1h", pattern="^(1h|3h|6h|8h|12h|24h|7d|30d)$"),
) -> MetricsResponse:
    delta = {
        "1h": timedelta(hours=1),
        "3h": timedelta(hours=3),
        "6h": timedelta(hours=6),
        "8h": timedelta(hours=8),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }[range]
    since = datetime.now(timezone.utc) - delta
    test = db.get(Test, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")

    rows = _bounded_metric_rows(db, test_id, since)

    points = [
        MetricsPoint(
            t=r.started_at,
            status=r.status.value,
            rtt_ms=_metric_float(r.metrics, "rtt_ms"),
            loss_pct=_metric_float(r.metrics, "loss_pct"),
            jitter_ms=_metric_float(r.metrics, "jitter_ms"),
            up_mbps=_metric_float(r.metrics, "up_mbps"),
            down_mbps=_metric_float(r.metrics, "down_mbps"),
            hops=_metric_hops(r.metrics),
            throughput_mbps=_metric_throughput(r.metrics),
            stages=r.stages or [],
        )
        for r in rows
    ]
    return MetricsResponse(test_id=test.id, test_slug=test.slug, points=points)


@router.get("/issues", response_model=list[IssueOut])
def list_issues(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    status_filter: Optional[str] = Query(None, alias="status"),
) -> list[IssueOut]:
    stmt = select(Issue).order_by(desc(Issue.opened_at))
    if status_filter:
        try:
            stmt = stmt.where(Issue.status == IssueStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
    issues = db.scalars(stmt).all()
    return [_issue_to_out(db, i, include_failures=False) for i in issues]


@router.get("/issues/{issue_id}", response_model=IssueOut)
def get_issue(
    issue_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> IssueOut:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_to_out(db, issue, include_failures=True)


@router.get("/wifi-scans", response_model=list[WifiObservationOut])
def wifi_scans(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    monitor_id: UUID = Query(...),
    range: str = Query("1h", pattern="^(1h|24h|7d)$"),
) -> list[WifiObservationOut]:
    delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}[range]
    since = datetime.now(timezone.utc) - delta
    if db.get(Monitor, monitor_id) is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    rows = db.scalars(
        select(WifiObservation)
        .where(WifiObservation.monitor_id == monitor_id, WifiObservation.scanned_at >= since)
        .order_by(WifiObservation.scanned_at)
    ).all()
    return [
        WifiObservationOut(
            scanned_at=row.scanned_at,
            ssid=row.ssid,
            bssid=row.bssid,
            vendor=row.vendor,
            band=row.band,
            channel=row.channel,
            channel_width=row.channel_width,
            rssi_dbm=row.rssi_dbm,
            noise_dbm=row.noise_dbm,
            security=row.security,
            phy=row.phy,
            max_link_rate_mbps=row.max_link_rate_mbps,
        )
        for row in rows
    ]

def _issue_to_out(db: Session, issue: Issue, include_failures: bool) -> IssueOut:
    monitor = db.get(Monitor, issue.monitor_id) if issue.monitor_id else None
    network = db.get(Network, issue.network_id) if issue.network_id else None
    test = db.get(Test, issue.test_id) if issue.test_id else None
    failures: list[ResultOut] = []
    if include_failures and issue.test_id:
        rows = db.scalars(
            select(TestResult)
            .where(
                TestResult.test_id == issue.test_id,
                TestResult.status.in_([ResultStatus.fail, ResultStatus.degraded]),
            )
            .options(
                joinedload(TestResult.test).joinedload(Test.monitor),
                joinedload(TestResult.test).joinedload(Test.network),
            )
            .order_by(desc(TestResult.started_at))
            .limit(10)
        ).unique().all()
        failures = [_result_to_out(r) for r in rows]

    return IssueOut(
        id=issue.id,
        title=issue.title,
        summary=issue.summary,
        severity=issue.severity.value,
        status=issue.status.value,
        root_cause_stage=issue.root_cause_stage,
        opened_at=issue.opened_at,
        resolved_at=issue.resolved_at,
        monitor_id=issue.monitor_id,
        monitor_slug=monitor.slug if monitor else None,
        network_id=issue.network_id,
        network_slug=network.slug if network else None,
        test_id=issue.test_id,
        test_slug=test.slug if test else None,
        recent_failures=failures,
    )
