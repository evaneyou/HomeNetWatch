#!/bin/sh
# Tune Raspberry Pi Wi-Fi link stability, NetworkManager radio isolation,
# multicore IRQ / RPS packet steering, and fq_codel AQM on WireGuard tunnels.
# Idempotent: safe to run from install.sh, update.sh, restore_tunnel.sh, and boot.
set -eu

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

link_wifi="$(python3 -c 'import sys; sys.path.insert(0, "/opt/monitor"); from checks import wifi_roles; print(wifi_roles().get("link") or "")' 2>/dev/null || true)"
if [ -z "$link_wifi" ] && [ -d /sys/class/net/wlan0 ]; then
  link_wifi="wlan0"
fi

# 1. Keep the link Wi-Fi radio awake and unmanage Kismet / P2P interfaces in NetworkManager.
if [ -d /etc/NetworkManager/conf.d ]; then
  nm_reload=0

  powersave_conf="/etc/NetworkManager/conf.d/hnw-wifi-powersave.conf"
  powersave_want="[connection]
wifi.powersave = 2"
  if [ ! -f "$powersave_conf" ] || [ "$(cat "$powersave_conf" 2>/dev/null || true)" != "$powersave_want" ]; then
    printf '%s\n' "$powersave_want" > "$powersave_conf"
    nm_reload=1
  fi

  radio_conf="/etc/NetworkManager/conf.d/hnw-radio.conf"
  radio_want="[keyfile]
unmanaged-devices=interface-name:hnwradio*;interface-name:p2p-dev-*"
  if [ ! -f "$radio_conf" ] || [ "$(cat "$radio_conf" 2>/dev/null || true)" != "$radio_want" ]; then
    printf '%s\n' "$radio_want" > "$radio_conf"
    nm_reload=1
  fi

  if [ "$nm_reload" -eq 1 ] && systemctl is-active --quiet NetworkManager 2>/dev/null; then
    nmcli general reload 2>/dev/null || systemctl reload NetworkManager 2>/dev/null || true
  fi
fi

if command -v nmcli >/dev/null 2>&1; then
  for dev_path in /sys/class/net/hnwradio*; do
    [ -e "$dev_path" ] || continue
    dev_name="$(basename "$dev_path")"
    nmcli device set "$dev_name" managed no 2>/dev/null || true
  done
  for p2p_dev in $(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi-p2p" || $1 ~ /^p2p-dev-/ {print $1}'); do
    nmcli device set "$p2p_dev" managed no 2>/dev/null || true
  done
fi

# 2. Disable 802.11 power save and pin the active 5 GHz band + BSSID on the link Wi-Fi connection.
if [ -n "$link_wifi" ] && command -v iw >/dev/null 2>&1; then
  iw dev "$link_wifi" set power_save off 2>/dev/null || true

  if command -v nmcli >/dev/null 2>&1; then
    link_info="$(iw dev "$link_wifi" link 2>/dev/null || true)"
    bssid="$(printf '%s\n' "$link_info" | awk '/Connected to / {print toupper($3); exit}')"
    freq="$(printf '%s\n' "$link_info" | awk '/freq:/ {print int($2); exit}')"
    con_uuid="$(nmcli -g GENERAL.CON-UUID device show "$link_wifi" 2>/dev/null | head -n 1 || true)"

    if [ -n "$con_uuid" ] && [ -n "$bssid" ] && [ -n "$freq" ] && [ "$freq" -ge 5000 ] 2>/dev/null; then
      cur_band="$(nmcli -g 802-11-wireless.band connection show uuid "$con_uuid" 2>/dev/null || true)"
      cur_bssid="$(nmcli -g 802-11-wireless.bssid connection show uuid "$con_uuid" 2>/dev/null | tr '[:lower:]' '[:upper:]' || true)"
      cur_ps="$(nmcli -g 802-11-wireless.powersave connection show uuid "$con_uuid" 2>/dev/null | awk '{print $1}' || true)"

      if [ "$cur_band" != "a" ] || [ "$cur_bssid" != "$bssid" ]; then
        nmcli connection modify uuid "$con_uuid" \
          802-11-wireless.band a \
          802-11-wireless.bssid "$bssid" 2>/dev/null || true
        nmcli device reapply "$link_wifi" 2>/dev/null || true
      fi
      if [ "$cur_ps" != "2" ]; then
        nmcli connection modify uuid "$con_uuid" \
          802-11-wireless.powersave 2 2>/dev/null || true
      fi
    fi
  fi
fi

# 3. Move mmc1 (Broadcom SDIO Wi-Fi) IRQ off CPU0 and enable multicore RPS/RFS across CPUs 1-3.
if [ -d /sys/devices/system/cpu/cpu3 ]; then
  if [ -r /proc/interrupts ]; then
    for irq in $(awk '/[[:space:]]mmc1$/ {gsub(/:/, "", $1); print $1}' /proc/interrupts 2>/dev/null); do
      if [ -w "/proc/irq/${irq}/smp_affinity_list" ]; then
        printf '1\n' > "/proc/irq/${irq}/smp_affinity_list" 2>/dev/null || true
      fi
    done
  fi

  if [ -w /proc/sys/net/core/rps_sock_flow_entries ]; then
    printf '32768\n' > /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || true
  fi
  if [ -w /proc/sys/net/core/netdev_max_backlog ]; then
    printf '4096\n' > /proc/sys/net/core/netdev_max_backlog 2>/dev/null || true
  fi

  for netdev in "$link_wifi" eth0 wg0 wg1; do
    [ -n "$netdev" ] || continue
    rps_cpus="/sys/class/net/${netdev}/queues/rx-0/rps_cpus"
    rps_flows="/sys/class/net/${netdev}/queues/rx-0/rps_flow_cnt"
    if [ -w "$rps_cpus" ]; then
      printf 'e\n' > "$rps_cpus" 2>/dev/null || true
    fi
    if [ -w "$rps_flows" ]; then
      printf '4096\n' > "$rps_flows" 2>/dev/null || true
    fi
  done
fi

# 4. Apply fq_codel on WireGuard tunnels and restore standard MTU quantum on the Wi-Fi interface.
if command -v tc >/dev/null 2>&1; then
  for netdev in wg0 wg1; do
    [ -d "/sys/class/net/${netdev}" ] || continue
    tc qdisc replace dev "$netdev" root fq_codel quantum 300 limit 10240 flows 1024 2>/dev/null || true
  done
  if [ -n "$link_wifi" ] && [ -d "/sys/class/net/${link_wifi}" ]; then
    tc qdisc replace dev "$link_wifi" root fq_codel limit 10240 flows 1024 2>/dev/null || true
  fi
fi

# 5. Slow Kismet channel hopping on the USB capture radio to 1/sec to reduce USB bus and adjacent-antenna RF contention.
kismet_site="/etc/kismet/kismet_site.conf"
if [ -f "$kismet_site" ]; then
  kismet_changed=0
  if grep -q '^source=hnwradio0:name=hnw-radio,hop=true$' "$kismet_site" 2>/dev/null; then
    sed -i 's|^source=hnwradio0:name=hnw-radio,hop=true$|source=hnwradio0:name=hnw-radio,hop=true,hop_rate=1/sec|' "$kismet_site"
    kismet_changed=1
  fi
  if grep -q '^channel_hop_speed=' "$kismet_site" 2>/dev/null; then
    if ! grep -q '^channel_hop_speed=1/sec$' "$kismet_site" 2>/dev/null; then
      sed -i 's|^channel_hop_speed=.*|channel_hop_speed=1/sec|' "$kismet_site"
      kismet_changed=1
    fi
  else
    printf 'channel_hop_speed=1/sec\n' >> "$kismet_site"
    kismet_changed=1
  fi
  if [ "$kismet_changed" -eq 1 ] && systemctl is-active --quiet kismet.service 2>/dev/null; then
    systemctl restart kismet.service 2>/dev/null || true
  fi
fi
