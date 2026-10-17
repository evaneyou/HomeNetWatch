"""WireGuard hub state shared with the hub container through the state directory.

The server writes peers.conf and one preshared key per monitor under psk/.
The hub writes server.pub and a handshakes file from `wg show wg0 latest-handshakes`.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Monitor

TUNNEL_SERVER_IP = "10.7.0.1"
WIRELESS_SERVER_IP = "10.7.1.1"
PERSISTENT_KEEPALIVE = 25
# Wired traffic uses 10.7.0.0/24. Wireless uses 10.7.1.0/24 so each tunnel
# owns a distinct route to the server. FwMark steers the encrypted packets
# out the matching physical interface.
_LINKS = {
    "wired": ("10.7.0.", "wg_address", TUNNEL_SERVER_IP, 51820, 100),
    "wireless": ("10.7.1.", "wg_wireless_address", WIRELESS_SERVER_IP, 51821, 101),
}


class TunnelNotReady(Exception):
    """The state directory is missing or the hub has not written its public key yet."""


def server_public_key() -> str:
    directory = _state_dir()
    if directory is None:
        return ""
    path = directory / "server.pub"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def endpoint() -> str:
    return get_settings().wireguard_endpoint.strip()


def allocate_address(db: Session, link: str = "wired") -> str:
    prefix, attribute, _server, _mark, _table = _LINKS[link]
    column = getattr(Monitor, attribute)
    used = set(db.scalars(select(column).where(column.is_not(None))).all())
    for host in range(2, 255):
        address = f"{prefix}{host}"
        if address not in used:
            return address
    raise RuntimeError("No tunnel addresses left")


def new_keypair() -> tuple[str, str]:
    """WireGuard X25519 private key and public key. The private key is not stored."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    private = X25519PrivateKey.generate()
    secret = base64.b64encode(private.private_bytes_raw()).decode("ascii")
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    return secret, public


def new_preshared_key(monitor_id: uuid.UUID, link: str = "wired") -> str:
    directory = _require_state_dir()
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    _write_private(_psk_path(directory, monitor_id, link), key)
    return key


def read_preshared_key(monitor_id: uuid.UUID, link: str = "wired") -> str | None:
    directory = _state_dir()
    if directory is None:
        return None
    path = _psk_path(directory, monitor_id, link)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def remove_preshared_key(monitor_id: uuid.UUID, link: str | None = None) -> None:
    directory = _state_dir()
    if directory is None:
        return
    links = (link,) if link else tuple(_LINKS)
    for name in links:
        _psk_path(directory, monitor_id, name).unlink(missing_ok=True)


def client_config(
    *,
    link: str,
    private_key: str,
    address: str,
    server_public_key: str,
    endpoint_host: str,
    preshared_key: str,
) -> str:
    """wg-quick config. Table is off so each tunnel can own its own server route."""
    _prefix, _attribute, server_ip, fwmark, table = _LINKS[link]
    # Rule priority must be lower than the main table (32766). The fwmark
    # itself is 51820/51821, which would sort after main and never match.
    rule_priority = table
    source_priority = table + 1000
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {address}/32",
        f"FwMark = {fwmark}",
        "Table = off",
        f"PostUp = ip route replace {server_ip}/32 dev %i",
        f"PostUp = ip rule del fwmark {fwmark} lookup {table} priority {fwmark} 2>/dev/null || true",
        f"PostUp = ip rule del fwmark {fwmark} lookup {table} priority {rule_priority} 2>/dev/null || true",
        f"PostUp = ip rule add fwmark {fwmark} lookup {table} priority {rule_priority}",
        f"PostUp = {_pin_command(link, table, source_priority)}",
        f"PostDown = ip rule del fwmark {fwmark} lookup {table} priority {rule_priority} 2>/dev/null || true",
        f"PostDown = ip rule del lookup {table} priority {source_priority} 2>/dev/null || true",
        f"PostDown = ip route flush table {table} 2>/dev/null || true",
        f"PostDown = ip route del {server_ip}/32 dev %i 2>/dev/null || true",
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {preshared_key}",
        f"Endpoint = {endpoint_host}",
        f"AllowedIPs = {server_ip}/32",
        f"PersistentKeepalive = {PERSISTENT_KEEPALIVE}",
        "",
    ]
    return "\n".join(lines)


def rewrite_peers(db: Session) -> None:
    directory = _state_dir()
    if directory is None:
        return
    blocks: list[str] = []
    monitors = db.scalars(select(Monitor).where(Monitor.wg_public_key.is_not(None))).all()
    for monitor in monitors:
        _append_peer(
            blocks,
            monitor.wg_public_key,
            monitor.wg_address,
            read_preshared_key(monitor.id, "wired"),
            monitor.identity_public_key,
        )
        _append_peer(
            blocks,
            getattr(monitor, "wg_wireless_public_key", None),
            getattr(monitor, "wg_wireless_address", None),
            read_preshared_key(monitor.id, "wireless"),
            monitor.identity_public_key,
        )
    _write_private(directory / "peers.conf", "\n".join(blocks))


def latest_handshakes() -> dict[str, datetime]:
    """Peer public key to its latest handshake. Peers that never connected are left out."""
    directory = _state_dir()
    if directory is None:
        return {}
    path = directory / "handshakes"
    if not path.is_file():
        return {}
    seen: dict[str, datetime] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        stamp = int(parts[1])
        if stamp > 0:
            seen[parts[0]] = datetime.fromtimestamp(stamp, tz=timezone.utc)
    return seen


def refresh_handshakes(db: Session, monitors: list[Monitor]) -> None:
    seen = latest_handshakes()
    for monitor in monitors:
        moment = seen.get(monitor.wg_public_key or "")
        if moment is not None and (monitor.last_handshake_at is None or moment > monitor.last_handshake_at):
            monitor.last_handshake_at = moment
        wireless_key = getattr(monitor, "wg_wireless_public_key", None) or ""
        wireless_at = seen.get(wireless_key)
        previous = getattr(monitor, "last_wireless_handshake_at", None)
        if wireless_at is not None and (previous is None or wireless_at > previous):
            monitor.last_wireless_handshake_at = wireless_at


def _append_peer(
    blocks: list[str],
    public_key: str | None,
    address: str | None,
    preshared: str | None,
    identity_public_key: str | None,
) -> None:
    if not address or not public_key:
        return
    # Enrolled peers without a preshared key are waiting for approval to finish.
    if preshared is None and identity_public_key is not None:
        return
    psk_line = f"PresharedKey = {preshared}\n" if preshared else ""
    blocks.append(
        "[Peer]\n"
        f"PublicKey = {public_key}\n"
        f"{psk_line}"
        f"AllowedIPs = {address}/32\n"
    )


def _pin_command(link: str, table: int, source_priority: int) -> str:
    """Send this tunnel's UDP, and probes sourced on that NIC, out the matching interface."""
    if link == "wired":
        discover = (
            "DEV=$(ip -o link show | awk -F': ' "
            "'$2 ~ /^(eth|enp|end|eno|ens)/ { sub(/@.*/, \"\", $2); print $2; exit }')"
        )
    else:
        # sysfs works when iw is not installed. Skip tunnels and virtual NICs.
        # basename avoids '#' in the hook, which wg-quick would treat as a comment.
        discover = (
            "DEV=$(for pass in builtin usb; do "
            "for n in /sys/class/net/*; do "
            "[ -d \"$n/wireless\" ] || continue; "
            "b=$(basename \"$n\"); "
            "case $b in lo|wg*|tun*|docker*|veth*|br-*) continue;; esac; "
            "[ \"$(cat \"$n/carrier\" 2>/dev/null)\" = 1 ] || continue; "
            "ip -4 -o addr show dev \"$b\" | grep -q . || continue; "
            "case $(readlink -f \"$n/device\" 2>/dev/null) in */usb*) kind=usb;; *) kind=builtin;; esac; "
            "[ \"$kind\" = \"$pass\" ] || continue; "
            "printf %s \"$b\"; break 2; done; done)"
        )
    return (
        f"{discover}; "
        "GW=$(ip route show default dev \"$DEV\" 2>/dev/null | awk '{print $3; exit}'); "
        "SRC=$(ip -4 -o addr show dev \"$DEV\" 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f1); "
        "if [ -n \"$DEV\" ]; then "
        "LINK=$(ip -4 route show dev \"$DEV\" scope link | awk '{print $1; exit}'); "
        f"if [ -n \"$LINK\" ]; then ip route replace \"$LINK\" dev \"$DEV\" table {table}; fi; "
        f"if [ -n \"$GW\" ]; then ip route replace default via \"$GW\" dev \"$DEV\" table {table}; "
        f"else ip route replace default dev \"$DEV\" table {table}; fi; "
        f"if [ -n \"$SRC\" ]; then ip rule del from \"$SRC\" lookup {table} priority {table} 2>/dev/null || true; "
        f"ip rule del from \"$SRC\" lookup {table} priority {source_priority} 2>/dev/null || true; "
        f"ip rule add from \"$SRC\" lookup {table} priority {source_priority}; fi; fi"
    )


def _psk_path(directory: Path, monitor_id: uuid.UUID, link: str = "wired") -> Path:
    name = f"{monitor_id}.key" if link == "wired" else f"{monitor_id}-wireless.key"
    return directory / "psk" / name


def _write_private(target: Path, text: str) -> None:
    target.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _require_state_dir() -> Path:
    directory = _state_dir()
    if directory is None:
        raise TunnelNotReady()
    return directory


SRTP_KEY_LEN = 30
RTNP_UID = 65532


def ensure_srtp_key(monitor_id: uuid.UUID) -> bytes | None:
    """Return this monitor's 30-byte SRTP key, creating an encrypted copy if needed.

    The ciphertext lives next to the WireGuard preshared keys. A plaintext file is
    written for the receiver process only. The key is not returned in logs.
    """
    directory = _state_dir()
    if directory is None:
        return None
    master = _srtp_master(directory)
    path = directory / "srtp" / f"{monitor_id}.bin"
    if path.is_file():
        stored = path.read_bytes()
        if len(stored) < 13:
            raise ValueError("Stored SRTP key is unreadable")
        key = AESGCM(master).decrypt(stored[:12], stored[12:], None)
    else:
        key = secrets.token_bytes(SRTP_KEY_LEN)
        nonce = secrets.token_bytes(12)
        _write_bytes(path, nonce + AESGCM(master).encrypt(nonce, key, None))
    if len(key) != SRTP_KEY_LEN:
        raise ValueError("Stored SRTP key is unreadable")
    _publish_srtp_key(monitor_id, key)
    return key


def _srtp_master(directory: Path) -> bytes:
    path = directory / "srtp.master"
    if path.is_file():
        master = path.read_bytes()
        if len(master) == 32:
            return master
    master = secrets.token_bytes(32)
    _write_bytes(path, master)
    return master


def _publish_srtp_key(monitor_id: uuid.UUID, key: bytes) -> None:
    raw = get_settings().rtnp_state_dir.strip()
    if not raw:
        return
    directory = Path(raw) / "keys"
    directory.mkdir(mode=0o750, exist_ok=True)
    _give_to_receiver(directory)
    path = directory / str(monitor_id)
    _write_bytes(path, key)
    _give_to_receiver(path)


def _give_to_receiver(path: Path) -> None:
    try:
        os.chown(path, RTNP_UID, RTNP_UID)
        os.chmod(path, 0o500 if path.is_dir() else 0o400)
    except OSError:
        pass


def _write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _state_dir() -> Path | None:
    raw = get_settings().wireguard_state_dir.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_dir():
        return None
    return path
