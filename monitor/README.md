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
`pack.sh` includes it. Use the same RTN_PackGen release as the server's receiver. From RTNP 0.2.2 the sender has
`--video-ease-in`, which the agent uses when the server asks (it checks the sender's help text first, so an older
sender still runs, without the gentle start).

The reports of all flows are sent together, about two requests every 10 seconds, to `/api/v1/synthetic/samples/batch` (one report at a time to `/api/v1/synthetic/samples` on a server that has no batch address). The server leaves a check that runs in bursts (Video conference: 75 seconds every 5 minutes) out of the assignment between bursts, and the agent stops and starts that flow as the assignment changes. Install and update put it at
`/opt/monitor/rtnp-send` owned by root, and warn when the file there is not the real program (for example a script
that starts a copy kept somewhere else).

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

The agent pings `10.7.0.1` (wired `wg0`) and `10.7.1.1` (Wi-Fi `wg1`) about every 30 seconds. After two misses it asks a root helper to run `/opt/monitor/restore_tunnel.sh wgN`, which stops and starts that `wg-quick` unit so PostUp re-pins the current default route. While home internet on that link is also down, restores are limited to once per 15 minutes. Once public access is back (probe of `1.1.1.1` / `8.8.8.8` on the NIC) but the tunnel peer is still dead, it retries about once a minute so a stale pin from the outage does not leave Wi-Fi apps stranded. A dashboard **Restart probe** leaves the tunnels alone; the watch checks them as soon as the agent is back.

The agent runs as user `monitor` with `NoNewPrivileges`, so it cannot use `sudo`. It asks by creating `/run/monitor/restore-wgN`. systemd watches for that file (`hnw-restore-tunnel@wgN.path`) and runs the script as root (`hnw-restore-tunnel@wgN.service`), which writes the outcome to `/run/hnw-restore-wgN.result` and removes the request. Install and update enable both path units. `journalctl -u 'hnw-restore-tunnel@*'` shows each restart.

The same loop watches the Wi-Fi link on a Pi that has a Wi-Fi tunnel. After a failed security handshake NetworkManager treats the saved password as wrong, finds nobody to ask for a new one and stops retrying, so the Pi stays off Wi-Fi. When the Wi-Fi adapter has had no link for four checks (about two minutes) the agent creates `/run/monitor/restore-wifi`, and `hnw-restore-wifi.path` runs `restore_wifi.sh` as root, which tells NetworkManager to connect each disconnected Wi-Fi adapter again with its saved network. It asks at most once every 5 minutes. Capture radios (`hnwradio*`, monitor mode) are left alone. `journalctl -u hnw-restore-wifi` shows each attempt.

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
| Outbound, outside the tunnel | `speed.cloudflare.com` `443` (wired link) | Cloudflare Speed Test, about once an hour |
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
| `cloudflare_speed.py` | Cloudflare Speed Test: download, upload and latency against `speed.cloudflare.com` on the wired link |
| `install.sh` | First install: packages, systemd unit, and tunnels when configs are present |
| `update.sh` | Replaces the agent code and restarts `monitor` |
| `repair-kismet-memory.sh` | Caps Kismet memory and enables the daily restart on an existing Pi |
| `kismet-restart.service`, `kismet-restart.timer` | Restart Kismet once a day so the capture helper cannot grow without bound |
| `monitor.service` | `systemd` unit (runs with `CAP_NET_RAW` and `CAP_NET_BIND_SERVICE` only) |
| `avahi-monitor.service` | Advertises the setup page as `<hostname>.local` |
| `synthetic.py` | Real-time application traffic on the physical NIC |
| `tunnel_watch.py` | Pings tunnel peers and restarts a dead `wg0` / `wg1` |
| `restore_tunnel.sh` | Root helper that restarts one WireGuard unit |
| `restore_wifi.sh` | Root helper that tells NetworkManager to rejoin Wi-Fi on a disconnected adapter |
| `hnw-restore-wifi.path`, `hnw-restore-wifi.service` | Run `restore_wifi.sh` as root when the agent leaves a request in `/run/monitor` |
| `hnw-restore-tunnel@.path`, `hnw-restore-tunnel@.service` | Run `restore_tunnel.sh` as root when the agent leaves a request in `/run/monitor` |
| `tune_wifi.sh` | Unmanages `hnwradio*`/`p2p-dev-*`, keeps the Wi-Fi connection on 5 GHz without tying it to one access point, and sets multicore IRQ/RPS + `fq_codel` |
| `hnw-wifi-tune.service` | `systemd` oneshot unit that applies `tune_wifi.sh` after boot |
| `lan_scan.py` | On-demand Scapy ARP + Nmap for Airwaves Security |
| `rtnp_bind.c` | `LD_PRELOAD` shim that pins a measurement socket to one link; `install.sh` compiles it to `rtnp-bind.so` |
| `pack.sh` | Builds `dist/monitor.tar.gz` (adds `rtnp-send` when it is present) |
