"""One key per purpose, derived from SECRET_KEY.

SECRET_KEY used to sign sessions, make CSRF tokens, protect installer codes and encrypt the stored Telegram and
Gemini secrets all at once. Each purpose now gets its own key from HKDF (RFC 5869), so a key that leaks from
one use can't be replayed in another.

Rotating SECRET_KEY: put the old value in SECRET_KEY_PREVIOUS and the new one in SECRET_KEY, and restart. The old
key's sessions, CSRF tokens and installer codes stay valid, the stored secrets are re-encrypted under the new key
at startup, and new values use the new key. Once the old sessions have expired (7 days), remove
SECRET_KEY_PREVIOUS.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings, get_settings

SESSION = "session"
CSRF = "csrf"
INSTALLER_CODE = "installer-code"
STORED_SECRETS = "stored-secrets"
_SALT = b"homenetwatch-keys-v1"


@lru_cache(maxsize=32)
def _derive(secret: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=purpose.encode()).derive(secret.encode())


def secrets_in_use(settings: Optional[Settings] = None) -> list[str]:
    """SECRET_KEY, then SECRET_KEY_PREVIOUS when it is set."""
    settings = settings or get_settings()
    found = [settings.secret_key]
    previous = settings.secret_key_previous.strip()
    if previous and previous != settings.secret_key:
        found.append(previous)
    return found


def key(purpose: str, settings: Optional[Settings] = None) -> bytes:
    """The key that signs or encrypts anything new for this purpose."""
    return _derive((settings or get_settings()).secret_key, purpose)


def keys(purpose: str, settings: Optional[Settings] = None) -> list[bytes]:
    """Every key that may still verify or decrypt for this purpose, the current one first."""
    return [_derive(secret, purpose) for secret in secrets_in_use(settings)]
