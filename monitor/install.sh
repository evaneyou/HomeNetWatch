#!/bin/sh
set -eu
kismet=0
for arg in "$@"; do
  if [ "$arg" = "--kismet" ]; then
    kismet=1
  else
    echo "Unknown option: $arg" >&2
    echo "Usage: sudo ./install.sh [--kismet]" >&2
    exit 1
  fi
done
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./install.sh"
  exit 1
fi
cd "$(dirname "$0")"
if ! id monitor >/dev/null 2>&1; then
  useradd --system --home /opt/monitor --shell /usr/sbin/nologin monitor
fi
install -d -m 755 /opt/monitor
install -m 644 agent.py agent_state.py enrollment.py health.py radio.py scheduler.py checks.py kismet_view.py rf_spool.py synthetic.py speedtest.py /opt/monitor/
if [ -f rtnp-send ]; then
  install -m 755 rtnp-send /opt/monitor/rtnp-send
fi
# The sender binds its own UDP port, so the measurement socket can only be moved
# onto one link by this shim. Without it every link measures the default route.
if [ -f rtnp_bind.c ] && command -v cc >/dev/null 2>&1; then
  cc -O2 -fPIC -shared -o /opt/monitor/rtnp-bind.so rtnp_bind.c
  chmod 644 /opt/monitor/rtnp-bind.so
elif [ -f rtnp-bind.so ]; then
  install -m 644 rtnp-bind.so /opt/monitor/rtnp-bind.so
else
  echo "No C compiler and no rtnp-bind.so: Wi-Fi measurements would use the wired link." >&2
fi
install -d -o monitor -g monitor -m 750 /etc/monitor
install -m 644 monitor.service /etc/systemd/system/monitor.service
if [ -d /etc/avahi/services ]; then
  install -m 644 avahi-monitor.service /etc/avahi/services/monitor.service
  systemctl try-restart avahi-daemon || true
else
  echo "Avahi is not installed. The page is still available at this Pi's IP address on port 80."
fi
need=""
command -v ping >/dev/null 2>&1 || need="$need iputils-ping"
command -v mtr >/dev/null 2>&1 || need="$need mtr-tiny"
command -v iw >/dev/null 2>&1 || need="$need iw"
if [ -n "$need" ]; then
  apt-get update
  apt-get install -y $need
fi
# Keep the Wi-Fi link radio awake so power-save naps do not add latency spikes to probes.
install -d -m 755 /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/hnw-wifi-powersave.conf <<'EOF'
[connection]
wifi.powersave = 2
EOF
link_wifi="$(python3 -c 'import sys; sys.path.insert(0, "/opt/monitor"); from checks import wifi_roles; print(wifi_roles().get("link") or "")' 2>/dev/null || true)"
if [ -n "$link_wifi" ] && command -v iw >/dev/null 2>&1; then
  iw dev "$link_wifi" set power_save off 2>/dev/null || true
fi
cat > /usr/local/bin/hnw-reset <<'EOF'
#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo hnw-reset" >&2
  exit 1
fi
rm -f /etc/monitor/config.json /etc/monitor/identity.pem /etc/monitor/wg.key /etc/monitor/wg-wireless.key
systemctl restart monitor.service
echo "Enrollment cleared. Open this Pi's setup page to connect again."
EOF
chmod 755 /usr/local/bin/hnw-reset
systemctl daemon-reload
systemctl enable monitor.service
systemctl restart monitor.service
if [ -f wg0.conf ] || [ -f wg1.conf ]; then
  apt-get update
  apt-get install -y wireguard
  install -d -m 700 /etc/wireguard
fi
if [ -f wg0.conf ]; then
  install -m 600 wg0.conf /etc/wireguard/wg0.conf
  systemctl enable wg-quick@wg0
  systemctl restart wg-quick@wg0
  echo "Wired tunnel is up. Set the server address to http://10.7.0.1:8847."
fi
if [ -f wg1.conf ]; then
  install -m 600 wg1.conf /etc/wireguard/wg1.conf
  systemctl enable wg-quick@wg1
  systemctl restart wg-quick@wg1
  echo "Wireless tunnel is up. Wireless checks use http://10.7.1.1:8847."
fi
host="$(hostname -s)"
echo "Open http://${host}.local/ from a PC on this LAN."
echo "Register the installer code shown on that page, then enter the server address and TLS key pins."

install_kismet() {
  export DEBIAN_FRONTEND=noninteractive
  # shellcheck disable=SC1091
  . /etc/os-release
  codename="${VERSION_CODENAME:-}"
  if [ -z "$codename" ]; then
    echo "Kismet was not installed. This Pi's Debian release is not in /etc/os-release."
    return 0
  fi
  radio="$(python3 -c 'import sys; sys.path.insert(0, "/opt/monitor"); from checks import wifi_roles; print(wifi_roles().get("radio") or "")')"
  if [ -z "$radio" ]; then
    echo "Kismet was not enabled. No second radio adapter was found."
    return 0
  fi
  phy="$(iw dev "$radio" info 2>/dev/null | awk '/wiphy/ {print $2; exit}')"
  if [ -z "$phy" ] || ! iw phy "phy${phy}" info 2>/dev/null | grep -q '\* monitor'; then
    echo "Kismet was not enabled. ${radio} does not support monitor mode."
    return 0
  fi
  keyring=/usr/share/keyrings/kismet-archive-keyring.gpg
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  curl -fsSL https://www.kismetwireless.net/repos/kismet-release.gpg.key | gpg --dearmor --yes -o "$keyring"
  echo "deb [signed-by=${keyring}] https://www.kismetwireless.net/repos/apt/release/${codename} ${codename} main" > /etc/apt/sources.list.d/kismet.list
  if ! apt-get update; then
    echo "Kismet was not installed. The official apt repo has no packages for ${codename}."
    return 0
  fi
  echo "kismet-capture-common kismet-capture-common/install-setuid boolean true" | debconf-set-selections
  apt-get install -y kismet
  if [ ! -e /usr/bin/kismet_cap_linux_wifi ]; then
    echo "kismet_cap_linux_wifi was not installed." >&2
    exit 1
  fi
  dpkg-statoverride --remove /usr/bin/kismet_cap_linux_wifi 2>/dev/null || true
  dpkg-statoverride --update --add root kismet 4750 /usr/bin/kismet_cap_linux_wifi
  mode="$(stat -c '%a %U:%G' /usr/bin/kismet_cap_linux_wifi)"
  if [ "$mode" != "4750 root:kismet" ]; then
    echo "kismet_cap_linux_wifi is ${mode}, expected 4750 root:kismet" >&2
    exit 1
  fi
  mac="$(cat "/sys/class/net/${radio}/address")"
  install -d -m 755 /etc/systemd/network
  cat > /etc/systemd/network/10-hnwradio.link <<EOF
[Match]
MACAddress=${mac}

[Link]
Name=hnwradio0
EOF
  ip link set "$radio" down || true
  if ! ip link set "$radio" name hnwradio0; then
    echo "The radio will be named hnwradio0 after it is re-plugged or the Pi reboots."
  fi
  install -d -m 755 /etc/NetworkManager/conf.d
  cat > /etc/NetworkManager/conf.d/hnw-radio.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:hnwradio0
EOF
  if systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager || true
  fi
  user="hnw$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
  password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  if ! getent group kismet >/dev/null; then
    groupadd --system kismet
  fi
  if ! id kismet >/dev/null 2>&1; then
    useradd --system --gid kismet --home-dir /var/lib/kismet --shell /usr/sbin/nologin kismet
  fi
  install -d -o kismet -g kismet -m 750 /var/lib/kismet
  install -d -o root -g kismet -m 750 /etc/kismet
  umask 077
  cat > /etc/kismet/kismet_site.conf <<EOF
server_name=HomeNetWatch
httpd_bind_address=127.0.0.1
httpd_port=2501
httpd_username=${user}
httpd_password=${password}
source=hnwradio0:name=hnw-radio,hop=true
enable_logging=false
tracker_device_timeout=600
tracker_max_devices=5000
dot11_keep_eapol=false
ulimit_mbytes=768
EOF
  chown root:kismet /etc/kismet/kismet_site.conf
  chmod 0640 /etc/kismet/kismet_site.conf
  HNW_KISMET_USER="$user" HNW_KISMET_PASSWORD="$password" python3 <<'PY'
import json, os
from pathlib import Path
path = Path("/etc/monitor/kismet.json")
path.write_text(json.dumps({
    "username": os.environ["HNW_KISMET_USER"],
    "password": os.environ["HNW_KISMET_PASSWORD"],
    "url": "http://127.0.0.1:2501",
}) + "\n")
os.chmod(path, 0o640)
PY
  chown root:monitor /etc/monitor/kismet.json
  chmod 0640 /etc/monitor/kismet.json
  if [ -e /usr/bin/kismet_cap_hak5_wifi_coconut ]; then
    dpkg-statoverride --remove /usr/bin/kismet_cap_hak5_wifi_coconut 2>/dev/null || true
    dpkg-statoverride --update --add root kismet 0754 /usr/bin/kismet_cap_hak5_wifi_coconut
  fi
  umask 022
  rm -f /tmp/.kismet_cap_linux_wifi_interface_lock
  install -d -m 755 /etc/systemd/system/kismet.service.d
  cat > /etc/systemd/system/kismet.service.d/privileges.conf <<EOF
[Service]
User=kismet
Group=kismet
WorkingDirectory=/var/lib/kismet
EOF
  cat > /etc/systemd/system/kismet.service.d/memory.conf <<EOF
[Service]
# The Linux capture helper grows over days of hopping. Cap the cgroup so a
# runaway capture is restarted instead of taking the Pi down with it.
MemoryHigh=768M
MemoryMax=1G
KillMode=control-group
EOF
  if [ -f kismet-restart.service ] && [ -f kismet-restart.timer ]; then
    install -m 644 kismet-restart.service /etc/systemd/system/kismet-restart.service
    install -m 644 kismet-restart.timer /etc/systemd/system/kismet-restart.timer
    systemctl daemon-reload
    systemctl enable --now kismet-restart.timer
  else
    systemctl daemon-reload
  fi
  systemctl enable kismet.service
  systemctl restart kismet.service
  echo "Kismet is listening on 127.0.0.1:2501 only."
}

if [ "$kismet" -eq 1 ]; then
  install_kismet
fi
