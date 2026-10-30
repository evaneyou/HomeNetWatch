"""Installer-code enrollment for remote agents.

Agent endpoints are signed with the agent's Ed25519 identity key and only served over HTTPS.
Admin endpoints need an admin session and a CSRF token, and each one writes an audit record.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app import tls
from app.api.common import _device_name, _home_group, _name_taken, _unique_slug, monitor_admin_out
from app.auth import get_current_user, require_admin_csrf
from app.config import get_settings
from app.db import get_db
from app.enrollment import (
    CHECKIN_TTL,
    CODE_TTL,
    MAX_WAITING,
    NONCE_TTL_SECONDS,
    PSK_RESEND_AFTER,
    clean_hostname,
    clean_lan_ip,
    decode_key,
    installer_code_hash,
    limiter,
    new_token,
    nonces,
    normalize_installer_code,
    signed_message,
    verify_signature,
)
from app.models import AuditEvent, EnrollmentCheckin, InstallerCode, Monitor, MonitorStatus, User
from app.schemas import (
    ApproveIn,
    CheckInIn,
    CheckinOut,
    CheckInOut,
    InstallerCodeIn,
    MonitorAdminOut,
    NonceIn,
    NonceOut,
    PollIn,
    PollOut,
    RegistrationOut,
    TunnelConfigOut,
)
from app.wireguard_tunnel import (
    PERSISTENT_KEEPALIVE,
    TUNNEL_SERVER_IP,
    WIRELESS_SERVER_IP,
    TunnelNotReady,
    allocate_address,
    ensure_srtp_key,
    endpoint,
    new_preshared_key,
    read_preshared_key,
    refresh_handshakes,
    rewrite_peers,
    server_public_key,
)

router = APIRouter(prefix="/api/v1")

NONCE_PATH = "/api/v1/agents/nonce"
CHECK_IN_PATH = "/api/v1/agents/check-in"
POLL_PATH = "/api/v1/agents/poll"

_WAITING_FOR_CODE = "Waiting for this code to be registered"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_https(request: Request) -> None:
    if get_settings().agent_require_https and request.url.scheme != "https":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Use the HTTPS port")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _approved(db: Session, identity_public_key: str) -> Monitor | None:
    return db.scalar(select(Monitor).where(Monitor.identity_public_key == identity_public_key))


def _check_key(value: str) -> None:
    try:
        decode_key(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _verify(path: str, identity_public_key: str, nonce: str, fields: list[str], signature: str) -> None:
    rejected = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature check failed")
    if not nonces.consume(nonce, identity_public_key):
        raise rejected
    try:
        message = signed_message(path, nonce, [identity_public_key, *fields])
    except ValueError as exc:
        raise rejected from exc
    if not verify_signature(identity_public_key, message, signature):
        raise rejected


def _rate_limit(db: Session, request: Request, identity_public_key: str) -> None:
    if _approved(db, identity_public_key) is not None:
        return
    if not limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests, try again soon")


@router.post("/agents/nonce", response_model=NonceOut)
def agent_nonce(body: NonceIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> NonceOut:
    _require_https(request)
    _check_key(body.identity_public_key)
    _rate_limit(db, request, body.identity_public_key)
    nonce = nonces.issue(body.identity_public_key)
    if nonce is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Busy, try again soon")
    return NonceOut(nonce=nonce, expires_in=NONCE_TTL_SECONDS, server_time=_now())


@router.post("/agents/check-in", response_model=CheckInOut)
def agent_check_in(body: CheckInIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> CheckInOut:
    _require_https(request)
    _check_key(body.identity_public_key)
    _check_key(body.wg_public_key)
    signed_fields = [body.wg_public_key]
    if body.wg_wireless_public_key:
        _check_key(body.wg_wireless_public_key)
        signed_fields.append(body.wg_wireless_public_key)
    signed_fields.extend([body.installer_code, body.hostname, body.lan_ip])
    if body.platform:
        signed_fields.append(body.platform)
    if body.device_name:
        signed_fields.append(body.device_name)
    _rate_limit(db, request, body.identity_public_key)
    _verify(
        CHECK_IN_PATH,
        body.identity_public_key,
        body.nonce,
        signed_fields,
        body.signature,
    )
    now = _now()
    if _approved(db, body.identity_public_key) is not None:
        return CheckInOut(state="approved", server_time=now)
    try:
        hostname = clean_hostname(body.hostname)
        lan_ip = clean_lan_ip(body.lan_ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    waiting = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_WAITING_FOR_CODE)
    try:
        code_hash = installer_code_hash(normalize_installer_code(body.installer_code))
    except ValueError as exc:
        raise waiting from exc
    code = db.scalar(
        select(InstallerCode).where(
            InstallerCode.code_hash == code_hash,
            InstallerCode.monitor_id.is_(None),
            InstallerCode.expires_at > now,
        )
    )
    if code is None:
        raise waiting

    db.execute(delete(EnrollmentCheckin).where(EnrollmentCheckin.last_seen_at < now - CHECKIN_TTL))
    row = db.scalar(
        select(EnrollmentCheckin).where(
            EnrollmentCheckin.code_id == code.id,
            EnrollmentCheckin.identity_public_key == body.identity_public_key,
        )
    )
    if row is None:
        if (db.scalar(select(func.count(EnrollmentCheckin.id))) or 0) >= MAX_WAITING:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many remote agents are waiting for approval",
            )
        row = EnrollmentCheckin(
            code_id=code.id,
            identity_public_key=body.identity_public_key,
            first_seen_at=now,
        )
        db.add(row)
    row.wg_public_key = body.wg_public_key
    row.wg_wireless_public_key = body.wg_wireless_public_key
    row.platform = body.platform or "pi"
    row.device_name = _clean_chosen_name(body.device_name)
    row.hostname = hostname
    row.lan_ip = lan_ip
    row.observed_ip = _client_ip(request)
    row.last_seen_at = now
    db.flush()
    count = db.scalar(select(func.count(EnrollmentCheckin.id)).where(EnrollmentCheckin.code_id == code.id)) or 0
    db.commit()
    return CheckInOut(state="waiting_for_approval", conflict=count > 1, server_time=now)


@router.post("/agents/poll", response_model=PollOut)
def agent_poll(body: PollIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> PollOut:
    _require_https(request)
    _check_key(body.identity_public_key)
    _verify(
        POLL_PATH,
        body.identity_public_key,
        body.nonce,
        ["1" if body.have_token else "0"],
        body.signature,
    )
    now = _now()
    monitor = _approved(db, body.identity_public_key)
    if monitor is None:
        pending = db.scalar(
            select(EnrollmentCheckin.id).where(
                EnrollmentCheckin.identity_public_key == body.identity_public_key,
                EnrollmentCheckin.last_seen_at >= now - CHECKIN_TTL,
            )
        )
        if pending is not None:
            return PollOut(state="waiting_for_approval", server_time=now)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This remote agent is not on the dashboard")

    refresh_handshakes(db, [monitor])
    token = None
    if not monitor.token_hash or not body.have_token:
        token, monitor.token_hash = new_token()
    db.commit()
    wired = _tunnel_config(monitor, now, "wired")
    wireless = _tunnel_config(monitor, now, "wireless")
    return PollOut(
        state="approved",
        server_time=now,
        monitor_id=monitor.id,
        name=monitor.name,
        token=token,
        tunnel=wired,
        tunnels=[item for item in (wired, wireless) if item is not None],
    )


def _tunnel_config(monitor: Monitor, now: datetime, link: str = "wired") -> TunnelConfigOut | None:
    hub_key = server_public_key()
    hub_endpoint = endpoint()
    if link == "wireless":
        address = monitor.wg_wireless_address
        preshared = read_preshared_key(monitor.id, "wireless")
        handshake = monitor.last_wireless_handshake_at
        server_ip = WIRELESS_SERVER_IP
    else:
        address = monitor.wg_address
        preshared = read_preshared_key(monitor.id, "wired")
        handshake = monitor.last_handshake_at
        server_ip = TUNNEL_SERVER_IP
    if not hub_key or not hub_endpoint or not address or preshared is None:
        return None
    recent = handshake is not None and now - handshake <= PSK_RESEND_AFTER
    return TunnelConfigOut(
        link=link,
        address=address,
        server_public_key=hub_key,
        endpoint=hub_endpoint,
        allowed_ips=f"{server_ip}/32",
        persistent_keepalive=PERSISTENT_KEEPALIVE,
        preshared_key=None if recent else preshared,
    )


def setup_key(request: Request) -> str | None:
    """`hnw1.` and the URL-safe base64 of {"u": server address, "p": [TLS key pins]}.

    The address is the host the dashboard was opened on, so it is the one the agent can reach. Without a key file
    there is nothing to pin, and the dashboard says so instead of showing a key that would not work.
    """
    pins = tls.pin_list(Path(get_settings().tls_dir))
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else ""
    if not pins or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", name):
        return None
    payload = json.dumps({"u": f"https://{name}", "p": pins}, separators=(",", ":"))
    return "hnw1." + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _clean_chosen_name(raw: str | None) -> str | None:
    """The name an agent asked for. Empty means the dashboard's registration name is used."""
    name = " ".join((raw or "").split())
    return name[:128] or None


def _registration_out(code: InstallerCode, now: datetime, key: str | None = None) -> RegistrationOut:
    fresh = sorted(
        (row for row in code.checkins if row.last_seen_at >= now - CHECKIN_TTL),
        key=lambda row: row.first_seen_at,
    )
    return RegistrationOut(
        id=code.id,
        device_name=code.device_name,
        location=code.location_label,
        registered_by=code.registered_by,
        created_at=code.created_at,
        expires_at=code.expires_at,
        conflict=len(fresh) > 1,
        setup_key=key,
        checkins=[
            CheckinOut(
                id=row.id,
                hostname=row.hostname,
                device_name=row.device_name,
                lan_ip=row.lan_ip,
                observed_ip=row.observed_ip,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
            )
            for row in fresh
        ],
    )


def _open_code(db: Session, code_id: UUID) -> InstallerCode:
    code = db.scalar(
        select(InstallerCode)
        .where(InstallerCode.id == code_id, InstallerCode.monitor_id.is_(None))
        .options(selectinload(InstallerCode.checkins))
    )
    if code is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    return code


def _audit(db: Session, admin: User, action: str, detail: str, monitor_id: UUID | None = None) -> None:
    db.add(AuditEvent(username=admin.username, action=action, monitor_id=monitor_id, detail=detail))


@router.get("/enrollment", response_model=list[RegistrationOut])
def list_registrations(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[RegistrationOut]:
    now = _now()
    codes = db.scalars(
        select(InstallerCode)
        .where(InstallerCode.monitor_id.is_(None), InstallerCode.expires_at > now)
        .options(selectinload(InstallerCode.checkins))
        .order_by(InstallerCode.created_at)
    ).all()
    key = setup_key(request)
    return [_registration_out(code, now, key) for code in codes]


@router.post("/enrollment/codes", response_model=RegistrationOut, status_code=201)
def register_code(
    body: InstallerCodeIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> RegistrationOut:
    try:
        code_hash = installer_code_hash(normalize_installer_code(body.installer_code))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    device_name = _device_name(body.device_name)
    location = (body.location or "").strip() or None
    now = _now()
    code = db.scalar(select(InstallerCode).where(InstallerCode.code_hash == code_hash))
    if code is not None and code.monitor_id is not None:
        owner = db.get(Monitor, code.monitor_id)
        raise HTTPException(status_code=409, detail=f"This code already belongs to {owner.name if owner else 'a remote agent'}")
    if code is not None and code.expires_at > now:
        raise HTTPException(status_code=409, detail="This code is already registered")
    if code is None:
        code = InstallerCode(code_hash=code_hash)
        db.add(code)
    else:
        db.execute(delete(EnrollmentCheckin).where(EnrollmentCheckin.code_id == code.id))
    code.device_name = device_name
    code.location_label = location
    code.registered_by = admin.username
    code.created_at = now
    code.expires_at = now + CODE_TTL
    _audit(db, admin, "register_code", device_name)
    db.commit()
    db.expire_all()
    return _registration_out(_open_code(db, code.id), now, setup_key(request))


@router.delete("/enrollment/codes/{code_id}", status_code=204)
def remove_code(
    code_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> None:
    code = _open_code(db, code_id)
    _audit(db, admin, "remove_code", code.device_name)
    db.delete(code)
    db.commit()


@router.post("/enrollment/codes/{code_id}/start-over", response_model=RegistrationOut)
def start_over(
    code_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> RegistrationOut:
    code = _open_code(db, code_id)
    db.execute(delete(EnrollmentCheckin).where(EnrollmentCheckin.code_id == code.id))
    _audit(db, admin, "start_over", code.device_name)
    db.commit()
    db.expire_all()
    return _registration_out(_open_code(db, code_id), _now(), setup_key(request))


@router.post("/enrollment/codes/{code_id}/approve", response_model=MonitorAdminOut)
def approve(
    code_id: UUID,
    body: ApproveIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> MonitorAdminOut:
    now = _now()
    code = _open_code(db, code_id)
    # Check-ins are inserted by identity key, not through this relationship. A shared
    # session can still be holding the empty list from Start over, so read the table.
    db.expire(code, ["checkins"])
    if code.expires_at <= now:
        raise HTTPException(status_code=409, detail="This registration expired. Register the code again.")
    fresh = [row for row in code.checkins if row.last_seen_at >= now - CHECKIN_TTL]
    if not fresh:
        raise HTTPException(status_code=409, detail="No remote agent has checked in with this code yet")
    if len(fresh) > 1:
        raise HTTPException(
            status_code=409,
            detail="More than one device used this code. Choose Start over before approving.",
        )
    checkin = fresh[0]
    # A device that was given a name during its own setup keeps it. Otherwise the registration name is used.
    final_name = checkin.device_name or code.device_name
    if _approved(db, checkin.identity_public_key) is not None:
        raise HTTPException(status_code=409, detail="This remote agent is already on the dashboard")

    name_taken = HTTPException(status_code=409, detail="A remote agent with this device name already exists")
    if body.replace_monitor_id is None:
        group = _home_group(db)
        if _name_taken(db, group.id, final_name):
            raise name_taken
        try:
            address = allocate_address(db)
            wireless_address = (
                allocate_address(db, "wireless") if checkin.wg_wireless_public_key else None
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        monitor = Monitor(
            group_id=group.id,
            slug=_unique_slug(db, group.id, final_name),
            name=final_name,
            location_label=code.location_label,
            status=MonitorStatus.unknown,
            config_version=1,
            applied_config_version=0,
            health_interval_seconds=60,
            scan_interval_seconds=300,
            wg_address=address,
            wg_wireless_address=wireless_address,
            platform=checkin.platform,
        )
        db.add(monitor)
        action = "approve"
    else:
        monitor = db.get(Monitor, body.replace_monitor_id)
        if monitor is None:
            raise HTTPException(status_code=404, detail="Remote agent not found")
        if _name_taken(db, monitor.group_id, final_name, monitor.id):
            raise name_taken
        if monitor.name != final_name:
            monitor.name = final_name
            monitor.config_version += 1
        if code.location_label:
            monitor.location_label = code.location_label
        # The old Pi's own code is released so it can be registered again later.
        db.execute(delete(InstallerCode).where(InstallerCode.monitor_id == monitor.id))
        if not monitor.wg_address:
            monitor.wg_address = allocate_address(db)
        if checkin.wg_wireless_public_key and not monitor.wg_wireless_address:
            monitor.wg_wireless_address = allocate_address(db, "wireless")
        monitor.token_hash = None
        monitor.last_handshake_at = None
        if checkin.wg_wireless_public_key:
            monitor.last_wireless_handshake_at = None
        action = "replace"

    monitor.identity_public_key = checkin.identity_public_key
    monitor.platform = checkin.platform
    monitor.wg_public_key = checkin.wg_public_key
    if checkin.wg_wireless_public_key:
        monitor.wg_wireless_public_key = checkin.wg_wireless_public_key
    monitor.lan_ip = checkin.lan_ip
    db.flush()
    try:
        new_preshared_key(monitor.id, "wired")
        if monitor.wg_wireless_public_key:
            new_preshared_key(monitor.id, "wireless")
        ensure_srtp_key(monitor.id)
    except TunnelNotReady as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="WireGuard hub is not ready") from exc
    code.monitor_id = monitor.id
    db.execute(delete(EnrollmentCheckin).where(EnrollmentCheckin.code_id == code.id))
    _audit(db, admin, action, f"{monitor.name} ({checkin.hostname})", monitor.id)
    db.commit()
    rewrite_peers(db)
    db.refresh(monitor)
    return monitor_admin_out(monitor)
