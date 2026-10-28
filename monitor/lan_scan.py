"""On-demand LAN discovery (Scapy ARP) and port scan (Nmap) for Airwaves Security."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import threading

from checks import _wired_interface

# Common home-LAN TCP ports. Kept short so an on-demand scan finishes in a few minutes.
NMAP_PORTS = "22,53,80,443,445,548,631,3389,5000,8080,8443,9100"
_lock = threading.Lock()
_scanning = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iface_cidr(name: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            return parts[parts.index("inet") + 1]
    return None


def lan_subnet() -> tuple[str, str]:
    """Return (iface, subnet CIDR) for the wired home LAN. Never a WireGuard address."""
    iface = _wired_interface()
    if not iface:
        raise ValueError("No wired LAN interface was found")
    cidr = _iface_cidr(iface)
    if not cidr:
        raise ValueError(f"{iface} has no IPv4 address")
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version != 4:
        raise ValueError("Only IPv4 LAN scans are supported")
    if str(network.network_address).startswith("10.7."):
        raise ValueError("The WireGuard tunnel cannot be scanned")
    if network.is_loopback or network.is_link_local:
        raise ValueError("That network cannot be scanned")
    return iface, str(network)


def _arp_hosts(subnet: str, iface: str) -> list[dict[str, Any]]:
    try:
        from scapy.all import ARP, Ether, conf, srp  # type: ignore
    except Exception as exc:  # noqa: BLE001 — missing optional package
        raise ValueError(f"Scapy is not installed ({exc})") from exc
    conf.verb = 0
    packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet)
    answered, _ = srp(packet, iface=iface, timeout=3, inter=0.05, retry=1)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _sent, received in answered:
        ip = str(getattr(received, "psrc", "") or "")
        mac = str(getattr(received, "hwsrc", "") or "").lower()
        if not ip or ip in seen or ip.startswith("10.7."):
            continue
        seen.add(ip)
        found.append({"ip": ip, "mac": mac, "vendor": "", "ports": []})
    found.sort(key=lambda row: row["ip"])
    return found


def _nmap_ports(hosts: list[dict[str, Any]]) -> None:
    binary = shutil.which("nmap")
    if not binary:
        raise ValueError("nmap is not installed")
    if not hosts:
        return
    targets = [host["ip"] for host in hosts]
    try:
        completed = subprocess.run(
            [
                binary,
                "-n",
                "-Pn",
                "-T4",
                "--max-retries",
                "1",
                "--host-timeout",
                "30s",
                "-p",
                NMAP_PORTS,
                "-oX",
                "-",
                *targets,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(120, 25 * len(targets)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"nmap failed: {exc}") from exc
    if completed.returncode not in {0, 1}:
        note = (completed.stderr or completed.stdout or "nmap failed").strip().splitlines()
        raise ValueError(note[-1][:200] if note else "nmap failed")
    open_by_ip: dict[str, list[int]] = {host["ip"]: [] for host in hosts}
    # Prefer ElementTree over regex so odd hostnames cannot break parsing.
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(completed.stdout or "<nmaprun/>")
    except ET.ParseError:
        return
    for host in root.findall("host"):
        addr = ""
        for address in host.findall("address"):
            if address.get("addrtype") == "ipv4":
                addr = address.get("addr") or ""
                break
        if addr not in open_by_ip:
            continue
        ports: list[int] = []
        for port in host.findall("./ports/port"):
            state = port.find("state")
            if state is None or state.get("state") != "open":
                continue
            try:
                ports.append(int(port.get("portid") or 0))
            except ValueError:
                continue
        open_by_ip[addr] = sorted(p for p in ports if 1 <= p <= 65535)
    for host in hosts:
        host["ports"] = open_by_ip.get(host["ip"], [])


def run_lan_scan() -> dict[str, Any]:
    started = _now()
    try:
        iface, subnet = lan_subnet()
        hosts = _arp_hosts(subnet, iface)
        _nmap_ports(hosts)
        return {
            "started_at": started.isoformat(),
            "finished_at": _now().isoformat(),
            "status": "finished",
            "error": "",
            "subnet": subnet,
            "hosts": hosts,
        }
    except ValueError as exc:
        return {
            "started_at": started.isoformat(),
            "finished_at": _now().isoformat(),
            "status": "failed",
            "error": str(exc)[:400],
            "subnet": "",
            "hosts": [],
        }


def upload_lan_scan(config: dict, post_json) -> None:
    global _scanning
    with _lock:
        if _scanning:
            return
        _scanning = True
    try:
        body = run_lan_scan()
        base = str(config.get("server_url") or "").rstrip("/")
        try:
            post_json(
                f"{base}/api/v1/monitors/lan-scan",
                body,
                config.get("token"),
                config.get("name"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"lan_scan: upload failed: {exc}", file=sys.stderr, flush=True)
            try:
                import agent_state

                agent_state.note_log("lan_scan", "warning", f"LAN scan upload failed: {exc}"[:200])
            except Exception:
                pass
            return
        try:
            import agent_state

            if body.get("status") == "failed":
                agent_state.note_log("lan_scan", "warning", f"LAN scan failed: {body.get('error') or 'unknown'}"[:200])
            else:
                agent_state.note_log(
                    "lan_scan",
                    "info",
                    f"LAN scan finished: {len(body.get('hosts') or [])} hosts on {body.get('subnet')}",
                )
        except Exception:
            pass
    finally:
        with _lock:
            _scanning = False
