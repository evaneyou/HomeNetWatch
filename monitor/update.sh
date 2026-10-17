#!/bin/sh
# Replace the agent code and restart monitor.
# Enrollment, WireGuard, and /etc/monitor are left as they are.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./update.sh" >&2
  exit 1
fi

cd "$(dirname "$0")"

files="agent.py agent_state.py enrollment.py health.py radio.py scheduler.py checks.py kismet_view.py rf_spool.py synthetic.py speedtest.py"
for file in $files; do
  if [ ! -f "$file" ]; then
    echo "Missing $file. Unpack monitor.tar.gz and run this script from that directory." >&2
    exit 1
  fi
done

if [ ! -f /opt/monitor/agent.py ]; then
  echo "No agent is installed here. For a first install, run sudo ./install.sh." >&2
  exit 1
fi

install -d -o root -g root -m 755 /opt/monitor
rm -rf /opt/monitor/__pycache__
# shellcheck disable=SC2086
install -o root -g root -m 644 $files /opt/monitor/
if [ -f rtnp-send ]; then
  install -o root -g root -m 755 rtnp-send /opt/monitor/rtnp-send
fi
if [ -f rtnp_bind.c ] && command -v cc >/dev/null 2>&1; then
  cc -O2 -fPIC -shared -o /opt/monitor/rtnp-bind.so rtnp_bind.c
  chown root:root /opt/monitor/rtnp-bind.so
  chmod 644 /opt/monitor/rtnp-bind.so
elif [ -f rtnp-bind.so ]; then
  install -o root -g root -m 644 rtnp-bind.so /opt/monitor/rtnp-bind.so
fi

# Keep the Wi-Fi link radio awake so power-save naps do not add latency spikes to probes.
if [ -d /etc/NetworkManager/conf.d ]; then
  cat > /etc/NetworkManager/conf.d/hnw-wifi-powersave.conf <<'EOF'
[connection]
wifi.powersave = 2
EOF
fi
link_wifi="$(python3 -c 'import sys; sys.path.insert(0, "/opt/monitor"); from checks import wifi_roles; print(wifi_roles().get("link") or "")' 2>/dev/null || true)"
if [ -n "$link_wifi" ] && command -v iw >/dev/null 2>&1; then
  iw dev "$link_wifi" set power_save off 2>/dev/null || true
fi

systemctl restart monitor.service

version=$(sed -n 's/^AGENT_VERSION = "\(.*\)"/\1/p' /opt/monitor/agent_state.py)
echo "Agent ${version:-unknown} is running. Enrollment, tunnels, and /etc/monitor were left in place."
