"""KPI reporting: availability, packet loss and application health over a day, week, month or year.

Raw measurements are averaged after six months, which would blur a long report. So every hour the
exact counts (checks run, checks failed, loss, latency) are written to `kpi_hourly`, and the report
adds those up. Counts and sums are kept, never averages, so any period combines without bias.

Internet availability is the share of path pings that did not fail on the wired and Wi-Fi links, except
when a path fail coincides with a healthy public web check on the same link — that is a dead measurement
tunnel (``10.7.x.1``), not home internet down, and is not counted as an outage. Voice and video
availability is the share of their checks (every few seconds) that passed the limits set for them.
Packet drop is the average packet loss those checks reported.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Date, DateTime, case, cast, delete, func, literal, select
from sqlalchemy.orm import Session, aliased

from app.internet_tests import WEB_SERVICES
from app.models import KpiDaily, KpiHourly, Monitor, Network, NetworkKind, ResultStatus, SyntheticRun, SyntheticSample, Test, TestKind, TestResult
from app.synthetic import SOFTWARE, is_blackout

log = logging.getLogger(__name__)

TARGETS = {"internet": 99.95, "voice": 99.9, "video": 99.9, "web": 99.9, "loss_pct": 0.1}
KINDS = ("internet", "voice", "video", "web", "apps", "speed")
WEB_SLUGS = frozenset(
    [*(f"http-{key}" for key, _, _, _ in WEB_SERVICES), *(f"icmp-{key}" for key, _, _, _ in WEB_SERVICES)]
)
WEB_HTTP_SLUGS = frozenset(f"http-{key}" for key, _, _, _ in WEB_SERVICES)

# The daily health score: how much each service counts, and where a result is worth 0 points.
# At or better than its target a service scores 100; at its zero point or worse, 0; in between, in proportion.
SCORE_WEIGHTS = {"internet": 60, "voice": 20, "video": 10, "web": 10, "loss": 10}
SCORE_ZERO = {"internet": 99.0, "voice": 98.0, "video": 98.0, "web": 98.0}  # availability (%) that scores 0
SCORE_ZERO_LOSS = 1.0  # packet drop (%) that scores 0
SCORE_GREEN = 95.0
SCORE_AMBER = 80.0


def _scaled(value: Optional[float], good: float, zero: float) -> Optional[float]:
    """0 to 100: 100 at `good` or better, 0 at `zero` or worse. Works whichever way round the two are."""
    if value is None:
        return None
    if (good >= zero and value >= good) or (good < zero and value <= good):
        return 100.0
    if (good >= zero and value <= zero) or (good < zero and value >= zero):
        return 0.0
    return 100.0 * (value - zero) / (good - zero)


def health_score(
    internet: Optional[float],
    voice: Optional[float],
    video: Optional[float],
    loss: Optional[float],
    targets: Optional[dict[str, float]] = None,
    web: Optional[float] = None,
    needs_internet: bool = True,
) -> dict[str, Any]:
    """One 0 to 100 score for a day: internet 60, voice 20, video 10, packet drop 10 (plus web when present).

    A service with no data that day is left out and the others share its weight. With no internet figure
    there is no score, because internet is most of it. An agent that never runs the internet checks (an
    Android agent) passes needs_internet=False and is scored on what it does run.
    """
    goals = targets or TARGETS
    parts = {
        "internet": _scaled(internet, goals["internet"], SCORE_ZERO["internet"]),
        "voice": _scaled(voice, goals["voice"], SCORE_ZERO["voice"]),
        "video": _scaled(video, goals["video"], SCORE_ZERO["video"]),
        "web": _scaled(web, goals.get("web", 99.9), SCORE_ZERO["web"]),
        "loss": _scaled(loss, goals["loss_pct"], SCORE_ZERO_LOSS),
    }
    if parts["internet"] is None and needs_internet:
        return {"score": None, "tone": "none", "parts": {}}
    used = {name: value for name, value in parts.items() if value is not None}
    if not used:
        return {"score": None, "tone": "none", "parts": {}}
    weight = sum(SCORE_WEIGHTS[name] for name in used)
    score = sum(SCORE_WEIGHTS[name] * value for name, value in used.items()) / weight
    tone = "good" if score >= SCORE_GREEN else "warn" if score >= SCORE_AMBER else "bad"
    return {"score": round(score, 1), "tone": tone, "parts": {name: round(value) for name, value in used.items()}}
KEEP_YEARS = 5
PERIODS = {
    # name: (bucket unit, number of buckets)
    "day": ("hour", 24),
    "week": ("day", 7),
    "month": ("day", 30),
    "year": ("month", 12),
}
OUTAGE_GAP = timedelta(minutes=3)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hour_of(value: datetime) -> datetime:
    return _utc(value).replace(minute=0, second=0, microsecond=0)


def _link(value: str) -> str:
    return "wireless" if value in ("wifi", "wireless") else "wired"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass
class Acc:
    n: int = 0
    failed: int = 0
    degraded: int = 0
    incidents: int = 0
    loss_sum: float = 0.0
    loss_n: int = 0
    loss_max: Optional[float] = None
    rtt_sum: float = 0.0
    rtt_n: int = 0
    jitter_sum: float = 0.0
    jitter_n: int = 0
    down_sum: float = 0.0
    down_n: int = 0
    up_sum: float = 0.0
    up_n: int = 0
    gap_min: float = 0.0
    gaps: int = 0

    def add_metrics(self, rtt: Optional[float], jitter: Optional[float], loss: Optional[float]) -> None:
        if rtt is not None:
            self.rtt_sum += rtt
            self.rtt_n += 1
        if jitter is not None:
            self.jitter_sum += jitter
            self.jitter_n += 1
        if loss is not None:
            self.loss_sum += loss
            self.loss_n += 1
            self.loss_max = loss if self.loss_max is None else max(self.loss_max, loss)


Key = tuple[datetime, UUID, str, str]


def _nearby(times: list[datetime], moment: datetime, window: timedelta = OUTAGE_GAP) -> bool:
    """Whether a sorted list of timestamps has one within `window` of `moment`. Binary search, not a scan."""
    index = bisect_left(times, moment - window)
    return index < len(times) and times[index] <= moment + window


def _public_web_ok_times(db: Session, start: datetime, end: datetime) -> dict[tuple[UUID, str], list[datetime]]:
    """Successful public HTTP checks (not http-path) per monitor/link, sorted — proves home internet access.

    Meant for one rebuild chunk (a day). For report-sized windows use `_not_tunnel_only` in SQL instead.
    """
    out: dict[tuple[UUID, str], list[datetime]] = defaultdict(list)
    for started, monitor_id, kind in db.execute(
        select(TestResult.started_at, Test.monitor_id, Network.kind)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .where(
            Test.slug.in_(list(WEB_HTTP_SLUGS)),
            Test.enabled.is_(True),
            TestResult.status == ResultStatus.ok,
            TestResult.started_at >= start - OUTAGE_GAP,
            TestResult.started_at < end + OUTAGE_GAP,
            ~TestResult.external_id.startswith("rollup-"),
        )
    ).all():
        out[(monitor_id, _link(kind.value))].append(started)
    for times in out.values():
        times.sort()
    return out


def _not_tunnel_only():
    """SQL filter for a path TestResult row: no public web check on the same monitor and link succeeded
    within OUTAGE_GAP. Correlated subquery, so a report over months never loads web rows into Python."""
    web_result = aliased(TestResult)
    web_test = aliased(Test)
    web_network = aliased(Network)
    nearby_web_ok = (
        select(literal(1))
        .select_from(web_result)
        .join(web_test, web_result.test_id == web_test.id)
        .join(web_network, web_test.network_id == web_network.id)
        .where(
            web_test.monitor_id == Test.monitor_id,
            web_network.kind == Network.kind,
            web_test.slug.in_(list(WEB_HTTP_SLUGS)),
            web_test.enabled.is_(True),
            web_result.status == ResultStatus.ok,
            web_result.started_at >= TestResult.started_at - OUTAGE_GAP,
            web_result.started_at <= TestResult.started_at + OUTAGE_GAP,
            ~web_result.external_id.startswith("rollup-"),
        )
    )
    return ~nearby_web_ok.exists()


def _public_web_near(db: Session, monitor_id: UUID, link: str, moment: datetime) -> bool:
    """Whether a public HTTP check on this link succeeded within OUTAGE_GAP of `moment`."""
    kind = NetworkKind.wifi if link == "wireless" else NetworkKind.wired
    return (
        db.scalar(
            select(TestResult.id)
            .join(Test, TestResult.test_id == Test.id)
            .join(Network, Test.network_id == Network.id)
            .where(
                Test.monitor_id == monitor_id,
                Network.kind == kind,
                Test.slug.in_(list(WEB_HTTP_SLUGS)),
                Test.enabled.is_(True),
                TestResult.status == ResultStatus.ok,
                TestResult.started_at >= moment - OUTAGE_GAP,
                TestResult.started_at <= moment + OUTAGE_GAP,
                ~TestResult.external_id.startswith("rollup-"),
            )
            .limit(1)
        )
        is not None
    )


def compute_hours(db: Session, start: datetime, end: datetime) -> dict[Key, Acc]:
    """Counts for every hour in [start, end) from the raw measurements."""
    start, end = hour_of(start), hour_of(end)
    out: dict[Key, Acc] = defaultdict(Acc)

    # Internet: icmp-path is aimed at the WireGuard peer (10.7.x.1). A fail while public web on the
    # same link still succeeds is tunnel death, not an ISP/home-internet outage.
    public_web_ok = _public_web_ok_times(db, start, end)
    path_tests = {
        row[0]: (row[1], _link(row[2].value))
        for row in db.execute(
            select(Test.id, Test.monitor_id, Network.kind).join(Network, Test.network_id == Network.id).where(Test.slug == "icmp-path")
        ).all()
    }
    path_rows: list = []
    if path_tests:
        previous_failed: dict[UUID, bool] = {}
        previous_at: dict[UUID, datetime] = {}
        for test_id in path_tests:
            last = db.execute(
                select(TestResult.status, TestResult.started_at)
                .where(TestResult.test_id == test_id, TestResult.started_at < start, ~TestResult.external_id.startswith("rollup-"))
                .order_by(TestResult.started_at.desc())
                .limit(1)
            ).first()
            # Seed prior state only from failures that counted as internet down (not tunnel-only).
            prior_down = False
            if last is not None and last[0].value == "fail":
                mon_id, lnk = path_tests[test_id]
                if _nearby(public_web_ok.get((mon_id, lnk), []), last[1]):
                    prior_down = False
                else:
                    prior_down = not _public_web_near(db, mon_id, lnk, last[1])
            previous_failed[test_id] = prior_down
            if last is not None:
                previous_at[test_id] = last[1]
        path_rows = list(
            db.execute(
                select(TestResult.test_id, TestResult.started_at, TestResult.status, TestResult.metrics)
                .where(
                    TestResult.test_id.in_(list(path_tests)),
                    TestResult.started_at >= start,
                    TestResult.started_at < end,
                    ~TestResult.external_id.startswith("rollup-"),
                )
                .order_by(TestResult.test_id, TestResult.started_at)
            ).all()
        )
        for test_id, started, status, metrics in path_rows:
            monitor_id, link = path_tests[test_id]
            acc = out[(hour_of(started), monitor_id, link, "internet")]
            acc.n += 1
            before = previous_at.get(test_id)
            if before is not None and started - before > OUTAGE_GAP:
                # No result for a while: the Pi was off, or the server was not there to take them.
                acc.gaps += 1
                acc.gap_min += (started - before).total_seconds() / 60 - 1
            previous_at[test_id] = started
            raw_fail = status.value == "fail"
            tunnel_only = raw_fail and _nearby(public_web_ok.get((monitor_id, link), []), started)
            down = raw_fail and not tunnel_only
            if down:
                acc.failed += 1
                if not previous_failed[test_id]:
                    acc.incidents += 1
            elif status.value == "degraded" and not tunnel_only:
                acc.degraded += 1
            previous_failed[test_id] = down
            if not tunnel_only:
                data = metrics or {}
                acc.add_metrics(_number(data.get("rtt_ms")), _number(data.get("jitter_ms")), _number(data.get("loss_pct")))

    # Speed: the bandwidth test.
    speed_rows = db.execute(
        select(TestResult.started_at, TestResult.status, TestResult.metrics, Test.monitor_id, Network.kind)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .where(Test.kind == TestKind.librespeed, TestResult.started_at >= start, TestResult.started_at < end, ~TestResult.external_id.startswith("rollup-"))
    ).all()
    for started, status, metrics, monitor_id, kind in speed_rows:
        if status.value == "fail":
            continue
        acc = out[(hour_of(started), monitor_id, _link(kind.value), "speed")]
        acc.n += 1
        data = metrics or {}
        down, up = _number(data.get("down_mbps")), _number(data.get("up_mbps"))
        if down is not None:
            acc.down_sum += down
            acc.down_n += 1
        if up is not None:
            acc.up_sum += up
            acc.up_n += 1

    # Web applications: SaaS HTTP and ICMP checks on each link.
    web_rows = db.execute(
        select(TestResult.started_at, TestResult.status, TestResult.metrics, Test.slug, Test.monitor_id, Network.kind)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .where(
            Test.slug.in_(list(WEB_SLUGS)),
            Test.enabled.is_(True),
            TestResult.started_at >= start,
            TestResult.started_at < end,
            ~TestResult.external_id.startswith("rollup-"),
        )
    ).all()
    healthy_link_times: dict[tuple[UUID, str], list[datetime]] = defaultdict(list)
    if path_tests:
        for test_id, started, status, _metrics in path_rows:
            if status.value == "ok":
                mon_id, lnk = path_tests[test_id]
                healthy_link_times[(mon_id, lnk)].append(started)
    for started, status, metrics, slug, monitor_id, kind in web_rows:
        link = _link(kind.value)
        if status.value == "ok":
            healthy_link_times[(monitor_id, link)].append(started)
        acc = out[(hour_of(started), monitor_id, link, "web")]
        data = metrics or {}
        if slug in WEB_HTTP_SLUGS:
            acc.n += 1
            if status.value == "fail":
                acc.failed += 1
            elif status.value == "degraded":
                acc.degraded += 1
            acc.add_metrics(_number(data.get("rtt_ms")), None, None)
        else:
            acc.add_metrics(_number(data.get("rtt_ms")), _number(data.get("jitter_ms")), _number(data.get("loss_pct")))

    # Applications: the checks that run all the time. Manual and stress runs are left out.
    sample_rows = db.execute(
        select(SyntheticSample, SyntheticRun.profile_slug, SyntheticRun.monitor_id)
        .join(SyntheticRun, SyntheticSample.run_id == SyntheticRun.id)
        .where(
            SyntheticRun.mode == "continuous",
            SyntheticSample.recorded_at >= start,
            SyntheticSample.recorded_at < end,
            ~SyntheticSample.sample_key.startswith("hour:"),
            ~SyntheticSample.sample_key.startswith("day:"),
        )
    ).all()
    rows = list(sample_rows)
    healthy: dict[tuple[UUID, str], list[tuple[datetime, str]]] = defaultdict(list)
    for sample, slug, monitor_id in rows:
        if sample.verdict == SOFTWARE or is_blackout(sample.rtt_ms, sample.jitter_ms, sample.loss_pct):
            continue
        healthy[(monitor_id, sample.link)].append((sample.recorded_at, slug))
    window = timedelta(minutes=3)
    for sample, slug, monitor_id in rows:
        # A total blackout while another check on the link is fine is a monitor problem, not the service.
        if sample.verdict == SOFTWARE or _software_blackout(sample, slug, monitor_id, healthy, healthy_link_times, window):
            continue
        kind = "voice" if slug.startswith("voip-") else "video" if slug == "video-conference" else "apps"
        acc = out[(hour_of(sample.recorded_at), monitor_id, sample.link, kind)]
        if sample.verdict in ("pass", "fail"):
            acc.n += 1
            if sample.verdict == "fail":
                acc.failed += 1
        acc.add_metrics(sample.rtt_ms, sample.jitter_ms, sample.loss_pct)
    return out


def _software_blackout(
    sample: SyntheticSample,
    slug: str,
    monitor_id: UUID,
    healthy: dict,
    healthy_link_times: dict[tuple[UUID, str], list[datetime]],
    window: timedelta,
) -> bool:
    if not is_blackout(sample.rtt_ms, sample.jitter_ms, sample.loss_pct):
        return False
    moment = sample.recorded_at
    for recorded, other in healthy.get((monitor_id, sample.link), ()):
        if other != slug and abs(recorded - moment) <= window:
            return True
    for recorded in healthy_link_times.get((monitor_id, sample.link), ()):
        if abs(recorded - moment) <= window:
            return True
    return False


_SUM_FIELDS = (
    "n",
    "failed",
    "degraded",
    "incidents",
    "loss_sum",
    "loss_n",
    "rtt_sum",
    "rtt_n",
    "jitter_sum",
    "jitter_n",
    "down_sum",
    "down_n",
    "up_sum",
    "up_n",
    "gap_min",
    "gaps",
    "outage_min",
)


def _store(db: Session, start: datetime, end: datetime, counts: dict[Key, Acc]) -> None:
    db.execute(delete(KpiHourly).where(KpiHourly.bucket_start >= start, KpiHourly.bucket_start < end))
    for (bucket, monitor_id, link, kind), acc in counts.items():
        outage = acc.failed * 60.0 / acc.n if kind == "internet" and acc.n else 0.0
        db.add(KpiHourly(bucket_start=bucket, monitor_id=monitor_id, link=link, kind=kind, outage_min=outage, **acc.__dict__))
    db.flush()


def rebuild_days(db: Session, first: date, last: date) -> int:
    """Write the daily record for each UTC day from `first` to `last`, added up from the hourly rows."""
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    end = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
    db.execute(delete(KpiDaily).where(KpiDaily.day >= first, KpiDaily.day <= last))
    day = cast(func.timezone("UTC", KpiHourly.bucket_start), Date)
    sums = [func.sum(getattr(KpiHourly, name)).label(name) for name in _SUM_FIELDS]
    rows = db.execute(
        select(day.label("day"), KpiHourly.monitor_id, KpiHourly.link, KpiHourly.kind, *sums, func.max(KpiHourly.loss_max).label("loss_max"))
        .where(KpiHourly.bucket_start >= start, KpiHourly.bucket_start < end)
        .group_by(day, KpiHourly.monitor_id, KpiHourly.link, KpiHourly.kind)
    ).mappings().all()
    for row in rows:
        db.add(KpiDaily(**{key: row[key] for key in row.keys()}))
    db.flush()
    return len(rows)


def rebuild(db: Session, start: datetime, end: datetime) -> int:
    """Recompute the hours in [start, end). Safe to repeat: the old rows for those hours are replaced."""
    start, end = hour_of(start), hour_of(end)
    counts = compute_hours(db, start, end)
    _store(db, start, end, counts)
    return len(counts)


def refresh(db: Session, now: Optional[datetime] = None) -> int:
    """The last few hours, so a late-arriving result is still counted, and the daily records they belong to."""
    now = now or datetime.now(timezone.utc)
    start = hour_of(now) - timedelta(hours=3)
    rows = rebuild(db, start, hour_of(now) + timedelta(hours=1))
    rebuild_days(db, start.date(), _utc(now).date())
    return rows


def backfill(db: Session, now: Optional[datetime] = None) -> int:
    """Fill in every hour that has raw data but no KPI row yet, a day at a time, then every daily record."""
    now = now or datetime.now(timezone.utc)
    total = _backfill_hours(db, now)
    first_hour = db.scalar(select(func.min(KpiHourly.bucket_start)))
    if first_hour is not None:
        rebuild_days(db, _utc(first_hour).date(), _utc(now).date())
        db.commit()
    return total


def _backfill_hours(db: Session, now: datetime) -> int:
    first_raw = [
        db.scalar(select(func.min(TestResult.started_at)).join(Test, TestResult.test_id == Test.id).where(Test.slug == "icmp-path")),
        db.scalar(select(func.min(SyntheticSample.recorded_at))),
    ]
    first_raw = [value for value in first_raw if value is not None]
    if not first_raw:
        return 0
    first_kpi = db.scalar(select(func.min(KpiHourly.bucket_start)))
    begin = hour_of(min(first_raw))
    stop = hour_of(first_kpi) if first_kpi is not None and first_kpi > begin + timedelta(hours=1) else None
    if first_kpi is not None and stop is None:
        return 0  # already covered from the first measurement
    stop = stop or hour_of(now) + timedelta(hours=1)
    total = 0
    cursor = begin
    while cursor < stop:
        chunk_end = min(cursor + timedelta(days=1), stop)
        total += rebuild(db, cursor, chunk_end)
        db.commit()
        cursor = chunk_end
    return total


def prune(db: Session, now: datetime) -> int:
    cutoff = now - timedelta(days=365 * KEEP_YEARS)
    hours = db.execute(delete(KpiHourly).where(KpiHourly.bucket_start < cutoff)).rowcount or 0
    days = db.execute(delete(KpiDaily).where(KpiDaily.day < cutoff.date())).rowcount or 0
    return hours + days


def run_once() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        refresh(db)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("kpi refresh failed")
    finally:
        db.close()


def run_backfill() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        rows = backfill(db)
        if rows:
            log.info("kpi backfill wrote %s hourly rows", rows)
    except Exception:
        db.rollback()
        log.exception("kpi backfill failed")
    finally:
        db.close()


# ---- the report

def _floor(value: datetime, unit: str) -> datetime:
    value = _utc(value)
    if unit == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    if unit == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add(value: datetime, unit: str, count: int) -> datetime:
    if unit == "hour":
        return value + timedelta(hours=count)
    if unit == "day":
        return value + timedelta(days=count)
    month = value.month - 1 + count
    return value.replace(year=value.year + month // 12, month=month % 12 + 1)


def window(period: str, now: datetime, anchor: Optional[date] = None) -> tuple[datetime, datetime, str]:
    """The buckets a report covers. Without a day it ends now. With a day it ends on that day (UTC)."""
    unit, count = PERIODS[period]
    if anchor is None:
        last = _floor(now, unit)
        return _add(last, unit, -(count - 1)), _add(last, unit, 1), unit
    midnight = datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc)
    if period == "day":
        return midnight, midnight + timedelta(days=1), "hour"
    last = _floor(midnight, unit)
    return _add(last, unit, -(count - 1)), _add(last, unit, 1), unit


def _availability(n: int, failed: int) -> Optional[float]:
    return None if n <= 0 else 100.0 * (1.0 - failed / n)


def _mean(total: float, count: int) -> Optional[float]:
    return None if count <= 0 else total / count


_FIELDS = (
    "n",
    "failed",
    "degraded",
    "incidents",
    "loss_sum",
    "loss_n",
    "rtt_sum",
    "rtt_n",
    "jitter_sum",
    "jitter_n",
    "down_sum",
    "down_n",
    "up_sum",
    "up_n",
    "gap_min",
    "gaps",
)


def _aggregate(db: Session, start: datetime, end: datetime, monitor_id: Optional[UUID], unit: Optional[str], daily: bool) -> list[dict[str, Any]]:
    """Add up the hourly rows (a day view) or the daily rows (week, month, year), by bucket or all together."""
    table = KpiDaily if daily else KpiHourly
    columns = [func.sum(getattr(table, name)).label(name) for name in _FIELDS]
    group: list[Any] = [table.link, table.kind]
    selected: list[Any] = [table.link, table.kind]
    if unit is not None:
        if daily:
            bucket = func.date_trunc(unit, cast(table.day, DateTime)).label("bucket")
        else:
            bucket = func.date_trunc(unit, func.timezone("UTC", table.bucket_start)).label("bucket")
        selected.insert(0, bucket)
        group.insert(0, bucket)
    query = select(*selected, *columns, func.max(table.loss_max).label("loss_max"), func.sum(table.outage_min).label("outage_min"))
    if daily:
        query = query.where(table.day >= start.date(), table.day < end.date())
    else:
        query = query.where(table.bucket_start >= start, table.bucket_start < end)
    if monitor_id is not None:
        query = query.where(table.monitor_id == monitor_id)
    rows = []
    for row in db.execute(query.group_by(*group)).mappings().all():
        item = dict(row)
        if "bucket" in item:
            item["bucket"] = item["bucket"].replace(tzinfo=timezone.utc)
        rows.append(item)
    return rows


def _sum(rows: list[dict[str, Any]]) -> dict[str, float]:
    total: dict[str, float] = {name: 0.0 for name in (*_FIELDS, "outage_min")}
    total["loss_max"] = None  # type: ignore[assignment]
    for row in rows:
        for name in (*_FIELDS, "outage_min"):
            total[name] += float(row[name] or 0)
        if row["loss_max"] is not None:
            total["loss_max"] = row["loss_max"] if total["loss_max"] is None else max(total["loss_max"], row["loss_max"])
    return total


def _summary(rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    mine = [row for row in rows if row["kind"] == kind]
    t = _sum(mine)
    links = len({row["link"] for row in mine}) or 1
    return {
        "samples": int(t["n"]),
        "failed": int(t["failed"]),
        "availability": _availability(int(t["n"]), int(t["failed"])),
        "loss_avg": _mean(t["loss_sum"], int(t["loss_n"])),
        "loss_max": t["loss_max"],
        "rtt_ms": _mean(t["rtt_sum"], int(t["rtt_n"])),
        "jitter_ms": _mean(t["jitter_sum"], int(t["jitter_n"])),
        "down_mbps": _mean(t["down_sum"], int(t["down_n"])),
        "up_mbps": _mean(t["up_sum"], int(t["up_n"])),
        "incidents": int(t["incidents"]),
        "outage_minutes": round(t["outage_min"], 1),
        "gap_minutes": round(t["gap_min"] / links, 1),
        "gaps": int(round(t["gaps"] / links)),
    }


def _by_link(rows: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {link: _summary([row for row in rows if row["link"] == link], kind) for link in ("wired", "wireless") if any(row["link"] == link and row["kind"] == kind for row in rows)}


def _service_summary(rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Service-attributed summary: when both Wired and Wi-Fi have comparable check counts (>= 5) and one link
    is healthy while the other degraded, the service itself was reachable and healthy, so headline availability
    and loss reflect the healthier link while `links` keeps the exact per-link breakdown.
    """
    blended = _summary(rows, kind)
    if kind not in ("internet", "voice", "video", "web", "apps"):
        return blended
    by_link = _by_link(rows, kind)
    wired = by_link.get("wired")
    wireless = by_link.get("wireless")
    if wired is None or wireless is None:
        return blended
    if wired["samples"] < 5 or wireless["samples"] < 5:
        return blended
    w_avail = wired["availability"]
    f_avail = wireless["availability"]
    if w_avail is None or f_avail is None:
        return blended
    if w_avail == f_avail and (wired["loss_avg"] or 0.0) == (wireless["loss_avg"] or 0.0):
        return blended
    # Pick the healthier link for headline service health, while keeping total sample count
    best = wired if (w_avail > f_avail or (w_avail == f_avail and (wired["loss_avg"] or 0.0) <= (wireless["loss_avg"] or 0.0))) else wireless
    return {
        **blended,
        "availability": best["availability"],
        "loss_avg": best["loss_avg"],
        "rtt_ms": best["rtt_ms"],
        "jitter_ms": best["jitter_ms"],
    }


def _diagnose_kpi(
    summary: dict[str, dict[str, Any]],
    links: dict[str, dict[str, dict[str, Any]]],
    targets: dict[str, float],
    *,
    wireless_tunnel_stale: bool = False,
    wired_tunnel_stale: bool = False,
) -> Optional[dict[str, str]]:
    """Diagnose Wi-Fi-only, Wired-only, tunnel-only, ISP/WAN, or server/agent degradation."""
    from app.services.link_correlation import diagnose_from_links

    return diagnose_from_links(
        links,
        targets,
        wireless_tunnel_stale=wireless_tunnel_stale,
        wired_tunnel_stale=wired_tunnel_stale,
    )

def incidents(db: Session, start: datetime, end: datetime, monitor_id: Optional[UUID], limit: int = 10) -> list[dict[str, Any]]:
    """Internet outages in the period: runs of failed path pings that were not tunnel-only, newest first."""
    query = (
        select(TestResult.started_at, Network.kind, Test.monitor_id)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .where(
            Test.slug == "icmp-path",
            TestResult.status == ResultStatus.fail,
            TestResult.started_at >= start,
            TestResult.started_at < end,
            _not_tunnel_only(),  # a fail while public web still answered is the tunnel, not the internet
        )
        .order_by(Network.kind, TestResult.started_at)
        .limit(5000)
    )
    if monitor_id is not None:
        query = query.where(Test.monitor_id == monitor_id)
    runs: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    for started, kind, _mon_id in db.execute(query).all():
        link = _link(kind.value)
        if current is not None and current["link"] == link and started - current["last"] <= OUTAGE_GAP:
            current["last"] = started
            current["checks"] += 1
        else:
            current = {"link": link, "start": started, "last": started, "checks": 1}
            runs.append(current)
    runs.sort(key=lambda item: item["start"], reverse=True)
    return [
        {
            "start": item["start"],
            "link": item["link"],
            "minutes": max(1, round((item["last"] - item["start"]).total_seconds() / 60) + 1),
            "checks": item["checks"],
        }
        for item in runs[:limit]
    ]


def gap_log(db: Session, start: datetime, end: datetime, monitor_id: Optional[UUID], limit: int = 10) -> list[dict[str, Any]]:
    """Stretches with no ping result on a link, newest first. Gaps on both links at once are one entry.

    Reads the detailed records, so it covers the last 31 days of the period at most.
    """
    begin = max(start, end - timedelta(days=31))
    prev = func.lag(TestResult.started_at).over(partition_by=TestResult.test_id, order_by=TestResult.started_at).label("prev")
    inner = (
        select(TestResult.started_at.label("at"), prev, Network.kind.label("kind"))
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .where(Test.slug == "icmp-path", TestResult.started_at >= begin - timedelta(days=1), TestResult.started_at < end, ~TestResult.external_id.startswith("rollup-"))
    )
    if monitor_id is not None:
        inner = inner.where(Test.monitor_id == monitor_id)
    inner = inner.subquery()
    found = db.execute(
        select(inner.c.prev, inner.c.at, inner.c.kind).where(inner.c.prev.is_not(None), inner.c.at >= begin, inner.c.at - inner.c.prev > OUTAGE_GAP).order_by(inner.c.prev)
    ).all()
    merged: list[dict[str, Any]] = []
    for before, at, kind in found:
        link = _link(kind.value)
        if merged and before <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], at)
            merged[-1]["links"].add(link)
        else:
            merged.append({"start": before, "end": at, "links": {link}})
    merged.sort(key=lambda item: item["start"], reverse=True)
    return [
        {
            "start": item["start"],
            "minutes": max(1, round((item["end"] - item["start"]).total_seconds() / 60 - 1)),
            "links": "both" if len(item["links"]) > 1 else next(iter(item["links"])),
            "ongoing": False,
        }
        for item in merged[:limit]
    ]


def ongoing_gap(db: Session, now: datetime, monitor_id: Optional[UUID]) -> Optional[dict[str, Any]]:
    """No result for several minutes right now. It is added to the report before the next result closes it."""
    query = select(func.max(TestResult.started_at)).join(Test, TestResult.test_id == Test.id).where(Test.slug == "icmp-path", ~TestResult.external_id.startswith("rollup-"))
    if monitor_id is not None:
        query = query.where(Test.monitor_id == monitor_id)
    last = db.scalar(query)
    if last is None or now - last <= OUTAGE_GAP:
        return None
    return {"start": last, "minutes": max(1, round((now - last).total_seconds() / 60 - 1)), "links": "both", "ongoing": True}


def report(db: Session, period: str, now: Optional[datetime] = None, monitor_id: Optional[UUID] = None, day: Optional[date] = None) -> dict[str, Any]:
    if period not in PERIODS:
        raise ValueError("period must be day, week, month or year")
    now = now or datetime.now(timezone.utc)
    today = _utc(now).date()
    if day is not None and day > today:
        day = today
    start, end, unit = window(period, now, day)
    previous_start = start - (end - start)
    daily = period != "day"

    current = _aggregate(db, start, end, monitor_id, None, daily)
    previous = _aggregate(db, previous_start, start, monitor_id, None, daily)
    buckets = _aggregate(db, start, end, monitor_id, unit, daily)

    by_bucket: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in buckets:
        by_bucket[row["bucket"]].append(row)
    series = []
    cursor = start
    while cursor < end:
        rows = by_bucket.get(cursor, [])
        internet = _summary(rows, "internet")
        wired = _summary([r for r in rows if r["link"] == "wired"], "internet")
        wireless = _summary([r for r in rows if r["link"] == "wireless"], "internet")
        voice = _service_summary(rows, "voice")
        voice_wired = _summary([r for r in rows if r["link"] == "wired"], "voice")
        voice_wireless = _summary([r for r in rows if r["link"] == "wireless"], "voice")
        video = _service_summary(rows, "video")
        video_wired = _summary([r for r in rows if r["link"] == "wired"], "video")
        video_wireless = _summary([r for r in rows if r["link"] == "wireless"], "video")
        web = _service_summary(rows, "web")
        web_wired = _summary([r for r in rows if r["link"] == "wired"], "web")
        web_wireless = _summary([r for r in rows if r["link"] == "wireless"], "web")
        speed = _summary(rows, "speed")
        series.append(
            {
                "t": cursor,
                "internet": internet["availability"],
                "internet_wired": wired["availability"],
                "internet_wireless": wireless["availability"],
                "voice": voice["availability"],
                "voice_wired": voice_wired["availability"],
                "voice_wireless": voice_wireless["availability"],
                "video": video["availability"],
                "video_wired": video_wired["availability"],
                "video_wireless": video_wireless["availability"],
                "web": web["availability"],
                "web_wired": web_wired["availability"],
                "web_wireless": web_wireless["availability"],
                "loss_internet": internet["loss_avg"],
                "loss_voice": voice["loss_avg"],
                "loss_video": video["loss_avg"],
                "rtt_ms": internet["rtt_ms"],
                "jitter_ms": internet["jitter_ms"],
                "down_mbps": speed["down_mbps"],
                "up_mbps": speed["up_mbps"],
                "outage_minutes": internet["outage_minutes"],
                "gap_minutes": internet["gap_minutes"],
            }
        )
        cursor = _add(cursor, unit, 1)

    data_since = db.scalar(select(func.min(KpiDaily.day)).where(*([KpiDaily.monitor_id == monitor_id] if monitor_id else [])))
    data_since = datetime(data_since.year, data_since.month, data_since.day, tzinfo=timezone.utc) if data_since else None
    summary = {kind: _service_summary(current, kind) for kind in ("internet", "voice", "video", "web", "apps", "speed")}
    before = {kind: _service_summary(previous, kind) for kind in ("internet", "voice", "video", "web", "apps", "speed")}
    internet = summary["internet"]
    internet["mttr_minutes"] = round(internet["outage_minutes"] / internet["incidents"], 1) if internet["incidents"] else None
    gaps = gap_log(db, start, min(end, now), monitor_id)
    live_gap = ongoing_gap(db, now, monitor_id) if start <= now < end else None
    if live_gap is not None:
        internet["gap_minutes"] = round(internet["gap_minutes"] + live_gap["minutes"], 1)
        internet["gaps"] += 1
        gaps.insert(0, live_gap)
    loss_parts = [(summary[k]["loss_avg"], summary[k]["samples"]) for k in ("voice", "video") if summary[k]["loss_avg"] is not None]
    weight = sum(count for _value, count in loss_parts)
    app_loss = sum(value * count for value, count in loss_parts) / weight if weight else None
    links = {kind: _by_link(current, kind) for kind in ("internet", "voice", "video", "web")}
    wireless_stale = False
    wired_stale = False
    if monitor_id is not None:
        from app.services.link_correlation import tunnel_is_stale

        mon = db.get(Monitor, monitor_id)
        if mon is not None:
            wireless_stale = tunnel_is_stale(mon, "wireless", now)
            wired_stale = tunnel_is_stale(mon, "wired", now)
    diagnosis = _diagnose_kpi(
        summary,
        links,
        TARGETS,
        wireless_tunnel_stale=wireless_stale,
        wired_tunnel_stale=wired_stale,
    )
    return {
        "period": period,
        "day": day,
        "bucket": unit,
        "from": start,
        "to": min(end, now),
        "generated_at": now,
        "data_since": data_since,
        "targets": TARGETS,
        "summary": summary,
        "previous": before,
        "app_loss_avg": app_loss,
        "links": links,
        "diagnosis": diagnosis,
        "series": series,
        "incidents": incidents(db, start, min(end, now), monitor_id),
        "gaps": gaps,
    }


def daily_history(db: Session, first: date, last: date, monitor_id: Optional[UUID] = None, limit: int = 400) -> list[dict[str, Any]]:
    """One line per UTC day from the stored daily records, newest first."""
    query = select(
        KpiDaily.day,
        KpiDaily.link,
        KpiDaily.kind,
        *[func.sum(getattr(KpiDaily, name)).label(name) for name in _FIELDS],
        func.max(KpiDaily.loss_max).label("loss_max"),
        func.sum(KpiDaily.outage_min).label("outage_min"),
    ).where(KpiDaily.day >= first, KpiDaily.day <= last)
    needs_internet = True
    if monitor_id is not None:
        query = query.where(KpiDaily.monitor_id == monitor_id)
        monitor = db.get(Monitor, monitor_id)
        needs_internet = monitor is None or monitor.platform != "android"
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(query.group_by(KpiDaily.day, KpiDaily.link, KpiDaily.kind)).mappings().all():
        by_day[row["day"]].append(dict(row))
    out = []
    for day in sorted(by_day, reverse=True)[:limit]:
        rows = by_day[day]
        internet = _summary(rows, "internet")
        voice = _service_summary(rows, "voice")
        video = _service_summary(rows, "video")
        web = _service_summary(rows, "web")
        parts = [(item["loss_avg"], item["samples"]) for item in (voice, video) if item["loss_avg"] is not None]
        weight = sum(count for _value, count in parts)
        loss = sum(value * count for value, count in parts) / weight if weight else None
        health = health_score(
            internet["availability"], voice["availability"], video["availability"], loss, web=web["availability"], needs_internet=needs_internet
        )
        day_links = {kind: _by_link(rows, kind) for kind in ("internet", "voice", "video", "web")}
        diag = _diagnose_kpi({"internet": internet, "voice": voice, "video": video, "web": web}, day_links, TARGETS)
        out.append(
            {
                "day": day,
                "internet": internet["availability"],
                "internet_wired": _summary([r for r in rows if r["link"] == "wired"], "internet")["availability"],
                "internet_wireless": _summary([r for r in rows if r["link"] == "wireless"], "internet")["availability"],
                "voice": voice["availability"],
                "voice_wired": _summary([r for r in rows if r["link"] == "wired"], "voice")["availability"],
                "voice_wireless": _summary([r for r in rows if r["link"] == "wireless"], "voice")["availability"],
                "video": video["availability"],
                "video_wired": _summary([r for r in rows if r["link"] == "wired"], "video")["availability"],
                "video_wireless": _summary([r for r in rows if r["link"] == "wireless"], "video")["availability"],
                "web": web["availability"],
                "web_wired": _summary([r for r in rows if r["link"] == "wired"], "web")["availability"],
                "web_wireless": _summary([r for r in rows if r["link"] == "wireless"], "web")["availability"],
                "loss": loss,
                "score": health["score"],
                "tone": health["tone"],
                "score_parts": health["parts"],
                "root_cause": diag["cause"] if diag else None,
                "root_cause_title": diag["title"] if diag else None,
                "offline_minutes": internet["outage_minutes"],
                "gap_minutes": internet["gap_minutes"],
                "outages": internet["incidents"],
                "rtt_ms": internet["rtt_ms"],
            }
        )
    return out
