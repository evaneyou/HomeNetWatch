"""Server Health: the machine running HomeNetWatch, for an admin."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import fault_simulation, gemini_rca
from app.auth import require_reader
from app.db import get_db
from app.models import User
from app.server_health import package_detail, report

router = APIRouter(prefix="/api/v1/server-health")


class FaultSimulationIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=fault_simulation.MAX_PROMPT_CHARS)


@router.get("")
def get_server_health(
    db: Annotated[Session, Depends(get_db)],
    _reader: Annotated[User, Depends(require_reader)],
    range_name: Annotated[str, Query(alias="range", pattern="^(1h|6h|24h|7d)$")] = "1h",
) -> dict[str, Any]:
    return report(db, range_name)


@router.get("/software")
def get_software_package(
    db: Annotated[Session, Depends(get_db)],
    _reader: Annotated[User, Depends(require_reader)],
    source: Annotated[str, Query(max_length=64)],
    name: Annotated[str, Query(max_length=200)],
) -> dict[str, Any]:
    found = package_detail(db, source, name)
    if found is None:
        raise HTTPException(status_code=404, detail="That package is not in the latest check.")
    return found


@router.post("/fault-simulation")
def post_fault_simulation(
    body: FaultSimulationIn,
    db: Annotated[Session, Depends(get_db)],
    _reader: Annotated[User, Depends(require_reader)],
) -> dict[str, Any]:
    """Explain what HomeNetWatch would do for a described failure. Does not inject data."""
    try:
        return fault_simulation.simulate_fault(db, body.prompt)
    except gemini_rca.GeminiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
