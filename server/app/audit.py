"""Security events in audit_events: sign-ins and where they came from, failures, blocks and settings changes.

After a break-in these answer when the admin account was used and from where. Failed sign-ins are written only
while the address or account is not yet blocked, and a block is written once, when it starts, so a flood of
attempts can't fill the table.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, TelegramSetting

log = logging.getLogger(__name__)


def record(db: Session, username: str, action: str, detail: str = "", monitor_id: Optional[UUID] = None) -> None:
    db.add(AuditEvent(username=username[:64], action=action, detail=detail[:500], monitor_id=monitor_id))


def new_address(db: Session, username: str, address: str) -> bool:
    """True when this account has never signed in from this address before."""
    seen = select(AuditEvent.id).where(
        AuditEvent.username == username, AuditEvent.action == "login", AuditEvent.detail == address
    )
    return db.scalar(seen.limit(1)) is None


def alert_new_address(username: str, address: str) -> None:
    """Tell the owner's Telegram chat about an admin sign-in from a new address. Runs after the response is sent."""
    from app import telegram
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            row = db.get(TelegramSetting, 1)
            if row is None or not (row.enabled and row.chat_id and row.bot_token_enc):
                return
            token = telegram.decrypt(row.bot_token_enc)
            if not token:
                return
            telegram.send(
                token,
                row.chat_id,
                f"🔐 HomeNetWatch: {username} signed in from a new address, {address}. If that wasn't you, "
                f"change the password and run: python -m app.sessions revoke {username}",
            )
    except Exception:
        log.warning("could not send the new sign-in address alert", exc_info=True)
