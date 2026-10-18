"""Wi-Fi survey upload and the Kismet capture loop."""

from __future__ import annotations

import base64
import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import agent_state as state
import rf_spool
from checks import link_is_busy, link_lock, parse_link_bssid, parse_link_ssid, scan_may_run, wifi_roles
from enrollment import load_config
from health import post_json
from kismet_view import (
    DEVICE_FIELDS,
    iw_scan_skipped,
    map_kismet_capture,
    prefer_survey,
    survey_channels,
    visible_clients,
    with_busy,
)

def joined_link() -> tuple[str, str]:
    iface = wifi_roles().get("link")
    if not iface:
        return "", ""
    try:
        subprocess.run(
            ["iw", "dev", iface, "set", "power_save", "off"],
            check=False,
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        text = subprocess.check_output(
            ["iw", "dev", iface, "link"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return "", ""
    return parse_link_ssid(text), parse_link_bssid(text)

def remember_trusted(body: dict) -> None:
    raw = body.get("trusted_bssids")
    if isinstance(raw, list):
        state._trusted_bssids = {str(item).lower() for item in raw}

def airwave_mode() -> str:
    if state._airwave_mode == "kismet":
        return "kismet"
    roles = wifi_roles()
    if roles["radio"] or roles["link"]:
        return "scan"
    return "off"

def wifi_scan_text(iface: str) -> str | None:
    try:
        return subprocess.check_output(["iw", "dev", iface, "scan"], text=True, timeout=25, stderr=subprocess.STDOUT)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None

def wifi_survey_text(iface: str) -> str:
    try:
        return subprocess.check_output(
            ["iw", "dev", iface, "survey", "dump"],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return ""

def upload_wifi_scan(config: dict, forced: bool = False) -> None:
    """Post one real scan. The radio adapter is used whenever a second NIC exists."""
    if not state._scan_lock.acquire(blocking=False):
        return
    link_held = False
    try:
        roles = wifi_roles()
        if iw_scan_skipped(airwave_mode(), roles["radio"]):
            return
        iface = roles["radio"] or roles["link"]
        role = "radio" if roles["radio"] else "link" if roles["link"] else ""
        if not iface or not role:
            return
        now = time.monotonic()
        if not scan_may_run(
            role=role,
            last_link_scan=state._last_link_scan,
            now=now,
            link_busy=link_is_busy(),
            forced=forced,
        ):
            return
        if role == "link" and not link_lock.acquire(blocking=False):
            return
        if role == "link":
            link_held = True
        text = wifi_scan_text(iface)
        if text is None:
            return
        post_json(
            f"{config['server_url'].rstrip('/')}/api/v1/monitors/wifi-scans",
            {
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "raw": text,
                "survey": wifi_survey_text(iface),
                "scan_iface_role": role,
            },
            config.get("token"),
            config.get("name"),
            timeout=30,
        )
        if role == "link":
            state._last_link_scan = time.monotonic()
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return
    finally:
        if link_held:
            link_lock.release()
        state._scan_lock.release()

def _kismet_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"

def kismet_error_text(exc: BaseException) -> str:
    """HTTP status and body, so a rejected field list is visible instead of an empty failure."""
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        body = " ".join(body.split())[:500]
        if body:
            return f"Kismet HTTP {exc.code}: {body}"
        return f"Kismet HTTP {exc.code}"
    return str(exc)

def kismet_get(base: str, user: str, password: str, path: str) -> object:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"Authorization": _kismet_auth(user, password)},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def kismet_devices(base: str, user: str, password: str, since: int) -> object:
    """Ask Kismet for the device fields we map, instead of a full record."""
    body = urllib.parse.urlencode({"json": json.dumps({"fields": DEVICE_FIELDS})}).encode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}/devices/last-time/{since}/devices.json",
        data=body,
        headers={
            "Authorization": _kismet_auth(user, password),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

def kismet_survey_text() -> str:
    roles = wifi_roles()
    radio = str(roles.get("radio") or "")
    link = str(roles.get("link") or "")
    radio_text = wifi_survey_text(radio) if radio else ""
    link_text = "" if not link or link == radio else wifi_survey_text(link)
    return prefer_survey(radio_text, link_text)

def _capture_body(devices: object, channels: object, alerts: object) -> dict:
    mapped = map_kismet_capture(devices, channels, alerts)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "aps": mapped["aps"],
        "clients": visible_clients(mapped["clients"], state._trusted_bssids),
        "channels": with_busy(mapped["channels"], survey_channels(kismet_survey_text())),
        "alerts": mapped["alerts"],
    }

def _post_capture(config: dict, payload: dict) -> None:
    post_json(
        f"{config['server_url'].rstrip('/')}/api/v1/monitors/rf-capture",
        payload,
        config.get("token"),
        config.get("name"),
        timeout=30,
    )

def _drain_spool(config: dict) -> None:
    for row_id, payload in rf_spool.pending(state.SPOOL_PATH):
        _post_capture(config, payload)
        rf_spool.drop(state.SPOOL_PATH, row_id)

def kismet_once(config: dict) -> None:
    if not state.KISMET_PATH.is_file():
        state._airwave_mode = "off"
        return
    try:
        creds = json.loads(state.KISMET_PATH.read_text(encoding="utf-8"))
        base = str(creds.get("url") or "http://127.0.0.1:2501")
        user = str(creds["username"])
        password = str(creds["password"])
        kismet_get(base, user, password, "/system/status.json")
    except urllib.error.HTTPError as exc:
        state._airwave_mode = "off"
        state._last_error = kismet_error_text(exc)
        return
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        state._airwave_mode = "off"
        state._last_error = kismet_error_text(exc)
        return
    state._airwave_mode = "kismet"
    now = int(time.time())
    device_since = state._kismet_devices_since or now - 60
    alert_since = state._kismet_alerts_since or now - 60
    try:
        devices = kismet_devices(base, user, password, device_since)
        channels = kismet_get(base, user, password, "/channels/channels.json")
        alerts = kismet_get(base, user, password, f"/alerts/last-time/{alert_since}/alerts.json")
        payload = _capture_body(devices, channels, alerts)
        try:
            _drain_spool(config)
            _post_capture(config, payload)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError):
            rf_spool.push(state.SPOOL_PATH, payload)
        state._kismet_devices_since = now
        state._kismet_alerts_since = now
        if state._last_error.startswith("Kismet HTTP"):
            state._last_error = ""
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        state._last_error = kismet_error_text(exc)
        return

def kismet_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                kismet_once(dict(config))
            except Exception as exc:
                state._last_error = str(exc)
        state._stop.wait(60)

def scan_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                upload_wifi_scan(dict(config))
            except Exception as exc:
                state._last_error = str(exc)
        with state._lock:
            interval = int(load_config().get("scan_interval_seconds") or 300)
        state._stop.wait(max(interval, 15))
