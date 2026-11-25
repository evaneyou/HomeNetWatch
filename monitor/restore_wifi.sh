#!/bin/sh
# Tell NetworkManager to rejoin Wi-Fi on any adapter it has given up on.
# After a failed security handshake NetworkManager asks for a new password, finds nobody to ask and stops
# retrying, although the saved password is right. Connecting the device again clears that.
# Takes no arguments: the adapters come from NetworkManager, never from the caller.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (the agent asks through hnw-restore-wifi.service)." >&2
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "NetworkManager is not in use here; nothing to do."
  exit 0
fi

status=0
found=0
for dev in $(nmcli -t -f DEVICE,TYPE,STATE device status | awk -F: '$2 == "wifi" && $3 == "disconnected" { print $1 }'); do
  found=1
  echo "Rejoining Wi-Fi on ${dev}."
  nmcli --wait 45 device connect "$dev" || status=1
done
if [ "$found" -eq 0 ]; then
  echo "No disconnected Wi-Fi adapter."
fi
exit "$status"
