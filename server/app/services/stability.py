"""Stable status for web services. Pure functions, no database access."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from app.models import ResultStatus

PRESET_FIELDS = (
    "window_size",
    "degraded_count",
    "fail_count",
    "recover_count",
    "pct_floor",
    "abs_floor_http_ms",
    "abs_floor_icmp_ms",
    "abs_floor_jitter_ms",
    "quorum_degraded_pct",
    "quorum_fail_pct",
)


@dataclass(frozen=True)
class StabilityConfig:
    window_size: int = 5
    degraded_count: int = 3
    fail_count: int = 3
    recover_count: int = 3
    pct_floor: int = 50
    abs_floor_http_ms: int = 40
    abs_floor_icmp_ms: int = 20
    abs_floor_jitter_ms: int = 15
    quorum_degraded_pct: int = 25
    quorum_fail_pct: int = 40
    poll_interval_seconds: int = 300


BALANCED = StabilityConfig()
RELAXED = StabilityConfig(
    window_size=5,
    degraded_count=4,
    fail_count=4,
    recover_count=4,
    pct_floor=75,
    abs_floor_http_ms=80,
    abs_floor_icmp_ms=40,
    abs_floor_jitter_ms=30,
    quorum_degraded_pct=40,
    quorum_fail_pct=60,
)
STRICT = StabilityConfig(
    window_size=3,
    degraded_count=2,
    fail_count=2,
    recover_count=2,
    pct_floor=30,
    abs_floor_http_ms=25,
    abs_floor_icmp_ms=12,
    abs_floor_jitter_ms=8,
    quorum_degraded_pct=15,
    quorum_fail_pct=25,
)
PRESETS = {"relaxed": RELAXED, "balanced": BALANCED, "strict": STRICT}


def preset_name(cfg: StabilityConfig) -> str:
    for name, preset in PRESETS.items():
        if all(getattr(cfg, field) == getattr(preset, field) for field in PRESET_FIELDS):
            return name
    return "custom"


def next_state(current: ResultStatus, recent: list[ResultStatus], cfg: StabilityConfig) -> ResultStatus:
    """Next stable status. `recent` is newest-first raw sample statuses."""
    if len(recent) < 3 and current == ResultStatus.ok:
        return ResultStatus.ok
    window = recent[: cfg.window_size]
    fails = sum(1 for status in window if status == ResultStatus.fail)
    bad = sum(1 for status in window if status in (ResultStatus.degraded, ResultStatus.fail))
    if len(window) >= 3 and fails >= cfg.fail_count:
        return ResultStatus.fail
    if len(window) >= 3 and bad >= cfg.degraded_count:
        return ResultStatus.degraded
    streak = 0
    for status in window:
        if status != ResultStatus.ok:
            break
        streak += 1
    if streak >= cfg.recover_count:
        return ResultStatus.ok
    # Also recovered: the newest result is good, nothing in the window failed, and the window holds as many
    # good results as recovery asks for. One slow result among good ones would not have moved a healthy check
    # (that needs `degraded_count`), so it must not restart the count for one that is recovering either. After
    # a long outage one slow reading used to hold a check at "fail" for another 20 minutes.
    oks = sum(1 for status in window if status == ResultStatus.ok)
    if window and window[0] == ResultStatus.ok and fails == 0 and oks >= cfg.recover_count:
        return ResultStatus.ok
    return current


def latency_limit(baseline: float, pct_floor: int, abs_floor_ms: float) -> float:
    """Slowest time that still counts as normal. Never tighter than the absolute floor."""
    return max(baseline * (1 + pct_floor / 100), baseline + abs_floor_ms)


def cell_quorum(
    statuses: list[ResultStatus], cfg: StabilityConfig, sustained_fail: bool = False
) -> tuple[str, int, int]:
    """Group color from each service's stable status. Returns status, bad count, total."""
    total = len(statuses)
    if total == 0:
        return "unknown", 0, 0
    fails = sum(1 for status in statuses if status == ResultStatus.fail)
    bad = sum(1 for status in statuses if status in (ResultStatus.degraded, ResultStatus.fail))
    fail_need = max(2, ceil(total * cfg.quorum_fail_pct / 100))
    if fails == total or fails >= fail_need:
        return "fail", bad, total
    degraded_need = max(1, ceil(total * cfg.quorum_degraded_pct / 100))
    if bad >= degraded_need or sustained_fail:
        return "degraded", bad, total
    return "ok", bad, total


def service_status(http_status: ResultStatus | None, icmp_status: ResultStatus | None) -> ResultStatus | None:
    """HTTP decides whether the service is up. ICMP alone cannot mark it failed."""
    if http_status is not None:
        return http_status
    if icmp_status is None:
        return None
    if icmp_status == ResultStatus.fail:
        return ResultStatus.degraded
    return icmp_status
