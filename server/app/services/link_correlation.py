"""Correlate access-plane health (public web) vs measurement-tunnel health (WireGuard / RTNP).

Public web HTTP checks leave through the NIC to the open internet. Path checks (icmp-path /
http-path) target the WireGuard peer (``10.7.x.1``) and need the measurement tunnel. Synthetic
app flows also use the tunnel. Mixing those planes made a dead wg1 look like "Wi-Fi down" or
"internet down".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Monitor, Network, NetworkKind, ResultStatus, Test, TestResult

LinkName = Literal["wired", "wireless"]
PlaneStatus = Literal["access_ok", "tunnel_down", "link_down", "unknown"]

TUNNEL_STALE = timedelta(minutes=10)
ACCESS_WINDOW = timedelta(minutes=10)

# KPI kinds that travel over the measurement tunnel / RTNP, not the open internet.
TUNNEL_BACKED_KINDS = frozenset({"voice", "video", "apps"})
# KPI kinds that prove the access plane on that link.
ACCESS_KINDS = frozenset({"internet", "web"})


@dataclass(frozen=True)
class LinkCorrelation:
    link: LinkName
    status: PlaneStatus
    access_ok: bool
    tunnel_configured: bool
    tunnel_stale: bool
    detail: str = ""


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def handshake_for(monitor: Monitor, link: LinkName) -> Optional[datetime]:
    if link == "wireless":
        return _aware(monitor.last_wireless_handshake_at)
    return _aware(monitor.last_handshake_at)


def tunnel_configured(monitor: Monitor, link: LinkName) -> bool:
    if link == "wireless":
        return bool(monitor.wg_wireless_address)
    return bool(monitor.wg_address)


def tunnel_is_stale(monitor: Monitor, link: LinkName, now: datetime, stale_after: timedelta = TUNNEL_STALE) -> bool:
    """True when this link's tunnel is configured and the last handshake is missing or too old."""
    if not tunnel_configured(monitor, link):
        return False
    stamp = handshake_for(monitor, link)
    if stamp is None:
        return True
    return now - stamp > stale_after


def network_kind(link: LinkName) -> NetworkKind:
    return NetworkKind.wifi if link == "wireless" else NetworkKind.wired


def access_ok_recent(
    db: Session,
    monitor_id: UUID,
    link: LinkName,
    now: datetime,
    window: timedelta = ACCESS_WINDOW,
) -> bool:
    """True if a recent public web HTTP check on this link succeeded (home internet up).

    Path checks are excluded: they target the WireGuard peer and only prove the tunnel.
    """
    since = now - window
    kind = network_kind(link)
    return (
        db.scalar(
            select(TestResult.id)
            .join(Test, TestResult.test_id == Test.id)
            .join(Network, Test.network_id == Network.id)
            .where(
                Test.monitor_id == monitor_id,
                Network.kind == kind,
                Test.slug.startswith("http-"),
                Test.slug != "http-path",
                TestResult.status == ResultStatus.ok,
                TestResult.started_at >= since,
            )
            .limit(1)
        )
        is not None
    )


def correlate_link(
    monitor: Monitor,
    link: LinkName,
    *,
    access_ok: bool,
    now: datetime,
    stale_after: timedelta = TUNNEL_STALE,
) -> LinkCorrelation:
    configured = tunnel_configured(monitor, link)
    stale = tunnel_is_stale(monitor, link, now, stale_after=stale_after) if configured else False
    if access_ok and stale:
        label = "Wi-Fi" if link == "wireless" else "Wired"
        return LinkCorrelation(
            link=link,
            status="tunnel_down",
            access_ok=True,
            tunnel_configured=configured,
            tunnel_stale=True,
            detail=f"The {label} measurement tunnel to the server is down while public web checks on that link still succeed.",
        )
    if access_ok:
        return LinkCorrelation(
            link=link,
            status="access_ok",
            access_ok=True,
            tunnel_configured=configured,
            tunnel_stale=stale,
        )
    if not access_ok and configured and stale:
        # Access also bad: prefer link/ISP over blaming only the tunnel.
        return LinkCorrelation(
            link=link,
            status="link_down",
            access_ok=False,
            tunnel_configured=configured,
            tunnel_stale=stale,
            detail="Public web checks on this link are failing.",
        )
    if not access_ok:
        return LinkCorrelation(
            link=link,
            status="link_down",
            access_ok=False,
            tunnel_configured=configured,
            tunnel_stale=stale,
            detail="Public web checks on this link are failing.",
        )
    return LinkCorrelation(link=link, status="unknown", access_ok=access_ok, tunnel_configured=configured, tunnel_stale=stale)


def access_met_target(links: dict, link: LinkName, targets: dict[str, float]) -> bool:
    """Whether internet and web on this link meet their KPI targets (or have no samples)."""
    for kind in ACCESS_KINDS:
        avail = links.get(kind, {}).get(link, {}).get("availability")
        if avail is None:
            continue
        if avail < targets.get(kind, 99.9):
            return False
    # Need at least one access-plane signal to call the link healthy.
    return any(links.get(kind, {}).get(link) is not None for kind in ACCESS_KINDS)


def tunnel_backed_only_missed(missed: list[str]) -> bool:
    """True when every missed KPI kind is tunnel-backed (voice/video/apps/loss from those)."""
    if not missed:
        return False
    return all(item in TUNNEL_BACKED_KINDS or item == "loss" for item in missed)


def diagnose_from_links(
    links: dict[str, dict[str, dict]],
    targets: dict[str, float],
    *,
    wireless_tunnel_stale: bool = False,
    wired_tunnel_stale: bool = False,
) -> Optional[dict[str, str]]:
    """KPI / period diagnosis using access vs tunnel planes. Shared with alerts narrative."""

    def _link_missed(lnk: str) -> list[str]:
        missed_list: list[str] = []
        for k in ("internet", "voice", "video", "web"):
            avail = links.get(k, {}).get(lnk, {}).get("availability")
            if avail is not None and avail < targets.get(k, 99.9):
                missed_list.append(k)
        for k in ("voice", "video"):
            loss = links.get(k, {}).get(lnk, {}).get("loss_avg")
            if loss is not None and loss > targets["loss_pct"] and "loss" not in missed_list:
                missed_list.append("loss")
        return missed_list

    wired_missed = _link_missed("wired")
    wifi_missed = _link_missed("wireless")
    has_both = any("wired" in links.get(k, {}) and "wireless" in links.get(k, {}) for k in ("internet", "voice", "video", "web"))

    wifi_access_ok = access_met_target(links, "wireless", targets)
    wired_access_ok = access_met_target(links, "wired", targets)

    # Tunnel-only: access OK, only voice/video (tunnel-backed) missed — or handshake was stale.
    if has_both and wifi_missed and not wired_missed and wifi_access_ok and (
        tunnel_backed_only_missed(wifi_missed) or wireless_tunnel_stale
    ):
        return {
            "cause": "tunnel",
            "title": "Wi-Fi measurement tunnel or receiver issue",
            "summary": (
                "Path and web checks on Wi-Fi met target while application checks that use the Wi-Fi "
                "WireGuard tunnel dipped. Home Wi-Fi and internet access were up; the measurement tunnel "
                "or synthetic receiver was not."
            ),
        }
    if has_both and wired_missed and not wifi_missed and wired_access_ok and (
        tunnel_backed_only_missed(wired_missed) or wired_tunnel_stale
    ):
        return {
            "cause": "tunnel",
            "title": "Wired measurement tunnel or receiver issue",
            "summary": (
                "Path and web checks on Wired met target while application checks that use the Wired "
                "WireGuard tunnel dipped. The local Ethernet path and internet access were up; the "
                "measurement tunnel or synthetic receiver was not."
            ),
        }

    if has_both and wifi_missed and not wired_missed:
        return {
            "cause": "wifi",
            "title": "Local Wi-Fi network degradation",
            "summary": "All service targets were met on Wired Ethernet; packet loss or availability dips occurred only on the Wi-Fi link.",
        }
    if has_both and wired_missed and not wifi_missed:
        return {
            "cause": "wired",
            "title": "Local Wired Ethernet degradation",
            "summary": "All service targets were met on Wi-Fi; packet loss or availability dips occurred only on the Wired Ethernet link.",
        }
    if wired_missed and wifi_missed:
        w_inet = links.get("internet", {}).get("wired", {}).get("availability")
        f_inet = links.get("internet", {}).get("wireless", {}).get("availability")
        w_web = links.get("web", {}).get("wired", {}).get("availability")
        f_web = links.get("web", {}).get("wireless", {}).get("availability")
        if (w_inet is not None and w_inet < targets["internet"]) and (f_inet is not None and f_inet < targets["internet"]):
            if (w_web is None or w_web >= targets["web"]) and (f_web is None or f_web >= targets["web"]) and (w_web is not None or f_web is not None):
                return {
                    "cause": "server",
                    "title": "Server path or receiver issue",
                    "summary": "External Web checks met target on both links while checks to the HomeNetWatch server dipped.",
                }
            return {
                "cause": "wan",
                "title": "Upstream ISP, remote agent, or WAN outage",
                "summary": "Checks across both Wired and Wi-Fi dipped during the period, indicating an upstream ISP or connectivity outage rather than individual application faults.",
            }
    return None
