"""The KPI dashboard: one report for a day, week, month or year, and the stored daily history."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import kpi
from app.auth import get_current_user
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/api/v1/kpi")


@router.get("")
def get_report(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    period: Annotated[str, Query(pattern="^(day|week|month|year)$")] = "day",
    day: Annotated[Optional[date], Query(description="End the report on this UTC day. Leave out for the latest.")] = None,
    monitor_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    return kpi.report(db, period, monitor_id=monitor_id, day=day)


@router.get("/daily")
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
