"""hoststat copies only the host files Server Health reads, and the API reads them back like the real thing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.host_snapshot import mirror_proc, mirror_root, write_storage
from app.server_health import list_processes, read_machine, read_storage_snapshot


def _fake_proc(root: Path) -> Path:
    proc = root / "proc"
    (proc / "1" / "net").mkdir(parents=True)
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n")
    (proc / "version").write_text("Linux version 6.8.0-demo (x) #1 SMP\n")
    (proc / "1" / "net" / "dev").write_text(
        "Inter-|\n face |\n    lo: 1 0 0 0 0 0 0 0 1 0 0 0 0 0 0 0\n  eth0: 5000 0 0 0 0 0 0 0 7000 0 0 0 0 0 0 0\n"
    )
    for pid, name in ((1, "systemd"), (42, "postgres")):
        entry = proc / str(pid)
        entry.mkdir(exist_ok=True)
        (entry / "stat").write_text(f"{pid} ({name}) S 0 0 0 0 0 0 0 0 0 0 10 5 0 0 0 0 1 0 0 0 0\n")
        (entry / "cmdline").write_bytes(name.encode() + b"\x00--flag")
        (entry / "statm").write_text("100 50 0 0 0 0 0\n")
        (entry / "environ").write_bytes(b"SECRET=do-not-copy")
    (proc / "1" / "root").mkdir()
    (proc / "1" / "root" / "shadow").write_text("do-not-copy")
    return proc


def test_the_proc_copy_holds_only_counters_and_process_basics(tmp_path):
    proc = _fake_proc(tmp_path)
    out = tmp_path / "hostinfo"
    out.mkdir()
    mirror_proc(proc, out)
    copied = out / "proc"
    assert copied.is_symlink()
    names = sorted(str(path.relative_to(copied.resolve())).replace("\\", "/") for path in copied.resolve().rglob("*") if path.is_file())
    assert names == [
        "1/cmdline", "1/net/dev", "1/stat", "1/statm", "42/cmdline", "42/stat", "42/statm", "meminfo", "stat", "version",
    ]
    assert {row["name"] for row in list_processes(copied)} == {"systemd", "postgres"}
    machine = read_machine(copied, tmp_path, None)
    assert machine["mem_total_mb"] == 2000
    assert machine["host_iface"] == "eth0"


def test_older_copies_are_cleared(tmp_path):
    proc = _fake_proc(tmp_path)
    out = tmp_path / "hostinfo"
    out.mkdir()
    for _ in range(4):
        mirror_proc(proc, out)
    assert len([path for path in out.glob("proc-*") if path.is_dir()]) == 2


def test_the_root_copy_has_the_os_and_package_list_only(tmp_path):
    host = tmp_path / "host"
    (host / "etc").mkdir(parents=True)
    (host / "etc" / "os-release").write_text('PRETTY_NAME="Demo"\n')
    (host / "etc" / "shadow").write_text("do-not-copy")
    (host / "var" / "lib" / "dpkg").mkdir(parents=True)
    (host / "var" / "lib" / "dpkg" / "status").write_text("Package: demo\nVersion: 1\n")
    out = tmp_path / "hostinfo"
    out.mkdir()
    mirror_root(host, out)
    copied = (out / "root").resolve()
    files = sorted(str(path.relative_to(copied)).replace("\\", "/") for path in copied.rglob("*") if path.is_file())
    assert files == ["etc/os-release", "var/lib/dpkg/status"]


def test_storage_snapshot_splits_out_the_database(tmp_path):
    payload = {
        "version": 1,
        "used_bytes": 100,
        "total_bytes": 200,
        "groups": [{"key": "volumes", "title": "Docker volumes", "bytes": 50, "children": []}],
    }
    path = tmp_path / "storage.json"
    when = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    path.write_text(json.dumps({"measured_at": when.isoformat(), "payload": payload}))
    measured_at, found = read_storage_snapshot(path, db_bytes=30)
    assert measured_at == when
    assert found["groups"][0]["children"] == [{"name": "Database", "bytes": 30}, {"name": "Other volumes", "bytes": 20}]
    assert read_storage_snapshot(tmp_path / "missing.json", 0) is None


def test_write_storage_leaves_json(tmp_path):
    host = tmp_path / "host"
    (host / "usr").mkdir(parents=True)
    out = tmp_path / "hostinfo"
    out.mkdir()
    write_storage(host, out)
    data = json.loads((out / "storage.json").read_text())
    assert "groups" in data["payload"] and data["measured_at"]
