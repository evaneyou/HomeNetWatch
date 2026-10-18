"""Identity, certificate pins, WireGuard keys, and the enroll loop."""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
from pathlib import Path

import agent_state as state
import synthetic

def load_config() -> dict:
    if not state.CONFIG_PATH.exists():
        return {}
    return json.loads(state.CONFIG_PATH.read_text(encoding="utf-8"))

def save_config(config: dict) -> None:
    state.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o640)
    tmp.replace(state.CONFIG_PATH)

def server_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return False
    return address.is_private or address.is_loopback

def signed_message(path: str, nonce: str, fields: list[str]) -> bytes:
    parts = [state.SIGNATURE_CONTEXT, path, nonce, *fields]
    if any("\n" in part for part in parts):
        raise ValueError("Signed fields cannot contain newlines")
    return "\n".join(parts).encode()

def new_installer_code() -> str:
    raw = "".join(secrets.choice(state._CROCKFORD) for _ in range(state.CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"

def identity_path() -> Path:
    return state.CONFIG_PATH.with_name("identity.pem")

def wireguard_secret_path(link: str) -> Path:
    name = "wg.key" if link == "wired" else "wg-wireless.key"
    return state.CONFIG_PATH.with_name(name)

def wireguard_dir() -> Path:
    return Path(os.environ.get("MONITOR_WIREGUARD_DIR", "/etc/wireguard"))

def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    temporary.replace(path)

def _openssl(args: list[str], payload: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(["openssl", *args], input=payload, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("openssl is required for enrollment") from exc
    if result.returncode != 0:
        raise RuntimeError("openssl is required for enrollment")
    return result.stdout

def _raw_public(pem_path: Path) -> str:
    der = _openssl(["pkey", "-in", str(pem_path), "-pubout", "-outform", "DER"])
    if len(der) < 32:
        raise RuntimeError("openssl is required for enrollment")
    return base64.b64encode(der[-32:]).decode("ascii")

def _raw_private(pem_path: Path) -> str:
    der = _openssl(["pkey", "-in", str(pem_path), "-outform", "DER"])
    return base64.b64encode(_pkcs8_seed(der)).decode("ascii")

def _pkcs8_seed(der: bytes) -> bytes:
    content, _end = _tlv_end(der, 0)
    _value, content = _tlv_end(der, content)
    _value, content = _tlv_end(der, content)
    secret_start, secret_end = _tlv_end(der, content)
    secret = der[secret_start:secret_end]
    if secret[:1] == b"\x04":
        inner_start, inner_end = _tlv_end(secret, 0)
        secret = secret[inner_start:inner_end]
    if len(secret) != 32:
        raise RuntimeError("openssl is required for enrollment")
    return secret

def ensure_identity() -> str:
    path = identity_path()
    if not path.is_file():
        _write_secret(path, _openssl(["genpkey", "-algorithm", "ED25519"]).decode("ascii"))
    return _raw_public(path)

def ensure_wireguard_keys() -> tuple[str, str, str, str]:
    """Wired private, wired public, wireless private, wireless public. Private keys stay on disk."""
    pairs: list[tuple[str, str]] = []
    for link in ("wired", "wireless"):
        path = wireguard_secret_path(link)
        if not path.is_file():
            _write_secret(path, _openssl(["genpkey", "-algorithm", "X25519"]).decode("ascii"))
        pairs.append((_raw_private(path), _raw_public(path)))
    return pairs[0][0], pairs[0][1], pairs[1][0], pairs[1][1]

def sign_identity(message: bytes) -> str:
    fd, name = tempfile.mkstemp(prefix="hns-sign-")
    try:
        os.write(fd, message)
        os.close(fd)
        fd = -1
        signature = _openssl(["pkeyutl", "-sign", "-inkey", str(identity_path()), "-rawin", "-in", name])
    finally:
        if fd >= 0:
            os.close(fd)
        os.unlink(name)
    return base64.b64encode(signature).decode("ascii")

def ensure_installer_code(config: dict) -> dict:
    if config.get("token"):
        return config
    if config.get("installer_code"):
        return config
    config["installer_code"] = new_installer_code()
    save_config(config)
    return config

def enrollment_base(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    host = parsed.hostname
    if parsed.scheme != "https" or not host:
        raise ValueError("Enrollment uses HTTPS")
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{state.ENROLL_PORT}"

def parse_pins(raw: str) -> list[str]:
    pins: list[str] = []
    for part in re.split(r"[\s,]+", raw.strip()):
        if not part:
            continue
        if part.startswith("sha256/"):
            part = part[len("sha256/") :]
        try:
            digest = base64.b64decode(part, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("TLS pins look like sha256/ and a SHA-256 value") from None
        if len(digest) != 32:
            raise ValueError("TLS pins are SHA-256")
        pins.append(part)
    if not pins:
        raise ValueError("Enter the server TLS key pin")
    return pins

def _tlv_end(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise ValueError("Certificate is truncated")
    length = data[offset + 1]
    header = offset + 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or header + count > len(data):
            raise ValueError("Certificate is truncated")
        length = int.from_bytes(data[header : header + count], "big")
        header += count
    end = header + length
    if end > len(data):
        raise ValueError("Certificate is truncated")
    return header, end

def certificate_pin(cert_der: bytes) -> str:
    """SHA-256 of the certificate subject public key, the same pin the server prints."""
    value, _ = _tlv_end(cert_der, 0)
    tbs, _ = _tlv_end(cert_der, value)
    offset = tbs
    if cert_der[offset] == 0xA0:
        _, offset = _tlv_end(cert_der, offset)
    for _ in range(5):
        _, offset = _tlv_end(cert_der, offset)
    _, spki_end = _tlv_end(cert_der, offset)
    digest = hashlib.sha256(cert_der[offset:spki_end]).digest()
    return base64.b64encode(digest).decode("ascii")

def agent_hostname() -> str:
    name = socket.gethostname().split(".")[0].strip().lower()
    cleaned = re.sub(r"[^a-z0-9-]", "", name).strip("-")
    return cleaned[:63] or "remote-agent"

def check_in_payload(
    identity_public: str,
    wg_public: str,
    wg_wireless_public: str,
    installer_code: str,
    hostname: str,
    lan: str,
    nonce: str,
) -> dict:
    fields = [wg_public, wg_wireless_public, installer_code, hostname, lan]
    return {
        "identity_public_key": identity_public,
        "wg_public_key": wg_public,
        "wg_wireless_public_key": wg_wireless_public,
        "installer_code": installer_code,
        "hostname": hostname,
        "lan_ip": lan,
        "nonce": nonce,
        "signature": sign_identity(signed_message(state.CHECK_IN_PATH, nonce, [identity_public, *fields])),
    }

def poll_payload(identity_public: str, have_token: bool, nonce: str) -> dict:
    flag = "1" if have_token else "0"
    return {
        "identity_public_key": identity_public,
        "have_token": have_token,
        "nonce": nonce,
        "signature": sign_identity(signed_message(state.POLL_PATH, nonce, [identity_public, flag])),
    }

def post_pinned(url: str, payload: dict, pins: list[str], timeout: int = 10) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Enrollment uses HTTPS")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout, context=context)
    try:
        connection.connect()
        peer = connection.sock.getpeercert(binary_form=True) if connection.sock is not None else None
        if not peer or certificate_pin(peer) not in set(pins):
            raise ssl.SSLError("Server key is not pinned")
        body = json.dumps(payload).encode("utf-8")
        connection.request("POST", parsed.path or "/", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(data))
        if not data:
            return {}
        return json.loads(data.decode("utf-8"))
    finally:
        connection.close()

def http_detail(exc: urllib.error.HTTPError, secret: str = "") -> str:
    raw = ""
    if exc.fp is not None:
        raw = exc.fp.read().decode("utf-8", errors="replace")
    detail = raw
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
            detail = parsed["detail"]
    except json.JSONDecodeError:
        detail = raw
    if not isinstance(detail, str) or not detail.strip():
        return "Could not enroll"
    if secret and secret in detail:
        return "Could not enroll"
    return detail.strip()[:300]

def _pin_command(link: str, table: int, source_priority: int) -> str:
    if link == "wired":
        discover = (
            "DEV=$(ip -o link show | awk -F': ' "
            "'$2 ~ /^(eth|enp|end|eno|ens)/ { sub(/@.*/, \"\", $2); print $2; exit }')"
        )
    else:
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

def tunnel_config_text(
    *,
    link: str,
    private_key: str,
    address: str,
    server_public_key: str,
    endpoint: str,
    allowed_ips: str,
    persistent_keepalive: int,
    preshared_key: str,
) -> str:
    server_ip, fwmark, table, _iface = state._TUNNELS["wireless" if link == "wireless" else "wired"]
    rule_priority = table
    source_priority = table + 1000
    chosen = link if link == "wireless" else "wired"
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
        f"PostUp = {_pin_command(chosen, table, source_priority)}",
        f"PostDown = ip rule del fwmark {fwmark} lookup {table} priority {rule_priority} 2>/dev/null || true",
        f"PostDown = ip rule del lookup {table} priority {source_priority} 2>/dev/null || true",
        f"PostDown = ip route flush table {table} 2>/dev/null || true",
        f"PostDown = ip route del {server_ip}/32 dev %i 2>/dev/null || true",
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {preshared_key}",
        f"Endpoint = {endpoint}",
        f"AllowedIPs = {allowed_ips}",
        f"PersistentKeepalive = {persistent_keepalive}",
        "",
    ]
    return "\n".join(lines)

def apply_tunnels(wired_private: str, wireless_private: str, tunnels: list[dict]) -> bool:
    wrote = False
    directory = wireguard_dir()
    for tunnel in tunnels:
        link = "wireless" if tunnel.get("link") == "wireless" else "wired"
        preshared = str(tunnel.get("preshared_key") or "")
        private_key = wireless_private if link == "wireless" else wired_private
        address = str(tunnel.get("address") or "")
        server_public = str(tunnel.get("server_public_key") or "")
        endpoint = str(tunnel.get("endpoint") or "")
        allowed = str(tunnel.get("allowed_ips") or "")
        if not preshared or not private_key or not address or not server_public or not endpoint or not allowed:
            continue
        _server_ip, _fwmark, _table, iface = state._TUNNELS[link]
        text = tunnel_config_text(
            link=link,
            private_key=private_key,
            address=address,
            server_public_key=server_public,
            endpoint=endpoint,
            allowed_ips=allowed,
            persistent_keepalive=int(tunnel.get("persistent_keepalive") or 25),
            preshared_key=preshared,
        )
        _write_secret(directory / f"{iface}.conf", text)
        wrote = True
        if directory == Path("/etc/wireguard"):
            subprocess.run(["systemctl", "enable", f"wg-quick@{iface}"], check=False, timeout=15)
            subprocess.run(["systemctl", "restart", f"wg-quick@{iface}"], check=False, timeout=15)
    return wrote

def _safe_error(detail: str, config: dict) -> str:
    secret = str(config.get("installer_code") or "")
    if secret and secret in detail:
        return "Could not enroll"
    return detail

def enroll_once(config: dict) -> None:
    from health import lan_ip
    base = enrollment_base(str(config.get("enrollment_url") or ""))
    pins = [str(pin) for pin in config.get("tls_pins") or []]
    if not pins:
        raise ValueError("Enter the server TLS key pin")
    identity_public = ensure_identity()
    wired_private, wired_public, wireless_private, wireless_public = ensure_wireguard_keys()
    installer_code = str(config.get("installer_code") or "")
    if not installer_code:
        raise ValueError("The installer code is missing")
    hostname = agent_hostname()
    lan = lan_ip(base)
    if not lan:
        state._last_error = "Could not find a LAN address for the server"
        return
    try:
        issued = post_pinned(f"{base}{state.NONCE_PATH}", {"identity_public_key": identity_public}, pins)
        nonce = str(issued["nonce"])
        check = post_pinned(
            f"{base}{state.CHECK_IN_PATH}",
            check_in_payload(identity_public, wired_public, wireless_public, installer_code, hostname, lan, nonce),
            pins,
        )
    except urllib.error.HTTPError as exc:
        state._last_error = _safe_error(http_detail(exc, installer_code), config)
        return
    if check.get("conflict"):
        state._last_error = "More than one device used this code. Start over on the dashboard."
    elif check.get("state") == "waiting_for_approval":
        state._last_error = ""
    have_token = bool(config.get("token"))
    try:
        issued = post_pinned(f"{base}{state.NONCE_PATH}", {"identity_public_key": identity_public}, pins)
        poll = post_pinned(
            f"{base}{state.POLL_PATH}",
            poll_payload(identity_public, have_token, str(issued["nonce"])),
            pins,
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            state._last_error = _safe_error(http_detail(exc, installer_code), config)
        return
    if poll.get("state") != "approved":
        return
    remember_approval(config, poll, wired_private, wireless_private)

def remember_approval(config: dict, poll: dict, wired_private: str, wireless_private: str) -> None:
    tunnels = list(poll.get("tunnels") or [])
    if not tunnels and isinstance(poll.get("tunnel"), dict):
        tunnels = [poll["tunnel"]]
    wrote = apply_tunnels(wired_private, wireless_private, tunnels)
    token = poll.get("token") or config.get("token")
    name = poll.get("name") or config.get("name")
    updated = dict(config)
    if token:
        updated["token"] = token
    if name:
        updated["name"] = name
    if wrote and any(item.get("link") != "wireless" for item in tunnels):
        updated["server_url"] = f"http://{state.TUNNEL_SERVER_IP}:8847"
    elif wrote:
        updated["server_url"] = f"http://{state.WIRELESS_SERVER_IP}:8847"
    for item in tunnels:
        endpoint = str(item.get("endpoint") or "")
        host = endpoint.split(":")[0]
        if host.startswith("[") or ":" in host:
            continue
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if address.version == 4 and not synthetic.is_tunnel_target(host):
            updated["measurement_host"] = host
            break
    if updated.get("token") and str(updated.get("server_url") or "").startswith("http://10.7."):
        updated.pop("installer_code", None)
        updated.setdefault("health_interval_seconds", 60)
        updated.setdefault("scan_interval_seconds", 300)
        updated.setdefault("config_version", 0)
    with state._lock:
        save_config(updated)
    if updated.get("token") and updated.get("name") and updated.get("server_url"):
        state.ensure_health_loop()

def enrollment_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        if config.get("token") and str(config.get("server_url") or "").startswith("http://10.7."):
            return
        if config.get("enrollment_url") and config.get("tls_pins") and config.get("installer_code"):
            try:
                enroll_once(dict(config))
            except Exception as exc:
                message = str(exc)
                secret = str(config.get("installer_code") or "")
                if secret and secret in message:
                    message = "Could not enroll"
                state._last_error = message
        with state._lock:
            done = bool(load_config().get("token")) and str(load_config().get("server_url") or "").startswith("http://10.7.")
        if done:
            return
        state._stop.wait(15)

def ensure_enrollment_loop() -> None:
    with state._lock:
        start = not state._enroll_started
        state._enroll_started = True
    if start:
        threading.Thread(target=enrollment_loop, name="enroll", daemon=True).start()
