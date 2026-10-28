"""Check-in paths the remote agent calls: results, health, and commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from app.api.common import TRACE_WAIT, _resolve_monitor_identity
from app.db import get_db
from app.internet_tests import ensure_internet_tests, scheduled_tests
from app.link_trust import remember_association
from app.models import AdhocTrace, ApInventory, Monitor, MonitorHealth, MonitorStatus, SystemLog
from app.schemas import (
    DeviceConfigOut,
    DeviceHealthIn,
    IngestRequest,
    IngestResponse,
    ProbeCommandOut,
    ScheduledTestOut,
)
from app.services.ingest import ingest_results

router = APIRouter(prefix="/api/v1")

def _trusted_bssids(db: Session, monitor_id: UUID) -> list[str]:
    rows = db.scalars(
        select(ApInventory.bssid).where(ApInventory.monitor_id == monitor_id, ApInventory.trusted.is_(True))
    ).all()
    return sorted(row.lower() for row in rows)


def _config_out(db: Session, monitor: Monitor) -> DeviceConfigOut:
    return DeviceConfigOut(
        name=monitor.name,
        health_interval_seconds=monitor.health_interval_seconds,
        scan_interval_seconds=monitor.scan_interval_seconds,
        config_version=monitor.config_version,
        tests=[ScheduledTestOut(**item) for item in scheduled_tests(db, monitor)],
        restart=monitor.restart_requested_at is not None,
        scan=monitor.scan_requested_at is not None,
        lan_scan=monitor.lan_scan_requested_at is not None,
        trusted_bssids=_trusted_bssids(db, monitor.id),
    )

def _take_trace_requests(db: Session, monitor: Monitor) -> list[dict[str, str]]:
    """Hand the agent the traces waiting for it, once each; a trace it never answers is given up on."""
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(AdhocTrace).where(AdhocTrace.monitor_id == monitor.id, AdhocTrace.state.in_(("pending", "running")))
    ).all()
    out: list[dict[str, str]] = []
    for row in rows:
        if now - row.requested_at > TRACE_WAIT:
            row.state = "failed"
            row.error = "The remote agent did not answer in time"
            row.finished_at = now
        elif row.state == "pending":
            row.state = "running"
            out.append({"id": str(row.id), "target": row.target, "link": row.link})
    if rows:
        db.commit()
    return out


@router.post("/monitors/command", response_model=ProbeCommandOut)
def ack_probe_command(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> ProbeCommandOut:
    """The probe calls this once it is about to replace its process."""
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    monitor.restart_requested_at = None
    db.commit()
    return ProbeCommandOut(
        restart=False,
        scan=monitor.scan_requested_at is not None,
        lan_scan=monitor.lan_scan_requested_at is not None,
        trusted_bssids=_trusted_bssids(db, monitor.id),
    )

@router.post("/ingest", response_model=IngestResponse)
def ingest(
    body: IngestRequest,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> IngestResponse:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    accepted, duplicates, ids = ingest_results(db, monitor, body.results)
    monitor.last_seen_at = datetime.now(timezone.utc)
    monitor.status = MonitorStatus.online
    db.commit()
    return IngestResponse(accepted=accepted, duplicates=duplicates, ids=ids)

@router.post("/monitors/health", response_model=DeviceConfigOut)
def post_device_health(
    body: DeviceHealthIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> DeviceConfigOut:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    now = datetime.now(timezone.utc)
    monitor.last_seen_at = now
    monitor.status = MonitorStatus.online
    monitor.lan_ip = body.lan_ip
    if body.public_ip:
        monitor.public_ip = body.public_ip
    if body.isp:
        monitor.isp = body.isp
    monitor.agent_version = body.agent_version
    monitor.applied_config_version = body.config_version
    cleaned_error = (body.last_error or "").strip()[:600] or None
    prev_error = db.scalar(
        select(MonitorHealth.last_error)
        .where(MonitorHealth.monitor_id == monitor.id)
        .order_by(desc(MonitorHealth.reported_at))
        .limit(1)
    )
    db.add(
        MonitorHealth(
            monitor_id=monitor.id,
            reported_at=now,
            hostname=body.hostname,
            uptime_seconds=body.uptime_seconds,
            cpu_percent=body.cpu_percent,
            load_1=body.load_1,
            mem_used_mb=body.mem_used_mb,
            mem_total_mb=body.mem_total_mb,
            disk_used_mb=body.disk_used_mb,
            disk_total_mb=body.disk_total_mb,
            temp_c=body.temp_c,
            lan_ip=body.lan_ip,
            public_ip=body.public_ip,
            isp=body.isp,
            config_version=body.config_version,
            airwave_mode=body.airwave_mode if body.airwave_mode in {"kismet", "scan", "off"} else None,
            wired_iface=body.wired_iface,
            wifi_link_iface=body.wifi_link_iface,
            wifi_radio_iface=body.wifi_radio_iface,
            last_error=cleaned_error,
        )
    )
    logged_messages: set[str] = set()
    for item in body.logs[:50]:
        msg = item.message.strip()[:600]
        if not msg:
            continue
        logged_messages.add(msg)
        sev = item.severity.lower() if item.severity.lower() in {"info", "warning", "critical"} else "info"
        db.add(
            SystemLog(
                recorded_at=item.recorded_at or now,
                source="agent",
                monitor_id=monitor.id,
                subsystem=(item.subsystem or "agent").strip()[:32] or "agent",
                severity=sev,
                message=msg,
                detail={"hostname": body.hostname, "agent_version": body.agent_version},
            )
        )
    if cleaned_error and cleaned_error != prev_error and cleaned_error not in logged_messages:
        db.add(
            SystemLog(
                recorded_at=now,
                source="agent",
                monitor_id=monitor.id,
                subsystem="health",
                severity="warning",
                message=cleaned_error,
                detail={"hostname": body.hostname, "agent_version": body.agent_version},
            )
        )
    if "link_ssid" in body.model_fields_set or "link_bssid" in body.model_fields_set:
        remember_association(
            db,
            monitor,
            body.link_ssid,
            body.link_bssid,
            ssid_set="link_ssid" in body.model_fields_set,
            bssid_set="link_bssid" in body.model_fields_set,
        )
    cutoff = now - timedelta(days=7)
    db.execute(
        delete(MonitorHealth).where(
            MonitorHealth.monitor_id == monitor.id,
            MonitorHealth.reported_at < cutoff,
        )
    )
    ensure_internet_tests(db, monitor)
    db.commit()
    return _config_out(db, monitor)


@router.get("/monitors/command", response_model=ProbeCommandOut)
def probe_command(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> ProbeCommandOut:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    return ProbeCommandOut(
        restart=monitor.restart_requested_at is not None,
        scan=monitor.scan_requested_at is not None,
        lan_scan=monitor.lan_scan_requested_at is not None,
        traces=_take_trace_requests(db, monitor),
        trusted_bssids=_trusted_bssids(db, monitor.id),
    )
