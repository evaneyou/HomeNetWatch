"""Group a traceroute into networks and compare it with an announced AS path.

Enrichment (reverse DNS, ASN, IXP, RPKI) is optional. Missing fields stay empty
so a trace still renders when those lookups are unavailable.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlsplit

WARN_STEP_MS = 15

_PRIVATE = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def clean_trace_target(raw: str) -> str | None:
    """A host name or public IP address from what someone typed, or None if it is not one.

    A pasted URL gives its host. Private, loopback and link-local addresses are refused:
    the agent sits inside the home network, and a trace should not be a way to probe it.
    """
    text = raw.strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    else:
        text = text.split("/")[0].split("?")[0]
        if text.count(":") == 1:
            text = text.split(":")[0]
    text = text.strip("[]").rstrip(".")
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text if _HOSTNAME.fullmatch(text) and text != "localhost" else None
    if not address.is_global or address.is_multicast:
        return None
    return str(address)


def resolved_public_target(raw: str) -> tuple[str, str] | None:
    """The typed target and the address the agent should trace, after this server has resolved a name.

    A public-looking name can still point at an address inside the house, and it can change its answer between
    two lookups (DNS rebinding). So the agent is never given the name: it gets the public IPv4 address checked
    here, and the name is kept only to show in the dashboard. The agent traces over IPv4, so a name with no
    public IPv4 address is refused.
    """
    target = clean_trace_target(raw)
    if target is None:
        return None
    try:
        ipaddress.ip_address(target)
    except ValueError:
        addresses = _public_addresses(target)
        ipv4 = [address for address in addresses or [] if address.version == 4]
        if not ipv4:
            return None
        return target, str(ipv4[0])
    return target, target


def _public_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address] | None:
    """Every address the name resolves to, or None if it doesn't resolve or any answer is not public."""
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(3)
    try:
        try:
            answers = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return None
    finally:
        socket.setdefaulttimeout(previous)
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in answers:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if not address.is_global or address.is_multicast:
            return None
        if address not in found:
            found.append(address)
    return found or None


_SILENT_HOSTS = {"", "?", "???", "*", "***"}

_ROLE_LABEL = {
    "home": "Your network",
    "isp": "Your ISP",
    "ixp": "Exchange point",
    "transit": "Transit",
    "unknown": "Hidden by router",
    "destination": "Destination",
}

# The first hop of a transit or destination group sits on the interconnect
# into that network, so its address may belong to the neighbor.


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def is_private(value: str | None) -> bool:
    parsed = _ip(value)
    if not isinstance(parsed, ipaddress.IPv4Address):
        return False
    return any(parsed in network for network in _PRIVATE)


def _silent(raw: dict[str, Any], host: str, loss: float | None, avg: float | None) -> bool:
    if raw.get("silent") is True:
        return True
    if host in _SILENT_HOSTS:
        return True
    return loss is not None and loss >= 100 and (avg is None or avg <= 0)


def normalize_hops(raw_hops: list[Any]) -> list[dict[str, Any]]:
    hops: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_hops, start=1):
        if not isinstance(raw, dict):
            continue
        host = str(raw.get("ip") if raw.get("ip") is not None else raw.get("host") or "").strip()
        loss = _number(raw.get("loss_pct"))
        avg = _number(raw.get("avg_ms"))
        silent = _silent(raw, host, loss, avg)
        parsed = _ip(host)
        rdns = raw.get("rdns")
        ip = None if silent or parsed is None else host
        if rdns is None and not silent and parsed is None and host:
            rdns = host
        asn = raw.get("asn")
        hops.append(
            {
                "ttl": int(raw.get("ttl") or raw.get("hop") or index),
                "ip": ip,
                "rdns": None if rdns in (None, "") else str(rdns),
                "silent": silent,
                "loss_pct": 100.0 if silent and loss is None else loss,
                "best_ms": None if silent else _number(raw.get("best_ms")),
                "avg_ms": None if silent else avg,
                "worst_ms": None if silent else _number(raw.get("worst_ms")),
                "asn": int(asn) if isinstance(asn, int) and not isinstance(asn, bool) else None,
                "as_name": raw.get("as_name") or raw.get("org"),
                "prefix": raw.get("prefix"),
                "registry": raw.get("registry") or raw.get("reg"),
                "country": raw.get("country"),
                "is_ixp": bool(raw.get("is_ixp") or raw.get("role") == "ixp"),
                "ixp_name": raw.get("ixp_name") or (raw.get("name") if raw.get("role") == "ixp" else None),
            }
        )
    return hops


def _first_public_asn(hops: list[dict[str, Any]]) -> int | None:
    for hop in hops:
        if hop["silent"] or hop["is_ixp"] or is_private(hop["ip"]):
            continue
        if hop["asn"] is not None:
            return hop["asn"]
    return None


def _destination_asn(hops: list[dict[str, Any]]) -> int | None:
    for hop in reversed(hops):
        if not hop["silent"] and hop["asn"] is not None and not is_private(hop["ip"]) and not hop["is_ixp"]:
            return hop["asn"]
    return None


def assign_role(hop: dict[str, Any], *, first_public_asn: int | None, dest_asn: int | None, is_last: bool) -> str:
    """Spec section 4. Unenriched public hops fall into the ISP segment, and the last hop is the destination."""
    if hop["silent"]:
        return "unknown"
    if is_private(hop["ip"]):
        return "home"
    if hop["is_ixp"]:
        return "ixp"
    asn = hop["asn"]
    if asn is not None and first_public_asn is not None and asn == first_public_asn and asn != dest_asn:
        return "isp"
    if dest_asn is not None and asn == dest_asn:
        return "destination"
    if asn is not None and first_public_asn is not None and asn == first_public_asn:
        return "isp"
    if asn is not None:
        return "transit"
    if is_last:
        return "destination"
    return "isp"


def _group_name(role: str, hop: dict[str, Any], isp_name: str | None, target_name: str | None) -> str:
    if role == "home":
        return "Home network"
    if role == "unknown":
        return "No reply"
    if role == "ixp":
        return str(hop.get("ixp_name") or hop.get("as_name") or "Exchange point")
    if role == "isp":
        return str(hop.get("as_name") or isp_name or "Your ISP")
    if role == "destination":
        return str(hop.get("as_name") or target_name or "Destination")
    return str(hop.get("as_name") or "Transit")


def _asn_label(role: str, hop: dict[str, Any]) -> str:
    if role == "home":
        return "LAN"
    if role == "ixp":
        return "IXP"
    if role == "unknown" or hop.get("asn") is None:
        return "AS ?"
    return f"AS{hop['asn']}"


def _network_key(hop: dict[str, Any]) -> str:
    role = hop["role"]
    if role == "unknown":
        return f"unknown:{hop['ttl']}"
    if role == "ixp":
        return "ixp:" + str(hop.get("ixp_name") or "")
    if role == "home":
        return "home"
    if hop.get("asn") is not None:
        return f"{role}:{hop['asn']}"
    return role


def _fmt_as(hop: dict[str, Any] | None, fallback: str) -> str:
    if hop is None:
        return fallback
    if hop.get("asn") is not None:
        return f"AS{hop['asn']}"
    return fallback


def build_trace(
    raw_hops: list[Any],
    *,
    isp_name: str | None = None,
    target_name: str | None = None,
    announced: list[int] | None = None,
    as_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    hops = normalize_hops(raw_hops)
    last_ttl = hops[-1]["ttl"] if hops else None
    first_asn = _first_public_asn(hops)
    dest_asn = _destination_asn(hops)
    for hop in hops:
        hop["role"] = assign_role(
            hop,
            first_public_asn=first_asn,
            dest_asn=dest_asn,
            is_last=hop["ttl"] == last_ttl,
        )
        hop["inferred"] = False

    groups: list[dict[str, Any]] = []
    for hop in hops:
        key = _network_key(hop)
        if groups and groups[-1]["key"] == key:
            groups[-1]["hops"].append(hop)
        else:
            groups.append({"key": key, "role": hop["role"], "hops": [hop]})

    for group in groups:
        if group["role"] in {"transit", "destination"}:
            group["hops"][0]["inferred"] = True
        group["name"] = _group_name(group["role"], group["hops"][0], isp_name, target_name)
        group["asn"] = group["hops"][0].get("asn") if group["role"] not in {"home", "unknown", "ixp"} else None
        for hop in group["hops"]:
            hop["name"] = group["name"]
            hop["role_label"] = _ROLE_LABEL[group["role"]]
            hop["asn_label"] = _asn_label(group["role"], hop)

    prev_reply: dict[str, Any] | None = None
    for group in groups:
        replying = [hop for hop in group["hops"] if not hop["silent"] and hop["avg_ms"] is not None]
        first = replying[0] if replying else None
        group["delta_in_ms"] = None
        if first is not None and prev_reply is not None:
            group["delta_in_ms"] = round(first["avg_ms"] - prev_reply["avg_ms"], 1)
        group["prev_ttl"] = prev_reply["ttl"] if prev_reply else None
        group["prev_asn"] = prev_reply.get("asn") if prev_reply else None
        group["prev_name"] = prev_reply.get("name") if prev_reply else None
        if replying:
            prev_reply = replying[-1]

    warn_ttls: list[int] = []
    biggest: dict[str, Any] | None = None
    for group in groups:
        delta = group["delta_in_ms"]
        if delta is None:
            continue
        if delta >= WARN_STEP_MS:
            first = next(hop for hop in group["hops"] if not hop["silent"])
            warn_ttls.append(first["ttl"])
        if biggest is None or delta > biggest["delta_in_ms"]:
            biggest = group

    observed: list[dict[str, Any]] = []
    for group in groups:
        if group["role"] in {"home", "unknown"}:
            continue
        hop = group["hops"][0]
        if group["role"] == "ixp":
            observed.append({"type": "ixp", "asn": None, "name": group["name"], "role": "ixp", "label": "IXP"})
        else:
            observed.append(
                {
                    "type": "as",
                    "asn": hop.get("asn"),
                    "name": group["name"],
                    "role": group["role"],
                    "label": _asn_label(group["role"], hop),
                }
            )

    observed_asns = [item["asn"] for item in observed if item["type"] == "as"]
    path_match: bool | None = None
    announced_items: list[dict[str, Any]] = []
    if announced is not None:
        path_match = observed_asns == list(announced)
        names = {hop["asn"]: hop["name"] for hop in hops if hop.get("asn") is not None}
        roles = {item["asn"]: item["role"] for item in observed if item["type"] == "as" and item["asn"] is not None}
        for asn in announced:
            announced_items.append(
                {
                    "type": "as",
                    "asn": asn,
                    "name": names.get(asn) or (as_names or {}).get(asn) or f"AS{asn}",
                    "role": roles.get(asn) or "transit",
                    "label": f"AS{asn}",
                }
            )

    insight = ""
    if biggest and biggest.get("prev_ttl") is not None:
        first = next(hop for hop in biggest["hops"] if not hop["silent"])
        between = [
            hop["ttl"]
            for hop in hops
            if hop["silent"] and biggest["prev_ttl"] < hop["ttl"] < first["ttl"]
        ]
        left = _fmt_as({"asn": biggest["prev_asn"], "name": biggest["prev_name"]}, str(biggest["prev_name"] or "the previous hop"))
        right = _fmt_as(first, first["name"])
        insight = (
            f"Latency steps up {biggest['delta_in_ms']:.1f} ms between hop {biggest['prev_ttl']} ({left}) "
            f"and hop {first['ttl']} ({right})."
        )
        if between:
            label = ", ".join(str(ttl) for ttl in between)
            noun = "Hop" if len(between) == 1 else "Hops"
            verb = "stays" if len(between) == 1 else "stay"
            insight += f" {noun} {label} {verb} silent, so the exact handoff point is hidden."

    replying = [hop for hop in hops if not hop["silent"]]
    end = hops[-1] if hops else None
    asn_count = len({hop["asn"] for hop in replying if hop.get("asn") is not None})
    ixp_count = sum(1 for group in groups if group["role"] == "ixp")
    network_groups = [group for group in groups if group["role"] not in {"home", "unknown"}]
    network_count = asn_count or len(network_groups)
    focus = None
    if biggest is not None:
        focus = next(hop["ttl"] for hop in biggest["hops"] if not hop["silent"])
    elif hops:
        focus = hops[-1]["ttl"]

    public_groups = [group for group in groups if group["role"] not in {"home", "unknown"}]
    return {
        "hops": [
            {
                "ttl": hop["ttl"],
                "ip": hop["ip"],
                "rdns": hop["rdns"],
                "silent": hop["silent"],
                "loss_pct": hop["loss_pct"],
                "best_ms": hop["best_ms"],
                "avg_ms": hop["avg_ms"],
                "worst_ms": hop["worst_ms"],
                "asn": hop["asn"],
                "as_name": hop["as_name"],
                "prefix": hop["prefix"],
                "registry": hop["registry"],
                "country": hop["country"],
                "role": hop["role"],
                "name": hop["name"],
                "role_label": hop["role_label"],
                "asn_label": hop["asn_label"],
                "inferred": hop["inferred"],
            }
            for hop in hops
        ],
        "groups": [
            {
                "role": group["role"],
                "asn": group["asn"],
                "name": group["name"],
                "asn_label": _asn_label(group["role"], group["hops"][0]),
                "role_label": _ROLE_LABEL[group["role"]],
                "hop_ttls": [hop["ttl"] for hop in group["hops"]],
                "delta_in_ms": group["delta_in_ms"],
            }
            for group in groups
        ],
        "warn_ttls": warn_ttls,
        "focus_ttl": focus,
        "insight": insight,
        "stats": {
            "hop_count": len(hops),
            "silent_count": len(hops) - len(replying),
            "network_count": network_count,
            "asn_count": asn_count,
            "ixp_count": ixp_count,
            "end_to_end_ms": None if end is None or end["silent"] else end["avg_ms"],
            "loss_pct": None if end is None else end["loss_pct"],
            "biggest_step_ms": None if biggest is None else biggest["delta_in_ms"],
            "biggest_step_name": None if biggest is None else biggest["name"],
        },
        "as_path_observed": observed,
        "bgp": {
            "origin_asn": dest_asn,
            "prefix": next((hop["prefix"] for hop in reversed(hops) if hop.get("prefix")), None),
            "rpki": None,
            "as_path_announced": announced_items,
            "path_match": path_match,
            # True when the announced path starts at the agent's own provider, so both paths begin in the same place.
            "via_isp": bool(announced) and first_asn is not None and announced[0] == first_asn,
            "stable_days": None,
        },
        "network_count": len(public_groups) if not asn_count else asn_count,
    }
