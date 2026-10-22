"""Replace each letter and digit of a guest's identifying fields with #.

Dots, colons, and spaces stay, so an address still has the shape of an address.
The real value never leaves the server.
"""

from __future__ import annotations

import json
import threading
import time
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.models import UserRole

# Path markings that name a traffic class. Dropped rather than shown.
_DROP = {"tos", "dscp", "dscp_seen"}

# Present values are hashed character by character. Empty values stay empty so "not set yet"
# does not look like a hidden address.
_MASK = {
    "public_ip",
    "lan_ip",
    "ip",
    "observed_ip",
    "tunnel_address",
    "wireless_tunnel_address",
    "wg_address",
    "wg_wireless_address",
    "hostname",
    "isp",
    "location",
    "location_label",
    "region",
    "country",
    "latitude",
    "longitude",
    "ssid",
    "bssid",
    "link_ssid",
    "link_bssid",
    "probed_ssids",
    "mac",
    "client_mac",
    "wired_iface",
    "wifi_link_iface",
    "wifi_radio_iface",
    "host",
    "rdns",
    "asn",
    "origin_asn",
    "asn_label",
    "trusted_bssids",
    "as_name",
    "biggest_step_name",
    "prefix",
    "vendor",
    "text",
}

# These stay unique inside one response so a list of networks does not collapse
# into a single row, without sending the real address.
_ID_KEYS = {"bssid", "mac", "client_mac", "asn", "origin_asn"}


# The probe's own place. It is a chosen pin, not the real home address, so a guest may see it.
# A guest visitor's region and country are different records and stay masked.
_PROBE_PLACE = {"location_label", "region", "country", "latitude", "longitude"}


def redact(value, role: UserRole):
    if role != UserRole.guest:
        return value
    return _scrub(value, {})


def _scrub(value, ids: dict):
    if isinstance(value, dict):
        cleaned = {}
        token = None
        probe = "monitor_id" in value and ("location_label" in value or "latitude" in value)
        for key, item in value.items():
            if key in _DROP:
                continue
            if key in {"name", "label"} and any(marker in value for marker in ("asn", "bssid", "ssid")):
                cleaned[key] = _masked(item, ids)
                continue
            if key in _PROBE_PLACE and probe:
                cleaned[key] = _scrub(item, ids)
                continue
            if key in _MASK:
                cleaned[key] = _masked(item, ids)
                if key in _ID_KEYS and item not in (None, "") and not isinstance(item, (list, dict)):
                    token = token or _token(ids, item)
            else:
                cleaned[key] = _scrub(item, ids)
        if token:
            cleaned["anon_key"] = token
        return cleaned
    if isinstance(value, list):
        return [_scrub(item, ids) for item in value]
    return value


def _masked(value, ids: dict):
    if value is None or value == "":
        return value
    if isinstance(value, list):
        return [_masked(item, ids) for item in value]
    if isinstance(value, dict):
        return _scrub(value, ids)
    return _hash_chars(value)


def _hash_chars(value) -> str:
    """Swap each letter or digit for # and leave dots, colons, and spaces in place."""
    return "".join("#" if char.isalnum() else char for char in str(value))


def _token(ids: dict, value) -> str:
    text = str(value)
    token = ids.get(text)
    if token is None:
        token = f"a{len(ids) + 1}"
        ids[text] = token
    return token


# Whether a user is a guest, remembered briefly so most requests never touch the database.
_GUEST_TTL_SECONDS = 15.0
_GUEST_CACHE_MAX = 1000
_guest_cache: dict[UUID, tuple[float, bool]] = {}
_guest_cache_lock = threading.Lock()


def _user_is_guest(user_id: UUID) -> bool:
    from app.db import SessionLocal
    from app.models import User

    now = time.monotonic()
    with _guest_cache_lock:
        hit = _guest_cache.get(user_id)
    if hit is not None and now - hit[0] < _GUEST_TTL_SECONDS:
        return hit[1]
    with SessionLocal() as db:
        user = db.get(User, user_id)
    result = user is not None and user.is_active and user.role == UserRole.guest
    with _guest_cache_lock:
        if len(_guest_cache) >= _GUEST_CACHE_MAX:
            _guest_cache.clear()
        _guest_cache[user_id] = (now, result)
    return result


def _request_is_guest(request: Request) -> bool:
    from app.auth import _serializer
    from app.config import get_settings

    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return False
    try:
        data = _serializer(settings).loads(token, max_age=settings.session_max_age)
        user_id = UUID(data["uid"])
    except Exception:
        return False
    return _user_is_guest(user_id)


async def _single_body(body: bytes):
    yield body


class GuestAnonymizeMiddleware(BaseHTTPMiddleware):
    """Replace identifying fields in every JSON body a guest session receives.

    The middleware hands back a stream, not the original response object, so the
    bytes have to be read from that stream. Replacing ``response.body`` never
    reaches the browser.
    """

    async def dispatch(self, request: Request, call_next):
        # Decide before the endpoint runs. Doing it afterwards opened a second pooled connection
        # while the endpoint still held its own, and under load every connection was held by a
        # request waiting for another one. The lookup runs off the event loop for the same reason.
        is_guest = await run_in_threadpool(_request_is_guest, request)
        response = await call_next(request)
        if not is_guest:
            return response
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunks.append(chunk)
        body = b"".join(chunks)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            response.body_iterator = _single_body(body)
            return response
        masked = json.dumps(redact(payload, UserRole.guest)).encode()
        response.body_iterator = _single_body(masked)
        raw = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw.append((b"content-length", str(len(masked)).encode("latin-1")))
        response.raw_headers = raw
        return response
