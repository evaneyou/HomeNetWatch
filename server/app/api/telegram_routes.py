"""Settings > Configure Telegram. The bot token goes in and is never sent back out."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import alerts, audit, telegram
from app.auth import require_admin, require_admin_csrf
from app.db import get_db
from app.models import AlertEvent, TelegramSetting, User

router = APIRouter(prefix="/api/v1/settings/telegram")

_CHAT = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


class TelegramIn(BaseModel):
    token: Optional[str] = None  # empty or missing keeps the saved token
    chat_id: str = ""
    enabled: bool = True
    notify_network: bool = True
    notify_pi: bool = True
    notify_server: bool = True
    notify_app: bool = True
    notify_recovery: bool = True


class DetectIn(BaseModel):
    token: Optional[str] = None


def _out(row: TelegramSetting) -> dict[str, Any]:
    return {
        "configured": alerts.configured(row),
        "enabled": row.enabled,
        "token_set": bool(row.bot_token_enc),
        "token_hint": row.token_hint,
        "bot_username": row.bot_username,
        "chat_id": row.chat_id,
        "notify_network": row.notify_network,
        "notify_pi": row.notify_pi,
        "notify_server": row.notify_server,
        "notify_app": row.notify_app,
        "notify_recovery": row.notify_recovery,
        "last_ok_at": row.last_ok_at,
        "last_error": row.last_error,
    }


def _fail(exc: telegram.TelegramError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def read(db: Annotated[Session, Depends(get_db)], _admin: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
    row = alerts.get_settings_row(db)
    db.commit()
    return _out(row)


@router.put("")
def save(
    body: TelegramIn,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    row = alerts.get_settings_row(db)
    chat = body.chat_id.strip()
    if chat and not _CHAT.fullmatch(chat):
        raise HTTPException(status_code=400, detail="The chat ID is a number such as 123456789 (a group's starts with -), or @channelname.")
    token = (body.token or "").strip()
    if token:
        if not telegram.valid_token(token):
            raise HTTPException(status_code=400, detail="That does not look like a bot token. It looks like 123456789:AA... and comes from @BotFather.")
        try:
            row.bot_username = telegram.bot_username(token)
        except telegram.TelegramError as exc:
            raise _fail(exc) from None
        row.bot_token_enc = telegram.encrypt(token)
        row.token_hint = token[-4:]
    if body.enabled and not (row.bot_token_enc and chat):
        raise HTTPException(status_code=400, detail="Add the bot token and the chat ID before turning alerts on.")
    row.chat_id = chat
    row.enabled = body.enabled
    row.notify_network = body.notify_network
    row.notify_pi = body.notify_pi
    row.notify_server = body.notify_server
    row.notify_app = body.notify_app
    row.notify_recovery = body.notify_recovery
    row.updated_at = datetime.now(timezone.utc)
    if token:
        row.last_error = ""
    changes = ["alerts on" if body.enabled else "alerts off", "chat set" if chat else "no chat"]
    if token:
        changes.append("new bot token")
    audit.record(db, admin.username, "telegram_settings", ", ".join(changes))
    db.commit()
    return _out(row)


@router.post("/test")
def send_test(db: Annotated[Session, Depends(get_db)], _admin: Annotated[User, Depends(require_admin_csrf)]) -> dict[str, Any]:
    row = alerts.get_settings_row(db)
    token = telegram.decrypt(row.bot_token_enc)
    if not token or not row.chat_id:
        raise HTTPException(status_code=400, detail="Save the bot token and the chat ID first.")
    try:
        telegram.send(token, row.chat_id, "✅ HomeNetWatch test message. Alerts for outages and system problems will arrive in this chat.")
    except telegram.TelegramError as exc:
        row.last_error = str(exc)[:300]
        db.commit()
        raise _fail(exc) from None
    row.last_ok_at = datetime.now(timezone.utc)
    row.last_error = ""
    db.commit()
    return _out(row)


@router.post("/detect")
def detect_chats(
    body: DetectIn,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
) -> dict[str, Any]:
    """Find the chat ID: the chats that have sent the bot a message."""
    row = alerts.get_settings_row(db)
    token = (body.token or "").strip() or telegram.decrypt(row.bot_token_enc)
    if not token:
        raise HTTPException(status_code=400, detail="Enter the bot token first.")
    if not telegram.valid_token(token):
        raise HTTPException(status_code=400, detail="That does not look like a bot token.")
    try:
        return {"chats": telegram.recent_chats(token)}
    except telegram.TelegramError as exc:
        raise _fail(exc) from None


@router.delete("")
def disconnect(db: Annotated[Session, Depends(get_db)], admin: Annotated[User, Depends(require_admin_csrf)]) -> dict[str, Any]:
    row = alerts.get_settings_row(db)
    row.enabled = False
    row.bot_token_enc = ""
    row.token_hint = ""
    row.bot_username = ""
    row.chat_id = ""
    row.last_error = ""
    row.updated_at = datetime.now(timezone.utc)
    audit.record(db, admin.username, "telegram_disconnected")
    db.commit()
    return _out(row)


@router.get("/events")
def events(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AlertEvent).where(AlertEvent.status != "pending").order_by(desc(AlertEvent.opened_at)).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "category": row.category,
            "severity": row.severity,
            "title": row.title,
            "detail": row.detail,
            "status": row.status,
            "opened_at": row.opened_at,
            "resolved_at": row.resolved_at,
            "notified": row.notified_at is not None,
        }
        for row in rows
    ]
