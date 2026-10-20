from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque
from typing import Annotated, Optional
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


# A real bcrypt hash so a missing account costs the same as a wrong password.
DUMMY_PASSWORD_HASH = pwd_context.hash("hns-login-timing-pad")
LOGIN_FAILURE_LIMIT = 8


class _FailureWindow:
    """Failed sign-ins from one address. A success drops that address's count."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return len(self._prune(key, now)) >= self.limit

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._prune(key, now)
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 10_000:
                for stale in [item for item, values in self._hits.items() if not values or values[-1] <= now - self._window]:
                    del self._hits[stale]

    def forget(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if not hits and key in self._hits:
            del self._hits[key]
        return hits


login_failures = _FailureWindow(LOGIN_FAILURE_LIMIT, 15 * 60)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="hns-session")


def create_session_token(user_id: UUID, settings: Settings) -> str:
    return _serializer(settings).dumps({"uid": str(user_id)})


def set_session_cookie(
    response: Response,
    token: str,
    settings: Settings,
    max_age: int | None = None,
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.session_max_age if max_age is None else max_age,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


def get_current_user(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    hns_session: Annotated[Optional[str], Cookie(alias="hns_session")] = None,
) -> User:
    if not hns_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        data = _serializer(settings).loads(hns_session, max_age=settings.session_max_age)
        user_id = UUID(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.role == UserRole.guest:
        try:
            _serializer(settings).loads(hns_session, max_age=settings.guest_idle_seconds)
        except SignatureExpired as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
        fresh = create_session_token(user.id, settings)
        set_session_cookie(response, fresh, settings, max_age=settings.guest_idle_seconds)
    return user


def require_reader(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Anyone signed in except a viewer. Server Health is read-only, so a guest may see it."""
    if user.role == UserRole.viewer:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


def csrf_token_for(session_token: str, settings: Settings) -> str:
    return hmac.new(settings.secret_key.encode(), f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


def require_admin_csrf(
    user: Annotated[User, Depends(require_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
    hns_session: Annotated[Optional[str], Cookie(alias="hns_session")] = None,
    x_csrf_token: Annotated[Optional[str], Header()] = None,
) -> User:
    expected = csrf_token_for(hns_session or "", settings)
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")
    return user
