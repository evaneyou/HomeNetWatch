"""Names, identity, and monitor rows shared by more than one route module."""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import verify_password
from app.enrollment import CONNECTED_WITHIN, is_token_hash, token_hash
from app.models import Group, Monitor
from app.schemas import MonitorAdminOut

# How long a requested trace stays with the agent before it is given up on.
TRACE_WAIT = timedelta(seconds=90)


def _resolve_monitor_identity(
    db: Session,
    authorization: Optional[str],
    device_name: Optional[str],
) -> Monitor:
    """The device name and key must both match. The name comparison is case-sensitive."""
    presented = urllib.parse.unquote(device_name or "").strip()
    key = ""
    if authorization and authorization.lower().startswith("bearer "):
        key = authorization.split(" ", 1)[1].strip()
    mismatch = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Device name and key do not match",
    )
    if not key:
        raise mismatch
    issued = db.scalar(select(Monitor).where(Monitor.token_hash == token_hash(key)))
    if issued is not None:
        if presented and issued.name != presented:
            raise mismatch
        return issued
    legacy = select(Monitor).where(Monitor.token_hash.is_not(None))
    if presented:
        legacy = legacy.where(Monitor.name == presented)
    for monitor in db.scalars(legacy).all():
        if is_token_hash(monitor.token_hash):
            continue
        try:
            if verify_password(key, monitor.token_hash):
                return monitor
        except ValueError:
            continue
    raise mismatch

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    if not slug:
        raise HTTPException(status_code=400, detail="Name needs a letter or number")
    return slug


def _unique_slug(db: Session, group_id: UUID, name: str) -> str:
    base = _slugify(name)
    slug = base
    number = 2
    while db.scalar(select(Monitor).where(Monitor.group_id == group_id, Monitor.slug == slug)) is not None:
        suffix = f"-{number}"
        slug = f"{base[: 64 - len(suffix)]}{suffix}"
        number += 1
    return slug


def _device_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 128:
        raise HTTPException(status_code=400, detail="Name must be 1 to 128 characters")
    return name


def _name_taken(db: Session, group_id: UUID, name: str, ignore_id: UUID | None = None) -> bool:
    query = select(Monitor).where(Monitor.group_id == group_id, Monitor.name == name)
    if ignore_id is not None:
        query = query.where(Monitor.id != ignore_id)
    return db.scalar(query) is not None


def _home_group(db: Session) -> Group:
    group = db.scalar(select(Group).where(Group.slug == "demo-home"))
    if group is None:
        group = db.scalar(select(Group).order_by(Group.created_at))
    if group is None:
        raise HTTPException(status_code=400, detail="No site is configured")
    return group

def _handshake_state(identity: str | None, handshake: datetime | None) -> str | None:
    if identity is None:
        return None
    if handshake is None:
        return "never_connected"
    if datetime.now(timezone.utc) - handshake <= CONNECTED_WITHIN:
        return "connected"
    return "disconnected"


def _tunnel_state(monitor: Monitor) -> str | None:
    return _handshake_state(monitor.identity_public_key, monitor.last_handshake_at)


def monitor_admin_out(monitor: Monitor) -> MonitorAdminOut:
    return MonitorAdminOut(
        id=monitor.id,
        slug=monitor.slug,
        name=monitor.name,
        platform=monitor.platform,
        status=monitor.status.value,
        last_seen_at=monitor.last_seen_at,
        lan_ip=monitor.lan_ip,
        public_ip=monitor.public_ip,
        isp=monitor.isp,
        agent_version=monitor.agent_version,
        location_label=monitor.location_label,
        region=monitor.region,
        country=monitor.country,
        tunnel_address=monitor.wg_address,
        wireless_tunnel_address=monitor.wg_wireless_address,
        tunnel_state=_tunnel_state(monitor),
        wireless_tunnel_state=_handshake_state(
            monitor.identity_public_key if monitor.wg_wireless_address else None,
            monitor.last_wireless_handshake_at,
        ),
        last_handshake_at=monitor.last_handshake_at,
        health_interval_seconds=monitor.health_interval_seconds,
        config_version=monitor.config_version,
        applied_config_version=monitor.applied_config_version,
        restart_requested_at=monitor.restart_requested_at,
    )


def agent_links(monitor: Monitor) -> tuple[str, ...]:
    """The links an agent can test. An Android agent (an NVIDIA Shield) is on Wi-Fi only."""
    return ("wireless",) if monitor.platform == "android" else ("wired", "wireless")


def require_agent_links(monitor: Monitor, links: list[str]) -> None:
    """Refuse a test on a link this agent does not have, whatever the dashboard sent."""
    if any(link not in agent_links(monitor) for link in links):
        raise HTTPException(
            status_code=400,
            detail=f"{monitor.name} is an Android agent on Wi-Fi only. Choose Wi-Fi.",
        )
