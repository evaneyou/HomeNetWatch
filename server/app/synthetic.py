"""Synthetic traffic profiles, public-path checks, and stored reports.

Measurement packets use the server's public address. They never target the
WireGuard addresses 10.7.0.1 or 10.7.1.1.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RtnpSecret, SyntheticProfile, SyntheticRun, SyntheticSample

LINKS = ("wired", "wireless")
LINK_LABELS = {"wired": "Wired", "wireless": "Wi-Fi"}
UDP_PORT = 8998
CONTROL_PORT = 8999

_EDITABLE = ("report_interval_s", "max_rtt_ms", "max_jitter_ms", "max_loss_pct", "ptime_ms")

PROFILES: list[dict[str, Any]] = [
    {
        "slug": "voip-g711a",
        "name": "Voice G.711 A-law",
        "category": "voice",
        "position": 1,
        "options": {
            "preset": "voip-g711a",
            "wire": "rtp",
            "rtp": "g711a",
            "ptime_ms": 20,
            "bidir": True,
            "rtcp_interval_s": 5,
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "voip-g729",
        "name": "Voice G.729",
        "category": "voice",
        "position": 2,
        "options": {
            "preset": "voip-g729",
            "wire": "rtp",
            "rtp": "g729",
            "ptime_ms": 20,
            "bidir": True,
            "rtcp_interval_s": 5,
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "voip-g723",
        "name": "Voice G.723.1",
        "category": "voice",
        "position": 3,
        "options": {
            "preset": "voip-g723",
            "wire": "rtp",
            "rtp": "g723",
            "ptime_ms": 30,
            "bidir": True,
            "rtcp_interval_s": 5,
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "video-conference",
        "name": "Video conference",
        "category": "video",
        "position": 4,
        "options": {
            "preset": "video-conference",
            "wire": "rtp",
            "video": True,
            "rtcp_interval_s": 5,
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "fortnite",
        "name": "Fortnite",
        "category": "games",
        "position": 5,
        "options": {
            "preset": "fortnite",
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "call-of-duty",
        "name": "Call of Duty",
        "category": "games",
        "position": 6,
        "options": {
            "preset": "call-of-duty",
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "remote-desktop",
        "name": "Remote desktop",
        "category": "enterprise",
        "position": 7,
        "options": {
            "preset": "remote-desktop",
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
    {
        "slug": "market-data",
        "name": "Market data",
        "category": "enterprise",
        "position": 8,
        "options": {
            "preset": "market-data",
            "report_interval_s": 10,
            "max_rtt_ms": 300,
            "max_jitter_ms": 30,
            "max_loss_pct": 1,
        },
    },
]


def is_tunnel_target(host: str) -> bool:
    """True when the address is the WireGuard hub, which synthetic traffic must not use."""
    value = host.strip().lower()
    return value.startswith("10.7.0.") or value.startswith("10.7.1.")


def public_host(endpoint: str) -> str:
    """Host portion of WIREGUARD_ENDPOINT. The port is the tunnel, not the measurement."""
    text = endpoint.strip()
    if text.startswith("["):
        end = text.find("]")
        host = text[1:end] if end > 1 else text
    elif text.count(":") == 1:
        host = text.split(":", 1)[0]
    else:
        host = text
    if is_tunnel_target(host):
        raise ValueError("Synthetic traffic must not use the tunnel address")
    if not host or ":" in host:
        raise ValueError("Synthetic traffic is IPv4 only")
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Synthetic traffic needs an IPv4 address") from exc
    if parsed.version != 4:
        raise ValueError("Synthetic traffic is IPv4 only")
    return host


def launch_plan(link: str, target: str, interface: Optional[str]) -> dict[str, str]:
    """The send stays on the physical NIC for this link."""
    if link not in LINKS:
        raise ValueError("Choose wired or Wi-Fi")
    if is_tunnel_target(target):
        raise ValueError("Synthetic traffic must not use the tunnel")
    if not interface:
        raise ValueError("Synthetic traffic needs the physical NIC for this link")
    if interface.startswith("wg"):
        raise ValueError("Synthetic traffic must not leave through WireGuard")
    return {"link": link, "target": target, "interface": interface}


def route_device(route_text: str) -> Optional[str]:
    parts = route_text.split()
    if "dev" not in parts:
        return None
    index = parts.index("dev")
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def route_is_physical(route_text: str, interface: str) -> bool:
    device = route_device(route_text)
    if not device or device.startswith("wg"):
        return False
    return device == interface


VOICE_PRESETS = {"voip-g711a", "voip-g729", "voip-g723"}
PRESETS = {item["slug"] for item in PROFILES}
MAX_RATE_PPS = 200
MAX_DURATION_SECONDS = 3600
CATEGORY_ORDER = ("voice", "video", "enterprise", "games")
CATEGORY_NAMES = {
    "voice": "Voice",
    "video": "Video",
    "enterprise": "Enterprise apps",
    "games": "Games",
}


def profile_category(slug: str) -> Optional[str]:
    for item in PROFILES:
        if item["slug"] == slug:
            category = item.get("category")
            return category if isinstance(category, str) else None
    return None


def group_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group presets in display order. Empty groups and unknown categories are left out."""
    groups = []
    for category in CATEGORY_ORDER:
        items = [item for item in profiles if item.get("category") == category]
        if not items:
            continue
        groups.append({"category": category, "name": CATEGORY_NAMES[category], "profiles": items})
    return groups


def send_args(
    binary: str,
    options: dict[str, Any],
    data_host: str,
    *,
    control_host: str,
    key_file: str,
    srtp_key_file: str,
    forever: bool,
    duration_seconds: Optional[int] = None,
) -> list[str]:
    """Build an argument list. The SRTP key stays in a file and never appears here."""
    if is_tunnel_target(data_host) or ":" in data_host:
        raise ValueError("Synthetic traffic must use the public IPv4 address")
    if not is_tunnel_target(control_host):
        raise ValueError("The control channel must use the tunnel")
    preset = str(options.get("preset") or "")
    if preset not in PRESETS:
        raise ValueError("Unknown traffic preset")
    if options.get("meter"):
        raise ValueError("Packet reflection is disabled")
    payload = str(options.get("payload") or "")
    if payload.startswith("file:") or payload.startswith("wav:"):
        raise ValueError("File payloads are not allowed")
    rate = options.get("rate_pps")
    if rate is not None and float(rate) > MAX_RATE_PPS:
        raise ValueError("Packet rate is too high")
    args = [
        binary,
        "flow",
        "-t",
        f"{data_host}:{UDP_PORT}",
        "-c",
        f"{control_host}:{CONTROL_PORT}",
        "--preset",
        preset,
        "--name",
        preset,
        "--json",
        "-i",
        f"{int(options['report_interval_s'])}s",
        "--key-file",
        key_file,
        "--srtp-key-file",
        srtp_key_file,
        "--max-rtt",
        str(options["max_rtt_ms"]),
        "--max-jitter",
        str(options["max_jitter_ms"]),
        "--max-loss",
        str(options["max_loss_pct"]),
    ]
    if options.get("wire") == "rtp":
        args.extend(["--wire", "rtp"])
    if options.get("rtp"):
        args.extend(["--rtp", str(options["rtp"])])
    if options.get("ptime_ms"):
        args.extend(["--ptime", str(options["ptime_ms"])])
    if options.get("bidir"):
        if preset not in VOICE_PRESETS:
            raise ValueError("Two-way traffic is only for the voice profiles")
        args.append("--bidir")
    if options.get("video"):
        args.append("--video")
    if options.get("rtcp_interval_s") is not None:
        args.extend(["--rtcp-interval", f"{int(options['rtcp_interval_s'])}s"])
    if forever:
        args.append("--forever")
    else:
        if duration_seconds is None or duration_seconds < 1 or duration_seconds > MAX_DURATION_SECONDS:
            raise ValueError("A timed run needs a duration of at most 3600 seconds")
        args.extend(["-d", f"{int(duration_seconds)}s"])
    return args


def ensure_profiles(db: Session) -> list[SyntheticProfile]:
    existing = {row.slug: row for row in db.scalars(select(SyntheticProfile)).all()}
    for item in PROFILES:
        row = existing.get(item["slug"])
        if row is None:
            row = SyntheticProfile(
                slug=item["slug"],
                name=item["name"],
                position=item["position"],
                options=item["options"],
            )
            db.add(row)
            existing[item["slug"]] = row
        else:
            row.name = item["name"]
            row.position = item["position"]
            row.options = item["options"]
    db.flush()
    return [existing[item["slug"]] for item in PROFILES]


def ensure_key(db: Session) -> str:
    row = db.get(RtnpSecret, 1)
    if row is None:
        row = RtnpSecret(id=1, key=secrets.token_hex(32))
        db.add(row)
        db.flush()
    directory = get_settings().rtnp_state_dir.strip()
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, "key")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        handle = os.open(path, flags, 0o600)
        try:
            os.write(handle, (row.key + "\n").encode("ascii"))
        finally:
            os.close(handle)
        from app.wireguard_tunnel import _give_to_receiver

        _give_to_receiver(Path(path))
    return row.key


def apply_options(profile: SyntheticProfile, override: Optional[dict[str, Any]]) -> dict[str, Any]:
    options = dict(profile.options)
    incoming = override or {}
    for key in _EDITABLE:
        if key not in incoming or incoming[key] is None:
            continue
        value = incoming[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        number = int(value) if key != "max_loss_pct" else float(value)
        _check_limit(key, number)
        options[key] = number
    return options


def _check_limit(key: str, value: float) -> None:
    bounds = {
        "report_interval_s": (1, 300),
        "max_rtt_ms": (1, 10000),
        "max_jitter_ms": (0, 1000),
        "max_loss_pct": (0, 100),
        "ptime_ms": (10, 200),
    }
    low, high = bounds[key]
    if value < low or value > high:
        raise ValueError(f"{key} must be between {low} and {high}")


def normalize_links(links: list[str]) -> list[str]:
    chosen = []
    for link in links:
        if link not in LINKS:
            raise ValueError("Choose Wired, Wi-Fi, or both")
        if link not in chosen:
            chosen.append(link)
    if not chosen:
        raise ValueError("Choose Wired, Wi-Fi, or both")
    return chosen


def metrics_from_interval(payload: dict[str, Any]) -> dict[str, Any]:
    flows = payload.get("flows") or []
    flow = flows[0] if flows else {}
    rx = flow.get("rx") or {}
    rtt = flow.get("rtt") or {}
    verdict = flow.get("verdict") or {}
    rtt_ms = _number(rtt.get("avg_ms"))
    if rtt_ms is None and not rx.get("delay_relative"):
        delay = _number(rx.get("delay_avg_ms"))
        if delay is not None and delay >= 0:
            rtt_ms = delay
    jitter = rtt.get("jitter_ms")
    if jitter is None:
        jitter = rx.get("jitter_rfc3550_ms", rx.get("jitter_ms"))
    loss = flow.get("loss_pct")
    if loss is None:
        loss = rx.get("loss_pct")
    passed = verdict.get("pass")
    label = None
    if passed is True:
        label = "pass"
    elif passed is False:
        label = "fail"
    if rtt_ms is None and jitter is None and loss is None:
        label = None
    return {
        "rtt_ms": rtt_ms,
        "jitter_ms": _number(jitter),
        "loss_pct": _number(loss),
        "verdict": label,
    }


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_report(run: SyntheticRun, samples: list[SyntheticSample], name: str) -> dict[str, Any]:
    """Rebuild the totals from stored intervals. This does not send traffic again."""
    limits = run.options or {}
    columns = []
    for link in run.links:
        rows = [sample for sample in samples if sample.link == link and not sample.sample_key.startswith(("hour:", "day:"))]
        if not rows:
            rows = [sample for sample in samples if sample.link == link]
        columns.append(_column(link, rows, limits))
    return {
        "run_id": str(run.id),
        "profile_slug": run.profile_slug,
        "name": name,
        "mode": run.mode,
        "status": run.status,
        "duration_seconds": run.duration_seconds,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "links": columns,
    }


def _column(link: str, rows: list[SyntheticSample], limits: dict[str, Any]) -> dict[str, Any]:
    rtts = [row.rtt_ms for row in rows if row.rtt_ms is not None]
    jitters = [row.jitter_ms for row in rows if row.jitter_ms is not None]
    losses = [row.loss_pct for row in rows if row.loss_pct is not None]
    rtt_avg = sum(rtts) / len(rtts) if rtts else None
    jitter = max(jitters) if jitters else None
    loss = max(losses) if losses else None
    return {
        "link": link,
        "label": LINK_LABELS[link],
        "intervals": len(rows),
        "rtt_min_ms": min(rtts) if rtts else None,
        "rtt_avg_ms": rtt_avg,
        "rtt_max_ms": max(rtts) if rtts else None,
        "jitter_ms": jitter,
        "loss_pct": loss,
        "verdict": _verdict(rtt_avg, jitter, loss, limits),
    }


def _verdict(
    rtt: Optional[float],
    jitter: Optional[float],
    loss: Optional[float],
    limits: dict[str, Any],
) -> str:
    if rtt is None and jitter is None and loss is None:
        return "unknown"
    if rtt is not None and rtt > float(limits.get("max_rtt_ms", 300)):
        return "fail"
    if jitter is not None and jitter > float(limits.get("max_jitter_ms", 30)):
        return "fail"
    if loss is not None and loss > float(limits.get("max_loss_pct", 1)):
        return "fail"
    return "pass"


def range_start(name: str, now: datetime) -> datetime:
    hours = {"1h": 1, "6h": 6, "12h": 12, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(name, 1)
    return now - timedelta(hours=hours)


def rollup_samples(
    db: Session,
    *,
    hourly_before: datetime,
    daily_before: datetime,
    delete_before: datetime,
) -> dict[str, int]:
    """Same windows as the other measurements: raw 6 months, hourly, daily, then delete."""
    deleted = db.execute(
        delete(SyntheticSample).where(SyntheticSample.recorded_at < delete_before)
    ).rowcount
    daily = _collapse(db, delete_before, daily_before, "day")
    hourly = _collapse(db, daily_before, hourly_before, "hour")
    return {"deleted_synthetic": deleted or 0, "daily_synthetic": daily, "hourly_synthetic": hourly}


def _collapse(db: Session, start: datetime, end: datetime, unit: str) -> int:
    written = 0
    cursor = start
    step = timedelta(days=7)
    while cursor < end:
        window_end = min(cursor + step, end)
        rows = db.scalars(
            select(SyntheticSample).where(
                SyntheticSample.recorded_at >= cursor,
                SyntheticSample.recorded_at < window_end,
                SyntheticSample.sample_key.not_like(f"{unit}:%"),
            )
        ).all()
        cursor = window_end
        if not rows:
            continue
        grouped: dict[tuple, list[SyntheticSample]] = {}
        for row in rows:
            stamp = row.recorded_at.astimezone(timezone.utc)
            if unit == "hour":
                bucket = stamp.replace(minute=0, second=0, microsecond=0)
            else:
                bucket = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
            grouped.setdefault((row.run_id, row.link, bucket), []).append(row)
        delete_ids = []
        for (run_id, link, bucket), members in grouped.items():
            if len(members) == 1 and members[0].sample_key.startswith(f"{unit}:"):
                continue
            key = f"{unit}:{bucket.isoformat()}"
            existing = db.scalar(
                select(SyntheticSample).where(
                    SyntheticSample.run_id == run_id,
                    SyntheticSample.link == link,
                    SyntheticSample.sample_key == key,
                )
            )
            averaged = _average(members)
            if existing is None:
                db.add(
                    SyntheticSample(
                        run_id=run_id,
                        link=link,
                        sample_key=key,
                        interval_index=None,
                        recorded_at=bucket,
                        metrics={"rolled_up": unit},
                        **averaged,
                    )
                )
            else:
                existing.rtt_ms = averaged["rtt_ms"]
                existing.jitter_ms = averaged["jitter_ms"]
                existing.loss_pct = averaged["loss_pct"]
                existing.verdict = averaged["verdict"]
            for member in members:
                if member.sample_key != key:
                    delete_ids.append(member.id)
            written += 1
        if delete_ids:
            db.execute(delete(SyntheticSample).where(SyntheticSample.id.in_(delete_ids)))
        db.flush()
    return written


def _average(rows: list[SyntheticSample]) -> dict[str, Any]:
    def mean(values: list[Optional[float]]) -> Optional[float]:
        numbers = [value for value in values if value is not None]
        if not numbers:
            return None
        return sum(numbers) / len(numbers)

    verdicts = [row.verdict for row in rows if row.verdict]
    verdict = "fail" if "fail" in verdicts else ("pass" if verdicts else None)
    return {
        "rtt_ms": mean([row.rtt_ms for row in rows]),
        "jitter_ms": mean([row.jitter_ms for row in rows]),
        "loss_pct": mean([row.loss_pct for row in rows]),
        "verdict": verdict,
    }
