"""Fill traceroute hops with ASN, prefix, and the announced BGP path.

Lookups use the public RIPEstat data API. A failed lookup leaves that hop
unenriched so the path still renders.
"""

from __future__ import annotations

import ipaddress
import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.parse import quote

import httpx

RIPE = "https://stat.ripe.net/data"
_CACHE: dict[str, tuple[float, dict | None]] = {}
_TTL_SECONDS = 6 * 60 * 60
_MISS_TTL_SECONDS = 5 * 60


def _public_ip(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    ):
        return None
    return str(parsed)


def _url(dataset: str, **params: str) -> str:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return f"{RIPE}/{dataset}/data.json?{query}&sourceapp=homenetwatch"


def _default_fetch(url: str) -> dict:
    response = httpx.get(url, timeout=4.0, headers={"User-Agent": "HomeNetWatch"})
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("RIPEstat did not return an object")
    return body


def _cached(url: str, fetch: Callable[[str], dict]) -> dict | None:
    import time

    now = time.monotonic()
    hit = _CACHE.get(url)
    if hit is not None:
        stored, body = hit
        fresh = _TTL_SECONDS if body is not None else _MISS_TTL_SECONDS
        if now - stored < fresh:
            return body
    try:
        body = fetch(url)
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        _CACHE[url] = (now, None)
        return None
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[url] = (now, body)
    return body


def _data(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _announced_prefix(body: dict | None) -> str | None:
    """The BGP prefix covering this address. prefix-overview's block is only the RIR allocation."""
    prefix = _data(body).get("prefix")
    if isinstance(prefix, str) and "/" in prefix:
        return prefix
    return None


def _prefix_info(body: dict | None) -> dict[str, Any]:
    data = _data(body)
    asns = data.get("asns")
    first = asns[0] if isinstance(asns, list) and asns and isinstance(asns[0], dict) else {}
    asn = first.get("asn")
    block = data.get("block")
    prefix = None
    if isinstance(block, dict) and isinstance(block.get("resource"), str):
        prefix = block["resource"]
    elif isinstance(block, str):
        prefix = block
    holder = first.get("holder") if isinstance(first.get("holder"), str) else None
    return {
        "asn": int(asn) if isinstance(asn, int) and not isinstance(asn, bool) else None,
        "as_name": holder or None,
        "prefix": prefix,
    }


def _announced_path(body: dict | None, origin: int | None, via: int | None = None) -> list[int] | None:
    """The most common path the route collectors see to the origin.

    With `via` (the agent's own provider), a path through that network is preferred and shown from it
    onward, so it can be compared with the trace. Without one, any collector's path is used.
    """
    rows = _data(body).get("bgp_state")
    if not isinstance(rows, list):
        return None
    paths: list[tuple[int, ...]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), list):
            continue
        path = [item for item in row["path"] if isinstance(item, int) and not isinstance(item, bool)]
        if not path:
            continue
        if origin is not None and path[-1] != origin:
            continue
        # An AS repeated to make a route less attractive (prepending) is still one network.
        paths.append(tuple(asn for index, asn in enumerate(path) if index == 0 or path[index - 1] != asn))
    if not paths:
        return None
    if via is not None and via != origin:
        through = [path[path.index(via):] for path in paths if via in path]
        if through:
            return list(Counter(through).most_common(1)[0][0])
    return list(Counter(paths).most_common(1)[0][0])


def _rpki(body: dict | None) -> str | None:
    status = _data(body).get("status")
    if status in {"valid", "invalid", "unknown"}:
        return str(status)
    return None


def _holder(body: dict | None) -> str | None:
    holder = _data(body).get("holder")
    return holder if isinstance(holder, str) and holder else None


def _ptr(ip: str) -> str | None:
    try:
        name = socket.getnameinfo((ip, 0), socket.NI_NAMEREQD)[0]
    except OSError:
        return None
    return name or None


def enrich_hops(
    raw_hops: list[Any],
    *,
    fetch: Callable[[str], dict] | None = None,
    resolve_names: bool = True,
) -> tuple[list[Any], list[int] | None, dict[int, str], str | None]:
    """Return hops, the announced AS path, names for those ASes, and RPKI status."""
    fetcher = fetch or _default_fetch
    hops = [dict(hop) if isinstance(hop, dict) else hop for hop in raw_hops]
    addresses: list[str] = []
    for hop in hops:
        if not isinstance(hop, dict):
            continue
        ip = _public_ip(hop.get("ip") if hop.get("ip") is not None else hop.get("host"))
        if ip and ip not in addresses:
            addresses.append(ip)
    infos: dict[str, dict[str, Any]] = {}
    if addresses:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = {}
            for ip in addresses:
                pending[pool.submit(_cached, _url("prefix-overview", resource=ip), fetcher)] = ("overview", ip)
                pending[pool.submit(_cached, _url("network-info", resource=ip), fetcher)] = ("network", ip)
            overviews: dict[str, dict | None] = {}
            networks: dict[str, dict | None] = {}
            for job in as_completed(pending):
                kind, ip = pending[job]
                if kind == "overview":
                    overviews[ip] = job.result()
                else:
                    networks[ip] = job.result()
        for ip in addresses:
            info = _prefix_info(overviews.get(ip))
            announced_prefix = _announced_prefix(networks.get(ip))
            if announced_prefix:
                info["prefix"] = announced_prefix
            infos[ip] = info

    origin_ip = addresses[-1] if addresses else None
    origin = infos.get(origin_ip or "", {}).get("asn")
    prefix = infos.get(origin_ip or "", {}).get("prefix")
    announced: list[int] | None = None
    rpki: str | None = None
    if origin_ip:
        resource = prefix or origin_ip
        # The agent's provider: the network of the first public hop.
        provider = infos.get(addresses[0], {}).get("asn")
        announced = _announced_path(
            _cached(_url("bgp-state", resource=resource), fetcher),
            origin if isinstance(origin, int) else None,
            provider if isinstance(provider, int) else None,
        )
        if isinstance(origin, int) and prefix:
            rpki = _rpki(_cached(_url("rpki-validation", resource=str(origin), prefix=prefix), fetcher))

    names: dict[int, str] = {}
    for info in infos.values():
        if isinstance(info.get("asn"), int) and info.get("as_name"):
            names[info["asn"]] = info["as_name"]
    missing = [asn for asn in announced or [] if asn not in names]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = {pool.submit(_cached, _url("as-overview", resource=f"AS{asn}"), fetcher): asn for asn in missing}
            for job in as_completed(pending):
                holder = _holder(job.result())
                if holder:
                    names[pending[job]] = holder

    pointers: dict[str, str | None] = {}
    if resolve_names and addresses:
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1.2)
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                pending = {pool.submit(_ptr, ip): ip for ip in addresses}
                for job in as_completed(pending):
                    pointers[pending[job]] = job.result()
        finally:
            socket.setdefaulttimeout(previous)

    for hop in hops:
        if not isinstance(hop, dict):
            continue
        ip = _public_ip(hop.get("ip") if hop.get("ip") is not None else hop.get("host"))
        info = infos.get(ip or "", {})
        if hop.get("asn") is None and info.get("asn") is not None:
            hop["asn"] = info["asn"]
        if not hop.get("as_name") and info.get("as_name"):
            hop["as_name"] = info["as_name"]
        if not hop.get("prefix") and info.get("prefix"):
            hop["prefix"] = info["prefix"]
        if not hop.get("rdns") and ip and pointers.get(ip):
            hop["rdns"] = pointers[ip]
    return hops, announced, names, rpki
