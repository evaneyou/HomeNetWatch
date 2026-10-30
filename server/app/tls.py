"""Self-signed key for the agent HTTPS port. Remote agents pin the key, not the certificate.

    python -m app.tls ensure   create the key, backup key, and certificate if missing, then print the pins
    python -m app.tls pins     print the pins
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.config import get_settings

KEY_FILE = "key.pem"
BACKUP_KEY_FILE = "backup-key.pem"
CERT_FILE = "cert.pem"


def spki_pin(public_key: ec.EllipticCurvePublicKey) -> str:
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")


def ensure(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = _load_or_create(directory / KEY_FILE)
    _load_or_create(directory / BACKUP_KEY_FILE)
    cert_path = directory / CERT_FILE
    if cert_path.is_file():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if spki_pin(cert.public_key()) == spki_pin(key.public_key()):
            return
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hns-server")])
    start = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=365 * 100))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def pins(directory: Path) -> tuple[str, str]:
    primary = _load(directory / KEY_FILE)
    backup = _load(directory / BACKUP_KEY_FILE)
    return spki_pin(primary.public_key()), spki_pin(backup.public_key())


def pin_list(directory: Path) -> list[str]:
    """The pins as an agent expects them, or nothing when the key files are not there."""
    try:
        primary, backup = pins(directory)
    except (OSError, ValueError):
        return []
    return [f"sha256/{primary}", f"sha256/{backup}"]


def _load(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError(f"{path} is not an EC key")
    return key


def _load_or_create(path: Path) -> ec.EllipticCurvePrivateKey:
    if path.is_file():
        return _load(path)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return key


def main(argv: list[str]) -> int:
    directory = Path(get_settings().tls_dir)
    command = argv[1] if len(argv) > 1 else "pins"
    if command == "ensure":
        ensure(directory)
    elif command != "pins":
        print(__doc__)
        return 2
    primary, backup = pins(directory)
    print(f"Server key pin: sha256/{primary}")
    print(f"Backup key pin: sha256/{backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
