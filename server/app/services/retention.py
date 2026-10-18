"""Keep two years of measurements, and one year of guest visits.

Raw samples stay for 6 months. From 6 to 12 months they are averaged into one
row per hour. From 12 months to 2 years they are averaged into one row per day.
Anything older than 2 years is deleted.
"""

from __future__ import annotations

import calendar
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import guest_visits
from app.db import SessionLocal
from app.models import GuestVisit, ResultStatus, RfAlert, RfChannelStat, RfClient, SystemLog, TestResult, WifiObservation

log = logging.getLogger(__name__)

_STATUS_RANK = {ResultStatus.ok: 0, ResultStatus.degraded: 1, ResultStatus.fail: 2}
_METRIC_KEYS = ("rtt_ms", "loss_pct", "total_ms", "up_mbps", "down_mbps")


def run_retention() -> None:
    db = SessionLocal()
    try:
        apply_retention(db, datetime.now(timezone.utc))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def apply_retention(db: Session, now: datetime) -> dict[str, int]:
    """Roll up and delete measurement rows.

    ``now - 6 months`` through ``now`` stays raw.
    ``now - 12 months`` through ``now - 6 months`` becomes one row per hour.
    ``now - 24 months`` through ``now - 12 months`` becomes one row per day.
    Older than 24 months is deleted.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hourly_before = _subtract_months(now, 6)
    daily_before = _subtract_months(now, 12)
    delete_before = _subtract_months(now, 24)

    deleted_results = db.execute(
        delete(TestResult).where(TestResult.started_at < delete_before)
    ).rowcount
    deleted_wifi = db.execute(
        delete(WifiObservation).where(WifiObservation.scanned_at < delete_before)
    ).rowcount
    deleted_channels = db.execute(
        delete(RfChannelStat).where(RfChannelStat.measured_at < delete_before)
    ).rowcount
    deleted_clients = db.execute(delete(RfClient).where(RfClient.observed_at < delete_before)).rowcount
    deleted_alerts = db.execute(delete(RfAlert).where(RfAlert.observed_at < delete_before)).rowcount
    deleted_system_logs = db.execute(delete(SystemLog).where(SystemLog.recorded_at < delete_before)).rowcount
    from app import kpi as kpi_module

    deleted_kpi = kpi_module.prune(db, now)
    deleted_visits = db.execute(
        delete(GuestVisit).where(GuestVisit.visited_at < now - timedelta(days=guest_visits.RETENTION_DAYS))
    ).rowcount

    daily_results = _rollup_results(db, delete_before, daily_before, "day")
    hourly_results = _rollup_results(db, daily_before, hourly_before, "hour")
    daily_wifi = _rollup_wifi(db, delete_before, daily_before, "day")
    hourly_wifi = _rollup_wifi(db, daily_before, hourly_before, "hour")
    daily_channels = _rollup_channels(db, delete_before, daily_before, "day")
    hourly_channels = _rollup_channels(db, daily_before, hourly_before, "hour")
    daily_clients = _rollup_clients(db, delete_before, daily_before, "day")
    hourly_clients = _rollup_clients(db, daily_before, hourly_before, "hour")
    # Imported here: app.synthetic reaches back into the services package.
    from app.synthetic import rollup_samples

    synthetic = rollup_samples(
        db,
        hourly_before=hourly_before,
        daily_before=daily_before,
        delete_before=delete_before,
    )
    summary = {
        "deleted_results": deleted_results or 0,
        "deleted_wifi": deleted_wifi or 0,
        "deleted_channels": deleted_channels or 0,
        "deleted_clients": deleted_clients or 0,
        "deleted_alerts": deleted_alerts or 0,
        "deleted_system_logs": deleted_system_logs or 0,
        "deleted_guest_visits": deleted_visits or 0,
        "deleted_kpi_hours": deleted_kpi,
        "daily_results": daily_results,
        "hourly_results": hourly_results,
        "daily_wifi": daily_wifi,
        "hourly_wifi": hourly_wifi,
        "daily_channels": daily_channels,
        "hourly_channels": hourly_channels,
        "daily_clients": daily_clients,
        "hourly_clients": hourly_clients,
        **synthetic,
    }
    log.info("measurement retention %s", summary)
    return summary


def _rollup_results(db: Session, start: datetime, end: datetime, resolution: str) -> int:
    rows = db.scalars(
        select(TestResult).where(TestResult.started_at >= start, TestResult.started_at < end)
    ).all()
    groups: dict[tuple, list[TestResult]] = defaultdict(list)
    for row in rows:
        groups[(row.test_id, _bucket(row.started_at, resolution))].append(row)
    rolled = 0
    for (test_id, bucket), members in groups.items():
        if len(members) < 2:
            continue
        rolled += 1
        averaged = _average_result(members, bucket)
        finished_at = max(m.finished_at for m in members)
        db.execute(delete(TestResult).where(TestResult.id.in_([m.id for m in members])))
        db.add(
            TestResult(
                external_id=f"rollup-{uuid.uuid4().hex}",
                test_id=test_id,
                started_at=bucket,
                finished_at=finished_at,
                status=averaged["status"],
                stages=averaged["stages"],
                metrics=averaged["metrics"],
                message="Daily average" if resolution == "day" else "Hourly average",
            )
        )
    return rolled


def _rollup_wifi(db: Session, start: datetime, end: datetime, resolution: str) -> int:
    rows = db.scalars(
        select(WifiObservation).where(
            WifiObservation.scanned_at >= start, WifiObservation.scanned_at < end
        )
    ).all()
    groups: dict[tuple, list[WifiObservation]] = defaultdict(list)
    for row in rows:
        groups[(row.monitor_id, row.bssid, _bucket(row.scanned_at, resolution))].append(row)
    rolled = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        rolled += 1
        latest = max(members, key=lambda row: row.scanned_at)
        bucket = _bucket(latest.scanned_at, resolution)
        averaged = WifiObservation(
            monitor_id=latest.monitor_id,
            scanned_at=bucket,
            ssid=latest.ssid,
            bssid=latest.bssid,
            vendor=latest.vendor,
            band=latest.band,
            channel=latest.channel,
            channel_width=latest.channel_width,
            rssi_dbm=_mean(m.rssi_dbm for m in members),
            noise_dbm=_mean_or_none(m.noise_dbm for m in members),
            security=latest.security,
            phy=latest.phy,
            max_link_rate_mbps=round(_mean(m.max_link_rate_mbps for m in members)),
            client_count=round(_mean(m.client_count for m in members)),
        )
        db.execute(delete(WifiObservation).where(WifiObservation.id.in_([m.id for m in members])))
        db.add(averaged)
    return rolled


def _rollup_channels(db: Session, start: datetime, end: datetime, resolution: str) -> int:
    rows = db.scalars(
        select(RfChannelStat).where(RfChannelStat.measured_at >= start, RfChannelStat.measured_at < end)
    ).all()
    groups: dict[tuple, list[RfChannelStat]] = defaultdict(list)
    for row in rows:
        groups[(row.monitor_id, row.band, row.channel, _bucket(row.measured_at, resolution))].append(row)
    rolled = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        rolled += 1
        latest = max(members, key=lambda row: row.measured_at)
        db.execute(delete(RfChannelStat).where(RfChannelStat.id.in_([m.id for m in members])))
        db.add(
            RfChannelStat(
                monitor_id=latest.monitor_id,
                measured_at=_bucket(latest.measured_at, resolution),
                band=latest.band,
                channel=latest.channel,
                noise_dbm=_mean_or_none(m.noise_dbm for m in members),
                busy_pct=_mean_or_none(m.busy_pct for m in members),
                airtime_pct=_mean_or_none(m.airtime_pct for m in members),
                packets=round(_mean(m.packets for m in members)) if any(m.packets is not None for m in members) else None,
            )
        )
    return rolled


def _rollup_clients(db: Session, start: datetime, end: datetime, resolution: str) -> int:
    rows = db.scalars(select(RfClient).where(RfClient.observed_at >= start, RfClient.observed_at < end)).all()
    groups: dict[tuple, list[RfClient]] = defaultdict(list)
    for row in rows:
        groups[(row.monitor_id, row.mac, _bucket(row.observed_at, resolution))].append(row)
    rolled = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        rolled += 1
        latest = max(members, key=lambda row: row.observed_at)
        bucket = _bucket(latest.observed_at, resolution)
        db.execute(delete(RfClient).where(RfClient.id.in_([m.id for m in members])))
        probed: list[str] = []
        for member in members:
            try:
                parsed = json.loads(member.probed_ssids or "[]")
            except json.JSONDecodeError:
                parsed = []
            for name in parsed if isinstance(parsed, list) else []:
                if isinstance(name, str) and name and name not in probed:
                    probed.append(name)
        opened = [member.first_seen for member in members if member.first_seen is not None]
        lows = [member.signal_min for member in members if member.signal_min is not None]
        highs = [member.signal_max for member in members if member.signal_max is not None]
        db.add(
            RfClient(
                monitor_id=latest.monitor_id,
                observed_at=bucket,
                dedupe_key=latest.mac,
                mac=latest.mac,
                vendor=next((member.vendor for member in sorted(members, key=lambda row: row.observed_at, reverse=True) if member.vendor), ""),
                bssid=latest.bssid,
                signal=_mean_or_none(m.signal for m in members if m.signal),
                signal_min=min(lows) if lows else None,
                signal_max=max(highs) if highs else None,
                last_seen=max(m.last_seen for m in members),
                first_seen=min(opened) if opened else None,
                randomized=any(m.randomized for m in members),
                band=latest.band,
                channel=latest.channel,
                packets=latest.packets,
                data_bytes=latest.data_bytes,
                retries=latest.retries,
                disconnects=latest.disconnects,
                probed_ssids=json.dumps(probed[:12]),
            )
        )
    return rolled


def _average_result(rows: list[TestResult], bucket: datetime) -> dict[str, Any]:
    status = max(rows, key=lambda row: _STATUS_RANK[row.status]).status
    metrics: dict[str, Any] = {}
    for key in _METRIC_KEYS:
        values = [row.metrics.get(key) for row in rows if isinstance(row.metrics, dict)]
        numbers = [v for v in values if isinstance(v, (int, float))]
        if numbers:
            metrics[key] = round(sum(numbers) / len(numbers), 2)
    hops = _average_hops(rows)
    if hops:
        metrics["hops"] = hops
    return {"status": status, "stages": _average_stages(rows), "metrics": metrics, "started_at": bucket}


def _average_hops(rows: list[TestResult]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    hosts: dict[int, str] = {}
    for row in rows:
        for hop in (row.metrics or {}).get("hops") or []:
            if not isinstance(hop, dict) or not isinstance(hop.get("hop"), int):
                continue
            grouped[hop["hop"]].append(hop)
            if isinstance(hop.get("host"), str):
                hosts[hop["hop"]] = hop["host"]
    averaged = []
    for number in sorted(grouped):
        items = grouped[number]

        def mean(key: str) -> float:
            nums = [item[key] for item in items if isinstance(item.get(key), (int, float))]
            if not nums:
                return 0.0
            return round(sum(nums) / len(nums), 2)

        averaged.append(
            {
                "hop": number,
                "host": hosts.get(number, ""),
                "loss_pct": mean("loss_pct"),
                "best_ms": mean("best_ms"),
                "avg_ms": mean("avg_ms"),
                "worst_ms": mean("worst_ms"),
            }
        )
    return averaged


def _average_stages(rows: list[TestResult]) -> list[dict[str, Any]]:
    order: list[str] = []
    durations: dict[str, list[float]] = defaultdict(list)
    statuses: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for stage in row.stages or []:
            name = stage.get("name")
            if not name:
                continue
            if name not in durations:
                order.append(name)
            if isinstance(stage.get("duration_ms"), (int, float)):
                durations[name].append(stage["duration_ms"])
            if stage.get("status"):
                statuses[name].append(stage["status"])
    averaged = []
    for name in order:
        item: dict[str, Any] = {"name": name, "duration_ms": round(_mean(durations[name]), 1)}
        if statuses[name]:
            item["status"] = max(statuses[name], key=lambda s: {"ok": 0, "degraded": 1, "fail": 2}.get(s, 0))
        averaged.append(item)
    return averaged


def _subtract_months(moment: datetime, months: int) -> datetime:
    year = moment.year
    month = moment.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _bucket(moment: datetime, resolution: str) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    if resolution == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return moment.replace(minute=0, second=0, microsecond=0)


def _mean(values) -> float:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def _mean_or_none(values) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return sum(nums) / len(nums)


def oldest_kept(now: Optional[datetime] = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _subtract_months(moment, 24)
