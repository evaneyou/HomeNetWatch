"""Telegram alerts: what counts as a problem, and announcing each problem once.

Every minute the rules below list the conditions that are true right now. A condition is
announced once it has held for its debounce time, and again when it clears. Nothing here
needs the dashboard: it runs in the server.

If the server itself is down it cannot send anything. That case needs a check from outside.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import telegram
from app.models import (
    AlertEvent,
    ApInventory,
    Issue,
    IssueSeverity,
    IssueStatus,
    Monitor,
    MonitorHealth,
    Network,
    ResultStatus,
    RfAlert,
    SyntheticRun,
    SyntheticSample,
    TelegramSetting,
    Test,
    TestResult,
    WifiObservation,
)

log = logging.getLogger(__name__)

CATEGORIES = ("network", "pi", "server", "app")
MAX_PER_HOUR = 30
MAX_ATTEMPTS = 10
KEEP_DAYS = 90

TEMP_LIMIT_C = 80.0
DISK_LIMIT_PCT = 90.0
SERVER_ERRORS_LIMIT = 10
SERVER_ERRORS_WINDOW_S = 300


@dataclass(frozen=True)
class Condition:
    key: str
    category: str
    severity: str
    title: str
    detail: str = ""
    debounce_s: int = 0


# ---- what the server saw about itself

_errors: deque[float] = deque(maxlen=500)
_errors_lock = threading.Lock()


def note_server_error() -> None:
    with _errors_lock:
        _errors.append(time.monotonic())


def recent_server_errors(window_s: int = SERVER_ERRORS_WINDOW_S) -> int:
    cutoff = time.monotonic() - window_s
    with _errors_lock:
        return sum(1 for stamp in _errors if stamp >= cutoff)


def _pool_use() -> tuple[int, int]:
    """Connections in use and the most the pool will open."""
    from app.db import engine

    pool = engine.pool
    try:
        return int(pool.checkedout()), int(pool.size()) + int(getattr(pool, "_max_overflow", 0))
    except Exception:  # pragma: no cover - a pool type without these
        return 0, 0


# ---- the rules

def _link_label(kind: str) -> str:
    return "Wi-Fi" if kind == "wifi" else "Wired"


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%H:%M UTC")


def network_conditions(db: Session, now: datetime, offline_ids: set) -> list[Condition]:
    """The ISP path is down on a link, or many web services are unreachable while the path is up.

    Path/web checks are the access plane. A dead WireGuard tunnel alone is not an internet outage.
    """
    from app.services.link_correlation import access_ok_recent, tunnel_is_stale

    out: list[Condition] = []
    rows = db.execute(
        select(Test, Network, Monitor)
        .join(Network, Test.network_id == Network.id)
        .join(Monitor, Test.monitor_id == Monitor.id)
        .where(Test.enabled.is_(True))
    ).all()
    path: dict[tuple, dict[str, Test]] = {}
    web: dict[tuple, list[Test]] = {}
    monitors: dict = {}
    for test, network, monitor in rows:
        if monitor.id in offline_ids:
            continue  # the Pi being offline is its own alert, and its old results say nothing new
        monitors[monitor.id] = monitor
        slot = (monitor.id, network.kind.value, monitor.name)
        if test.slug in ("http-path", "icmp-path"):
            path.setdefault(slot, {})[test.slug] = test
        elif test.slug.startswith("http-"):
            web.setdefault(slot, []).append(test)
    for (monitor_id, kind, name), tests in path.items():
        link = "wireless" if kind == "wifi" else "wired"
        monitor = monitors.get(monitor_id)
        # If the tunnel is stale but recent web/path samples still succeed, this is not a net outage.
        if monitor is not None and tunnel_is_stale(monitor, link, now) and access_ok_recent(db, monitor_id, link, now):
            continue
        down = [test for test in tests.values() if test.stable_status == ResultStatus.fail]
        if len(down) == len(tests) and tests:
            # Confirm access is actually down: do not alert if a recent web check still passes.
            if access_ok_recent(db, monitor_id, link, now):
                continue
            since = min((test.state_since for test in down if test.state_since), default=None)
            out.append(
                Condition(
                    key=f"net-down:{monitor_id}:{kind}",
                    category="network",
                    severity="critical",
                    title=f"Internet outage on the {_link_label(kind)} link",
                    detail=f"{name} cannot reach the internet over this link." + (f" Since {_utc(since)}." if since else ""),
                    debounce_s=60,
                )
            )
            continue
        degraded = [test for test in tests.values() if test.stable_status == ResultStatus.degraded]
        if degraded and not down:
            since = min((test.state_since for test in degraded if test.state_since), default=None)
            reason = ""
            for probe in degraded:
                latest_reason = db.scalar(
                    select(TestResult.status_reason)
                    .where(TestResult.test_id == probe.id)
                    .order_by(TestResult.started_at.desc())
                    .limit(1)
                )
                if latest_reason:
                    reason = f" {latest_reason}"
                    break
            out.append(
                Condition(
                    key=f"net-degraded:{monitor_id}:{kind}",
                    category="network",
                    severity="warning",
                    title=f"ISP / {_link_label(kind)} link is degraded",
                    detail=f"{name} is seeing sustained high latency, jitter, or packet loss on the {_link_label(kind)} path.{reason}" + (f" Since {_utc(since)}." if since else ""),
                    debounce_s=300,
                )
            )
        services = web.get((monitor_id, kind, name), [])
        failing = [test for test in services if test.stable_status == ResultStatus.fail]
        if len(services) >= 3 and len(failing) * 100 >= len(services) * 40:
            out.append(
                Condition(
                    key=f"net-services:{monitor_id}:{kind}",
                    category="network",
                    severity="warning",
                    title=f"Many internet services unreachable on the {_link_label(kind)} link",
                    detail=f"{len(failing)} of {len(services)} web services are failing from {name}, while the path to the server is up.",
                    debounce_s=120,
                )
            )
    airwave_issues = db.execute(
        select(Issue, Monitor)
        .join(Monitor, Issue.monitor_id == Monitor.id)
        .where(
            Issue.status == IssueStatus.open,
            Issue.root_cause_stage == "airwave",
            Issue.severity != IssueSeverity.info,
        )
    ).all()
    for issue, monitor in airwave_issues:
        if monitor.id in offline_ids:
            continue
        out.append(
            Condition(
                key=f"airwave:{issue.id}",
                category="network",
                severity="critical" if issue.severity.value == "critical" else "warning",
                title=f"Wi-Fi security alert on {monitor.name}: {issue.title}",
                detail=(issue.summary or issue.title)[:240],
                debounce_s=60,
            )
        )
    return out


def pi_conditions(db: Session, now: datetime) -> tuple[list[Condition], set]:
    """The Pi is offline, hot, short of disk, or reporting an error. Also returns which Pis are offline."""
    out: list[Condition] = []
    offline: set = set()
    for monitor in db.scalars(select(Monitor).where(Monitor.token_hash.is_not(None), Monitor.token_hash != "")).all():
        limit = timedelta(seconds=max(300, 3 * int(monitor.health_interval_seconds or 60)))
        seen = monitor.last_seen_at
        if seen is None:
            continue
        if now - seen > limit:
            offline.add(monitor.id)
            out.append(
                Condition(
                    key=f"pi-offline:{monitor.id}",
                    category="pi",
                    severity="critical",
                    title=f"{monitor.name} is offline",
                    detail=f"No report from the Pi since {_utc(seen)}.",
                )
            )
            continue
        out.extend(_tunnel_down_conditions(db, monitor, now))
        health = db.scalar(
            select(MonitorHealth).where(MonitorHealth.monitor_id == monitor.id).order_by(MonitorHealth.reported_at.desc()).limit(1)
        )
        if health is None:
            continue
        if health.temp_c is not None and health.temp_c >= TEMP_LIMIT_C:
            out.append(
                Condition(
                    key=f"pi-temp:{monitor.id}",
                    category="pi",
                    severity="warning",
                    title=f"{monitor.name} is running hot",
                    detail=f"CPU temperature is {health.temp_c:.0f} C.",
                    debounce_s=120,
                )
            )
        if health.disk_total_mb:
            used = 100.0 * health.disk_used_mb / health.disk_total_mb
            if used >= DISK_LIMIT_PCT:
                out.append(
                    Condition(
                        key=f"pi-disk:{monitor.id}",
                        category="pi",
                        severity="warning",
                        title=f"{monitor.name} is almost out of disk space",
                        detail=f"The Pi's disk is {used:.0f}% full.",
                        debounce_s=300,
                    )
                )
        if health.last_error:
            out.append(
                Condition(
                    key=f"pi-error:{monitor.id}",
                    category="pi",
                    severity="warning",
                    title=f"{monitor.name} reports a problem",
                    detail=health.last_error[:240],
                    debounce_s=600,
                )
            )
    return out, offline


def _tunnel_down_conditions(db: Session, monitor: Monitor, now: datetime) -> list[Condition]:
    """Measurement tunnel down while the Pi is still checking in (access plane may still be fine)."""
    from app.services.link_correlation import access_ok_recent, correlate_link, tunnel_is_stale

    out: list[Condition] = []
    for link, label in (("wired", "Wired"), ("wireless", "Wi-Fi")):
        if not tunnel_is_stale(monitor, link, now):
            continue
        access_ok = access_ok_recent(db, monitor.id, link, now)
        corr = correlate_link(monitor, link, access_ok=access_ok, now=now)
        detail = corr.detail or (
            f"No recent WireGuard handshake on the {label} tunnel. "
            "Application checks that use this tunnel will fail until it is restored."
        )
        out.append(
            Condition(
                key=f"tunnel-down:{monitor.id}:{link}",
                category="pi",
                severity="warning",
                title=f"{monitor.name} {label} tunnel to the server is down",
                detail=detail,
                debounce_s=300,
            )
        )
    return out


def server_conditions(now: datetime) -> list[Condition]:
    out: list[Condition] = []
    try:
        usage = shutil.disk_usage("/")
        used = 100.0 * usage.used / usage.total
        if used >= DISK_LIMIT_PCT:
            out.append(
                Condition(
                    key="server-disk",
                    category="server",
                    severity="critical",
                    title="The server is almost out of disk space",
                    detail=f"The server's disk is {used:.0f}% full.",
                    debounce_s=300,
                )
            )
    except OSError:
        pass
    in_use, limit = _pool_use()
    if limit and in_use >= limit - 1:
        out.append(
            Condition(
                key="server-db-pool",
                category="server",
                severity="critical",
                title="The server is running out of database connections",
                detail=f"{in_use} of {limit} connections are in use. Requests may be timing out.",
                debounce_s=120,
            )
        )
    errors = recent_server_errors()
    if errors >= SERVER_ERRORS_LIMIT:
        out.append(
            Condition(
                key="server-errors",
                category="server",
                severity="critical",
                title="The server is returning errors",
                detail=f"{errors} server errors in the last {SERVER_ERRORS_WINDOW_S // 60} minutes.",
            )
        )
    return out


def app_conditions(db: Session, now: datetime, offline_ids: set) -> list[Condition]:
    """A running application check (voice, video and so on) failed its last three reports.

    When the measurement tunnel is down, skip per-app spam; tunnel-down covers that case.
    """
    from app.services.link_correlation import access_ok_recent, tunnel_is_stale

    out: list[Condition] = []
    monitors = {
        m.id: m
        for m in db.scalars(select(Monitor).where(Monitor.token_hash.is_not(None), Monitor.token_hash != "")).all()
    }
    runs = db.scalars(select(SyntheticRun).where(SyntheticRun.mode == "continuous", SyntheticRun.status == "running")).all()
    for run in runs:
        if run.monitor_id in offline_ids:
            continue
        monitor = monitors.get(run.monitor_id)
        for link in run.links or []:
            if link not in ("wired", "wireless"):
                continue
            if monitor is not None and tunnel_is_stale(monitor, link, now) and access_ok_recent(db, run.monitor_id, link, now):
                continue
            rows = db.scalars(
                select(SyntheticSample)
                .where(
                    SyntheticSample.run_id == run.id,
                    SyntheticSample.link == link,
                    ~SyntheticSample.sample_key.startswith("hour:"),
                    ~SyntheticSample.sample_key.startswith("day:"),
                )
                .order_by(SyntheticSample.recorded_at.desc())
                .limit(3)
            ).all()
            if len(rows) < 3 or any(row.verdict != "fail" for row in rows) or now - rows[0].recorded_at > timedelta(minutes=15):
                continue
            kind = "wifi" if link == "wireless" else "wired"
            name = (run.options or {}).get("preset") or run.profile_slug
            latest = rows[0]
            bits = []
            if latest.rtt_ms is not None:
                bits.append(f"round trip {latest.rtt_ms:.0f} ms")
            if latest.jitter_ms is not None:
                bits.append(f"jitter {latest.jitter_ms:.0f} ms")
            if latest.loss_pct is not None:
                bits.append(f"loss {latest.loss_pct:.1f}%")
            out.append(
                Condition(
                    key=f"app:{run.id}:{link}",
                    category="app",
                    severity="warning",
                    title=f"{name} is failing on the {_link_label(kind)} link",
                    detail="Its last three checks failed" + (": " + ", ".join(bits) if bits else "") + ".",
                )
            )
    return out


def _resolve_stale_issues(db: Session, now: datetime) -> None:
    """Auto-resolve open issues whose monitor/test cleared or whose RF event aged out."""
    open_issues = db.scalars(select(Issue).where(Issue.status == IssueStatus.open)).all()
    if not open_issues:
        return
    changed = False
    for issue in open_issues:
        opened = issue.opened_at if issue.opened_at.tzinfo else issue.opened_at.replace(tzinfo=timezone.utc)
        if issue.monitor_id is None or issue.severity == IssueSeverity.info:
            issue.status = IssueStatus.resolved
            issue.resolved_at = now
            changed = True
            continue
        if issue.test_id is not None:
            test = db.get(Test, issue.test_id)
            if test is None or test.stable_status == ResultStatus.ok:
                issue.status = IssueStatus.resolved
                issue.resolved_at = now
                changed = True
                continue
        if issue.root_cause_stage == "airwave":
            bssid = issue.title.rsplit(" on ", 1)[-1].strip().lower() if " on " in issue.title else ""
            if issue.title.startswith("Possible evil twin ") and bssid:
                trusted = db.scalar(
                    select(ApInventory.trusted).where(
                        ApInventory.monitor_id == issue.monitor_id,
                        ApInventory.bssid == bssid,
                    )
                )
                if trusted:
                    issue.status = IssueStatus.resolved
                    issue.resolved_at = now
                    changed = True
                    continue
                if now - opened >= timedelta(hours=6):
                    recent_obs = db.scalar(
                        select(WifiObservation.id)
                        .where(
                            WifiObservation.monitor_id == issue.monitor_id,
                            WifiObservation.bssid == bssid,
                            WifiObservation.scanned_at >= now - timedelta(hours=6),
                        )
                        .limit(1)
                    )
                    if recent_obs is None:
                        issue.status = IssueStatus.resolved
                        issue.resolved_at = now
                        changed = True
                        continue
            elif issue.title.startswith("Airwave ") and now - opened >= timedelta(hours=1):
                recent_alert = db.scalar(
                    select(RfAlert.id)
                    .where(
                        RfAlert.monitor_id == issue.monitor_id,
                        RfAlert.bssid == bssid,
                        RfAlert.observed_at >= now - timedelta(hours=1),
                    )
                    .limit(1)
                )
                if recent_alert is None:
                    issue.status = IssueStatus.resolved
                    issue.resolved_at = now
                    changed = True
                    continue
        elif issue.test_id is None and now - opened >= timedelta(hours=24):
            issue.status = IssueStatus.resolved
            issue.resolved_at = now
            changed = True
    if changed:
        db.flush()


def evaluate(db: Session, now: datetime) -> list[Condition]:
    from app.wireguard_tunnel import refresh_handshakes

    _resolve_stale_issues(db, now)
    monitors = list(db.scalars(select(Monitor).where(Monitor.token_hash.is_not(None), Monitor.token_hash != "")).all())
    refresh_handshakes(db, monitors)
    pi, offline = pi_conditions(db, now)
    return [*network_conditions(db, now, offline), *pi, *server_conditions(now), *app_conditions(db, now, offline)]


# ---- announcing

def get_settings_row(db: Session) -> TelegramSetting:
    row = db.get(TelegramSetting, 1)
    if row is None:
        row = TelegramSetting(id=1)
        db.add(row)
        db.flush()
    return row


def configured(row: TelegramSetting) -> bool:
    return bool(row.enabled and row.chat_id and row.bot_token_enc)


def _enabled_categories(row: TelegramSetting) -> set[str]:
    wanted = {"network": row.notify_network, "pi": row.notify_pi, "server": row.notify_server, "app": row.notify_app}
    return {name for name, on in wanted.items() if on}


def _opened_text(event: AlertEvent) -> str:
    icon = "\U0001F534" if event.severity == "critical" else "\U0001F7E0"
    lines = [f"{icon} {event.title}"]
    if event.detail:
        lines.append(event.detail)
    return "\n".join(lines)


def _resolved_text(event: AlertEvent) -> str:
    lasted = ""
    if event.opened_at and event.resolved_at:
        minutes = max(1, int((event.resolved_at - event.opened_at).total_seconds() // 60))
        lasted = f" It lasted {minutes} min." if minutes < 120 else f" It lasted {minutes // 60} h {minutes % 60} min."
    return f"✅ Resolved: {event.title}.{lasted}"


Sender = Callable[[str, str, str], None]


def run_cycle(db: Session, now: Optional[datetime] = None, sender: Optional[Sender] = None) -> dict[str, int]:
    """One pass. Returns counts, for logging and tests."""
    now = now or datetime.now(timezone.utc)
    _resolve_stale_issues(db, now)
    send = sender or telegram.send
    row = get_settings_row(db)
    counts = {"opened": 0, "resolved": 0, "sent": 0, "failed": 0, "limited": 0}
    if not configured(row):
        db.commit()
        return counts
    token = telegram.decrypt(row.bot_token_enc)
    if not token:
        row.last_error = "The saved bot token could not be read. Enter it again."
        db.commit()
        return counts
    allowed = _enabled_categories(row)
    firing = {item.key: item for item in evaluate(db, now) if item.category in allowed}
    active = {event.key: event for event in db.scalars(select(AlertEvent).where(AlertEvent.status.in_(("pending", "open")))).all()}

    for key, cond in firing.items():
        event = active.get(key)
        if event is None:
            event = AlertEvent(
                key=key,
                category=cond.category,
                severity=cond.severity,
                title=cond.title,
                detail=cond.detail,
                status="pending",
                first_seen_at=now,
            )
            db.add(event)
            active[key] = event
        event.title, event.detail, event.severity = cond.title, cond.detail, cond.severity
        if event.status == "pending" and (now - event.first_seen_at).total_seconds() >= cond.debounce_s:
            event.status = "open"
            event.opened_at = now
            counts["opened"] += 1

    for key, event in list(active.items()):
        if key in firing:
            continue
        if event.status == "pending":
            db.delete(event)  # a blip: it never held long enough to announce
        else:
            event.status = "resolved"
            event.resolved_at = now
            counts["resolved"] += 1
            if event.category not in allowed:
                event.resolved_notified_at = now
    db.flush()

    sent_last_hour = (
        db.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.notified_at >= now - timedelta(hours=1)))
        or 0
    ) + (
        db.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.resolved_notified_at >= now - timedelta(hours=1)))
        or 0
    )

    def deliver(event: AlertEvent, text: str, field: str) -> bool:
        nonlocal sent_last_hour
        if sent_last_hour >= MAX_PER_HOUR:
            counts["limited"] += 1
            return False
        event.attempts += 1
        try:
            send(token, row.chat_id, text)
        except telegram.TelegramError as exc:
            counts["failed"] += 1
            row.last_error = str(exc)[:300]
            if event.attempts >= MAX_ATTEMPTS:
                setattr(event, field, now)  # give up on this one, so a broken setup does not retry forever
            return False
        setattr(event, field, now)
        event.attempts = 0
        sent_last_hour += 1
        counts["sent"] += 1
        row.last_ok_at = now
        row.last_error = ""
        return True

    pending_open = db.scalars(select(AlertEvent).where(AlertEvent.status == "open", AlertEvent.notified_at.is_(None)).order_by(AlertEvent.opened_at)).all()
    for event in pending_open:
        if not deliver(event, _opened_text(event), "notified_at") and row.last_error:
            break
    if row.notify_recovery:
        pending_done = db.scalars(
            select(AlertEvent)
            .where(AlertEvent.status == "resolved", AlertEvent.notified_at.is_not(None), AlertEvent.resolved_notified_at.is_(None))
            .order_by(AlertEvent.resolved_at)
        ).all()
        for event in pending_done:
            event.attempts = 0 if event.attempts is None else event.attempts
            if not deliver(event, _resolved_text(event), "resolved_notified_at") and row.last_error:
                break
    else:
        db.execute(
            AlertEvent.__table__.update()
            .where(AlertEvent.status == "resolved", AlertEvent.resolved_notified_at.is_(None))
            .values(resolved_notified_at=now)
        )
    db.execute(delete(AlertEvent).where(AlertEvent.status == "resolved", AlertEvent.resolved_at < now - timedelta(days=KEEP_DAYS)))
    db.commit()
    return counts


def run_once() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        counts = run_cycle(db)
        if counts["opened"] or counts["resolved"] or counts["failed"]:
            log.info("alerts %s", counts)
    except Exception:
        db.rollback()
        log.exception("alert cycle failed")
    finally:
        db.close()
