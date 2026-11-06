# Remote agent (Raspberry Pi)

The agent runs on a Raspberry Pi or other Linux PC inside your home network. It serves a small
setup page on the LAN, reports device health to the server, and runs what the server assigns to it
over the wired and Wi-Fi links: HTTP and web-service checks, ICMP ping, MTR path traces (including
on-demand host traces), the Bandwidth Test and live Speed Test, real-time voice/video/remote-desktop
flows with `rtnp-send`, Network Test stress sessions, Wi-Fi scans and Kismet capture, and on-demand
LAN scans. It also watches its WireGuard tunnels and restores a dead one.

The agent uses only the Python 3 standard library. Its version is `AGENT_VERSION` in
[`agent_state.py`](./agent_state.py).

## Requirements

- Raspberry Pi OS or another Debian-based Linux with `systemd`
- Python 3 and root access for installation
- Network access to the server (see [Network](#network))

The installer adds `iputils-ping`, `mtr-tiny`, `iw`, `nmap` and `python3-scapy` if they are
missing, `wireguard` when a tunnel config is present, and Kismet with `--kismet`. It compiles
`rtnp_bind.c` when a C compiler is present (without it, Wi-Fi measurements would use the wired link).

The real-time flows need the `rtnp-send` binary from
[RTN_PackGen](https://github.com/evaneyou/RTN_PackGen). It is not committed: build it once with
`./scripts/build-rtnp.sh` (Docker, cross-compiles for 64-bit ARM into `monitor/rtnp-send`) and
`pack.sh` includes it. Use the same RTN_PackGen release as the server's receiver.

## Install

1. On a machine with this repository, build the package:

   ```bash
   ./monitor/pack.sh        # writes monitor/dist/monitor.tar.gz
   ```

2. Copy `monitor.tar.gz` to the Pi, then install (add `--kismet` for Airwaves capture on a second
   Wi-Fi adapter):

   ```bash
   mkdir monitor && tar -xzf monitor.tar.gz -C monitor
   cd monitor
   sudo ./install.sh
   ```

3. From a PC on the same LAN open `http://<hostname>.local/` (or the Pi's IP address on port 80).
   The page shows this Pi's **installer code**.
4. In the dashboard open **Settings → Remote Agent Onboarding** and register that installer code with
   a device name (and an optional location). A registration is valid for 24 hours.
5. On the setup page enter the server address (for example `https://hns.example.com`) and the
   server's TLS key pins, then choose **Connect**. Print the pins on the server with
   `docker compose exec agent-api python -m app.tls pins`.
6. The Pi checks in over HTTPS on port 8848 with a signed request. Choose **Approve** on its card in
   the dashboard (as a new agent, or to replace an existing one and keep its history and tunnel
   address). The Pi receives its wired (`wg0`) and Wi-Fi (`wg1`) tunnels with the approval; no
   `wgN.conf` files need copying.

To start over on a Pi, run `sudo hnw-reset`. That clears `/etc/monitor/config.json`.

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

On-demand scans from the dashboard use Scapy (ARP discovery) and Nmap (common TCP ports) on the Pi's wired LAN only. Each host is labelled with its maker, looked up from the MAC's IEEE prefix in the OUI table that ships with Nmap (`/usr/share/nmap/nmap-mac-prefixes`), and with the name it announces over mDNS (a reverse PTR query to `224.0.0.251`, no extra tools). A locally administered MAC (a phone or laptop randomizing its address) is reported as `Private address`. Install and update pull in `nmap` and `python3-scapy` when they are missing. WireGuard addresses are never scanned.

## Tunnel watch

The agent pings `10.7.0.1` (wired `wg0`) and `10.7.1.1` (Wi-Fi `wg1`) about every 30 seconds. After two misses it runs `sudo -n /opt/monitor/restore_tunnel.sh wgN`, which stops and starts that `wg-quick` unit so PostUp re-pins the current default route. While home internet on that link is also down, restores are limited to once per 15 minutes. Once public access is back (probe of `1.1.1.1` / `8.8.8.8` on the NIC) but the tunnel peer is still dead, it retries about once a minute so a stale pin from the outage does not leave Wi-Fi apps stranded. A dashboard **Restart probe** also bounces both tunnels before the agent process replaces itself. Install and update install a sudoers drop-in that allows only that script for user `monitor`.

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
| Outbound | Server `8848` (HTTPS) | Enrollment check-in, with a pinned key |
| Outbound | Server `51820/udp` | WireGuard tunnels (`wg0` wired, `wg1` Wi-Fi) |
| Outbound, in the tunnel | `10.7.0.1` / `10.7.1.1` port `8847` | Health reports, config and results |
| Outbound, in the tunnel | `10.7.0.1` / `10.7.1.1` port `8999` (TCP) and `8080` | RTNP control channel, LibreSpeed |
| Outbound, outside the tunnel | Server public IP `8998/udp` | Real-time flow packets (measures the real ISP path) |
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
| `kismet_view.py` | Turns Kismet JSON into the Airwaves upload |
| `rf_spool.py` | SQLite queue for Airwaves uploads while the server is unreachable (5 days, at most 50 MB) |
| `scheduler.py` | Due tests, on-demand traces, and synthetic traffic |
| `checks.py` | HTTP, ping, MTR, and Bandwidth Test checks |
| `speedtest.py` | Live Speed Test (speaks the LibreSpeed protocol and streams samples) |
| `install.sh` | First install: packages, systemd unit, and tunnels when configs are present |
| `update.sh` | Replaces the agent code and restarts `monitor` |
| `repair-kismet-memory.sh` | Caps Kismet memory and enables the daily restart on an existing Pi |
| `kismet-restart.service`, `kismet-restart.timer` | Restart Kismet once a day so the capture helper cannot grow without bound |
| `monitor.service` | `systemd` unit (runs with `CAP_NET_RAW` and `CAP_NET_BIND_SERVICE` only) |
| `avahi-monitor.service` | Advertises the setup page as `<hostname>.local` |
| `synthetic.py` | Real-time application traffic on the physical NIC |
| `tunnel_watch.py` | Pings tunnel peers and restarts a dead `wg0` / `wg1` |
| `restore_tunnel.sh` | Root helper invoked via sudoers to restart one WireGuard unit |
| `tune_wifi.sh` | Unmanages `hnwradio*`/`p2p-dev-*`, pins 5 GHz BSSID, and sets multicore IRQ/RPS + `fq_codel` |
| `hnw-wifi-tune.service` | `systemd` oneshot unit that applies `tune_wifi.sh` after boot |
| `lan_scan.py` | On-demand Scapy ARP + Nmap for Airwaves Security |
| `rtnp_bind.c` | `LD_PRELOAD` shim that pins a measurement socket to one link; `install.sh` compiles it to `rtnp-bind.so` |
| `pack.sh` | Builds `dist/monitor.tar.gz` (adds `rtnp-send` when it is present) |
