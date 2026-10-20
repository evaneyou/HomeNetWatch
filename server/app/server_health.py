"""Samples of the HomeNetWatch server itself: CPU, memory, disk, database, and the host NIC.

The container's own /proc hides the host NIC, so production mounts the host at /host.
A missing mount falls back to this process, which is what tests and a local run use.
"""

from __future__ import annotations

import gzip
import logging
import math
import os
import platform
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import ServerAdvisory, ServerDbDaily, ServerSample, ServerStorage, ServerStuck

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
SAMPLE_KEEP = timedelta(days=7)
DAILY_KEEP_YEARS = 2
SLEEP_MINUTES = 5
HOT_MINUTES = 15
HOT_SHARE = 0.9
FORECAST_DAYS = 7
FORECAST_FAR_DAYS = 365
RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PAYLOAD_VERSION = 2  # a cached check in an older shape is rebuilt on the next pass
MAX_DETAILS = 600

# How long each kind of row is kept, longest first. Matches services/retention.py. Measurements keep full
# detail for 6 months, then hourly and daily averages until they are deleted at 2 years.
RETENTION = [
    {"kept": "5 years", "items": ["KPI"]},
    {
        "kept": "2 years",
        "items": [
            "Test results",
            "Wi-Fi",
            "Channels",
            "Clients",
            "Synthetic samples",
            "Logs",
            "Airwave alerts",
            "Audit",
            "Speed-test results",
            "Resolved issues",
            "Daily database and disk size",
        ],
    },
    {"kept": "1 year", "items": ["Guest visits"]},
    {"kept": "90 days", "items": ["Resolved alerts"]},
    {"kept": "30 days", "items": ["Stress runs", "Speed samples", "Expired unused codes"]},
    {"kept": "7 days", "items": ["Pi health", "Traces", "Server samples"]},
]


def host_proc() -> Path:
    configured = Path(os.environ.get("HNS_HOST_PROC", "/host/proc"))
    if (configured / "stat").is_file():
        return configured
    return Path("/proc")


def host_root() -> Path:
    configured = Path(os.environ.get("HNS_HOST_ROOT", "/host"))
    if os.path.ismount(configured):
        return configured
    return Path("/")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def cpu_counters(stat_text: str) -> Optional[tuple[int, int]]:
    """Busy and total jiffies from the aggregate `cpu` line. Idle includes iowait."""
    for line in stat_text.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = [int(part) for part in line.split()[1:] if part.isdigit()]
        if len(parts) < 4:
            return None
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts) - idle, sum(parts)
    return None


def cpu_percent(previous: Optional[tuple[int, int]], current: Optional[tuple[int, int]]) -> Optional[float]:
    if previous is None or current is None:
        return None
    busy = current[0] - previous[0]
    total = current[1] - previous[1]
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * busy / total))


def memory_mb(meminfo: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in meminfo.splitlines():
        key, _, rest = line.partition(":")
        number = rest.strip().split()
        if number and number[0].isdigit():
            values[key] = int(number[0])
    total_kb = values.get("MemTotal", 0)
    available_kb = values.get("MemAvailable", values.get("MemFree", 0))
    used_kb = max(0, total_kb - available_kb)
    return used_kb // 1024, total_kb // 1024


def skipped_iface(name: str) -> bool:
    lowered = name.lower()
    return lowered == "lo" or lowered.startswith(("docker", "veth", "br-", "wg", "virbr"))


def parse_net(text: str) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if not name or name.lower() == "face":
            continue
        numbers = [int(part) for part in rest.split() if part.isdigit()]
        if len(numbers) < 9:
            continue
        found[name] = (numbers[0], numbers[8])
    return found


def choose_iface(counters: dict[str, tuple[int, int]]) -> tuple[str, int, int]:
    usable = {name: pair for name, pair in counters.items() if not skipped_iface(name)}
    if not usable:
        return "", 0, 0
    name = max(usable, key=lambda item: usable[item][0] + usable[item][1])
    rx, tx = usable[name]
    return name, rx, tx


def byte_rates(previous: Optional[tuple[datetime, int, int]], current: tuple[datetime, int, int]) -> tuple[Optional[float], Optional[float]]:
    if previous is None:
        return None, None
    elapsed = (current[0] - previous[0]).total_seconds()
    if elapsed <= 0:
        return None, None
    return max(0.0, (current[1] - previous[1]) / elapsed), max(0.0, (current[2] - previous[2]) / elapsed)


def parse_stat(text: str) -> Optional[tuple[str, str, int]]:
    end = text.rfind(")")
    start = text.find("(")
    if start < 0 or end < start:
        return None
    rest = text[end + 2 :].split()
    if len(rest) < 13:
        return None
    try:
        ticks = int(rest[11]) + int(rest[12])
    except ValueError:
        return None
    return text[start + 1 : end], rest[0], ticks


def list_processes(proc: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parsed = parse_stat((entry / "stat").read_text(encoding="utf-8", errors="replace"))
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if parsed is None:
            continue
        name, state, ticks = parsed
        found.append(
            {
                "pid": int(entry.name),
                "name": name[:128],
                "state": state,
                "ticks": ticks,
                "userland": bool(cmdline.replace(b"\x00", b"").strip()),
            }
        )
    return found


def judge_stuck(
    previous: dict[int, dict[str, Any]],
    processes: list[dict[str, Any]],
    now: datetime,
    *,
    clk_tck: int = 100,
) -> list[dict[str, Any]]:
    """Carry the sleep and hot counters forward. A row is reportable once `reason` is set."""
    hot_ticks = int(HOT_SHARE * clk_tck * 60)
    rows: list[dict[str, Any]] = []
    for proc in processes:
        if not proc.get("userland"):
            continue
        prior = previous.get(proc["pid"])
        state = proc["state"]
        d_minutes = (prior["d_minutes"] + 1) if prior and state == "D" and prior["state"] == "D" else (1 if state == "D" else 0)
        hot_minutes = 0
        if prior is not None and proc["ticks"] >= prior["cpu_ticks"]:
            if proc["ticks"] - prior["cpu_ticks"] >= hot_ticks:
                hot_minutes = prior["hot_minutes"] + 1
        reason = None
        if state == "Z":
            reason = "zombie"
        elif d_minutes >= SLEEP_MINUTES:
            reason = "sleep"
        elif hot_minutes >= HOT_MINUTES:
            reason = "cpu"
        same = prior is not None and prior.get("reason") == reason and reason is not None
        rows.append(
            {
                "pid": proc["pid"],
                "name": proc["name"],
                "state": state,
                "reason": reason,
                "first_seen": prior["first_seen"] if same else now,
                "seen_at": now,
                "d_minutes": d_minutes,
                "hot_minutes": hot_minutes,
                "cpu_ticks": proc["ticks"],
            }
        )
    return rows


def _dpkg_order(char: str) -> int:
    if char == "~":
        return -1
    if not char or char.isdigit():
        return 0
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _dpkg_part(left: str, right: str) -> int:
    """Compare one upstream or revision string the way dpkg does."""
    i = j = 0
    while i < len(left) or j < len(right):
        first_diff = 0
        while (i < len(left) and not left[i].isdigit()) or (j < len(right) and not right[j].isdigit()):
            a = _dpkg_order(left[i]) if i < len(left) else 0
            b = _dpkg_order(right[j]) if j < len(right) else 0
            if a != b:
                return -1 if a < b else 1
            i += 1
            j += 1
        while i < len(left) and left[i] == "0":
            i += 1
        while j < len(right) and right[j] == "0":
            j += 1
        while i < len(left) and left[i].isdigit() and j < len(right) and right[j].isdigit():
            if not first_diff:
                first_diff = ord(left[i]) - ord(right[j])
            i += 1
            j += 1
        if i < len(left) and left[i].isdigit():
            return 1
        if j < len(right) and right[j].isdigit():
            return -1
        if first_diff:
            return -1 if first_diff < 0 else 1
    return 0

def compare_versions(left: str, right: str) -> int:
    """-1, 0, or 1, in Debian version order: epoch, then upstream, then revision."""

    def split(value: str) -> tuple[int, str, str]:
        epoch = 0
        if ":" in value:
            head, value = value.split(":", 1)
            epoch = int(head) if head.isdigit() else 0
        upstream, _, revision = value.rpartition("-") if "-" in value else (value, "", "")
        return epoch, upstream, revision

    left_epoch, left_up, left_rev = split(left)
    right_epoch, right_up, right_rev = split(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    return _dpkg_part(left_up, right_up) or _dpkg_part(left_rev, right_rev)


def version_is_newer(latest: str, installed: str) -> bool:
    return compare_versions(latest, installed) > 0


def parse_dpkg(text: str) -> dict[str, str]:
    installed: dict[str, str] = {}
    name = version = status = ""

    def flush() -> None:
        if name and version and status == "install ok installed":
            installed[name] = version

    for line in text.splitlines():
        if line == "":
            flush()
            name = version = status = ""
        elif line.startswith("Package: "):
            name = line.split(": ", 1)[1].strip()
        elif line.startswith("Version: "):
            version = line.split(": ", 1)[1].strip()
        elif line.startswith("Status: "):
            status = line.split(": ", 1)[1].strip()
    flush()
    return installed


def parse_packages(text: str) -> dict[str, str]:
    """Highest version of each package in an apt Packages file."""
    newest: dict[str, str] = {}
    name = version = ""

    def flush() -> None:
        if name and version and (name not in newest or version_is_newer(version, newest[name])):
            newest[name] = version

    for line in text.splitlines():
        if line == "":
            flush()
            name = version = ""
        elif line.startswith("Package: "):
            name = line.split(": ", 1)[1].strip()
        elif line.startswith("Version: "):
            version = line.split(": ", 1)[1].strip()
    flush()
    return newest


def parse_os_release(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        found[key] = value.strip().strip('"')
    return found


WORD_RISK = {
    "critical": "critical",
    "high": "high",
    "important": "high",
    "medium": "medium",
    "moderate": "medium",
    "low": "low",
    "negligible": "low",
}

_CVSS3_WEIGHTS = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "CIA": {"H": 0.56, "L": 0.22, "N": 0.0},
}


def cvss3_score(vector: str) -> Optional[float]:
    """Base score from a CVSS v3 vector string. None when the vector is incomplete."""
    metrics = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    try:
        changed = metrics["S"] == "C"
        privileges = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[metrics["PR"]]
        exploit = (
            8.22
            * _CVSS3_WEIGHTS["AV"][metrics["AV"]]
            * _CVSS3_WEIGHTS["AC"][metrics["AC"]]
            * privileges
            * _CVSS3_WEIGHTS["UI"][metrics["UI"]]
        )
        iss = 1 - (
            (1 - _CVSS3_WEIGHTS["CIA"][metrics["C"]])
            * (1 - _CVSS3_WEIGHTS["CIA"][metrics["I"]])
            * (1 - _CVSS3_WEIGHTS["CIA"][metrics["A"]])
        )
    except KeyError:
        return None
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    if impact <= 0:
        return 0.0
    raw = min((impact + exploit) * (1.08 if changed else 1), 10)
    return math.ceil(raw * 10 - 1e-9) / 10


def risk_from_number(number: float) -> str:
    if number >= 9:
        return "critical"
    if number >= 7:
        return "high"
    if number >= 4:
        return "medium"
    return "low"


def risk_of(vuln: dict[str, Any]) -> str:
    """critical, high, medium, or low from an OSV record. Unscored records count as low."""
    severities = vuln.get("severity") or []
    # The distribution's own priority (Ubuntu, Debian) says more about this machine than a generic score.
    for severity in severities:
        word = str(severity.get("score") or "").strip().lower()
        if word in WORD_RISK:
            return WORD_RISK[word]
    for severity in severities:
        score = str(severity.get("score") or "").strip()
        number: Optional[float] = None
        if score.upper().startswith("CVSS:3"):
            number = cvss3_score(score)
        else:
            try:
                number = float(score)
            except ValueError:
                number = None
        if number is not None:
            return risk_from_number(number)
    word = str((vuln.get("database_specific") or {}).get("severity") or "").strip().lower()
    return WORD_RISK.get(word, "low")


def advisories_from_osv(
    results: list[dict[str, Any]],
    names: list[str],
    details: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, list[dict[str, str]]]:
    """Advisory rows per package. `details` holds the full record by id, because a batch answer has no score."""
    details = details or {}
    found: dict[str, list[dict[str, str]]] = {}
    for name, item in zip(names, results):
        rows = []
        for vuln in item.get("vulns") or []:
            full = details.get(str(vuln.get("id") or ""), vuln)
            rows.append(
                {
                    "id": str(vuln.get("id") or ""),
                    "risk": risk_of(full),
                    "summary": str(full.get("summary") or full.get("details") or "")[:240],
                }
            )
        if rows:
            rows.sort(key=lambda row: RISK_ORDER.get(row["risk"], 9))
            found[name] = rows
    return found


def package_rows(
    installed: dict[str, str],
    newest: dict[str, str],
    vulns: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Packages that are behind or have a known vulnerability, worst first."""
    packages = []
    for name, version in installed.items():
        latest = newest.get(name) or ""
        behind = bool(latest) and version_is_newer(latest, version)
        advisories = vulns.get(name) or []
        if not behind and not advisories:
            continue
        packages.append(
            {
                "name": name,
                "installed": version,
                "newest": latest or version,
                "behind": behind,
                "advisories": advisories,
                "risk": advisories[0]["risk"] if advisories else "low",
            }
        )
    packages.sort(key=lambda row: (RISK_ORDER.get(row["risk"], 9), row["name"]))
    return packages


def support_row(name: str, version: str, cycle: Optional[dict[str, Any]], today: date) -> tuple[Optional[dict[str, Any]], str]:
    """A row for a product with a support calendar (endoflife.date), and a one-line note."""
    if not cycle:
        return None, ""
    latest = str(cycle.get("latest") or "")
    eol_raw = cycle.get("eol")
    eol: Optional[date] = None
    if isinstance(eol_raw, str):
        try:
            eol = date.fromisoformat(eol_raw)
        except ValueError:
            eol = None
    expired = eol is not None and eol <= today
    behind = bool(latest) and version_is_newer(latest, version)
    note = ""
    if eol is not None:
        note = f"{name} support {'ended' if expired else 'runs until'} {eol.isoformat()}."
    if not behind and not expired:
        return None, note
    advisories = (
        [{"id": "End of life", "risk": "high", "summary": f"Support ended {eol.isoformat()}."}]
        if expired and eol is not None
        else []
    )
    return (
        {
            "name": name,
            "installed": version,
            "newest": latest or version,
            "behind": behind,
            "advisories": advisories,
            "risk": "high" if advisories else "low",
        },
        note,
    )


def build_advisory_payload(
    *,
    os_name: str,
    kernel: str,
    installed: dict[str, str],
    newest: dict[str, str],
    vulns: dict[str, list[dict[str, str]]],
    error: str = "",
    groups: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    return {
        "version": PAYLOAD_VERSION,
        "os": os_name,
        "kernel": kernel,
        "error": error,
        "packages": package_rows(installed, newest, vulns),
        "groups": groups or [],
    }


def next_sunday_3am(after: datetime) -> datetime:
    local = after.astimezone(EASTERN)
    days = (6 - local.weekday()) % 7
    candidate = (local + timedelta(days=days)).replace(hour=3, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def check_due(now: datetime, last: Optional[datetime]) -> bool:
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) >= next_sunday_3am(last)


def forecast(daily: list[tuple[date, int]], free_bytes: int) -> dict[str, Any]:
    """When a straight fit of daily database size would use up the free disk."""
    if len(daily) < FORECAST_DAYS:
        return {"ready": False, "growing": False, "full_on": None, "bytes_per_day": None, "far": False}
    origin = daily[0][0].toordinal()
    xs = [day.toordinal() - origin for day, _size in daily]
    ys = [size for _day, size in daily]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread <= 0:
        return {"ready": True, "growing": False, "full_on": None, "bytes_per_day": 0, "far": False}
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread
    if slope <= 0:
        return {"ready": True, "growing": False, "full_on": None, "bytes_per_day": round(slope), "far": False}
    intercept = mean_y - slope * mean_x
    target = ys[-1] + max(0, free_bytes)
    full_x = (target - intercept) / slope
    days_ahead = full_x - xs[-1]
    far = days_ahead > FORECAST_FAR_DAYS
    full_on = None
    if 0 <= days_ahead <= 365 * 200:
        full_on = (daily[-1][0] + timedelta(days=int(round(days_ahead)))).isoformat()
    return {
        "ready": True,
        "growing": True,
        "full_on": full_on,
        "bytes_per_day": round(slope),
        "far": far,
        "origin": daily[0][0].isoformat(),
        "intercept": intercept,
        "slope": slope,
    }


def predict_bytes(cast: dict[str, Any], when: datetime) -> Optional[float]:
    if not cast.get("growing"):
        return None
    origin = date.fromisoformat(cast["origin"]).toordinal()
    moment = when.astimezone(timezone.utc).date().toordinal() - origin
    return cast["intercept"] + cast["slope"] * moment


def _mb(value: int) -> int:
    return int(value // (1024 * 1024))


def read_machine(proc: Path, root: Path, previous_cpu: Optional[tuple[int, int]]) -> dict[str, Any]:
    counters = cpu_counters(_read(proc / "stat"))
    used_mb, total_mb = memory_mb(_read(proc / "meminfo"))
    try:
        disk = shutil.disk_usage(root if root.exists() else "/")
    except OSError:
        disk = shutil.disk_usage("/")
    iface, rx, tx = choose_iface(parse_net(_read(proc / "net" / "dev")))
    return {
        "cpu_percent": cpu_percent(previous_cpu, counters),
        "cpu_counters": counters,
        "mem_used_mb": used_mb,
        "mem_total_mb": total_mb,
        "disk_used_bytes": disk.used,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "disk_used_mb": _mb(disk.used),
        "disk_total_mb": _mb(disk.total),
        "iface": iface,
        "rx_bytes": rx,
        "tx_bytes": tx,
    }


def read_database(db: Session) -> tuple[int, int, list[dict[str, Any]]]:
    size = int(db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
    connections = int(
        db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar() or 0
    )
    tables = [
        {"name": name, "bytes": int(amount)}
        for name, amount in db.execute(
            text(
                """
                SELECT c.relname, pg_total_relation_size(c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY pg_total_relation_size(c.oid) DESC
                LIMIT 5
                """
            )
        ).all()
    ]
    return size, connections, tables


_last_cpu: Optional[tuple[int, int]] = None


def record_sample(db: Session, now: Optional[datetime] = None, *, proc: Optional[Path] = None, root: Optional[Path] = None) -> ServerSample:
    global _last_cpu
    now = now or datetime.now(timezone.utc)
    machine = read_machine(proc or host_proc(), root or host_root(), _last_cpu)
    _last_cpu = machine["cpu_counters"]
    db_bytes, connections, _tables = read_database(db)
    sample = ServerSample(
        recorded_at=now,
        cpu_percent=machine["cpu_percent"],
        mem_used_mb=machine["mem_used_mb"],
        mem_total_mb=machine["mem_total_mb"],
        disk_used_mb=machine["disk_used_mb"],
        disk_total_mb=machine["disk_total_mb"],
        db_bytes=db_bytes,
        connections=connections,
        iface=machine["iface"],
        rx_bytes=machine["rx_bytes"],
        tx_bytes=machine["tx_bytes"],
    )
    db.add(sample)
    day = now.astimezone(timezone.utc).date()
    daily = db.get(ServerDbDaily, day)
    if daily is None:
        daily = ServerDbDaily(day=day, db_bytes=db_bytes, free_bytes=machine["disk_free_bytes"])
        db.add(daily)
    else:
        daily.db_bytes = db_bytes
        daily.free_bytes = machine["disk_free_bytes"]
    daily.disk_used_bytes = machine["disk_used_bytes"]
    daily.disk_total_bytes = machine["disk_total_bytes"]
    _store_stuck(db, list_processes(proc or host_proc()), now)
    return sample


def _store_stuck(db: Session, processes: list[dict[str, Any]], now: datetime) -> None:
    previous = {
        row.pid: {
            "state": row.state,
            "reason": row.reason,
            "first_seen": row.first_seen,
            "d_minutes": row.d_minutes,
            "hot_minutes": row.hot_minutes,
            "cpu_ticks": row.cpu_ticks,
        }
        for row in db.scalars(select(ServerStuck)).all()
    }
    try:
        clk = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        clk = 100
    judged = judge_stuck(previous, processes, now, clk_tck=int(clk))
    db.execute(delete(ServerStuck))
    db.flush()
    for row in judged:
        db.add(ServerStuck(**row))


def kernel_version(proc: Path) -> str:
    text_value = _read(proc / "version")
    parts = text_value.split()
    if len(parts) >= 3 and parts[0] == "Linux":
        return parts[2]
    return parts[2] if len(parts) >= 3 else ""


def machine_arch() -> str:
    return "arm64" if platform.machine().lower() in {"aarch64", "arm64"} else "amd64"


def package_urls(distro: str, codename: str, arch: str) -> list[str]:
    """Package lists that name the newest version of each package in a release."""
    if distro == "debian":
        return [
            f"https://deb.debian.org/debian/dists/{codename}/main/binary-{arch}/Packages.gz",
            f"https://deb.debian.org/debian/dists/{codename}-updates/main/binary-{arch}/Packages.gz",
            f"https://security.debian.org/debian-security/dists/{codename}-security/main/binary-{arch}/Packages.gz",
        ]
    urls = []
    for pocket in (codename, f"{codename}-updates", f"{codename}-security"):
        for component in ("main", "universe"):
            if arch == "amd64":
                host = "https://security.ubuntu.com/ubuntu" if pocket.endswith("-security") else "https://archive.ubuntu.com/ubuntu"
            else:
                host = "https://ports.ubuntu.com/ubuntu-ports"
            urls.append(f"{host}/dists/{pocket}/{component}/binary-{arch}/Packages.gz")
    return urls


def fetch_newest(distro: str, codename: str, client: httpx.Client) -> dict[str, str]:
    newest: dict[str, str] = {}
    for url in package_urls(distro, codename, machine_arch()):
        try:
            response = client.get(url)
            response.raise_for_status()
            text_value = gzip.decompress(response.content).decode("utf-8", "replace")
        except (httpx.HTTPError, OSError, gzip.BadGzipFile) as exc:
            log.warning("package list unavailable: %s (%s)", url, exc)
            continue
        for name, version in parse_packages(text_value).items():
            if name not in newest or version_is_newer(version, newest[name]):
                newest[name] = version
    return newest


def fetch_details(ids: list[str], client: httpx.Client, cache: dict[str, dict[str, Any]]) -> None:
    """Full OSV records, which carry the score. A batch answer has only the id."""
    wanted = [item for item in dict.fromkeys(ids) if item and item not in cache][:MAX_DETAILS]

    def one(vuln_id: str) -> tuple[str, Optional[dict[str, Any]]]:
        try:
            response = client.get(f"https://api.osv.dev/v1/vulns/{vuln_id}")
            response.raise_for_status()
            return vuln_id, response.json()
        except (httpx.HTTPError, ValueError):
            return vuln_id, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for vuln_id, record in pool.map(one, wanted):
            if record is not None:
                cache[vuln_id] = record


def fetch_vulns(
    installed: dict[str, str],
    ecosystem: str,
    client: httpx.Client,
    cache: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, list[dict[str, str]]]:
    cache = cache if cache is not None else {}
    names = list(installed)
    answers: list[tuple[list[str], list[dict[str, Any]]]] = []
    for start in range(0, len(names), 100):
        chunk = names[start : start + 100]
        queries = [{"version": installed[name], "package": {"name": name, "ecosystem": ecosystem}} for name in chunk]
        try:
            response = client.post("https://api.osv.dev/v1/querybatch", json={"queries": queries})
            response.raise_for_status()
            answers.append((chunk, response.json().get("results") or []))
        except (httpx.HTTPError, ValueError):
            log.warning("vulnerability lookup failed for %s packages", len(chunk))
    ids = [str(vuln.get("id") or "") for _chunk, results in answers for item in results for vuln in item.get("vulns") or []]
    fetch_details(ids, client, cache)
    found: dict[str, list[dict[str, str]]] = {}
    for chunk, results in answers:
        found.update(advisories_from_osv(results, chunk, cache))
    return found


def fetch_support(product: str, client: httpx.Client) -> list[dict[str, Any]]:
    try:
        response = client.get(f"https://endoflife.date/api/{product}.json")
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except (httpx.HTTPError, ValueError):
        log.warning("support calendar unavailable for %s", product)
        return []


def os_package_group(title: str, root: Path, client: httpx.Client, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Packages of the operating system found under `root`, compared with that release's archive."""
    release = parse_os_release(_read(root / "etc" / "os-release"))
    installed = parse_dpkg(_read(root / "var" / "lib" / "dpkg" / "status"))
    name = release.get("PRETTY_NAME") or release.get("NAME") or "Linux"
    if not installed:
        return {"title": title, "note": name, "error": "The package list could not be read.", "packages": []}
    distro = release.get("ID", "")
    version_id = release.get("VERSION_ID", "")
    newest = fetch_newest(distro, release.get("VERSION_CODENAME", ""), client) if release.get("VERSION_CODENAME") else {}
    ecosystem = ""
    if distro == "ubuntu" and version_id:
        ecosystem = f"Ubuntu:{version_id}:LTS" if "LTS" in name else f"Ubuntu:{version_id}"
    elif distro == "debian" and version_id:
        ecosystem = f"Debian:{version_id}"
    vulns = fetch_vulns(installed, ecosystem, client, cache) if ecosystem else {}
    return {"title": title, "note": name, "packages": package_rows(installed, newest, vulns)}


def python_requirements() -> dict[str, str]:
    """The server's own Python libraries and the versions it is running."""
    installed: dict[str, str] = {}
    try:
        requires = metadata.requires("hns-server") or []
    except metadata.PackageNotFoundError:
        requires = []
    for requirement in requires:
        if "extra ==" in requirement:
            continue
        name = re.split(r"[\s\[<>=!~;(]", requirement, maxsplit=1)[0]
        try:
            installed[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return installed


def fetch_pypi_latest(names: list[str], client: httpx.Client) -> dict[str, str]:
    latest: dict[str, str] = {}
    for name in names:
        try:
            response = client.get(f"https://pypi.org/pypi/{name}/json")
            response.raise_for_status()
            latest[name] = str(response.json()["info"]["version"])
        except (httpx.HTTPError, ValueError, KeyError):
            log.warning("latest version unavailable for %s", name)
    return latest


def python_group(client: httpx.Client, cache: dict[str, dict[str, Any]], today: date) -> dict[str, Any]:
    installed = python_requirements()
    rows = package_rows(installed, fetch_pypi_latest(list(installed), client), fetch_vulns(installed, "PyPI", client, cache))
    version = platform.python_version()
    cycle_name = ".".join(version.split(".")[:2])
    cycles = fetch_support("python", client)
    cycle = next((item for item in cycles if str(item.get("cycle")) == cycle_name), None)
    row, note = support_row(f"Python {cycle_name}", version, cycle, today)
    if row is not None:
        rows.insert(0, row)
        rows.sort(key=lambda item: (RISK_ORDER.get(item["risk"], 9), item["name"]))
    return {"title": "Server application (Python)", "note": f"Python {version}. {note}".strip(), "packages": rows}


def postgres_group(db: Session, client: httpx.Client, today: date) -> dict[str, Any]:
    version = str(db.execute(text("SHOW server_version")).scalar() or "").split()[0]
    major = version.split(".")[0]
    cycle = next((item for item in fetch_support("postgresql", client) if str(item.get("cycle")) == major), None)
    row, note = support_row(f"PostgreSQL {major}", version, cycle, today)
    return {
        "title": "PostgreSQL",
        "note": f"Version {version}. {note}".strip(),
        "packages": [row] if row is not None else [],
    }


def refresh_advisories(
    db: Session,
    now: datetime,
    *,
    installed: dict[str, str],
    newest: dict[str, str],
    vulns: dict[str, list[dict[str, str]]],
    os_name: str,
    kernel: str,
    error: str = "",
    groups: Optional[list[dict[str, Any]]] = None,
) -> None:
    payload = build_advisory_payload(
        os_name=os_name, kernel=kernel, installed=installed, newest=newest, vulns=vulns, error=error, groups=groups
    )
    row = db.get(ServerAdvisory, 1)
    if row is None:
        db.add(ServerAdvisory(id=1, checked_at=now, payload=payload))
    else:
        row.checked_at = now
        row.payload = payload


def refresh_advisories_live(db: Session, now: datetime, *, proc: Optional[Path] = None, root: Optional[Path] = None) -> None:
    proc = proc or host_proc()
    root = root or host_root()
    release = parse_os_release(_read(root / "etc" / "os-release"))
    installed = parse_dpkg(_read(root / "var" / "lib" / "dpkg" / "status"))
    os_name = release.get("PRETTY_NAME") or release.get("NAME") or "Linux"
    kernel = kernel_version(proc)
    error = ""
    newest: dict[str, str] = {}
    vulns: dict[str, list[dict[str, str]]] = {}
    groups: list[dict[str, Any]] = []
    today = now.astimezone(timezone.utc).date()
    cache: dict[str, dict[str, Any]] = {}
    if not installed:
        error = "The host package list could not be read."
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            if installed:
                codename = release.get("VERSION_CODENAME") or ""
                version_id = release.get("VERSION_ID") or ""
                if codename:
                    newest = fetch_newest(release.get("ID", "ubuntu"), codename, client)
                if version_id:
                    ecosystem = f"Ubuntu:{version_id}:LTS" if "LTS" in os_name else f"Ubuntu:{version_id}"
                    vulns = fetch_vulns(installed, ecosystem, client, cache)
            for build in (
                lambda: postgres_group(db, client, today),
                lambda: python_group(client, cache, today),
                lambda: os_package_group("Server container", Path("/"), client, cache),
            ):
                try:
                    groups.append(build())
                except Exception:
                    log.exception("software group failed")
    except httpx.HTTPError as exc:
        error = "The version check could not be finished."
        log.warning("advisory refresh failed: %s", exc)
    refresh_advisories(
        db,
        now,
        installed=installed,
        newest=newest,
        vulns=vulns,
        os_name=os_name,
        kernel=kernel,
        error=error,
        groups=groups,
    )


def maybe_refresh_advisories(db: Session, now: datetime) -> None:
    row = db.get(ServerAdvisory, 1)
    last = row.checked_at if row is not None else None
    old_format = row is not None and (row.payload or {}).get("version") != PAYLOAD_VERSION
    if old_format or check_due(now, last):
        refresh_advisories_live(db, now)


def prune(db: Session, now: datetime) -> int:
    samples = db.execute(delete(ServerSample).where(ServerSample.recorded_at < now - SAMPLE_KEEP)).rowcount or 0
    cutoff = date(now.year - DAILY_KEEP_YEARS, now.month, now.day) if now.month != 2 or now.day != 29 else date(now.year - DAILY_KEEP_YEARS, 2, 28)
    daily = db.execute(delete(ServerDbDaily).where(ServerDbDaily.day < cutoff)).rowcount or 0
    stuck = db.execute(delete(ServerStuck).where(ServerStuck.seen_at < now - timedelta(hours=1))).rowcount or 0
    return samples + daily + stuck


def _range_start(name: str, now: datetime) -> datetime:
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 24 * 7}.get(name, 1)
    return now - timedelta(hours=hours)


PROJECTION_DAYS = 365  # how far ahead the storage line is drawn when the disk is not about to fill
STORAGE_EVERY = timedelta(hours=6)
STORAGE_HOME_CHILDREN = 3


def disk_projection(rows: list[ServerDbDaily], now: datetime) -> dict[str, Any]:
    """Where used disk is heading, from a straight fit of the daily readings."""
    readings = [(row.day, row.disk_used_bytes, row.disk_total_bytes) for row in rows if row.disk_used_bytes and row.disk_total_bytes]
    empty = {"ready": False, "growing": False, "bytes_per_day": None, "full_on": None, "far": False, "line": []}
    if len(readings) < FORECAST_DAYS:
        return empty
    free = max(0, readings[-1][2] - readings[-1][1])
    cast = forecast([(day, used) for day, used, _total in readings], free)
    if not cast["growing"]:
        return {**empty, "ready": True}
    slope = float(cast["slope"])
    used = float(readings[-1][1])
    days_to_full = free / slope if slope > 0 else None
    reach = min(days_to_full, PROJECTION_DAYS) if days_to_full is not None else PROJECTION_DAYS
    end = now + timedelta(days=reach)
    full_on = (now + timedelta(days=days_to_full)).date().isoformat() if days_to_full is not None and days_to_full < 365 * 200 else None
    return {
        "ready": True,
        "growing": True,
        "bytes_per_day": round(slope),
        "full_on": full_on,
        "far": days_to_full is None or days_to_full > PROJECTION_DAYS,
        "line": [{"t": now, "bytes": used}, {"t": end, "bytes": used + slope * reach}],
    }


def _du(path: Path, timeout: int = 600) -> Optional[int]:
    """Bytes under a path on the same filesystem, or None when it cannot be read."""
    if not path.exists():
        return None
    try:
        done = subprocess.run(
            ["nice", "-n", "19", "du", "-sxb", str(path)], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (done.stdout or "").split("\t", 1)[0].strip()
    return int(first) if first.isdigit() else None


def measure_storage(root: Path, used_bytes: int, total_bytes: int, db_bytes: int) -> dict[str, Any]:
    """What uses the disk, in groups with the parts inside them. Slow: it walks big folders."""
    layers = _du(root / "var" / "lib" / "containerd") or 0
    docker_dir = _du(root / "var" / "lib" / "docker") or 0
    volumes = _du(root / "var" / "lib" / "docker" / "volumes") or 0
    layers += max(0, docker_dir - volumes)

    home_children: list[dict[str, Any]] = []
    home_dir = root / "home"
    users = sorted(path for path in home_dir.iterdir() if path.is_dir()) if home_dir.is_dir() else []
    for user in users:
        try:
            entries = sorted(user.iterdir())
        except OSError:
            continue
        for entry in entries:
            size = _du(entry)
            if size:
                name = entry.name if len(users) == 1 else f"{user.name}/{entry.name}"
                home_children.append({"name": name, "bytes": size})
    home_children.sort(key=lambda child: -child["bytes"])
    home_total = _du(home_dir) or sum(child["bytes"] for child in home_children)
    shown = home_children[:STORAGE_HOME_CHILDREN]
    rest = home_total - sum(child["bytes"] for child in shown)
    if rest > 0:
        shown.append({"name": "Other", "bytes": rest})

    system = _du(root / "usr") or 0
    groups = [
        {
            "key": "docker",
            "title": "Docker images and build cache",
            "bytes": layers,
            "children": [
                {
                    "name": "Image layers and build cache",
                    "bytes": layers,
                    "note": "Mostly build cache left over from deploys. On the server, docker builder prune clears it.",
                }
            ],
        },
        {"key": "home", "title": "Home folders", "bytes": home_total, "children": shown},
        {
            "key": "system",
            "title": "System programs",
            "bytes": system,
            "children": [{"name": "Installed programs and libraries", "bytes": system, "note": "/usr"}],
        },
        {
            "key": "volumes",
            "title": "Docker volumes",
            "bytes": volumes,
            "children": [
                {"name": "Database", "bytes": min(db_bytes, volumes)},
                {"name": "Other volumes", "bytes": max(0, volumes - db_bytes)},
            ],
        },
    ]
    measured = sum(group["bytes"] for group in groups)
    rest_bytes = max(0, used_bytes - measured)
    groups.append(
        {
            "key": "other",
            "title": "Everything else",
            "bytes": rest_bytes,
            "children": [{"name": "Logs, snaps, package caches, boot files", "bytes": rest_bytes, "note": "What is left after the groups above."}],
        }
    )
    for group in groups:
        group["children"] = [child for child in group["children"] if child["bytes"] > 0]
    groups = [group for group in groups if group["bytes"] > 0]
    groups.sort(key=lambda group: -group["bytes"])
    return {"version": 1, "used_bytes": used_bytes, "total_bytes": total_bytes, "groups": groups}


def storage_due(db: Session, now: datetime) -> bool:
    row = db.get(ServerStorage, 1)
    if row is None:
        return True
    last = row.checked_at if row.checked_at.tzinfo else row.checked_at.replace(tzinfo=timezone.utc)
    return now - last >= STORAGE_EVERY


def run_storage() -> None:
    """Measure what uses the disk when the last measurement is old. It walks big folders, so it runs apart."""
    from app.db import SessionLocal

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        if not storage_due(db, now):
            return
        db_bytes = read_database(db)[0]
    finally:
        db.close()
    root = host_root()
    disk = shutil.disk_usage(root if root.exists() else "/")
    payload = measure_storage(root, disk.used, disk.total, db_bytes)
    db = SessionLocal()
    try:
        row = db.get(ServerStorage, 1)
        if row is None:
            db.add(ServerStorage(id=1, checked_at=now, payload=payload))
        else:
            row.checked_at = now
            row.payload = payload
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


PREVIEW_ADVISORIES = 3  # what a row hover shows; the details page has the rest


def source_key(title: str) -> str:
    """A URL-safe name for a software source such as "Server container"."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "source"


def _preview(row: dict[str, Any]) -> dict[str, Any]:
    """A package row with only its worst advisories, so one kernel does not send a thousand."""
    advisories = row.get("advisories") or []
    return {**row, "advisories": advisories[:PREVIEW_ADVISORIES], "advisory_count": len(advisories)}


def slim_advisories(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "packages": [_preview(row) for row in payload.get("packages") or []],
        "groups": [
            {**group, "key": source_key(str(group.get("title") or "")), "packages": [_preview(row) for row in group.get("packages") or []]}
            for group in payload.get("groups") or []
        ],
    }


def package_detail(db: Session, source: str, name: str) -> Optional[dict[str, Any]]:
    """One package from the cached weekly check, with every advisory."""
    advisory = db.get(ServerAdvisory, 1)
    if advisory is None:
        return None
    payload = advisory.payload
    title = "Host"
    rows = payload.get("packages") or []
    if source != "host":
        group = next((g for g in payload.get("groups") or [] if source_key(str(g.get("title") or "")) == source), None)
        if group is None:
            return None
        title = str(group.get("title") or "")
        rows = group.get("packages") or []
    row = next((item for item in rows if item.get("name") == name), None)
    if row is None:
        return None
    return {
        "source": source,
        "source_title": title,
        "os": payload.get("os", ""),
        "kernel": payload.get("kernel", ""),
        "checked_at": advisory.checked_at,
        "package": row,
    }


def report(db: Session, range_name: str, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = _range_start(range_name, now)
    samples = db.scalars(
        select(ServerSample).where(ServerSample.recorded_at >= since).order_by(ServerSample.recorded_at)
    ).all()
    daily_rows = db.scalars(select(ServerDbDaily).order_by(ServerDbDaily.day)).all()
    free = daily_rows[-1].free_bytes if daily_rows else 0
    cast = forecast([(row.day, row.db_bytes) for row in daily_rows], free)
    earlier = db.scalar(
        select(ServerSample).where(ServerSample.recorded_at < since).order_by(ServerSample.recorded_at.desc()).limit(1)
    )
    previous: Optional[tuple[datetime, int, int]] = (
        (earlier.recorded_at, earlier.rx_bytes, earlier.tx_bytes) if earlier is not None else None
    )
    points = []
    for sample in samples:
        rx_bps, tx_bps = byte_rates(previous, (sample.recorded_at, sample.rx_bytes, sample.tx_bytes))
        previous = (sample.recorded_at, sample.rx_bytes, sample.tx_bytes)
        points.append(
            {
                "t": sample.recorded_at,
                "cpu": sample.cpu_percent,
                "mem_used_mb": sample.mem_used_mb,
                "mem_total_mb": sample.mem_total_mb,
                "disk_used_mb": sample.disk_used_mb,
                "disk_total_mb": sample.disk_total_mb,
                "db_bytes": sample.db_bytes,
                "forecast_bytes": predict_bytes(cast, sample.recorded_at),
                "rx_bps": rx_bps,
                "tx_bps": tx_bps,
                "iface": sample.iface,
                "connections": sample.connections,
            }
        )
    latest = points[-1] if points else None
    _size, connections, tables = read_database(db)
    if latest is not None:
        latest = {**latest, "connections": connections}
    advisory = db.get(ServerAdvisory, 1)
    measured = db.get(ServerStorage, 1)
    stuck = [
        {
            "pid": row.pid,
            "name": row.name,
            "state": row.state,
            "reason": row.reason,
            "since": row.first_seen,
        }
        for row in db.scalars(select(ServerStuck).where(ServerStuck.reason.is_not(None)).order_by(ServerStuck.first_seen)).all()
    ]
    public_cast = {key: cast[key] for key in ("ready", "growing", "full_on", "bytes_per_day", "far")}
    return {
        "latest": latest,
        "points": points,
        "tables": tables,
        "forecast": public_cast,
        "retention": RETENTION,
        "advisories": (
            {"checked_at": advisory.checked_at, **slim_advisories(advisory.payload)} if advisory is not None else None
        ),
        "stuck": stuck,
        "storage": {
            "projection": disk_projection(daily_rows, now),
            "breakdown": {"checked_at": measured.checked_at, **measured.payload} if measured is not None else None,
        },
    }


def run_once() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        record_sample(db, now)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_advisories() -> None:
    """The weekly check. It downloads package lists, so it runs apart from the minute sample."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        maybe_refresh_advisories(db, datetime.now(timezone.utc))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
