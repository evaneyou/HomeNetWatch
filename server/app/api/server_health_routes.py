"""Server Health: the machine running HomeNetWatch, for an admin."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import User
from app.server_health import report

router = APIRouter(prefix="/api/v1/server-health")


@router.get("")
def get_server_health(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    range_name: Annotated[str, Query(alias="range", pattern="^(1h|6h|24h|7d)$")] = "1h",
) -> dict[str, Any]:
    return report(db, range_name)
