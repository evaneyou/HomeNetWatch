"""Installer codes, signed remote agent requests, and ingest tokens."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import keys

CODE_LENGTH = 12
CODE_TTL = timedelta(hours=24)
CHECKIN_TTL = timedelta(minutes=15)
MAX_WAITING = 10
NONCE_TTL_SECONDS = 60
MAX_NONCES = 2000
RATE_LIMIT = 30
RATE_WINDOW_SECONDS = 60
PSK_RESEND_AFTER = timedelta(minutes=10)
CONNECTED_WITHIN = timedelta(minutes=5)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TOKEN_PREFIX = "sha256:"
_SIGNATURE_CONTEXT = "hns-agent-v1"


def normalize_installer_code(raw: str) -> str:
    cleaned = re.sub(r"[\s-]", "", raw).upper().translate(_CROCKFORD_ALIASES)
    if len(cleaned) != CODE_LENGTH or any(ch not in _CROCKFORD for ch in cleaned):
        raise ValueError("Installer codes are 12 letters and numbers, like K7QF-2MXD-9PLA")
    return cleaned


def installer_code_hash(code: str) -> str:
    """How a new installer code is stored."""
    return installer_code_hashes(code)[0]


def installer_code_hashes(code: str) -> list[str]:
    """Every stored form a code may have: under SECRET_KEY, and under SECRET_KEY_PREVIOUS while rotating."""
    return [
        hmac.new(secret, f"installer:{code}".encode(), hashlib.sha256).hexdigest()
        for secret in keys.keys(keys.INSTALLER_CODE)
    ]


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return _TOKEN_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def is_token_hash(value: str | None) -> bool:
    return bool(value) and value.startswith(_TOKEN_PREFIX)


def decode_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Keys are base64") from exc
    if len(raw) != 32:
        raise ValueError("Keys are 32 bytes")
    return raw


def signed_message(path: str, nonce: str, fields: list[str]) -> bytes:
    parts = [_SIGNATURE_CONTEXT, path, nonce, *fields]
    if any("\n" in part for part in parts):
        raise ValueError("Signed fields cannot contain newlines")
    return "\n".join(parts).encode()


def verify_signature(identity_public_key: str, message: bytes, signature: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(decode_key(identity_public_key))
        key.verify(base64.b64decode(signature, validate=True), message)
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def clean_hostname(raw: str) -> str:
    name = raw.strip().lower().rstrip(".")
    if not name or len(name) > 253 or not all(_HOST_LABEL.match(label) for label in name.split(".")):
        raise ValueError("Hostname is not a valid DNS name")
    return name


def clean_lan_ip(raw: str) -> str:
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError as exc:
        raise ValueError("LAN address is not an IP address") from exc


class NonceStore:
    """One-use nonces bound to an identity key. A restart clears them."""

    def __init__(self, ttl_seconds: int = NONCE_TTL_SECONDS, capacity: int = MAX_NONCES) -> None:
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, identity_public_key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._items) >= self._capacity:
                return None
            nonce = secrets.token_urlsafe(24)
            self._items[nonce] = (identity_public_key, now + self._ttl)
            return nonce

    def consume(self, nonce: str, identity_public_key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            item = self._items.pop(nonce, None)
        if item is None:
            return False
        owner, expires = item
        return expires > now and hmac.compare_digest(owner, identity_public_key)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _prune(self, now: float) -> None:
        for nonce in [n for n, (_, expires) in self._items.items() if expires <= now]:
            del self._items[nonce]


class RateLimiter:
    """Sliding window per client address."""

    def __init__(self, limit: int = RATE_LIMIT, window_seconds: int = RATE_WINDOW_SECONDS) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= now - self._window]:
                    del self._hits[stale]
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


nonces = NonceStore()
limiter = RateLimiter()
