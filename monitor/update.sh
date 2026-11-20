#!/bin/sh
# Replace the agent code and restart monitor.
# Enrollment, WireGuard, and /etc/monitor are left as they are.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./update.sh" >&2
  exit 1
fi

cd "$(dirname "$0")"

files="agent.py agent_state.py enrollment.py health.py radio.py scheduler.py checks.py kismet_view.py rf_spool.py synthetic.py speedtest.py cloudflare_speed.py lan_scan.py tunnel_watch.py"
for file in $files; do
  if [ ! -f "$file" ]; then
    echo "Missing $file. Unpack monitor.tar.gz and run this script from that directory." >&2
    exit 1
  fi
done
for file in restore_tunnel.sh monitor.service hnw-restore-tunnel@.path hnw-restore-tunnel@.service; do
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
install -o root -g root -m 755 restore_tunnel.sh /opt/monitor/restore_tunnel.sh
if [ -f tune_wifi.sh ]; then
  install -o root -g root -m 755 tune_wifi.sh /opt/monitor/tune_wifi.sh
fi
if [ -f hnw-wifi-tune.service ] && [ -d /etc/systemd/system ]; then
  install -o root -g root -m 644 hnw-wifi-tune.service /etc/systemd/system/hnw-wifi-tune.service
  systemctl daemon-reload
  systemctl enable hnw-wifi-tune.service 2>/dev/null || true
fi
# The agent cannot use sudo (its service sets NoNewPrivileges), so a root systemd unit restarts a dead
# tunnel when the agent leaves a request in /run/monitor. The old sudoers rule never worked and is removed.
rm -f /etc/sudoers.d/hnw-restore-tunnel
if [ -d /etc/systemd/system ]; then
  install -o root -g root -m 644 hnw-restore-tunnel@.path hnw-restore-tunnel@.service /etc/systemd/system/
  install -o root -g root -m 644 monitor.service /etc/systemd/system/monitor.service
  systemctl daemon-reload
  systemctl enable --now hnw-restore-tunnel@wg0.path hnw-restore-tunnel@wg1.path
fi
if [ -f rtnp-send ]; then
  install -o root -g root -m 755 rtnp-send /opt/monitor/rtnp-send
fi
# The service runs this program, so it must be the real one, owned by root. A stand-in script that starts
# a copy somewhere else (a temporary folder, say) stops working when that copy is cleaned away.
if [ -f /opt/monitor/rtnp-send ]; then
  if [ "$(head -c 4 /opt/monitor/rtnp-send | od -An -c | tr -d ' \n')" = "177ELF" ]; then
    chown root:root /opt/monitor/rtnp-send
    chmod 755 /opt/monitor/rtnp-send
  else
    echo "WARNING: /opt/monitor/rtnp-send is not the rtnp-send program. Build it with scripts/build-rtnp.sh," >&2
    echo "pack again with monitor/pack.sh and run this script from the new package." >&2
  fi
fi
if [ -f rtnp_bind.c ] && command -v cc >/dev/null 2>&1; then
  cc -O2 -fPIC -shared -o /opt/monitor/rtnp-bind.so rtnp_bind.c
  chown root:root /opt/monitor/rtnp-bind.so
  chmod 644 /opt/monitor/rtnp-bind.so
elif [ -f rtnp-bind.so ]; then
  install -o root -g root -m 644 rtnp-bind.so /opt/monitor/rtnp-bind.so
fi

# Keep the Wi-Fi link radio awake so power-save naps do not add latency spikes to probes,
# unmanage secondary capture/P2P interfaces, pin 5 GHz BSSID, and apply multicore RPS + fq_codel.
if [ -d /etc/NetworkManager/conf.d ]; then
  cat > /etc/NetworkManager/conf.d/hnw-wifi-powersave.conf <<'EOF'
[connection]
wifi.powersave = 2
EOF
  cat > /etc/NetworkManager/conf.d/hnw-radio.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:hnwradio*;interface-name:p2p-dev-*
EOF
fi
link_wifi="$(python3 -c 'import sys; sys.path.insert(0, "/opt/monitor"); from checks import wifi_roles; print(wifi_roles().get("link") or "")' 2>/dev/null || true)"
if [ -n "$link_wifi" ] && command -v iw >/dev/null 2>&1; then
  iw dev "$link_wifi" set power_save off 2>/dev/null || true
fi
if [ -x /opt/monitor/tune_wifi.sh ]; then
  /opt/monitor/tune_wifi.sh || true
fi

# LAN scan tools for Airwaves Security (on-demand ARP + Nmap).
need=""
command -v nmap >/dev/null 2>&1 || need="$need nmap"
dpkg -s python3-scapy >/dev/null 2>&1 || need="$need python3-scapy"
if [ -n "$need" ]; then
  apt-get update
  apt-get install -y $need
fi

systemctl restart monitor.service

version=$(sed -n 's/^AGENT_VERSION = "\(.*\)"/\1/p' /opt/monitor/agent_state.py)
echo "Agent ${version:-unknown} is running. Enrollment, tunnels, and /etc/monitor were left in place."
