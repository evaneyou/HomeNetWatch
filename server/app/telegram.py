"""A small Telegram Bot API client, and the encryption for the bot token.

The token is a secret. It is encrypted before it is stored, never returned by the API, and kept
out of logs and error messages: Telegram puts it in the URL, so the HTTP library's own request
log is turned down and every error is reduced to a plain sentence.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app import keys

# httpx logs "POST https://api.telegram.org/bot<token>/..." at INFO. That would write the token to the logs.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

API = "https://api.telegram.org"
_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")


class TelegramError(Exception):
    """A reason that is safe to show: it never contains the token."""


def valid_token(token: str) -> bool:
    return bool(_TOKEN.fullmatch(token.strip()))


def _fernet() -> MultiFernet:
    """Encrypts with the current stored-secrets key; decrypts with any key the value may have been stored under."""
    current = [Fernet(base64.urlsafe_b64encode(secret)) for secret in keys.keys(keys.STORED_SECRETS)]
    # Before per-purpose keys, the Fernet key was SHA-256 of SECRET_KEY itself. Kept for reading only, so values
    # stored then still open until rotate_stored() re-encrypts them.
    legacy = [
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())) for secret in keys.secrets_in_use()
    ]
    return MultiFernet(current + legacy)


def encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt(stored: str) -> str:
    """The token, or an empty string if nothing is stored or no key in use can open it."""
    if not stored:
        return ""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def _rotated(stored: str) -> str:
    """The value re-encrypted under the current key, or unchanged if it is already, or can't be read."""
    if not stored:
        return stored
    current = Fernet(base64.urlsafe_b64encode(keys.key(keys.STORED_SECRETS)))
    try:
        current.decrypt(stored.encode())
        return stored
    except (InvalidToken, ValueError):
        pass
    try:
        return _fernet().rotate(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return stored


def rotate_stored(db) -> int:
    """Re-encrypt the stored bot token and Gemini key under the current key. Run at startup. Returns the count."""
    from app.models import GeminiSetting, TelegramSetting

    changed = 0
    for row in db.query(TelegramSetting).all():
        fresh = _rotated(row.bot_token_enc)
        if fresh != row.bot_token_enc:
            row.bot_token_enc = fresh
            changed += 1
    for row in db.query(GeminiSetting).all():
        fresh = _rotated(row.api_key_enc)
        if fresh != row.api_key_enc:
            row.api_key_enc = fresh
            changed += 1
    return changed


def _client() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def _reason(status: int, body: dict[str, Any]) -> str:
    text = str(body.get("description") or "").lower()
    if status == 401:
        return "Telegram rejected the bot token."
    if status == 403:
        return "The bot was blocked or removed from that chat."
    if status == 429:
        return "Telegram is limiting messages. It will retry."
    if "chat not found" in text:
        return "Telegram could not find that chat. Send your bot a message first."
    if status == 409:
        return "Telegram could not read messages because a webhook is set on this bot."
    return f"Telegram returned an error ({status})."


def _call(token: str, method: str, payload: Optional[dict[str, Any]] = None) -> Any:
    try:
        with _client() as client:
            response = client.post(f"{API}/bot{token}/{method}", json=payload or {})
    except httpx.HTTPError:
        raise TelegramError("Could not reach Telegram.") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not body.get("ok"):
        raise TelegramError(_reason(response.status_code, body if isinstance(body, dict) else {}))
    return body.get("result")


def bot_username(token: str) -> str:
    """Checks the token. Returns the bot's @name."""
    result = _call(token, "getMe")
    return str((result or {}).get("username") or "")


def send(token: str, chat_id: str, text: str) -> None:
    _call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})


def recent_chats(token: str) -> list[dict[str, str]]:
    """Chats that have messaged the bot lately, so the chat ID can be picked and not typed."""
    updates = _call(token, "getUpdates", {"limit": 50, "allowed_updates": ["message"]}) or []
    seen: dict[str, dict[str, str]] = {}
    for update in updates:
        chat = ((update or {}).get("message") or {}).get("chat") or {}
        if "id" not in chat:
            continue
        name = chat.get("title") or " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part) or chat.get("username") or ""
        seen[str(chat["id"])] = {"id": str(chat["id"]), "name": str(name), "type": str(chat.get("type") or "")}
    return list(seen.values())
