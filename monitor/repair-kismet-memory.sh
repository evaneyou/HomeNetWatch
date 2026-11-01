#!/bin/sh
# Bound Kismet memory on a Pi that already has airwave capture.
# Does not touch enrollment, WireGuard, or the monitor agent.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./repair-kismet-memory.sh" >&2
  exit 1
fi

cd "$(dirname "$0")"

if ! systemctl cat kismet.service >/dev/null 2>&1; then
  echo "Kismet is not installed on this Pi." >&2
  exit 1
fi

site=/etc/kismet/kismet_site.conf
if [ ! -f "$site" ]; then
  echo "Missing $site. Re-run sudo ./install.sh --kismet." >&2
  exit 1
fi

ensure_opt() {
  key=$1
  value=$2
  if grep -q "^${key}=" "$site"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$site"
  else
    printf '%s=%s\n' "$key" "$value" >>"$site"
  fi
}

ensure_opt enable_logging false
ensure_opt tracker_device_timeout 600
ensure_opt tracker_max_devices 5000
ensure_opt dot11_keep_eapol false
ensure_opt ulimit_mbytes 768
ensure_opt channel_hop_speed 1/sec
sed -i 's|^source=hnwradio0:name=hnw-radio,hop=true$|source=hnwradio0:name=hnw-radio,hop=true,hop_rate=1/sec|' "$site"
chown root:kismet "$site"
chmod 0640 "$site"

install -d -m 755 /etc/systemd/system/kismet.service.d
cat > /etc/systemd/system/kismet.service.d/memory.conf <<'EOF'
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
fi

systemctl daemon-reload
systemctl enable --now kismet-restart.timer
systemctl restart kismet.service

echo "Kismet was restarted with a memory cap and a daily 04:15 restart. Enrollment and tunnels were left alone."
