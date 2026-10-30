"""Monitor, tunnel, and trace routes.

Auth, settings, agent check-in, and dashboard readings live in their own modules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.common import TRACE_WAIT, _device_name, _name_taken, _resolve_monitor_identity, monitor_admin_out
from app.auth import get_current_user, require_admin_csrf
from app.db import get_db
from app.internet_tests import explain_result, load_web_services, select_trace_test
from app.models import (
    AdhocTrace,
    AuditEvent,
    Issue,
    Monitor,
    MonitorHealth,
    MonitorStatus,
    Network,
    SyntheticProfile,
    SyntheticRun,
    Test,
    TestResult,
    User,
    WifiObservation,
)
from app.path_enrich import enrich_hops
from app.path_view import build_trace, resolved_public_target
from app.regions import REGIONS_BY_COUNTRY
from app.schemas import MonitorAdminOut, MonitorLocationIn, TraceRequestIn, TraceResultIn, TunnelIssueOut
from app.wireguard_tunnel import (
    TunnelNotReady,
    allocate_address,
    client_config,
    endpoint,
    new_keypair,
    new_preshared_key,
    refresh_handshakes,
    remove_preshared_key,
    rewrite_peers,
    server_public_key,
)

router = APIRouter(prefix="/api/v1")

@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/monitors", response_model=list[MonitorAdminOut])
def list_monitors(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[MonitorAdminOut]:
    monitors = db.scalars(select(Monitor).order_by(Monitor.name)).all()
    refresh_handshakes(db, list(monitors))
    db.commit()
    return [monitor_admin_out(monitor) for monitor in monitors]


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> None:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    db.add(AuditEvent(username=admin.username, action="delete", monitor_id=monitor.id, detail=monitor.name))
    test_ids = list(db.scalars(select(Test.id).where(Test.monitor_id == monitor.id)))
    now_utc = datetime.now(timezone.utc)
    if test_ids:
        db.execute(delete(TestResult).where(TestResult.test_id.in_(test_ids)))
        for issue in db.scalars(select(Issue).where(Issue.test_id.in_(test_ids))):
            issue.test_id = None
            if issue.status == IssueStatus.open:
                issue.status = IssueStatus.resolved
                issue.resolved_at = now_utc
    for issue in db.scalars(select(Issue).where(Issue.monitor_id == monitor.id)):
        issue.monitor_id = None
        if issue.status == IssueStatus.open:
            issue.status = IssueStatus.resolved
            issue.resolved_at = now_utc
    db.flush()
    if test_ids:
        db.execute(delete(Test).where(Test.monitor_id == monitor.id))
    db.execute(delete(MonitorHealth).where(MonitorHealth.monitor_id == monitor.id))
    db.execute(delete(WifiObservation).where(WifiObservation.monitor_id == monitor.id))
    # Installer codes cascade with the monitor, which releases them for re-registration.
    db.delete(monitor)
    db.commit()
    remove_preshared_key(monitor_id)
    rewrite_peers(db)


@router.post("/monitors/{monitor_id}/tunnel", response_model=TunnelIssueOut)
def issue_tunnels(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> TunnelIssueOut:
    """Create the wired and wireless WireGuard configs. Private keys are shown once."""
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    hub_key = server_public_key()
    hub_endpoint = endpoint()
    if not hub_key or not hub_endpoint:
        raise HTTPException(status_code=503, detail="WireGuard hub is not ready")
    # A device that already has a wired tunnel keeps that tunnel. Only the
    # missing wireless peer is created, so the working wired config stays valid.
    add_wireless_only = bool(monitor.wg_address and monitor.wg_public_key and not monitor.wg_wireless_address)
    try:
        if not monitor.wg_address:
            monitor.wg_address = allocate_address(db, "wired")
        if not monitor.wg_wireless_address:
            monitor.wg_wireless_address = allocate_address(db, "wireless")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    wired_config = ""
    try:
        if add_wireless_only:
            wireless_private, wireless_public = new_keypair()
            monitor.wg_wireless_public_key = wireless_public
            monitor.last_wireless_handshake_at = None
            db.flush()
            wireless_psk = new_preshared_key(monitor.id, "wireless")
        else:
            wired_private, wired_public = new_keypair()
            wireless_private, wireless_public = new_keypair()
            monitor.wg_public_key = wired_public
            monitor.wg_wireless_public_key = wireless_public
            monitor.last_handshake_at = None
            monitor.last_wireless_handshake_at = None
            db.flush()
            wired_psk = new_preshared_key(monitor.id, "wired")
            wireless_psk = new_preshared_key(monitor.id, "wireless")
            wired_config = client_config(
                link="wired",
                private_key=wired_private,
                address=monitor.wg_address or "",
                server_public_key=hub_key,
                endpoint_host=hub_endpoint,
                preshared_key=wired_psk,
            )
    except TunnelNotReady as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="WireGuard hub is not ready") from exc
    db.commit()
    rewrite_peers(db)
    db.refresh(monitor)
    return TunnelIssueOut(
        id=monitor.id,
        name=monitor.name,
        address=monitor.wg_address or "",
        wireless_address=monitor.wg_wireless_address or "",
        config=wired_config,
        wireless_config=client_config(
            link="wireless",
            private_key=wireless_private,
            address=monitor.wg_wireless_address or "",
            server_public_key=hub_key,
            endpoint_host=hub_endpoint,
            preshared_key=wireless_psk,
        ),
    )

@router.post("/monitors/{monitor_id}/restart")
def request_probe_restart(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    if monitor.restart_requested_at is None:
        monitor.restart_requested_at = datetime.now(timezone.utc)
    db.add(AuditEvent(username=admin.username, action="restart", monitor_id=monitor.id, detail=monitor.name))
    db.commit()
    return {
        "id": str(monitor.id),
        "restart_requested_at": monitor.restart_requested_at.isoformat(),
    }


def _metric_number(metrics: dict, key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _latest_test_fields(test: Test, row: TestResult | None) -> dict:
    if row is None:
        return {
            "latest_status": "unknown",
            "latest_at": None,
            "latest_reason": None,
            "latest_rtt_ms": None,
            "latest_loss_pct": None,
        }
    metrics = row.metrics or {}
    reason = row.status_reason or explain_result(
        test.kind.value, row.status.value, metrics, row.stages, row.message
    )
    return {
        "latest_status": row.status.value,
        "latest_at": row.started_at.isoformat(),
        "latest_reason": reason,
        "latest_rtt_ms": _metric_number(metrics, "rtt_ms"),
        "latest_loss_pct": _metric_number(metrics, "loss_pct"),
    }


def _monitor_probes(db: Session, monitor_id: UUID) -> list[dict[str, str]]:
    """Profiles that have been started on this agent, including ones added after the log was built."""
    rows = db.execute(
        select(SyntheticProfile.slug, SyntheticProfile.name, SyntheticProfile.position)
        .join(SyntheticRun, SyntheticRun.profile_slug == SyntheticProfile.slug)
        .where(SyntheticRun.monitor_id == monitor_id)
        .distinct()
        .order_by(SyntheticProfile.position)
    ).all()
    return [{"slug": slug, "name": name} for slug, name, _position in rows]


@router.get("/monitors/{monitor_id}")
def get_monitor(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    tests = db.scalars(
        select(Test)
        .where(Test.monitor_id == monitor_id)
        .options(joinedload(Test.network))
    ).unique().all()

    test_ids = [t.id for t in tests]
    latest: dict = {}
    if test_ids:
        latest_id_subq = (
            select(TestResult.id)
            .where(TestResult.test_id == Test.id)
            .order_by(TestResult.started_at.desc())
            .limit(1)
            .correlate(Test)
            .scalar_subquery()
        )
        latest = {
            r.test_id: r
            for r in db.scalars(
                select(TestResult).where(
                    TestResult.id.in_(select(latest_id_subq).where(Test.id.in_(test_ids)))
                )
            ).all()
        }
    latest_health = db.scalar(
        select(MonitorHealth)
        .where(MonitorHealth.monitor_id == monitor_id)
        .order_by(desc(MonitorHealth.reported_at))
        .limit(1)
    )

    return {
        "id": str(monitor.id),
        "slug": monitor.slug,
        "name": monitor.name,
        "platform": monitor.platform,
        "status": monitor.status.value,
        "last_seen_at": monitor.last_seen_at.isoformat() if monitor.last_seen_at else None,
        "latitude": monitor.latitude,
        "longitude": monitor.longitude,
        "location_label": monitor.location_label,
        "lan_ip": monitor.lan_ip,
        "tunnel_address": monitor.wg_address,
        "wireless_tunnel_address": monitor.wg_wireless_address,
        "public_ip": monitor.public_ip,
        "isp": monitor.isp,
        "wired_iface": latest_health.wired_iface if latest_health else None,
        "wifi_link_iface": latest_health.wifi_link_iface if latest_health else None,
        "wifi_radio_iface": latest_health.wifi_radio_iface if latest_health else None,
        "airwave_mode": latest_health.airwave_mode if latest_health else None,
        "probes": _monitor_probes(db, monitor_id),
        "tests": [
            {
                "id": str(t.id),
                "slug": t.slug,
                "name": t.name,
                "kind": t.kind.value,
                "target": t.target,
                "network_id": str(t.network_id),
                "network_slug": t.network.slug,
                "network_name": t.network.name,
                **_latest_test_fields(t, latest.get(t.id)),
                "enabled": t.enabled,
            }
            for t in tests
        ],
    }


@router.get("/monitors/{monitor_id}/path")
def monitor_path(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    link: str = Query(pattern="^(wired|wireless)$"),
    destination: str = Query(default="server", pattern="^[a-z0-9-]{1,32}$"),
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    if destination == "server":
        slug = "mtr-path"
        target_name = "HomeNetWatch server"
        target_ip = ""
    else:
        service = next((row for row in load_web_services(db) if row.key == destination), None)
        if service is None:
            raise HTTPException(status_code=404, detail="Destination not found")
        slug = f"mtr-{service.key}"
        target_name = service.label
        target_ip = service.host
    network_slug = "wired" if link == "wired" else "wifi"
    test = select_trace_test(db, monitor_id, slug, network_slug)
    rows = []
    if test is not None:
        target_ip = test.target.split("/")[0] or target_ip
        rows = list(
            db.scalars(
                select(TestResult)
                .where(TestResult.test_id == test.id)
                .order_by(desc(TestResult.started_at))
                .limit(24)
            ).all()
        )
    def _hop_list(row: TestResult) -> list:
        hops = (row.metrics or {}).get("hops")
        return hops if isinstance(hops, list) else []

    latest = next((row for row in rows if _hop_list(row)), None)
    raw_hops = _hop_list(latest) if latest else []
    history: dict[str, list[float | None]] = {}
    for row in reversed(rows):
        for item in (row.metrics or {}).get("hops") or []:
            if not isinstance(item, dict):
                continue
            ttl = item.get("hop")
            if ttl is None:
                continue
            host = str(item.get("host") or "")
            loss = item.get("loss_pct")
            avg = item.get("avg_ms")
            silent = host.strip() in {"", "?", "???", "*", "***"} or (
                isinstance(loss, (int, float)) and loss >= 100 and (not isinstance(avg, (int, float)) or avg <= 0)
            )
            history.setdefault(str(int(ttl)), []).append(None if silent or not isinstance(avg, (int, float)) else float(avg))
    return _path_body(
        monitor,
        raw_hops if isinstance(raw_hops, list) else [],
        history,
        target_name=target_name,
        target_ip=target_ip,
        traced_at=latest.started_at if latest else None,
    )


def _path_body(
    monitor: Monitor,
    raw_hops: list,
    history: dict[str, list[float | None]],
    *,
    target_name: str,
    target_ip: str,
    traced_at: datetime | None,
) -> dict:
    enriched, announced, as_names, rpki = enrich_hops(raw_hops)
    body = build_trace(
        enriched,
        isp_name=monitor.isp,
        target_name=target_name,
        announced=announced,
        as_names=as_names,
    )
    if rpki:
        body["bgp"]["rpki"] = rpki
    body["status"] = "complete" if body["hops"] else "empty"
    body["traced_at"] = traced_at.isoformat() if traced_at else None
    body["probes_per_hop"] = 5
    body["agent"] = {"name": monitor.name, "online": monitor.status == MonitorStatus.online}
    body["target"] = {"name": target_name, "ip": target_ip}
    body["history"] = history
    return body


@router.post("/monitors/{monitor_id}/traces")
def request_trace(
    monitor_id: UUID,
    body: TraceRequestIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict:
    """Ask the agent to trace the path to a host someone typed in. The result is read back from the trace's id."""
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    target = resolved_public_target(body.target)
    if target is None:
        raise HTTPException(status_code=422, detail="Enter a public host name or IP address")
    now = datetime.now(timezone.utc)
    busy = db.scalar(
        select(func.count())
        .select_from(AdhocTrace)
        .where(
            AdhocTrace.monitor_id == monitor.id,
            AdhocTrace.state.in_(("pending", "running")),
            AdhocTrace.requested_at > now - TRACE_WAIT,
        )
    )
    if busy:
        raise HTTPException(status_code=409, detail="A trace is already running on this agent")
    recent = db.scalar(
        select(func.count()).select_from(AdhocTrace).where(AdhocTrace.monitor_id == monitor.id, AdhocTrace.requested_at > now - timedelta(hours=1))
    )
    if (recent or 0) >= 30:
        raise HTTPException(status_code=429, detail="Too many traces this hour")
    db.execute(delete(AdhocTrace).where(AdhocTrace.monitor_id == monitor.id, AdhocTrace.requested_at < now - timedelta(days=7)))
    row = AdhocTrace(monitor_id=monitor.id, link=body.link, target=target, requested_by=admin.username)
    db.add(row)
    db.add(AuditEvent(username=admin.username, action="trace", monitor_id=monitor.id, detail=f"{body.link} {target}"))
    db.commit()
    return {"id": str(row.id), "state": row.state, "target": target}


@router.get("/monitors/{monitor_id}/traces/{trace_id}")
def read_trace(
    monitor_id: UUID,
    trace_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    row = db.get(AdhocTrace, trace_id)
    if monitor is None or row is None or row.monitor_id != monitor.id:
        raise HTTPException(status_code=404, detail="Trace not found")
    if row.state in ("pending", "running") and datetime.now(timezone.utc) - row.requested_at > TRACE_WAIT:
        row.state = "failed"
        row.error = "The remote agent did not answer in time"
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
    hops = row.hops if isinstance(row.hops, list) else []
    body = _path_body(monitor, hops, {}, target_name=row.target, target_ip=row.target, traced_at=row.finished_at)
    body["trace_id"] = str(row.id)
    body["state"] = row.state
    body["error"] = row.error
    return body


@router.post("/monitors/traces/{trace_id}/result")
def submit_trace_result(
    trace_id: UUID,
    body: TraceResultIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict:
    """The agent hands back what it measured for a trace it was asked to run."""
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    row = db.get(AdhocTrace, trace_id)
    if row is None or row.monitor_id != monitor.id:
        raise HTTPException(status_code=404, detail="Trace not found")
    if row.state in ("done", "failed"):
        return {"state": row.state}
    row.hops = body.hops
    row.finished_at = datetime.now(timezone.utc)
    row.state = "done" if body.hops else "failed"
    row.error = "" if body.hops else (body.error or "The trace returned no hops")
    db.commit()
    return {"state": row.state}


_HEALTH_RANK = {"ok": 0, "degraded": 1, "fail": 2}


@router.get("/monitors/{monitor_id}/health")
def monitor_health(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    range: str = Query("1h", pattern="^(1h|24h|7d|30d)$"),
) -> dict:
    if db.get(Monitor, monitor_id) is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[range]
    since = datetime.now(timezone.utc) - delta
    rows = db.execute(
        select(TestResult.started_at, TestResult.status)
        .join(Test, Test.id == TestResult.test_id)
        .where(Test.monitor_id == monitor_id, TestResult.started_at >= since)
        .order_by(TestResult.started_at)
    ).all()
    buckets: dict[datetime, str] = {}
    for started_at, status in rows:
        moment = started_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        current = buckets.get(moment)
        value = status.value
        if current is None or _HEALTH_RANK[value] > _HEALTH_RANK[current]:
            buckets[moment] = value
    return {
        "monitor_id": str(monitor_id),
        "points": [{"t": moment.isoformat(), "status": buckets[moment]} for moment in sorted(buckets)],
    }


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _sample_out(row: MonitorHealth) -> dict:
    return {
        "reported_at": _as_utc(row.reported_at).isoformat(),
        "hostname": row.hostname,
        "uptime_seconds": row.uptime_seconds,
        "cpu_percent": row.cpu_percent,
        "load_1": row.load_1,
        "mem_used_mb": row.mem_used_mb,
        "mem_total_mb": row.mem_total_mb,
        "disk_used_mb": row.disk_used_mb,
        "disk_total_mb": row.disk_total_mb,
        "temp_c": row.temp_c,
        "lan_ip": row.lan_ip,
        "last_error": row.last_error,
    }


@router.get("/monitors/{monitor_id}/device")
def get_device(
    monitor_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    rows = db.scalars(
        select(MonitorHealth)
        .where(MonitorHealth.monitor_id == monitor_id, MonitorHealth.reported_at >= since)
        .order_by(MonitorHealth.reported_at)
    ).all()
    latest = rows[-1] if rows else db.scalar(
        select(MonitorHealth)
        .where(MonitorHealth.monitor_id == monitor_id)
        .order_by(desc(MonitorHealth.reported_at))
        .limit(1)
    )
    connected = False
    if latest is not None:
        interval = max(monitor.health_interval_seconds or 60, 15)
        connected = now - _as_utc(latest.reported_at) <= timedelta(seconds=interval * 2)
    return {
        "monitor_id": str(monitor.id),
        "name": monitor.name,
        "connected": connected,
        "last_seen_at": monitor.last_seen_at.isoformat() if monitor.last_seen_at else None,
        "lan_ip": monitor.lan_ip,
        "public_ip": monitor.public_ip,
        "isp": monitor.isp,
        "agent_version": monitor.agent_version,
        "hostname": latest.hostname if latest else None,
        "config_version": monitor.config_version,
        "applied_config_version": monitor.applied_config_version,
        "health_interval_seconds": monitor.health_interval_seconds,
        "restart_requested_at": monitor.restart_requested_at.isoformat() if monitor.restart_requested_at else None,
        "location_label": monitor.location_label,
        "region": monitor.region,
        "country": monitor.country,
        "latitude": monitor.latitude,
        "longitude": monitor.longitude,
        "latest": _sample_out(latest) if latest else None,
        "points": [
            {
                "t": _as_utc(row.reported_at).isoformat(),
                "cpu_percent": row.cpu_percent,
                "temp_c": row.temp_c,
                "mem_used_mb": row.mem_used_mb,
                "disk_used_mb": row.disk_used_mb,
            }
            for row in rows
        ],
    }


@router.patch("/monitors/{monitor_id}")
def update_monitor_location(
    monitor_id: UUID,
    body: MonitorLocationIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    sent = body.model_fields_set
    if "latitude" in sent or "longitude" in sent:
        if "latitude" not in sent or "longitude" not in sent:
            raise HTTPException(status_code=400, detail="Latitude and longitude are set together")
        if (body.latitude is None) != (body.longitude is None):
            raise HTTPException(status_code=400, detail="Latitude and longitude are set together")
        if body.latitude is not None and not -90 <= body.latitude <= 90:
            raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90")
        if body.longitude is not None and not -180 <= body.longitude <= 180:
            raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180")
        monitor.latitude = body.latitude
        monitor.longitude = body.longitude
    if "location_label" in sent:
        label = (body.location_label or "").strip()
        monitor.location_label = label or None
    if "country" in sent:
        country = (body.country or "").strip()
        if country and country not in {"United States", "Canada"}:
            raise HTTPException(status_code=400, detail="Country must be United States or Canada")
        monitor.country = country or None
    if "region" in sent:
        region = (body.region or "").strip()
        country_name = monitor.country or ""
        allowed = REGIONS_BY_COUNTRY.get(country_name, set())
        if region and region not in allowed:
            raise HTTPException(status_code=400, detail="State or province does not match the country")
        monitor.region = region or None
    config_changed = False
    if "name" in sent:
        name = _device_name(body.name or "")
        if name != monitor.name:
            if _name_taken(db, monitor.group_id, name, monitor.id):
                raise HTTPException(status_code=409, detail="A remote agent with this device name already exists")
            monitor.name = name
            config_changed = True
    if "health_interval_seconds" in sent:
        interval = body.health_interval_seconds
        if interval is None or not 15 <= interval <= 3600:
            raise HTTPException(status_code=400, detail="Report interval must be between 15 and 3600 seconds")
        if interval != monitor.health_interval_seconds:
            monitor.health_interval_seconds = interval
            config_changed = True
    if config_changed:
        monitor.config_version += 1
    db.commit()
    return {
        "id": str(monitor.id),
        "name": monitor.name,
        "latitude": monitor.latitude,
        "longitude": monitor.longitude,
        "location_label": monitor.location_label,
        "region": monitor.region,
        "country": monitor.country,
        "health_interval_seconds": monitor.health_interval_seconds,
        "config_version": monitor.config_version,
    }


@router.get("/tests/{test_id}")
def get_test(
    test_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    test = db.scalar(
        select(Test)
        .where(Test.id == test_id)
        .options(joinedload(Test.monitor), joinedload(Test.network))
    )
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    return {
        "id": str(test.id),
        "slug": test.slug,
        "name": test.name,
        "kind": test.kind.value,
        "target": test.target,
        "interval_seconds": test.interval_seconds,
        "monitor_id": str(test.monitor_id),
        "monitor_slug": test.monitor.slug,
        "monitor_name": test.monitor.name,
        "network_id": str(test.network_id),
        "network_slug": test.network.slug,
        "network_name": test.network.name,
    }
