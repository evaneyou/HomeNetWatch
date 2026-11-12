"""Dashboard settings for web checks, stability, and the internet service."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.auth import get_current_user, require_admin_csrf
from app.db import get_db
from app.internet_tests import (
    load_internet_service,
    load_web_services,
    load_web_stability,
    save_internet_service,
    save_web_services,
    save_web_stability,
    stability_config,
)
from app.models import User
from app.schemas import (
    InternetServiceSettingIn,
    InternetServiceSettingOut,
    WebServiceSettingOut,
    WebServiceSettingsIn,
    WebStabilityIn,
    WebStabilityOut,
)
from app.services.stability import preset_name

router = APIRouter(prefix="/api/v1")

def _web_service_out(row) -> WebServiceSettingOut:
    return WebServiceSettingOut(
        key=row.key,
        label=row.label,
        interval_seconds=row.interval_seconds,
        http_enabled=row.http_enabled,
        icmp_enabled=row.icmp_enabled,
        jitter_enabled=row.jitter_enabled,
        http_variance_pct=row.http_variance_pct,
        icmp_variance_pct=row.icmp_variance_pct,
        jitter_variance_pct=row.jitter_variance_pct,
    )


@router.get("/settings/web-services", response_model=list[WebServiceSettingOut])
def get_web_services(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[WebServiceSettingOut]:
    return [_web_service_out(row) for row in load_web_services(db)]


@router.put("/settings/web-services", response_model=list[WebServiceSettingOut])
def put_web_services(
    body: WebServiceSettingsIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> list[WebServiceSettingOut]:
    try:
        rows = save_web_services(db, [item.model_dump() for item in body.services])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(db, admin.username, "web_services_settings")
    db.commit()
    return [_web_service_out(row) for row in rows]


def _web_stability_out(row) -> WebStabilityOut:
    cfg = stability_config(row)
    return WebStabilityOut(
        poll_interval_seconds=row.poll_interval_seconds,
        window_size=row.window_size,
        degraded_count=row.degraded_count,
        fail_count=row.fail_count,
        recover_count=row.recover_count,
        pct_floor=row.pct_floor,
        abs_floor_http_ms=row.abs_floor_http_ms,
        abs_floor_icmp_ms=row.abs_floor_icmp_ms,
        abs_floor_jitter_ms=row.abs_floor_jitter_ms,
        quorum_degraded_pct=row.quorum_degraded_pct,
        quorum_fail_pct=row.quorum_fail_pct,
        preset=preset_name(cfg),
    )


@router.get("/settings/web-stability", response_model=WebStabilityOut)
def get_web_stability(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> WebStabilityOut:
    return _web_stability_out(load_web_stability(db))


@router.put("/settings/web-stability", response_model=WebStabilityOut)
def put_web_stability(
    body: WebStabilityIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> WebStabilityOut:
    try:
        row = save_web_stability(db, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(db, admin.username, "web_stability_settings")
    db.commit()
    return _web_stability_out(row)


def _internet_service_out(row) -> InternetServiceSettingOut:
    return InternetServiceSettingOut(
        interval_seconds=row.interval_seconds,
        http_enabled=row.http_enabled,
        icmp_enabled=row.icmp_enabled,
        mtr_enabled=row.mtr_enabled,
        http_variance_pct=row.http_variance_pct,
        icmp_variance_pct=row.icmp_variance_pct,
        mtr_variance_pct=row.mtr_variance_pct,
        librespeed_enabled=row.librespeed_enabled,
        librespeed_interval_seconds=row.librespeed_interval_seconds,
        librespeed_variance_pct=row.librespeed_variance_pct,
        cloudflare_enabled=row.cloudflare_enabled,
        cloudflare_interval_seconds=row.cloudflare_interval_seconds,
    )


@router.get("/settings/internet-service", response_model=InternetServiceSettingOut)
def get_internet_service(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> InternetServiceSettingOut:
    return _internet_service_out(load_internet_service(db))


@router.put("/settings/internet-service", response_model=InternetServiceSettingOut)
def put_internet_service(
    body: InternetServiceSettingIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> InternetServiceSettingOut:
    row = save_internet_service(db, body.model_dump())
    audit.record(db, admin.username, "internet_service_settings")
    db.commit()
    return _internet_service_out(row)
