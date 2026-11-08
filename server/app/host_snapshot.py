"""Copy the few host files Server Health reads into a shared folder, so the API never mounts the host.

Run by the `hoststat` container: no network, read-only, only the DAC_READ_SEARCH capability. It sees the host's
/proc and filesystem (read-only) and writes into the `hostinfo` volume:

- proc/: the machine-wide counters (stat, meminfo, version, 1/net/dev) and, per process, stat, cmdline and statm.
- root/: etc/os-release and var/lib/dpkg/status, for the weekly package check.
- storage.json: what uses the disk, measured every six hours (a slow walk of big folders).

The API container reads only that folder. Before, it mounted the whole host read-only as root, and /proc alone
reaches every file on the machine (/proc/1/root), so a bug in the API would have exposed every secret on the host.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("hns.host_snapshot")

PROC_EVERY_SECONDS = 15
ROOT_EVERY_SECONDS = 60 * 60
MACHINE_FILES = ("stat", "meminfo", "version")
PROCESS_FILES = ("stat", "cmdline", "statm")
ROOT_FILES = (("etc", "os-release"), ("var", "lib", "dpkg", "status"))
KEEP_SNAPSHOTS = 2


def _copy(source: Path, target: Path) -> bool:
    try:
        data = source.read_bytes()
    except OSError:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


def _swap_link(out: Path, name: str, fresh: Path) -> None:
    """Point out/name at the fresh folder in one step, so a reader never sees a half-written copy."""
    link = out / name
    temporary = out / f".{name}.{os.getpid()}.link"
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    os.symlink(fresh.name, temporary)
    os.replace(temporary, link)
    old = sorted(path for path in out.glob(f"{name}-*") if path.is_dir() and path != fresh)
    for path in old[: max(0, len(old) - (KEEP_SNAPSHOTS - 1))]:
        shutil.rmtree(path, ignore_errors=True)


def mirror_proc(host_proc: Path, out: Path) -> Path:
    """Copy the machine counters and each process's stat, cmdline and statm. Nothing else under /proc."""
    fresh = out / f"proc-{time.time_ns()}"
    fresh.mkdir(parents=True)
    for name in MACHINE_FILES:
        _copy(host_proc / name, fresh / name)
    _copy(host_proc / "1" / "net" / "dev", fresh / "1" / "net" / "dev")
    for entry in host_proc.iterdir():
        if not entry.name.isdigit():
            continue
        target = fresh / entry.name
        if not _copy(entry / "stat", target / "stat"):
            continue
        for name in PROCESS_FILES[1:]:
            _copy(entry / name, target / name)
    _swap_link(out, "proc", fresh)
    return fresh


def mirror_root(host_root: Path, out: Path) -> Path:
    fresh = out / f"root-{time.time_ns()}"
    fresh.mkdir(parents=True)
    for parts in ROOT_FILES:
        _copy(host_root.joinpath(*parts), fresh.joinpath(*parts))
    _swap_link(out, "root", fresh)
    return fresh


def write_storage(host_root: Path, out: Path, now: Optional[datetime] = None) -> None:
    """Measure the disk and leave the result as JSON. The API splits out the database, which it can measure."""
    from app.server_health import measure_storage

    now = now or datetime.now(timezone.utc)
    disk = shutil.disk_usage(host_root)
    payload = measure_storage(host_root, disk.used, disk.total, db_bytes=0)
    temporary = out / f".storage.{os.getpid()}.json"
    temporary.write_text(json.dumps({"measured_at": now.isoformat(), "payload": payload}), encoding="utf-8")
    os.replace(temporary, out / "storage.json")


def write_backups(source: Path, out: Path) -> None:
    """The names and sizes of backup.sh's copies, for Server Health. Never their contents."""
    try:
        entries = {entry.name: entry.stat().st_size for entry in source.iterdir() if entry.is_file()}
    except OSError:
        return
    temporary = out / f".backups.{os.getpid()}.json"
    temporary.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    os.replace(temporary, out / "backups.json")


def _storage_loop(host_root: Path, out: Path) -> None:
    from app.server_health import STORAGE_EVERY

    while True:
        try:
            write_storage(host_root, out)
        except Exception:
            log.exception("storage measurement failed")
        time.sleep(STORAGE_EVERY.total_seconds())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host_proc = Path(os.environ.get("HNS_SOURCE_PROC", "/host/proc"))
    host_root = Path(os.environ.get("HNS_SOURCE_ROOT", "/host"))
    out = Path(os.environ.get("HNS_HOSTINFO_DIR", "/var/lib/hns-hostinfo"))
    backups = os.environ.get("HNS_BACKUP_SOURCE", "").strip()
    out.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_storage_loop, args=(host_root, out), daemon=True).start()
    last_root = 0.0
    while True:
        try:
            if time.monotonic() - last_root >= ROOT_EVERY_SECONDS or not (out / "root").exists():
                mirror_root(host_root, out)
                last_root = time.monotonic()
            mirror_proc(host_proc, out)
            if backups:
                write_backups(Path(backups), out)
        except Exception:
            log.exception("host snapshot failed")
        time.sleep(PROC_EVERY_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
