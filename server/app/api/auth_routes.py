"""Sign-in, the current session, and the guest visit log."""

from __future__ import annotations

import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    DUMMY_PASSWORD_HASH,
    account_failures,
    clear_session_cookie,
    create_session_token,
    revoke_sessions,
    session_user,
    csrf_token_for,
    get_current_user,
    login_failures,
    require_admin,
    set_session_cookie,
    verify_password,
)
from app.config import Settings, get_settings
from app.db import get_db
from app import guest_visits as guest_visits_module
from app.models import AuditEvent, User, UserRole
from app.schemas import GuestSummaryOut, GuestVisitOut, LoginRequest, UserOut

router = APIRouter(prefix="/api/v1")

def _user_out(user: User, session_token: str, settings: Settings) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role.value,
        csrf_token=csrf_token_for(session_token, settings),
    )


_PROXY_TTL_SECONDS = 60.0
_proxy_cache: tuple[float, frozenset[str]] = (float("-inf"), frozenset())


def _proxy_addresses(settings: Settings) -> frozenset[str]:
    """The trusted proxies' addresses, with host names resolved and remembered for a minute."""
    global _proxy_cache
    now = time.monotonic()
    if now - _proxy_cache[0] < _PROXY_TTL_SECONDS:
        return _proxy_cache[1]
    found: set[str] = set()
    for item in settings.trusted_proxy_list:
        found.add(item)
        try:
            found.update(socket.gethostbyname_ex(item)[2])
        except OSError:
            continue
    _proxy_cache = (now, frozenset(found))
    return _proxy_cache[1]


def _visitor_address(request: Request, settings: Settings) -> str:
    """Who is signing in. X-Forwarded-For counts only from a trusted proxy (Caddy), and only its last entry.

    Anyone else, such as a client on the agent port or through the tunnel, could write any address there and dodge
    the sign-in limit, so for them the connection's own address is used.
    """
    peer = request.client.host if request.client is not None and request.client.host else ""
    if peer and peer in _proxy_addresses(settings):
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1][:64]
    return peer[:64] or "unknown"


@router.post("/auth/login", response_model=UserOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    user = db.scalar(select(User).where(User.username == body.username))
    address = _visitor_address(request, settings)
    # The public demo account is not limited per account, or anyone could lock every visitor out of it.
    account = None if user is not None and user.role == UserRole.guest else body.username.lower()
    # Hash even when the name is missing, so the two answers take the same time.
    password_ok = verify_password(body.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH)

    def blocked() -> bool:
        return login_failures.blocked(address) or (account is not None and account_failures.blocked(account))

    if blocked() or user is None or not password_ok:
        if user is None or not password_ok:
            login_failures.record(address)
            if account is not None:
                account_failures.record(account)
        if blocked():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in attempts. Wait a few minutes and try again.",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    login_failures.forget(address)
    if account is not None:
        account_failures.forget(account)
    token = create_session_token(user, settings)
    if user.role == UserRole.guest:
        db.add(AuditEvent(username=user.username, action="guest_login", detail=address))
        guest_visits_module.record(db, address)
        db.commit()
        set_session_cookie(response, token, settings, max_age=settings.guest_idle_seconds)
    else:
        set_session_cookie(response, token, settings)
    return _user_out(user, token, settings)


def _issue_guest_session(request: Request, db: Session, settings: Settings) -> tuple[User, str]:
    address = _visitor_address(request, settings)
    if login_failures.blocked(address):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Wait a few minutes and try again.",
        )
    user = db.scalar(select(User).where(User.username == settings.bootstrap_guest_username))
    if user is None or not user.is_active or user.role != UserRole.guest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest access is not available")
    token = create_session_token(user, settings)
    db.add(AuditEvent(username=user.username, action="guest_login", detail=address))
    guest_visits_module.record(db, address)
    db.commit()
    return user, token


def _request_host(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-host", "")
    host = forwarded.split(",")[0].strip() or request.headers.get("host", "")
    return host.split(":")[0].lower()


@router.get("/auth/guest")
def enter_as_guest() -> RedirectResponse:
    """Open the page that signs in as the guest. The cookie is set from that page, not this redirect."""
    return RedirectResponse(url="/welcome/enter.html", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/guest/session", status_code=status.HTTP_204_NO_CONTENT)
def guest_session(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Set the guest session from the public site itself, so the dashboard on that site receives it."""
    origin = request.headers.get("origin", "")
    host = _request_host(request)
    if not origin or origin.split("://", 1)[-1].split(":")[0].lower() != host:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Open the dashboard from this site")
    _user, token = _issue_guest_session(request, db, settings)
    set_session_cookie(response, token, settings, max_age=settings.guest_idle_seconds)


@router.post("/auth/logout")
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    hns_session: Annotated[Optional[str], Cookie(alias="hns_session")] = None,
) -> dict[str, str]:
    """Sign out. For a named account this ends every session it has, so a copied cookie stops working too.

    The guest account is shared by every visitor, so signing one guest out only drops that browser's cookie.
    """
    user = session_user(hns_session, db, settings)
    if user is not None and user.role != UserRole.guest:
        revoke_sessions(user)
        db.add(AuditEvent(username=user.username, action="logout", detail="all sessions ended"))
        db.commit()
    clear_session_cookie(response, settings)
    return {"status": "ok"}


@router.get("/settings/guest-visits", response_model=list[GuestVisitOut])
def guest_visits(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Optional[datetime] = None,
    range: Annotated[str, Query(pattern="^(7d|30d|90d|1y)$")] = guest_visits_module.DEFAULT_RANGE,
) -> list[GuestVisitOut]:
    """Newest first. Pass the last row's time as `before` to read the next page."""
    since = datetime.now(timezone.utc) - timedelta(days=guest_visits_module.range_days(range))
    rows = guest_visits_module.latest(db, limit=limit, before=before, since=since)
    return [
        GuestVisitOut(
            created_at=row.visited_at,
            address=row.address,
            country=row.country,
            country_code=row.country_code,
            region=row.region,
            city=row.city,
        )
        for row in rows
    ]


@router.get("/settings/guest-visits/summary", response_model=GuestSummaryOut)
def guest_visit_summary(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    range: Annotated[str, Query(pattern="^(7d|30d|90d|1y)$")] = guest_visits_module.DEFAULT_RANGE,
) -> GuestSummaryOut:
    """Totals, a series (days up to 90 days, weeks for a year), and the top countries and cities for a range."""
    return GuestSummaryOut(**guest_visits_module.summary(db, range))


@router.get("/auth/me", response_model=UserOut)
def me(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    hns_session: Annotated[Optional[str], Cookie(alias="hns_session")] = None,
) -> UserOut:
    return _user_out(user, hns_session or "", settings)
