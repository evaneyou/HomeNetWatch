#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p dist
files="agent.py agent_state.py enrollment.py health.py radio.py scheduler.py checks.py kismet_view.py rf_spool.py synthetic.py speedtest.py lan_scan.py tunnel_watch.py restore_tunnel.sh tune_wifi.sh hnw-wifi-tune.service rtnp_bind.c install.sh update.sh repair-kismet-memory.sh kismet-restart.service kismet-restart.timer monitor.service avahi-monitor.service"
if [ -f rtnp-send ]; then
  files="$files rtnp-send"
fi
# shellcheck disable=SC2086
tar -czf dist/monitor.tar.gz $files
echo "Wrote $(pwd)/dist/monitor.tar.gz"
