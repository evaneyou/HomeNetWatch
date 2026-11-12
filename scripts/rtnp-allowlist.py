#!/usr/bin/env python3
"""Refresh the UDP 8998 allowlist from the WireGuard peer endpoints.

Run as root every 60 seconds. An empty peer set drops every packet.

The filter is left alone when it already allows the current peers, and is otherwise replaced in one nft
transaction, so a refresh never drops a packet of a running measurement flow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.rtnp_allowlist import endpoints_from_wg, is_current, jump_commands, nft_commands, nft_script  # noqa: E402


def _wg_endpoints() -> Optional[str]:
    """The peers' endpoints, or None when WireGuard could not be asked (it is restarting, or Docker is)."""
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "wireguard", "wg", "show", "wg0", "endpoints"],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _nft_list(*what: str) -> str:
    completed = subprocess.run(["nft", "list", *what], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return ""
    return completed.stdout


def apply(commands: list[list[str]]) -> None:
    for command in commands:
        subprocess.run(command, check=False, capture_output=True)


def main() -> int:
    listing = _wg_endpoints()
    if listing is None:
        # Not knowing the peers is not the same as having none: keep the filter as it is until WireGuard answers.
        return 0
    addresses = endpoints_from_wg(listing)
    docker_user, docker_user6 = _nft_list("chain", "ip", "filter", "DOCKER-USER"), _nft_list("chain", "ip6", "filter", "DOCKER-USER")
    current = is_current(
        addresses,
        _nft_list("set", "ip", "filter", "rtnp_sources"),
        _nft_list("chain", "ip", "filter", "RTNP_IN"),
        _nft_list("chain", "ip6", "filter", "RTNP_IN"),
    )
    if not current:
        done = subprocess.run(["nft", "-f", "-"], input=nft_script(addresses), check=False, capture_output=True, text=True)
        if done.returncode != 0:
            apply(nft_commands(addresses, docker_user, docker_user6))
            return 0
    apply(jump_commands(docker_user, docker_user6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
