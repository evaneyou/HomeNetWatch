"""The KPI dashboard: one report for a day, week, month or year, the stored daily history, and Gemini root cause analysis."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import gemini_rca, kpi, telegram
from app.auth import get_current_user, require_admin, require_admin_csrf
from app.db import get_db
from app.models import GeminiSetting, User

router = APIRouter(prefix="/api/v1")


class RootCauseIn(BaseModel):
    period: str = Field(default="day", pattern="^(day|week|month|year)$")
    day: Optional[date] = None
    monitor_id: Optional[uuid.UUID] = None


class GeminiSettingIn(BaseModel):
    api_key: Optional[str] = None
    model: str = "gemini-2.5-flash"


def _gemini_out(row: GeminiSetting) -> dict[str, Any]:
    return {
        "configured": gemini_rca.is_configured(row),
        "key_set": bool(row.api_key_enc or gemini_rca.resolve_api_key(row)),
        "key_hint": row.key_hint or ("env" if gemini_rca.resolve_api_key(row) and not row.api_key_enc else ""),
        "model": row.model or gemini_rca.DEFAULT_MODEL,
        "allowed_models": list(gemini_rca.ALLOWED_MODELS),
        "last_ok_at": row.last_ok_at,
        "last_error": row.last_error,
    }


@router.get("/kpi")
def get_report(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    period: Annotated[str, Query(pattern="^(day|week|month|year)$")] = "day",
    day: Annotated[Optional[date], Query(description="End the report on this UTC day. Leave out for the latest.")] = None,
    monitor_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    return kpi.report(db, period, monitor_id=monitor_id, day=day)


@router.get("/kpi/daily")
def get_daily(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    first: Optional[date] = Query(default=None, alias="from"),
    last: Optional[date] = Query(default=None, alias="to"),
    limit: Annotated[int, Query(ge=1, le=1000)] = 400,
    monitor_id: Optional[uuid.UUID] = None,
) -> list[dict[str, Any]]:
    """The stored daily KPIs, newest first. Defaults to the last 90 days."""
    today = datetime.now(timezone.utc).date()
    last = min(last or today, today)
    first = first or last - timedelta(days=89)
    return kpi.daily_history(db, first, last, monitor_id, limit)


@router.post("/kpi/root-cause")
def determine_root_cause(
    body: RootCauseIn,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run an on-demand Gemini LLM root cause analysis over the event and metric log for this KPI window."""
    try:
        return gemini_rca.analyze_root_cause(
            db,
            period=body.period,
            day=body.day,
            monitor_id=body.monitor_id,
        )
    except gemini_rca.GeminiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/settings/gemini")
def get_gemini_settings(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
) -> dict[str, Any]:
    row = gemini_rca.get_settings_row(db)
    db.commit()
    return _gemini_out(row)


@router.put("/settings/gemini")
def save_gemini_settings(
    body: GeminiSettingIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    row = gemini_rca.get_settings_row(db)
    model = (body.model or gemini_rca.DEFAULT_MODEL).strip()
    if model not in gemini_rca.ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"Choose one of: {', '.join(gemini_rca.ALLOWED_MODELS)}")
    key = (body.api_key or "").strip()
    if key:
        if not gemini_rca.valid_key(key):
            raise HTTPException(status_code=400, detail="That does not look like a valid Gemini API key.")
        try:
            gemini_rca.verify_key(key, model=model)
        except gemini_rca.GeminiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        row.api_key_enc = telegram.encrypt(key)
        row.key_hint = key[-4:]
        row.last_ok_at = datetime.now(timezone.utc)
        row.last_error = ""
    row.model = model
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _gemini_out(row)


@router.delete("/settings/gemini")
def clear_gemini_settings(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    row = gemini_rca.get_settings_row(db)
    row.api_key_enc = ""
    row.key_hint = ""
    row.last_error = ""
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _gemini_out(row)
