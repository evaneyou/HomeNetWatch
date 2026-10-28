# Remote agent (Raspberry Pi)

The agent runs on a Raspberry Pi or other Linux PC inside your home network. It serves a small
setup page on the LAN, reports device health to the server, and runs the connectivity tests the
server assigns to it: HTTP, ICMP ping, MTR, and Bandwidth Test.

The agent uses only the Python 3 standard library. Its version is `AGENT_VERSION` in
[`agent_state.py`](./agent_state.py).

## Requirements

- Raspberry Pi OS or another Debian-based Linux with `systemd`
- Python 3 and root access for installation
- Network access to the server (see [Network](#network))

The installer adds `iputils-ping` and `mtr-tiny` if they are missing, plus `wireguard`
and `iw` when a tunnel config is present.

## Install

1. In the dashboard open **Settings → Remote Agent Onboarding** and add the device. Note its
   name and key. If you issue tunnels, save the wired tunnel as `wg0.conf` and the wireless tunnel
   as `wg1.conf`.
2. On a machine with this repository, build the package:

   ```bash
   ./monitor/pack.sh        # writes monitor/dist/monitor.tar.gz
   ```

3. Copy `monitor.tar.gz` (and any `wg0.conf` or `wg1.conf`) to the Pi, then install:

   ```bash
   mkdir monitor && tar -xzf monitor.tar.gz -C monitor
   cd monitor
   sudo ./install.sh
   ```

4. From a PC on the same LAN open `http://<hostname>.local/` (or the Pi's IP address on port 80).
5. Enter the device name, the server address and the device key, then choose **Connect**. The
   device name must match the dashboard exactly, including capital letters.

## Update

When the agent code changes, pack it again and copy `monitor.tar.gz` to the Pi. Unpack it in a new directory and run the update. This replaces the Python files and restarts `monitor`. It does not touch enrollment, the WireGuard tunnels, or `/etc/monitor`.

```bash
./monitor/pack.sh
# copy monitor/dist/monitor.tar.gz to the Pi, then:
mkdir -p monitor-update && tar -xzf monitor.tar.gz -C monitor-update
cd monitor-update
sudo ./update.sh
```

A first install still uses `sudo ./install.sh`.

## LAN scan (Airwaves Security)

On-demand scans from the dashboard use Scapy (ARP discovery) and Nmap (common TCP ports) on the Pi's wired LAN only. Install and update pull in `nmap` and `python3-scapy` when they are missing. WireGuard addresses are never scanned.

## Airwave memory

Kismet's Linux capture helper grows while it channel-hops. A first install with `--kismet` already caps that cgroup and restarts Kismet once a day. On a Pi that was set up earlier, run the repair from an unpacked package (enrollment and tunnels stay as they are):

```bash
sudo ./repair-kismet-memory.sh
```

The agent starts at boot as the `monitor` user under `systemd`. Check it with:

```bash
systemctl status monitor
journalctl -u monitor -f
```

## Network

| Direction | Destination | Purpose |
|-----------|-------------|---------|
| Outbound | Server `8847` (HTTP) or the WireGuard address `10.7.0.1` | Health reports and results |
| Outbound | Server `8848` (HTTPS) | Enrollment check-in, with a pinned key |
| Outbound | Server `51820/udp` | WireGuard tunnel |
| Inbound (LAN only) | Pi `80` | Setup page |

Setup accepts HTTPS, or HTTP to a private LAN address.

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Setup page, and the loops that start the rest |
| `agent_state.py` | Shared paths, locks, and `AGENT_VERSION` |
| `enrollment.py` | Identity, TLS pins, WireGuard keys, and enrollment |
| `health.py` | Device health and the report loop |
| `radio.py` | Wi-Fi survey upload and Kismet capture |
| `scheduler.py` | Due tests, on-demand traces, and synthetic traffic |
| `checks.py` | HTTP, ping, MTR, and Bandwidth Test checks |
| `install.sh` | First install: packages, systemd unit, and tunnels when configs are present |
| `update.sh` | Replaces the agent code and restarts `monitor` |
| `repair-kismet-memory.sh` | Caps Kismet memory and enables the daily restart on an existing Pi |
| `kismet-restart.timer` | Restarts Kismet once a day so the capture helper cannot grow without bound |
| `monitor.service` | `systemd` unit (runs with `CAP_NET_RAW` and `CAP_NET_BIND_SERVICE` only) |
| `avahi-monitor.service` | Advertises the setup page as `<hostname>.local` |
| `synthetic.py` | Real-time application traffic on the physical NIC |
| `rtnp_bind.c` | `LD_PRELOAD` shim that pins a measurement socket to one link; `install.sh` compiles it to `rtnp-bind.so` |
| `pack.sh` | Builds `dist/monitor.tar.gz` |
