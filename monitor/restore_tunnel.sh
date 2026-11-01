#!/bin/sh
# Restart one WireGuard tunnel used by the HomeNetWatch agent.
# Allowed interfaces: wg0 (wired) or wg1 (wireless).
# Re-running wg-quick re-applies PostUp route pins so a stale default gateway
# from an ISP outage does not leave the measurement tunnel stranded.
set -eu

iface="${1:-}"
case "$iface" in
  wg0|wg1) ;;
  *)
    echo "Usage: $0 wg0|wg1" >&2
    exit 2
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (or via the monitor sudoers rule)." >&2
  exit 1
fi

if [ ! -f "/etc/wireguard/${iface}.conf" ]; then
  echo "No /etc/wireguard/${iface}.conf; nothing to restore." >&2
  exit 1
fi

# Prefer a clean down/up so PostUp re-discovers the current default route on the NIC.
systemctl stop "wg-quick@${iface}" 2>/dev/null || true
ip link delete "$iface" 2>/dev/null || true
systemctl start "wg-quick@${iface}"
# Give the handshake a moment before callers re-probe.
sleep 3
if ! ip link show "$iface" >/dev/null 2>&1; then
  echo "Interface ${iface} did not come up." >&2
  exit 1
fi
if [ -x /opt/monitor/tune_wifi.sh ]; then
  /opt/monitor/tune_wifi.sh || true
fi
echo "Restored ${iface}."
