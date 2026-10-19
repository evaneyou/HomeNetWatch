"""Trust a same-SSID access point when it shares an OUI with the radio the remote agent is on."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApInventory, Issue, IssueSeverity, IssueStatus, Monitor, WifiObservation

MESH_LABEL = "mesh node"


def mac_oui(mac: str) -> str:
    parts = (mac or "").lower().split(":")
    if len(parts) < 3 or any(len(part) != 2 for part in parts[:3]):
        return ""
    return ":".join(parts[:3])


def matches_link(monitor: Monitor, ssid: str) -> bool:
    joined = (monitor.link_ssid or "").strip()
    return bool(joined) and ssid[:64] == joined


def remember_association(
    db: Session,
    monitor: Monitor,
    ssid: str | None,
    bssid: str | None,
    *,
    ssid_set: bool,
    bssid_set: bool,
) -> None:
    """Store the link the remote agent reported. The first time that link is known, finish pending auto-trust."""
    previous_ssid = (monitor.link_ssid or "").strip()
    previous_bssid = (monitor.link_bssid or "").strip()
    if ssid_set:
        cleaned = (ssid or "").strip()[:64]
        monitor.link_ssid = cleaned or None
    if bssid_set:
        cleaned = (bssid or "").strip().lower()[:17]
        monitor.link_bssid = cleaned or None
    had_link = bool(previous_ssid and previous_bssid)
    has_link = bool((monitor.link_ssid or "").strip() and (monitor.link_bssid or "").strip())
    if has_link and not had_link:
        _trust_pending(db, monitor)


def note_new_ap(db: Session, monitor: Monitor, record: ApInventory, ssid: str, created: bool, seen: datetime) -> None:
    """Decide trust once, when the inventory row is created."""
    if not created:
        return
    _decide(db, monitor, record, ssid, seen)


def _trust_pending(db: Session, monitor: Monitor) -> None:
    rows = db.scalars(
        select(ApInventory).where(
            ApInventory.monitor_id == monitor.id,
            ApInventory.trusted.is_(False),
            ApInventory.trust_source == "auto",
        )
    ).all()
    for record in rows:
        _decide(db, monitor, record, _latest_ssid(db, monitor.id, record.bssid), record.last_seen)


def _latest_ssid(db: Session, monitor_id, bssid: str) -> str:
    ssid = db.scalar(
        select(WifiObservation.ssid)
        .where(
            WifiObservation.monitor_id == monitor_id,
            WifiObservation.bssid == bssid.lower(),
        )
        .order_by(WifiObservation.scanned_at.desc())
        .limit(1)
    )
    return ssid or ""


def _decide(db: Session, monitor: Monitor, record: ApInventory, ssid: str, seen: datetime) -> None:
    if record.trust_source == "manual" or not matches_link(monitor, ssid):
        return
    associated = (monitor.link_bssid or "").lower()
    if not associated:
        record.trusted = False
        record.trust_source = "auto"
        return
    if mac_oui(record.bssid) and mac_oui(record.bssid) == mac_oui(associated):
        record.trusted = True
        record.trust_source = "auto"
        record.label = MESH_LABEL
        now_utc = datetime.now(timezone.utc)
        for issue in db.scalars(
            select(Issue).where(
                Issue.monitor_id == monitor.id,
                Issue.status == IssueStatus.open,
                Issue.root_cause_stage == "airwave",
                Issue.title.like(f"Possible evil twin % on {record.bssid.lower()}"),
            )
        ):
            issue.status = IssueStatus.resolved
            issue.resolved_at = now_utc
        return
    record.trusted = False
    record.trust_source = "auto"
    _open_evil_twin(db, monitor, ssid.strip()[:64], record.bssid, seen)


def _open_evil_twin(db: Session, monitor: Monitor, ssid: str, bssid: str, when: datetime) -> None:
    title = f"Possible evil twin {ssid} on {bssid.lower()}"[:255]
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
            summary=title,
            severity=IssueSeverity.warning,
            status=IssueStatus.open,
            root_cause_stage="airwave",
            opened_at=when,
        )
    )
