"""Shared paths, locks, and loop flags for the remote agent.

The setup page imports this, then replaces ensure_health_loop with the real starter.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AGENT_VERSION = "0.8.1"
# Latency checks wait on the network, so a few of them run at once.
# Bandwidth Test stays alone: running it beside others would slow them all down.
PROBE_WORKERS = 6
HEAVY_KINDS = {"librespeed"}
CONFIG_PATH = Path(os.environ.get("MONITOR_CONFIG", "/etc/monitor/config.json"))
PORT = int(os.environ.get("MONITOR_PORT", "80"))
ENROLL_PORT = 8848
SIGNATURE_CONTEXT = "hns-agent-v1"
NONCE_PATH = "/api/v1/agents/nonce"
CHECK_IN_PATH = "/api/v1/agents/check-in"
POLL_PATH = "/api/v1/agents/poll"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12
TUNNEL_SERVER_IP = "10.7.0.1"
WIRELESS_SERVER_IP = "10.7.1.1"
_TUNNELS = {
    "wired": (TUNNEL_SERVER_IP, 51820, 100, "wg0"),
    "wireless": (WIRELESS_SERVER_IP, 51821, 101, "wg1"),
}

_lock = threading.Lock()
_scan_lock = threading.Lock()
_stop = threading.Event()
_health_started = False
_enroll_started = False
_tests_started = False
_scan_started = False
_kismet_started = False
_synthetic_started = False
_speedtest_started = False
_last_link_scan = 0.0
_trusted_bssids: set[str] = set()
_airwave_mode = "off"
_kismet_devices_since = 0
_kismet_alerts_since = 0
KISMET_PATH = CONFIG_PATH.with_name("kismet.json")
SPOOL_PATH = CONFIG_PATH.with_name("rf-capture.sqlite")
_restarting = False
_last_error = ""
_last_health_at = ""
_log_buffer: list[dict[str, str]] = []
_assigned_tests: list[dict] = []
_next_run: dict[str, float] = {}
_probes = ThreadPoolExecutor(max_workers=PROBE_WORKERS, thread_name_prefix="probe")
_public_lock = threading.Lock()
_public_cached: tuple[str, str | None] | None = None
_public_cached_at = 0.0
PUBLIC_LOOKUP_URL = "http://ip-api.com/json/?fields=status,query,isp"
PUBLIC_LOOKUP_TTL = 24 * 60 * 60
PUBLIC_CACHE_PATH = CONFIG_PATH.with_name("public-cache.json")


def ensure_health_loop() -> None:
    """Filled in by the setup page once the other loops can be started."""
    return


def note_log(subsystem: str, severity: str, message: str) -> None:
    """Queue an operational log entry to be shipped on the next health check-in."""
    msg = (message or "").strip()[:600]
    if not msg:
        return
    with _lock:
        if _log_buffer and _log_buffer[-1]["message"] == msg and _log_buffer[-1]["subsystem"] == subsystem:
            return
        if len(_log_buffer) >= 50:
            _log_buffer.pop(0)
        _log_buffer.append(
            {
                "subsystem": subsystem[:32],
                "severity": severity,
                "message": msg,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )


def take_logs() -> list[dict[str, str]]:
    with _lock:
        items = list(_log_buffer)
        _log_buffer.clear()
        return items


def restore_logs(items: list[dict[str, str]]) -> None:
    if not items:
        return
    with _lock:
        combined = items + _log_buffer
        _log_buffer[:] = combined[-50:]
