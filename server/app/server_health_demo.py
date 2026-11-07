"""What a guest sees in Server Health instead of the real machine's software and processes.

The real lists name the host's OS, kernel, running programs and every package with a known vulnerability: a free
map of the server's patch gaps for anyone who signs in as the public guest. Guests get this fixed sample instead.
CPU, memory, disk, traffic and database charts stay real; they say nothing an attacker can use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

DEMO_NOTE = "Sample data. The real software and process lists are shown to the administrator only."

_HOST_PACKAGES: list[dict[str, Any]] = [
    {
        "name": "libexample-ssl3",
        "installed": "3.0.2-0demo1",
        "newest": "3.0.2-0demo4",
        "behind": True,
        "advisories": [
            {"id": "DEMO-2026-0001", "risk": "high", "summary": "Sample advisory: a crafted certificate can crash the TLS library."},
            {"id": "DEMO-2026-0002", "risk": "medium", "summary": "Sample advisory: timing difference in a key comparison."},
        ],
        "risk": "high",
    },
    {
        "name": "sample-compress",
        "installed": "1.2.11-demo",
        "newest": "1.2.13-demo",
        "behind": True,
        "advisories": [{"id": "DEMO-2026-0003", "risk": "medium", "summary": "Sample advisory: out-of-bounds read on a malformed archive."}],
        "risk": "medium",
    },
    {
        "name": "demo-utils",
        "installed": "9.4-demo1",
        "newest": "9.4-demo2",
        "behind": True,
        "advisories": [],
        "risk": "low",
    },
]

_GROUPS: list[dict[str, Any]] = [
    {
        "title": "Server container",
        "note": "Sample image",
        "packages": [
            {
                "name": "demo-archiver",
                "installed": "1.34-demo",
                "newest": "1.35-demo",
                "behind": True,
                "advisories": [{"id": "DEMO-2026-0004", "risk": "low", "summary": "Sample advisory: a path in an archive can escape its folder."}],
                "risk": "low",
            }
        ],
    },
    {"title": "Server application (Python)", "note": "Sample. Everything is up to date.", "packages": []},
]

_PROCESSES = [
    {"pid": 1101, "name": "postgres: writer", "cpu": 2.4},
    {"pid": 1202, "name": "uvicorn app.main:app", "cpu": 1.6},
    {"pid": 1303, "name": "caddy run", "cpu": 0.7},
]

_MEMORY_MB = [("postgres: shared buffers", 410), ("uvicorn app.main:app", 180), ("caddy run", 45)]


def _payload(checked_at: datetime) -> dict[str, Any]:
    from app.server_health import source_key

    return {
        "version": 2,
        "os": "Sample Linux 1.0",
        "kernel": "0.0.0-demo",
        "error": "",
        "demo": True,
        "note": DEMO_NOTE,
        "checked_at": checked_at,
        "packages": _HOST_PACKAGES,
        "groups": [{**group, "key": source_key(group["title"])} for group in _GROUPS],
    }


def guest_report(real: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    """The report with the software, process and folder names replaced by the sample."""
    now = now or datetime.now(timezone.utc)
    from app.server_health import slim_advisories

    total_mb = int((real.get("latest") or {}).get("mem_total_mb") or 0)
    memory = [
        {
            "pid": 2000 + index,
            "name": name,
            "bytes": mb * 1024 * 1024,
            "share": round(100.0 * mb / total_mb, 1) if total_mb > 0 else None,
        }
        for index, (name, mb) in enumerate(_MEMORY_MB)
    ]
    storage = dict(real.get("storage") or {})
    breakdown = storage.get("breakdown")
    if breakdown:
        storage["breakdown"] = {**breakdown, "groups": [_anonymous_group(group) for group in breakdown.get("groups") or []]}
    return {
        **real,
        "advisories": slim_advisories(_payload(now - timedelta(days=2))),
        "stuck": [],
        "processes": _PROCESSES,
        "memory": memory,
        "storage": storage,
    }


def _anonymous_group(group: dict[str, Any]) -> dict[str, Any]:
    """Folder names under /home are account names; a guest sees only how much space they take."""
    if group.get("key") != "home":
        return group
    children = [
        {**child, "name": f"Folder {index + 1}", "note": ""} for index, child in enumerate(group.get("children") or [])
    ]
    return {**group, "children": children}


def guest_package(source: str, name: str) -> Optional[dict[str, Any]]:
    """One sample package with all its advisories, for the package page."""
    from app.server_health import source_key

    title = "Host"
    rows = _HOST_PACKAGES
    if source != "host":
        group = next((g for g in _GROUPS if source_key(g["title"]) == source), None)
        if group is None:
            return None
        title, rows = group["title"], group["packages"]
    row = next((item for item in rows if item["name"] == name), None)
    if row is None:
        return None
    return {
        "source": source,
        "source_title": title,
        "os": "Sample Linux 1.0",
        "kernel": "0.0.0-demo",
        "checked_at": datetime.now(timezone.utc) - timedelta(days=2),
        "package": row,
        "demo": True,
    }
