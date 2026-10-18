"""Airwave reads and the Wi-Fi scan upload from a remote agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import _resolve_monitor_identity
from app.link_trust import note_new_ap
from app.auth import get_current_user, require_admin_csrf
from app.db import get_db
from app.models import (
    ApInventory,
    Issue,
    IssueSeverity,
    IssueStatus,
    Monitor,
    MonitorHealth,
    RfAlert,
    RfChannelStat,
    RfClient,
    User,
    WifiObservation,
)
from app.rf_view import (
    bucket_seconds,
    bucket_start,
    hill_points,
    noise_by_frequency,
    parse_iw_scan,
    parse_iw_survey,
    range_start,
    recommend_band,
)
from app.schemas import (
    RfAlertOut,
    RfApOut,
    RfCaptureIn,
    RfChannelBusy,
    RfChannelSnapshotOut,
    RfClientOut,
    RfClientPoint,
    RfHillSeries,
    RfHistoryPoint,
    RfOverviewOut,
    RfScanRequestOut,
    RfSparkPoint,
    WifiScanIn,
)

_CRITICAL_ALERTS = {
    "DEAUTHFLOOD",
    "DISCONFLOOD",
    "AUTHFLOOD",
    "APSPOOF",
    "EVILTWIN",
    "BSSIDSPOOF",
    "BSSTIMESTAMP",
    "CRYPTODROP",
    "KARMA",
    "AIRJACK",
    "WPSBRUTE",
}
_WARNING_ALERTS = {
    "WPA3DOWNGRADE",
    "DHCPCONFLICT",
    "DHCPROGUE",
    "ARPSPOOF",
}


def rf_alert_severity(alert_type: str) -> str:
    kind = (alert_type or "").strip().upper()
    if kind in _CRITICAL_ALERTS:
        return "critical"
    if kind in _WARNING_ALERTS:
        return "warning"
    return "info"

router = APIRouter(prefix="/api/v1")

_RANGES = "^(1h|6h|24h|7d)$"


def _monitor_or_404(db: Session, agent_id: UUID) -> Monitor:
    monitor = db.get(Monitor, agent_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Remote agent not found")
    return monitor


def _probed_text(names: list[str]) -> str:
    clean: list[str] = []
    for name in names:
        text = name.strip()[:64]
        if text and text not in clean:
            clean.append(text)
    return json.dumps(clean[:12])


def _probed_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _remember(db: Session, monitor_id: UUID, bssid: str, seen: datetime) -> tuple[ApInventory, bool]:
    key = bssid.lower()
    row = db.scalar(
        select(ApInventory).where(ApInventory.monitor_id == monitor_id, func.lower(ApInventory.bssid) == key)
    )
    if row is None:
        row = ApInventory(
            monitor_id=monitor_id,
            bssid=key,
            trusted=False,
            label="",
            first_seen=seen,
            last_seen=seen,
        )
        db.add(row)
        return row, True
    if seen < row.first_seen:
        row.first_seen = seen
    if seen > row.last_seen:
        row.last_seen = seen
    return row, False


@router.post("/monitors/wifi-scans")
def post_wifi_scans(
    body: WifiScanIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, int]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    scanned_at = body.scanned_at
    if scanned_at.tzinfo is None:
        scanned_at = scanned_at.replace(tzinfo=timezone.utc)
    survey = parse_iw_survey(body.survey)
    aps = parse_iw_scan(body.raw, noise_by_frequency(survey))
    stored = 0
    for ap in aps:
        bssid = ap["bssid"]
        exists = db.scalar(
            select(WifiObservation.id).where(
                WifiObservation.monitor_id == monitor.id,
                WifiObservation.bssid == bssid,
                WifiObservation.scanned_at == scanned_at,
            )
        )
        if exists is None:
            db.add(
                WifiObservation(
                    monitor_id=monitor.id,
                    scanned_at=scanned_at,
                    ssid=ap["ssid"][:64],
                    bssid=bssid,
                    vendor=ap["vendor"][:64],
                    band=ap["band"][:8],
                    channel=int(ap["channel"]),
                    channel_width=int(ap["channel_width"]),
                    rssi_dbm=float(ap["rssi_dbm"]),
                    noise_dbm=ap["noise_dbm"],
                    security=ap["security"][:32],
                    phy=ap["phy"][:8],
                    max_link_rate_mbps=int(ap["max_link_rate_mbps"]),
                )
            )
            stored += 1
        record, created = _remember(db, monitor.id, bssid, scanned_at)
        note_new_ap(db, monitor, record, ap["ssid"], created, scanned_at)
    for row in survey:
        band_name = str(row["band"])[:8]
        channel = int(row["channel"])
        exists = db.scalar(
            select(RfChannelStat.id).where(
                RfChannelStat.monitor_id == monitor.id,
                RfChannelStat.measured_at == scanned_at,
                RfChannelStat.band == band_name,
                RfChannelStat.channel == channel,
            )
        )
        if exists is None:
            db.add(
                RfChannelStat(
                    monitor_id=monitor.id,
                    measured_at=scanned_at,
                    band=band_name,
                    channel=channel,
                    noise_dbm=row["noise_dbm"],
                    busy_pct=row["busy_pct"],
                )
            )
    monitor.scan_requested_at = None
    db.commit()
    return {"stored": stored}


def _trusted_set(db: Session, monitor_id: UUID) -> set[str]:
    rows = db.scalars(
        select(ApInventory.bssid).where(ApInventory.monitor_id == monitor_id, ApInventory.trusted.is_(True))
    ).all()
    return {row.lower() for row in rows}


def _open_airwave_issue(db: Session, monitor: Monitor, alert_type: str, bssid: str, text: str, when: datetime) -> None:
    sev = rf_alert_severity(alert_type)
    if sev == "info":
        return
    if not bssid or bssid.lower() not in _trusted_set(db, monitor.id):
        return
    title = f"Airwave {alert_type} on {bssid.lower()}"[:255]
    existing = db.scalar(
        select(Issue.id).where(
            Issue.monitor_id == monitor.id,
            Issue.status == IssueStatus.open,
            Issue.root_cause_stage == "airwave",
            Issue.title == title,
        )
    )
    if existing is not None:
        return
    db.add(
        Issue(
            group_id=monitor.group_id,
            monitor_id=monitor.id,
            title=title,
            summary=(text or title)[:4000],
            severity=IssueSeverity.critical if sev == "critical" else IssueSeverity.warning,
            status=IssueStatus.open,
            root_cause_stage="airwave",
            opened_at=when,
        )
    )


@router.post("/monitors/rf-capture")
def post_rf_capture(
    body: RfCaptureIn,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
    x_monitor_name: Annotated[Optional[str], Header()] = None,
) -> dict[str, int]:
    monitor = _resolve_monitor_identity(db, authorization, x_monitor_name)
    captured = body.captured_at if body.captured_at.tzinfo else body.captured_at.replace(tzinfo=timezone.utc)
    stored = 0
    for ap in body.aps:
        if ap.band not in {"2.4", "5"}:
            continue
        bssid = ap.bssid.lower()
        row = db.scalar(
            select(WifiObservation).where(
                WifiObservation.monitor_id == monitor.id,
                WifiObservation.bssid == bssid,
                WifiObservation.scanned_at == captured,
            )
        )
        if row is None:
            db.add(
                WifiObservation(
                    monitor_id=monitor.id,
                    scanned_at=captured,
                    ssid=ap.ssid[:64],
                    bssid=bssid,
                    vendor="Unknown",
                    band=ap.band[:8],
                    channel=ap.channel,
                    channel_width=20,
                    rssi_dbm=ap.signal,
                    noise_dbm=None,
                    security="Unknown",
                    phy="a" if ap.band == "5" else "g",
                    max_link_rate_mbps=0,
                    client_count=max(ap.client_count, 0),
                )
            )
            stored += 1
        else:
            row.client_count = max(ap.client_count, 0)
        record, created = _remember(db, monitor.id, bssid, captured)
        note_new_ap(db, monitor, record, ap.ssid, created, captured)
    db.flush()
    trusted = _trusted_set(db, monitor.id)
    for client in body.clients:
        bssid = client.bssid.lower()
        if bssid not in trusted:
            continue
        mac = client.mac.lower()
        seen = datetime.fromtimestamp(client.last_seen, timezone.utc) if client.last_seen else captured
        opened = datetime.fromtimestamp(client.first_seen, timezone.utc) if client.first_seen else None
        exists = db.scalar(
            select(RfClient.id).where(
                RfClient.monitor_id == monitor.id,
                RfClient.observed_at == captured,
                RfClient.dedupe_key == mac,
            )
        )
        if exists is None:
            db.add(
                RfClient(
                    monitor_id=monitor.id,
                    observed_at=captured,
                    dedupe_key=mac,
                    mac=mac,
                    vendor=(client.vendor or "")[:64],
                    bssid=bssid,
                    signal=client.signal if client.signal else None,
                    signal_min=client.signal_min,
                    signal_max=client.signal_max,
                    last_seen=seen,
                    first_seen=opened,
                    randomized=client.randomized,
                    band=(client.band or "")[:8],
                    channel=client.channel,
                    packets=client.packets,
                    data_bytes=client.data_bytes,
                    retries=client.retries,
                    disconnects=client.disconnects,
                    probed_ssids=_probed_text(client.probed_ssids),
                )
            )
            stored += 1
    for channel in body.channels:
        if channel.band not in {"2.4", "5"}:
            continue
        stat = db.scalar(
            select(RfChannelStat).where(
                RfChannelStat.monitor_id == monitor.id,
                RfChannelStat.measured_at == captured,
                RfChannelStat.band == channel.band,
                RfChannelStat.channel == channel.channel,
            )
        )
        if stat is None:
            db.add(
                RfChannelStat(
                    monitor_id=monitor.id,
                    measured_at=captured,
                    band=channel.band,
                    channel=channel.channel,
                    airtime_pct=channel.airtime_pct,
                    busy_pct=channel.busy_pct,
                    packets=channel.packets,
                )
            )
            stored += 1
        else:
            stat.airtime_pct = channel.airtime_pct
            stat.busy_pct = channel.busy_pct
            stat.packets = channel.packets
    for alert in body.alerts:
        bssid = alert.bssid.lower()
        key = (alert.hash or f"{alert.type}:{bssid}:{alert.ts}")[:128]
        when = datetime.fromtimestamp(alert.ts, timezone.utc) if alert.ts else captured
        exists = db.scalar(
            select(RfAlert.id).where(
                RfAlert.monitor_id == monitor.id,
                RfAlert.observed_at == captured,
                RfAlert.dedupe_key == key,
            )
        )
        if exists is None:
            db.add(
                RfAlert(
                    monitor_id=monitor.id,
                    observed_at=captured,
                    dedupe_key=key,
                    alert_type=alert.type[:64],
                    bssid=bssid[:17],
                    text=alert.text[:4000],
                )
            )
            _open_airwave_issue(db, monitor, alert.type, bssid, alert.text, when)
            stored += 1
    monitor.scan_requested_at = None
    db.commit()
    return {"stored": stored}


def _latest_rows(db: Session, monitor_id: UUID) -> tuple[datetime | None, list[WifiObservation]]:
    latest = db.scalar(select(func.max(WifiObservation.scanned_at)).where(WifiObservation.monitor_id == monitor_id))
    if latest is None:
        return None, []
    rows = db.scalars(
        select(WifiObservation).where(
            WifiObservation.monitor_id == monitor_id,
            WifiObservation.scanned_at == latest,
        )
    ).all()
    return latest, list(rows)


def _as_ap(row: WifiObservation) -> dict:
    return {
        "bssid": row.bssid,
        "ssid": row.ssid,
        "hidden": row.ssid == "",
        "band": row.band,
        "channel": row.channel,
        "channel_width": row.channel_width,
        "rssi_dbm": row.rssi_dbm,
        "security": row.security,
        "vendor": row.vendor,
        "phy": row.phy,
    }


def _inventory_map(db: Session, monitor_id: UUID) -> dict[str, ApInventory]:
    rows = db.scalars(select(ApInventory).where(ApInventory.monitor_id == monitor_id)).all()
    return {row.bssid.lower(): row for row in rows}


def _ap_out(row: WifiObservation, inventory: ApInventory | None, latest: datetime) -> RfApOut:
    return RfApOut(
        bssid=row.bssid,
        ssid=row.ssid,
        hidden=row.ssid == "",
        band=row.band,
        channel=row.channel,
        channel_width=row.channel_width,
        rssi_dbm=row.rssi_dbm,
        security=row.security,
        vendor=row.vendor,
        phy=row.phy,
        first_seen=inventory.first_seen if inventory is not None else latest,
        last_seen=inventory.last_seen if inventory is not None else latest,
        trusted=bool(inventory.trusted) if inventory is not None else False,
        label=inventory.label if inventory is not None else "",
    )


@router.get("/rf/overview", response_model=RfOverviewOut)
def rf_overview(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    range_key: str = Query("1h", alias="range", pattern=_RANGES),
) -> RfOverviewOut:
    monitor = _monitor_or_404(db, agent_id)
    latest, rows = _latest_rows(db, monitor.id)
    advice = [recommend_band([_as_ap(row) for row in rows], band) for band in ("2.4", "5")]
    crowded = max(advice, key=lambda item: item["ap_count"])
    start = range_start(range_key)
    seconds = bucket_seconds(range_key)
    history = db.scalars(
        select(WifiObservation).where(
            WifiObservation.monitor_id == monitor.id,
            WifiObservation.scanned_at >= start,
        )
    ).all()
    counts: dict[datetime, dict[str, set[str]]] = {}
    for row in history:
        bucket = counts.setdefault(bucket_start(row.scanned_at, seconds), {"all": set(), "2.4": set(), "5": set()})
        bucket["all"].add(row.bssid)
        if row.band in bucket:
            bucket[row.band].add(row.bssid)
    sparkline = [
        RfSparkPoint(
            t=moment,
            ap_count=len(groups["all"]),
            count_24=len(groups["2.4"]),
            count_5=len(groups["5"]),
        )
        for moment, groups in sorted(counts.items())
    ]
    return RfOverviewOut(
        agent_id=monitor.id,
        agent_name=monitor.name,
        location_label=monitor.location_label,
        last_scan_at=latest,
        scan_requested=monitor.scan_requested_at is not None,
        bands=advice,
        headline=crowded["reason"],
        sparkline=sparkline,
        airwave_mode=_airwave_mode(db, monitor.id),
        alerts=_recent_alerts(db, monitor.id, start),
    )


@router.get("/rf/aps", response_model=list[RfApOut])
def rf_aps(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    band: str = "",
    security: str = "",
    hidden: Optional[bool] = None,
    q: str = "",
    sort: str = "rssi",
) -> list[RfApOut]:
    monitor = _monitor_or_404(db, agent_id)
    latest, rows = _latest_rows(db, monitor.id)
    if latest is None:
        return []
    inventory = _inventory_map(db, monitor.id)
    found = [_ap_out(row, inventory.get(row.bssid.lower()), latest) for row in rows]
    needle = q.strip().lower()
    if band:
        found = [ap for ap in found if ap.band == band]
    if security:
        found = [ap for ap in found if ap.security.lower() == security.lower()]
    if hidden is not None:
        found = [ap for ap in found if ap.hidden is hidden]
    if needle:
        found = [ap for ap in found if needle in ap.ssid.lower() or needle in ap.bssid]
    if sort == "ssid":
        found.sort(key=lambda ap: (ap.ssid.lower(), ap.bssid))
    elif sort == "channel":
        found.sort(key=lambda ap: (ap.channel, ap.bssid))
    elif sort == "last_seen":
        found.sort(key=lambda ap: ap.last_seen, reverse=True)
    else:
        found.sort(key=lambda ap: ap.rssi_dbm, reverse=True)
    return found


@router.get("/rf/aps/{bssid}/history", response_model=list[RfHistoryPoint])
def rf_ap_history(
    bssid: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    range_key: str = Query("1h", alias="range", pattern=_RANGES),
) -> list[RfHistoryPoint]:
    _monitor_or_404(db, agent_id)
    key = bssid.lower()
    seconds = bucket_seconds(range_key)
    rows = db.scalars(
        select(WifiObservation)
        .where(
            WifiObservation.monitor_id == agent_id,
            func.lower(WifiObservation.bssid) == key,
            WifiObservation.scanned_at >= range_start(range_key),
        )
        .order_by(WifiObservation.scanned_at)
    ).all()
    buckets: dict[datetime, list[WifiObservation]] = {}
    for row in rows:
        buckets.setdefault(bucket_start(row.scanned_at, seconds), []).append(row)
    points = []
    for moment, group in sorted(buckets.items()):
        last = group[-1]
        rssi = sum(item.rssi_dbm for item in group) / len(group)
        points.append(RfHistoryPoint(t=moment, rssi_dbm=round(rssi, 1), channel=last.channel))
    return points


def _busy_channels(db: Session, monitor_id: UUID, band: str, latest: datetime | None) -> list[RfChannelBusy]:
    if latest is None:
        return []
    moment = db.scalar(
        select(func.max(RfChannelStat.measured_at)).where(
            RfChannelStat.monitor_id == monitor_id,
            RfChannelStat.band == band,
            RfChannelStat.measured_at <= latest,
        )
    )
    if moment is None:
        return []
    rows = db.scalars(
        select(RfChannelStat)
        .where(
            RfChannelStat.monitor_id == monitor_id,
            RfChannelStat.band == band,
            RfChannelStat.measured_at == moment,
        )
        .order_by(RfChannelStat.channel)
    ).all()
    return [
        RfChannelBusy(
            channel=row.channel,
            noise_dbm=row.noise_dbm,
            busy_pct=row.busy_pct,
            airtime_pct=row.airtime_pct,
            packets=row.packets,
        )
        for row in rows
        if row.busy_pct is not None or row.noise_dbm is not None
    ]


@router.get("/rf/channel-snapshot", response_model=RfChannelSnapshotOut)
def rf_channel_snapshot(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    band: str = Query("2.4", pattern="^(2\\.4|5)$"),
) -> RfChannelSnapshotOut:
    monitor = _monitor_or_404(db, agent_id)
    _latest, rows = _latest_rows(db, monitor.id)
    group = [_as_ap(row) for row in rows if row.band == band]
    advice = recommend_band(group, band)
    inventory = _inventory_map(db, monitor.id)
    series = []
    for ap in sorted(group, key=lambda item: item["rssi_dbm"], reverse=True):
        record = inventory.get(ap["bssid"].lower())
        series.append(
            RfHillSeries(
                bssid=ap["bssid"],
                ssid=ap["ssid"] or "Hidden network",
                hidden=ap["hidden"],
                trusted=bool(record.trusted) if record is not None else False,
                channel=ap["channel"],
                channel_width=ap["channel_width"],
                rssi_dbm=ap["rssi_dbm"],
                security=ap["security"],
                points=hill_points(ap),
            )
        )
    return RfChannelSnapshotOut(
        band=band,
        recommended_channel=advice["recommended_channel"],
        reason=advice["reason"],
        series=series,
        channels=_busy_channels(db, monitor.id, band, _latest),
    )


def _airwave_mode(db: Session, monitor_id: UUID) -> str:
    row = db.scalar(
        select(MonitorHealth)
        .where(MonitorHealth.monitor_id == monitor_id)
        .order_by(MonitorHealth.reported_at.desc())
        .limit(1)
    )
    if row is None or row.airwave_mode not in {"kismet", "scan", "off"}:
        return "off"
    return row.airwave_mode


def _recent_alerts(db: Session, monitor_id: UUID, start: datetime, limit: int = 8) -> list[RfAlertOut]:
    rows = db.scalars(
        select(RfAlert)
        .where(RfAlert.monitor_id == monitor_id, RfAlert.observed_at >= start)
        .order_by(RfAlert.observed_at.desc())
        .limit(limit)
    ).all()
    return [
        RfAlertOut(
            alert_type=row.alert_type,
            bssid=row.bssid,
            text=row.text,
            observed_at=row.observed_at,
            severity=rf_alert_severity(row.alert_type),
        )
        for row in rows
    ]


@router.get("/rf/alerts", response_model=list[RfAlertOut])
def rf_alerts(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    range_key: str = Query("1h", alias="range", pattern=_RANGES),
) -> list[RfAlertOut]:
    _monitor_or_404(db, agent_id)
    return _recent_alerts(db, agent_id, range_start(range_key), limit=50)


@router.get("/rf/clients", response_model=list[RfClientOut])
def rf_clients(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    agent_id: UUID = Query(...),
    range_key: str = Query("1h", alias="range", pattern=_RANGES),
) -> list[RfClientOut]:
    _monitor_or_404(db, agent_id)
    trusted = _trusted_set(db, agent_id)
    rows = db.scalars(
        select(RfClient)
        .where(RfClient.monitor_id == agent_id, RfClient.observed_at >= range_start(range_key))
        .order_by(RfClient.observed_at)
    ).all()
    bssids = {row.bssid.lower() for row in rows}
    networks: dict[str, str] = {}
    if bssids:
        named = db.execute(
            select(WifiObservation.bssid, WifiObservation.ssid)
            .where(
                WifiObservation.monitor_id == agent_id,
                WifiObservation.bssid.in_(bssids),
                WifiObservation.ssid != "",
            )
            .distinct(WifiObservation.bssid)
            .order_by(WifiObservation.bssid, WifiObservation.scanned_at.desc())
        ).all()
        networks = {bssid.lower(): ssid for bssid, ssid in named}
    grouped: dict[str, list[RfClient]] = {}
    for row in rows:
        if row.bssid.lower() not in trusted:
            continue
        grouped.setdefault(row.mac, []).append(row)
    found = []
    for mac, samples in grouped.items():
        points = []
        previous = ""
        for sample in samples:
            if not sample.signal:
                continue
            roam = bool(previous) and sample.bssid != previous
            points.append(RfClientPoint(t=sample.observed_at, rssi_dbm=sample.signal, bssid=sample.bssid, roam=roam))
            previous = sample.bssid
        last = samples[-1]
        vendor = next((sample.vendor for sample in reversed(samples) if sample.vendor), "")
        heard = [sample.signal for sample in samples if sample.signal]
        lows = [sample.signal_min for sample in samples if sample.signal_min is not None]
        highs = [sample.signal_max for sample in samples if sample.signal_max is not None]
        opened = [sample.first_seen for sample in samples if sample.first_seen is not None]
        probed: list[str] = []
        for sample in samples:
            for name in _probed_list(sample.probed_ssids):
                if name not in probed:
                    probed.append(name)
        found.append(
            RfClientOut(
                mac=mac,
                bssid=last.bssid,
                ssid=networks.get(last.bssid.lower(), ""),
                vendor=vendor,
                randomized=last.randomized,
                signal=heard[-1] if heard else None,
                signal_min=min(lows) if lows else None,
                signal_max=max(highs) if highs else None,
                last_seen=last.last_seen,
                first_seen=min(opened) if opened else None,
                band=next((sample.band for sample in reversed(samples) if sample.band), ""),
                channel=next((sample.channel for sample in reversed(samples) if sample.channel), None),
                packets=next((sample.packets for sample in reversed(samples) if sample.packets), None),
                data_bytes=next((sample.data_bytes for sample in reversed(samples) if sample.data_bytes), None),
                retries=next((sample.retries for sample in reversed(samples) if sample.retries), None),
                disconnects=next((sample.disconnects for sample in reversed(samples) if sample.disconnects), None),
                probed_ssids=probed,
                points=points,
            )
        )
    found.sort(key=lambda item: item.signal if item.signal is not None else -999, reverse=True)
    return found


@router.post("/rf/scan-now", response_model=RfScanRequestOut)
def rf_scan_now(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
    agent_id: UUID = Query(...),
) -> RfScanRequestOut:
    monitor = _monitor_or_404(db, agent_id)
    if monitor.scan_requested_at is None:
        monitor.scan_requested_at = datetime.now(timezone.utc)
    db.commit()
    return RfScanRequestOut(
        agent_id=monitor.id,
        agent_name=monitor.name,
        scan_requested_at=monitor.scan_requested_at,
    )


def _set_trust(db: Session, agent_id: UUID, bssid: str, trusted: bool) -> RfApOut:
    monitor = _monitor_or_404(db, agent_id)
    key = bssid.lower()
    latest, rows = _latest_rows(db, monitor.id)
    row = next((item for item in rows if item.bssid.lower() == key), None)
    if row is None or latest is None:
        raise HTTPException(status_code=404, detail="Access point not found")
    record, _created = _remember(db, monitor.id, key, latest)
    record.trusted = trusted
    record.trust_source = "manual"
    db.commit()
    db.refresh(record)
    return _ap_out(row, record, latest)


@router.post("/rf/aps/{bssid}/trust", response_model=RfApOut)
def trust_ap(
    bssid: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
    agent_id: UUID = Query(...),
) -> RfApOut:
    return _set_trust(db, agent_id, bssid, True)


@router.delete("/rf/aps/{bssid}/trust", response_model=RfApOut)
def untrust_ap(
    bssid: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_csrf)],
    agent_id: UUID = Query(...),
) -> RfApOut:
    return _set_trust(db, agent_id, bssid, False)
