"""Adapter roles from a fake sysfs tree. No radio hardware."""

import importlib.util
from pathlib import Path


def _checks():
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "monitor" / "checks.py", Path("/monitor/checks.py")]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise FileNotFoundError("monitor/checks.py")
    spec = importlib.util.spec_from_file_location("monitor_checks", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _iface(root: Path, name: str, target: Path, carrier: str, modalias: str = "") -> None:
    entry = root / name
    (entry / "wireless").mkdir(parents=True)
    (entry / "carrier").write_text(carrier + "\n", encoding="utf-8")
    target.mkdir(parents=True, exist_ok=True)
    if modalias:
        (target / "modalias").write_text(modalias, encoding="utf-8")
    (entry / "device").symlink_to(target, target_is_directory=True)


def test_one_adapter_is_the_link(tmp_path: Path):
    checks = _checks()
    builtin = tmp_path / "bus" / "pci" / "0000"
    _iface(tmp_path, "wlan0", builtin, "1")
    roles = checks.wifi_roles(tmp_path, ipv4_of=lambda name: "10.0.0.2" if name == "wlan0" else None)
    assert roles == {"link": "wlan0", "radio": None}


def test_usb_mediatek_is_the_radio_when_a_builtin_carries_traffic(tmp_path: Path):
    checks = _checks()
    builtin = tmp_path / "bus" / "pci" / "0000"
    generic = tmp_path / "bus" / "usb2" / "2-1"
    mediatek = tmp_path / "bus" / "usb1" / "1-1"
    _iface(tmp_path, "wlan0", builtin, "1")
    _iface(tmp_path, "wlan1", generic, "0", "usb:v0b05p")
    _iface(tmp_path, "wlan2", mediatek, "0", "usb:v0E8Dp7615d0000")
    _iface(tmp_path, "hnwradio0mon", mediatek, "1", "usb:v0E8Dp7615d0000")
    (tmp_path / "hnwradio0mon" / "type").write_text("803\n", encoding="utf-8")
    roles = checks.wifi_roles(tmp_path, ipv4_of=lambda name: "10.0.0.2" if name == "wlan0" else None)
    assert roles["link"] == "wlan0"
    assert roles["radio"] == "wlan2"


def test_builtin_wins_the_link_when_both_adapters_have_an_address(tmp_path: Path):
    checks = _checks()
    builtin = tmp_path / "bus" / "pci" / "0000"
    usb = tmp_path / "bus" / "usb1" / "1-1"
    _iface(tmp_path, "wlan0", builtin, "1")
    _iface(tmp_path, "wlxusb", usb, "1", "usb:v0e8dp")
    roles = checks.wifi_roles(tmp_path, ipv4_of=lambda _name: "10.0.0.8")
    assert roles["link"] == "wlan0"
    assert roles["radio"] == "wlxusb"


def test_link_ssid_comes_from_the_iw_association():
    checks = _checks()
    text = "Connected to 7c:9a:54:76:24:5d (on wlan0)\n\tSSID: GenZen-WiFi\n\tfreq: 5785\n"
    assert checks.parse_link_ssid(text) == "GenZen-WiFi"
    assert checks.parse_link_bssid(text) == "7c:9a:54:76:24:5d"
    assert checks.parse_link_ssid("Not connected.\n") == ""
    assert checks.parse_link_bssid("Not connected.\n") == ""
    assert checks.parse_link_ssid("\tSSID: my:net\n") == "my:net"


def test_link_scans_wait_for_tests_and_fifteen_minutes():
    checks = _checks()
    assert checks.scan_may_run(role="radio", last_link_scan=0, now=10, link_busy=True, forced=False)
    assert not checks.scan_may_run(role="link", last_link_scan=0, now=10, link_busy=True, forced=True)
    assert checks.scan_may_run(role="link", last_link_scan=0, now=10, link_busy=False, forced=True)
    assert not checks.scan_may_run(role="link", last_link_scan=100, now=200, link_busy=False, forced=False)
    assert checks.scan_may_run(role="link", last_link_scan=100, now=1000, link_busy=False, forced=False)
    assert checks.link_lock.acquire(blocking=False)
    try:
        assert checks.link_is_busy()
    finally:
        checks.link_lock.release()
    assert not checks.link_is_busy()


def test_lan_reenroll_requires_the_current_device_key():
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "monitor" / "agent.py", Path("/monitor/agent.py")]
    path = next(item for item in candidates if item.is_file())
    import sys

    sys.path.insert(0, str(path.parent))
    for name in ("agent_state", "enrollment", "health", "radio", "scheduler"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("monitor_agent_setup", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.setup_allowed(False, "10.0.0.8", "", "") is True
    assert module.setup_allowed(True, "127.0.0.1", "", "secret") is True
    assert module.setup_allowed(True, "10.0.0.8", "secret", "secret") is True
    assert module.setup_allowed(True, "10.0.0.8", "wrong", "secret") is False
    assert "sudo hnw-reset" in module.PAGE


def test_lan_address_comes_from_the_adapters_not_the_route_to_the_server():
    checks = _checks()
    addresses = {"eth0": "192.0.2.20", "wlan0": "192.0.2.21"}

    def reader(name):
        return addresses.get(name)

    # The wired NIC wins when both are on the LAN.
    assert checks.lan_address({"wired": "eth0", "link": "wlan0", "radio": "wlan1"}, reader) == "192.0.2.20"
    # Without a wired address it falls back to the Wi-Fi NIC that carries traffic.
    assert checks.lan_address({"wired": "eth0", "link": "wlan0", "radio": None}, {"wlan0": "192.0.2.21"}.get) == "192.0.2.21"
    assert checks.lan_address({"wired": None, "link": "wlan0", "radio": None}, reader) == "192.0.2.21"
    # The monitor radio only listens; it is never the LAN address.
    assert checks.lan_address({"wired": None, "link": None, "radio": "wlan1"}, {"wlan1": "192.0.2.22"}.get) is None
    # A tunnel interface is never one of the roles, so its address cannot be reported.
    assert checks.lan_address({"wired": None, "link": None, "radio": None}, lambda name: "10.7.0.2") is None
