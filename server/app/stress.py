"""Stress tests: the catalog of traffic sessions, their limits, and the live numbers.

A stress test is a group of sessions that run on the physical link (or links)
for a few minutes. Each session is one RTN PackGen flow, stored as a
SyntheticRun with a stress_id. Adding or removing a session while the test runs
just creates or stops that row; the agent starts and stops the flow within a
few seconds.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StressRun, SyntheticRun, SyntheticSample

MAX_SECONDS = 300
MAX_SESSIONS = 60
MAX_COUNT = 20
REPORT_INTERVAL_S = 1
LINKS = ("wired", "wireless")

DSCP = [
    (0, "Best effort"),
    (8, "CS1 (background)"),
    (18, "AF21"),
    (26, "AF31"),
    (34, "AF41 (video)"),
    (46, "EF (voice)"),
]
_DSCP_FIELD = {"key": "dscp", "label": "Priority marking (DSCP)", "type": "choice", "default": 0, "choices": [{"value": v, "label": l} for v, l in DSCP]}

CODECS = {
    "g711": ("Voice G.711 µ-law", "voip-g711"),
    "g711a": ("Voice G.711 A-law", "voip-g711a"),
    "g729": ("Voice G.729", "voip-g729"),
    "g723": ("Voice G.723.1", "voip-g723"),
}

TESTS: list[dict[str, Any]] = [
    {
        "kind": "voice",
        "name": "Voice call",
        "description": "A real-time RTP voice stream, optionally two-way. Measures round trip, jitter and loss.",
        "fields": [
            {"key": "codec", "label": "Codec", "type": "choice", "default": "g711a", "choices": [{"value": k, "label": v[0].replace("Voice ", "")} for k, v in CODECS.items()]},
            {"key": "ptime_ms", "label": "Packet time", "type": "number", "default": 20, "min": 10, "max": 200, "step": 10, "unit": "ms"},
            {"key": "bidir", "label": "Two-way call", "type": "bool", "default": True},
            {"key": "vad", "label": "Talk and silence (one-way only)", "type": "bool", "default": False},
            _DSCP_FIELD,
        ],
    },
    {
        "kind": "video",
        "name": "Video conference",
        "description": "An RTP video stream with a keyframe and smaller frames between, about 1 Mbit/s by default.",
        "fields": [
            {"key": "fps", "label": "Frames per second", "type": "number", "default": 30, "min": 10, "max": 60, "unit": "fps"},
            {"key": "gop", "label": "Frames per keyframe", "type": "number", "default": 30, "min": 10, "max": 120},
            {"key": "i_kb", "label": "Keyframe size", "type": "number", "default": 30, "min": 5, "max": 200, "unit": "KB"},
            {"key": "p_kb", "label": "Other frame size", "type": "number", "default": 4, "min": 1, "max": 60, "unit": "KB"},
            _DSCP_FIELD,
        ],
    },
    {
        "kind": "desktop",
        "name": "Remote desktop",
        "description": "Bursty small packets with long gaps between bursts, like a VDI session.",
        "fields": [_DSCP_FIELD],
    },
    {
        "kind": "market",
        "name": "Market data",
        "description": "Many small packets with microbursts, like a unicast price feed.",
        "fields": [_DSCP_FIELD],
    },
    {
        "kind": "rtt",
        "name": "Round-trip probe",
        "description": "Small packets the far end reflects back, timed on the sender. Shows latency under load.",
        "fields": [
            {"key": "proto", "label": "Protocol", "type": "choice", "default": "udp", "choices": [{"value": "udp", "label": "UDP"}, {"value": "icmp", "label": "ICMP ping"}]},
            {"key": "pps", "label": "Packets per second", "type": "number", "default": 5, "min": 1, "max": 1000, "unit": "pps"},
            {"key": "size", "label": "Packet size", "type": "number", "default": 100, "min": 64, "max": 1400, "unit": "B"},
        ],
    },
    {
        "kind": "flood",
        "name": "Throughput flood",
        "description": "A steady UDP stream at the rate you choose, to fill the link. Shows loss and jitter as it saturates.",
        "fields": [
            {"key": "mbps", "label": "Rate", "type": "number", "default": 10, "min": 1, "max": 1000, "unit": "Mbit/s"},
            {"key": "size", "label": "Packet size", "type": "number", "default": 1200, "min": 200, "max": 1472, "unit": "B"},
            _DSCP_FIELD,
        ],
    },
]
_BY_KIND = {item["kind"]: item for item in TESTS}


def _field(test: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in test["fields"] if item["key"] == key)


def _clean(test: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Check each value against its field and fill in defaults. Unknown keys are refused."""
    known = {item["key"] for item in test["fields"]}
    extra = set(params) - known
    if extra:
        raise ValueError(f"Unknown option: {sorted(extra)[0]}")
    clean: dict[str, Any] = {}
    for field in test["fields"]:
        value = params.get(field["key"], field["default"])
        if field["type"] == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{field['label']} must be on or off")
        elif field["type"] == "choice":
            allowed = [item["value"] for item in field["choices"]]
            if value not in allowed:
                raise ValueError(f"{field['label']} is not one of the choices")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field['label']} must be a number")
            if value < field["min"] or value > field["max"]:
                raise ValueError(f"{field['label']} must be between {field['min']} and {field['max']}")
            value = int(value)
        clean[field["key"]] = value
    return clean


def build_session(kind: str, params: Optional[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """The options the agent runs and a short label for the session list."""
    test = _BY_KIND.get(kind)
    if test is None:
        raise ValueError("Unknown test")
    p = _clean(test, params or {})
    options: dict[str, Any] = {"stress": True, "kind": kind, "params": p, "report_interval_s": REPORT_INTERVAL_S}
    if "dscp" in p:
        options["tos"] = int(p["dscp"]) << 2
    if kind == "voice":
        codec = p["codec"]
        if p["bidir"] and p["vad"]:
            raise ValueError("Talk and silence is for one-way calls only")
        ptime = 30 if codec == "g723" else p["ptime_ms"]
        if codec == "g729" and ptime % 10:
            raise ValueError("G.729 packet time must be a multiple of 10 ms")
        options.update(preset=CODECS[codec][1], wire="rtp", rtp=codec, ptime_ms=ptime, bidir=p["bidir"], vad=p["vad"])
        label = f"{CODECS[codec][0]}, {ptime} ms{', two-way' if p['bidir'] else ''}"
    elif kind == "video":
        options.update(
            preset="video-conference",
            wire="rtp",
            video=True,
            video_fps=p["fps"],
            video_gop=p["gop"],
            video_i_size=f"normal:{p['i_kb'] * 1000},{p['i_kb'] * 100}",
            video_p_size=f"normal:{p['p_kb'] * 1000},{max(1, p['p_kb'] * 300)}",
        )
        label = f"Video {p['fps']} fps"
    elif kind == "desktop":
        options["preset"] = "remote-desktop"
        label = "Remote desktop"
    elif kind == "market":
        options["preset"] = "market-data"
        label = "Market data"
    elif kind == "rtt":
        options.update(proto=p["proto"], meter="rtt", rate_pps=p["pps"], size=p["size"])
        label = f"{'Ping' if p['proto'] == 'icmp' else 'UDP round trip'}, {p['pps']}/s"
    else:
        size = p["size"]
        pps = max(1, math.ceil(p["mbps"] * 1_000_000 / 8 / size))
        options.update(proto="udp", rate_pps=pps, size=size)
        label = f"Flood {p['mbps']} Mbit/s"
    return options, label


def catalog() -> dict[str, Any]:
    return {"tests": TESTS, "max_seconds": MAX_SECONDS, "max_sessions": MAX_SESSIONS, "max_count": MAX_COUNT}


def now() -> datetime:
    return datetime.now(timezone.utc)


def end_time(started: datetime, seconds: int) -> datetime:
    return started + timedelta(seconds=seconds)


def sessions_of(db: Session, stress: StressRun) -> list[SyntheticRun]:
    return list(
        db.scalars(select(SyntheticRun).where(SyntheticRun.stress_id == stress.id).order_by(SyntheticRun.started_at, SyntheticRun.id)).all()
    )


def stop_session(run: SyntheticRun, status: str = "stopped") -> None:
    if run.status in ("running", "pending"):
        run.status = status
        run.finished_at = now()


def expire(db: Session, stress: StressRun) -> None:
    """The five-minute cap. Called on every read so a stale test cannot stay open."""
    if stress.status != "running" or now() < stress.ends_at:
        return
    stress.status = "finished"
    stress.finished_at = now()
    for run in sessions_of(db, stress):
        stop_session(run)
    db.flush()


def stop_all(db: Session, stress: StressRun) -> None:
    if stress.status == "running":
        stress.status = "stopped"
        stress.finished_at = now()
    for run in sessions_of(db, stress):
        stop_session(run)
    db.flush()


def _flow(sample: SyntheticSample) -> dict[str, Any]:
    flows = (sample.metrics or {}).get("flows") or []
    return flows[0] if flows else {}


def _mbps(value: Any) -> float:
    return float(value) / 1000 if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def live(db: Session, stress: StressRun, since: Optional[datetime] = None) -> dict[str, Any]:
    """Session table, and the combined series per link since a time."""
    runs = sessions_of(db, stress)
    ids = [run.id for run in runs]
    query = select(SyntheticSample).where(SyntheticSample.run_id.in_(ids)) if ids else None
    samples: list[SyntheticSample] = []
    if query is not None:
        if since is not None:
            query = query.where(SyntheticSample.recorded_at > since)
        samples = list(db.scalars(query.order_by(SyntheticSample.recorded_at)).all())

    latest: dict[tuple[Any, str], SyntheticSample] = {}
    if ids:
        newest = db.scalars(
            select(SyntheticSample).where(SyntheticSample.run_id.in_(ids)).order_by(SyntheticSample.recorded_at.desc()).limit(len(ids) * 4)
        ).all()
        for row in newest:
            latest.setdefault((row.run_id, row.link), row)

    buckets: dict[tuple[str, int], list[SyntheticSample]] = defaultdict(list)
    for row in samples:
        buckets[(row.link, int(row.recorded_at.timestamp()))].append(row)
    series = []
    for (link, second), rows in sorted(buckets.items(), key=lambda item: item[0][1]):
        flows = [_flow(row) for row in rows]
        series.append(
            {
                "t": datetime.fromtimestamp(second, tz=timezone.utc),
                "link": link,
                "tx_mbps": sum(_mbps(flow.get("tx_kbps")) for flow in flows),
                "rx_mbps": sum(_mbps((flow.get("rx") or {}).get("bitrate_kbps")) for flow in flows),
                "rtt_ms": _mean([row.rtt_ms for row in rows if row.rtt_ms is not None]),
                "rtt_max_ms": max((row.rtt_ms for row in rows if row.rtt_ms is not None), default=None),
                "jitter_ms": _mean([row.jitter_ms for row in rows if row.jitter_ms is not None]),
                "loss_pct": max((row.loss_pct for row in rows if row.loss_pct is not None), default=None),
                "sessions": len(rows),
            }
        )

    out_sessions = []
    for run in runs:
        per_link = {}
        for link in run.links:
            row = latest.get((run.id, link))
            if row is None:
                continue
            flow = _flow(row)
            per_link[link] = {
                "tx_mbps": _mbps(flow.get("tx_kbps")),
                "rtt_ms": row.rtt_ms,
                "jitter_ms": row.jitter_ms,
                "loss_pct": row.loss_pct,
                "verdict": row.verdict,
            }
        options = run.options or {}
        out_sessions.append(
            {
                "id": run.id,
                "kind": options.get("kind"),
                "label": options.get("label") or run.profile_slug,
                "status": run.status,
                "error": options.get("error") or None,
                "links": per_link,
            }
        )
    return {
        "id": stress.id,
        "monitor_id": stress.monitor_id,
        "links": stress.links,
        "status": stress.status,
        "started_at": stress.started_at,
        "ends_at": stress.ends_at,
        "finished_at": stress.finished_at,
        "now": now(),
        "sessions": out_sessions,
        "series": series,
    }
