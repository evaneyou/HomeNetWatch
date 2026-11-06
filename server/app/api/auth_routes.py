"""Sign-in, the current session, and the guest visit log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    DUMMY_PASSWORD_HASH,
    clear_session_cookie,
    create_session_token,
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


def _visitor_address(request: Request) -> str:
    """The last forwarded hop is the one the proxy added. A client-supplied prefix is ignored."""
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if parts:
        return parts[-1][:64]
    if request.client is not None and request.client.host:
        return request.client.host[:64]
    return "unknown"


@router.post("/auth/login", response_model=UserOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    user = db.scalar(select(User).where(User.username == body.username))
    address = _visitor_address(request)
    # Hash even when the name is missing, so the two answers take the same time.
    password_ok = verify_password(body.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH)
    if login_failures.blocked(address) or user is None or not password_ok:
        if user is None or not password_ok:
            login_failures.record(address)
        if login_failures.blocked(address):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in attempts. Wait a few minutes and try again.",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    login_failures.forget(address)
    token = create_session_token(user.id, settings)
    if user.role == UserRole.guest:
        db.add(AuditEvent(username=user.username, action="guest_login", detail=address))
        guest_visits_module.record(db, address)
        db.commit()
        set_session_cookie(response, token, settings, max_age=settings.guest_idle_seconds)
    else:
        set_session_cookie(response, token, settings)
    return _user_out(user, token, settings)


@router.get("/auth/guest")
def enter_as_guest(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """Sign in as the read-only guest and open the dashboard. This is the public demo link."""
    address = _visitor_address(request)
    if login_failures.blocked(address):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Wait a few minutes and try again.",
        )
    user = db.scalar(select(User).where(User.username == settings.bootstrap_guest_username))
    if user is None or not user.is_active or user.role != UserRole.guest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guest access is not available")
    token = create_session_token(user.id, settings)
    db.add(AuditEvent(username=user.username, action="guest_login", detail=address))
    guest_visits_module.record(db, address)
    db.commit()
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(redirect, token, settings, max_age=settings.guest_idle_seconds)
    return redirect


@router.post("/auth/logout")
def logout(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
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
