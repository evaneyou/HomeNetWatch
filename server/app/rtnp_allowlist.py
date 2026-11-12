"""Turn WireGuard peer endpoints into the host filter for UDP 8998.

An empty set drops every packet. IPv6 endpoints are ignored so they cannot
bypass the IPv4 allowlist. Docker accepts published ports before the host
INPUT chain, so the filter is a chain jumped from DOCKER-USER.

The filter is rewritten only when the peer addresses change, and then in one nft transaction. Rewriting it
step by step every minute left a few milliseconds in which the set was empty and the chain still ended in
`drop`, so every running measurement flow lost a packet once a minute.
"""

from __future__ import annotations

import ipaddress
import re

ACCEPT_RULE = "ip saddr @rtnp_sources accept"
_ELEMENTS = re.compile(r"elements\s*=\s*\{([^}]*)\}", re.S)


def endpoints_from_wg(text: str) -> list[str]:
    """Parse `wg show <iface> endpoints`. Keep distinct IPv4 addresses only."""
    found: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        endpoint = parts[-1]
        if endpoint == "(none)" or endpoint.startswith("["):
            continue
        host = endpoint.rsplit(":", 1)[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version != 4:
            continue
        value = str(address)
        if value not in found:
            found.append(value)
    return found


def set_addresses(set_text: str) -> set[str]:
    """The addresses in `nft list set ip filter rtnp_sources`."""
    found = _ELEMENTS.search(set_text)
    if found is None:
        return set()
    return {item.strip() for item in found.group(1).split(",") if item.strip()}


def is_current(addresses: list[str], set_text: str, chain_text: str, chain6_text: str) -> bool:
    """True when the filter already allows exactly `addresses`, so there is nothing to rewrite."""
    if "drop" not in chain_text or "drop" not in chain6_text:
        return False
    if set_addresses(set_text) != set(addresses):
        return False
    return (ACCEPT_RULE in chain_text) == bool(addresses)


def nft_script(addresses: list[str]) -> str:
    """The whole filter as one `nft -f` file. nft applies a file as one transaction: no packet sees it half done."""
    lines = [
        "add table ip filter",
        "add table ip6 filter",
        "add set ip filter rtnp_sources { type ipv4_addr; }",
        "add chain ip filter RTNP_IN",
        "add chain ip6 filter RTNP_IN",
        "flush set ip filter rtnp_sources",
        "flush chain ip filter RTNP_IN",
        "flush chain ip6 filter RTNP_IN",
        "add rule ip6 filter RTNP_IN drop",
    ]
    if addresses:
        lines.append("add element ip filter rtnp_sources { " + ", ".join(addresses) + " }")
        lines.append(f"add rule ip filter RTNP_IN {ACCEPT_RULE}")
    lines.append("add rule ip filter RTNP_IN drop")
    return "\n".join(lines) + "\n"


def jump_commands(chain_text: str = "", chain6_text: str = "") -> list[list[str]]:
    """Send UDP 8998 through the filter once, then remove the drop-everything rule that covered the gap at boot."""
    commands: list[list[str]] = []
    if "jump RTNP_IN" not in chain_text:
        commands.append(["nft", "insert", "rule", "ip", "filter", "DOCKER-USER", "udp", "dport", "8998", "jump", "RTNP_IN"])
    if "jump RTNP_IN" not in chain6_text:
        commands.append(["nft", "insert", "rule", "ip6", "filter", "DOCKER-USER", "udp", "dport", "8998", "jump", "RTNP_IN"])
    commands.append(["iptables", "-D", "DOCKER-USER", "-p", "udp", "-m", "udp", "--dport", "8998", "-j", "DROP"])
    commands.append(["ip6tables", "-D", "DOCKER-USER", "-p", "udp", "-m", "udp", "--dport", "8998", "-j", "DROP"])
    return commands


def nft_commands(
    addresses: list[str],
    chain_text: str = "",
    chain6_text: str = "",
) -> list[list[str]]:
    """Rebuild the measurement chain step by step and jump to it from DOCKER-USER once.

    Only the fallback for a host whose nft refuses the single transaction of `nft_script`: between two of
    these steps the chain drops every packet.
    """
    commands: list[list[str]] = [
        ["nft", "add", "table", "ip", "filter"],
        ["nft", "add", "table", "ip6", "filter"],
        ["nft", "add", "set", "ip", "filter", "rtnp_sources", "{ type ipv4_addr; }"],
        ["nft", "flush", "set", "ip", "filter", "rtnp_sources"],
        ["nft", "add", "chain", "ip", "filter", "RTNP_IN"],
        ["nft", "flush", "chain", "ip", "filter", "RTNP_IN"],
        ["nft", "add", "chain", "ip6", "filter", "RTNP_IN"],
        ["nft", "flush", "chain", "ip6", "filter", "RTNP_IN"],
        ["nft", "add", "rule", "ip6", "filter", "RTNP_IN", "drop"],
    ]
    if addresses:
        commands.append(
            [
                "nft",
                "add",
                "element",
                "ip",
                "filter",
                "rtnp_sources",
                "{ " + ", ".join(addresses) + " }",
            ]
        )
        commands.append(
            ["nft", "add", "rule", "ip", "filter", "RTNP_IN", "ip", "saddr", "@rtnp_sources", "accept"]
        )
    commands.append(["nft", "add", "rule", "ip", "filter", "RTNP_IN", "drop"])
    if "jump RTNP_IN" not in chain_text:
        commands.append(
            ["nft", "insert", "rule", "ip", "filter", "DOCKER-USER", "udp", "dport", "8998", "jump", "RTNP_IN"]
        )
    if "jump RTNP_IN" not in chain6_text:
        commands.append(
            ["nft", "insert", "rule", "ip6", "filter", "DOCKER-USER", "udp", "dport", "8998", "jump", "RTNP_IN"]
        )
    commands.append(["iptables", "-D", "DOCKER-USER", "-p", "udp", "-m", "udp", "--dport", "8998", "-j", "DROP"])
    commands.append(["ip6tables", "-D", "DOCKER-USER", "-p", "udp", "-m", "udp", "--dport", "8998", "-j", "DROP"])
    return commands
