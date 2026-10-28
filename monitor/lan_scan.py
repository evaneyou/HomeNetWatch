"""On-demand LAN discovery (Scapy ARP) and port scan (Nmap) for Airwaves Security.

Each host is labelled with the maker behind its MAC (IEEE OUI table that ships with
Nmap) and the name it announces over mDNS (a reverse PTR query, no extra tools), when it
announces one.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import threading

from checks import _wired_interface

# Common home-LAN TCP ports. Kept short so an on-demand scan finishes in a few minutes.
NMAP_PORTS = "22,53,80,443,445,548,631,3389,5000,8080,8443,9100"
# OUI tables, first one found wins. Nmap always ships its own copy; ieee-data is optional.
OUI_FILES = (
    "/usr/share/nmap/nmap-mac-prefixes",
    "/usr/share/ieee-data/oui.txt",
    "/var/lib/ieee-data/oui.txt",
)
RANDOMIZED_MAC = "Private address"
_lock = threading.Lock()
_scanning = False
_oui_cache: Optional[dict[str, str]] = None
# Nmap: `7C9A54 Vendor`, also 28-bit `0055DA0 Vendor` and 36-bit `0055DA012 Vendor` blocks.
_NMAP_LINE = re.compile(r"^([0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{1}|[0-9A-Fa-f]{3})?)\s+(?!\()(\S.*?)\s*$")
# IEEE oui.txt: `7C-9A-54   (hex)		Vendor` (the `(base 16)` twin of each line is skipped).
_IEEE_LINE = re.compile(r"^([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(\S.*?)\s*$")
# Prefix lengths in hex digits, longest first so a 36-bit block beats the 24-bit one it lives in.
_PREFIX_LENGTHS = (9, 7, 6)


def _load_oui(paths: tuple[str, ...] = OUI_FILES) -> dict[str, str]:
    """Parse the first OUI table present, keyed by upper-case hex prefix (6, 7 or 9 digits)."""
    table: dict[str, str] = {}
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("#"):
                        continue
                    match = _NMAP_LINE.match(line)
                    if match:
                        table[match.group(1).upper()] = match.group(2)[:128]
                        continue
                    match = _IEEE_LINE.match(line)
                    if match:
                        table[(match.group(1) + match.group(2) + match.group(3)).upper()] = match.group(4)[:128]
        except OSError:
            continue
        if table:
            break
    return table


def mac_vendor(mac: str, table: Optional[dict[str, str]] = None) -> str:
    """Maker for a MAC, `Private address` for a randomized one, or "" when unknown."""
    global _oui_cache
    digits = re.sub(r"[^0-9a-f]", "", (mac or "").lower())
    if len(digits) != 12:
        return ""
    if int(digits[1], 16) & 0b10:  # locally administered: phones and laptops hiding their real MAC
        return RANDOMIZED_MAC
    if table is None:
        if _oui_cache is None:
            _oui_cache = _load_oui()
        table = _oui_cache
    upper = digits.upper()
    for length in _PREFIX_LENGTHS:
        vendor = table.get(upper[:length])
        if vendor:
            return vendor
    return ""


MDNS_GROUP = ("224.0.0.251", 5353)
_PTR = 12


def _ptr_question(ip: str) -> tuple[str, bytes]:
    """`(qname, wire)` for a reverse lookup of `ip`: `4.3.2.1.in-addr.arpa`, class IN with the unicast-reply bit."""
    labels = ip.split(".")[::-1] + ["in-addr", "arpa"]
    wire = b"".join(bytes([len(label)]) + label.encode() for label in labels) + b"\x00"
    return ".".join(labels), wire + struct.pack(">HH", _PTR, 0x8001)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a DNS name at `offset`, following compression pointers. Returns `(name, offset after it)`."""
    labels: list[str] = []
    end = -1
    hops = 0
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if end < 0:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            hops += 1
            if hops > 16:
                raise ValueError("compression loop")
            continue
        offset += 1
        labels.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), (end if end >= 0 else offset)


def _ptr_answers(data: bytes, wanted: dict[str, str], names: dict[str, str]) -> None:
    """Pull PTR answers for the names in `wanted` (qname -> ip) out of one mDNS response into `names`."""
    _ident, flags, questions, answers, _auth, _extra = struct.unpack(">HHHHHH", data[:12])
    if not flags & 0x8000:  # a query, not a response
        return
    offset = 12
    for _ in range(questions):
        _name, offset = _read_name(data, offset)
        offset += 4
    for _ in range(answers):
        name, offset = _read_name(data, offset)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        ip = wanted.get(name.lower())
        if rtype == _PTR and ip and ip not in names:
            target, _ = _read_name(data, offset)
            if target:
                names[ip] = target.rstrip(".")[:253]
        offset += rdlen


def _mdns_names(ips: list[str], iface_ip: Optional[str] = None, timeout: float = 2.5) -> dict[str, str]:
    """Names the hosts announce over mDNS, by reverse PTR query to 224.0.0.251 asking for unicast replies.

    No helper binary is needed. Hosts without an mDNS responder simply stay unnamed.
    """
    if not ips:
        return {}
    wanted: dict[str, str] = {}
    names: dict[str, str] = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except OSError:
        return {}
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if iface_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
        sock.bind(("0.0.0.0", 0))
        for index, ip in enumerate(ips):
            qname, question = _ptr_question(ip)
            wanted[qname] = ip
            header = struct.pack(">HHHHHH", (index + 1) & 0xFFFF, 0, 1, 0, 0, 0)
            sock.sendto(header + question, MDNS_GROUP)
        deadline = time.monotonic() + timeout
        while len(names) < len(ips):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            try:
                _ptr_answers(data, wanted, names)
            except (ValueError, IndexError, struct.error):
                continue
    except OSError:
        pass
    finally:
        sock.close()
    return names


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
        found.append({"ip": ip, "mac": mac, "vendor": mac_vendor(mac), "hostname": "", "ports": []})
    found.sort(key=lambda row: ipaddress.ip_address(row["ip"]))
    return found


def _name_hosts(hosts: list[dict[str, Any]], iface: str) -> None:
    cidr = _iface_cidr(iface) or ""
    names = _mdns_names([host["ip"] for host in hosts], cidr.split("/")[0] or None)
    for host in hosts:
        host["hostname"] = names.get(host["ip"], "")


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
        _name_hosts(hosts, iface)
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
