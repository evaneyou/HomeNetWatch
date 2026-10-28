#!/bin/sh
# Restart one WireGuard tunnel used by the HomeNetWatch agent.
# Allowed interfaces: wg0 (wired) or wg1 (wireless).
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

systemctl restart "wg-quick@${iface}"
# Give the handshake a moment before callers re-probe.
sleep 2
if ! ip link show "$iface" >/dev/null 2>&1; then
  echo "Interface ${iface} did not come up." >&2
  exit 1
fi
echo "Restored ${iface}."
