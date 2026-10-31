<p align="center">
  <img src="dashboard/public/homenetwatch.png" alt="HomeNetWatch" width="360" />
</p>

<h1 align="center">HomeNetWatch</h1>

<p align="center">
  Monitor the wired and wireless performance of your home network with a Raspberry Pi and Android TV.
</p>

<p align="center">
  <a href="https://github.com/evaneyou/homenetwatch/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/evaneyou/homenetwatch/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="https://github.com/evaneyou/homenetwatch/actions/workflows/android.yml"><img alt="Android agent" src="https://github.com/evaneyou/homenetwatch/actions/workflows/android.yml/badge.svg" /></a>
  <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <img alt="Version" src="https://img.shields.io/badge/version-0.6-informational" />
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange" />
</p>

<p align="center">
  <a href="https://www.homenetwatch.com">Overview &amp; Architecture</a> · <a href="https://live.homenetwatch.com">Live Guest Demo</a>
</p>

Small remote agents on a Raspberry Pi (or any Linux PC) and Android TV boxes (such as an NVIDIA Shield)
run connectivity, real-time voice/video, and throughput tests from inside your home and push the results
to a central server. A web dashboard shows what is healthy, what is degraded, and at which stage a
problem starts: DNS, TCP, TLS, the server's first byte, the measurement tunnel, or the ISP access plane.

## Features

- **Staged path & web checks.** HTTP checks broken into DNS, TCP, TLS and time-to-first-byte stages,
  plus ICMP ping, MTR path traces with BGP comparison and on-demand host tracing, and built-in or custom
  web-service checks.
- **Real-Time Application Health.** Continuous and manual synthetic UDP/SRTP flows powered by
  [`RTN_PackGen`](https://github.com/evaneyou/RTN_PackGen) (`rtnp-send`): two-way Voice (**G.711 A-law**,
  **G.711 µ-law**, **G.729**, **G.723.1**), **Video conference**, **Remote desktop**, **Market data**,
  and gaming presets (**Fortnite**, **Call of Duty**) with real round-trip timing (`--meter rtt`).
- **Speed Test & Network Test.** Live 30-second LibreSpeed download/upload throughput tests with animated
  path diagrams, plus multi-session stress runs to see how links hold up under load.
- **Wired and wireless views.** Test over Ethernet (`wg0`) and Wi-Fi (`wg1`) separately on a Raspberry Pi,
  or add Wi-Fi-only Android TV agents in other rooms, and compare links side by side.
- **KPI Dashboard & Gemini AI Root Cause Analysis.** Daily, weekly, monthly and yearly availability,
  voice/video quality, packet drop, latency and speed reports (with per-sensor views and CSV export),
  plus an on-demand **Determine Root Cause** analysis powered by Google Gemini.
- **Telegram alerts & hierarchical correlation.** Real-time notifications that enforce a top-down root-cause
  hierarchy (`pi-offline` → shared WAN outage / ISP bandwidth collapse → single-link fault → measurement
  tunnel down → individual application failure) so downstream alert noise is suppressed automatically.
- **Wi-Fi scans and Airwaves Security.** Dual-radio scanning (`iw` and Kismet), channel graphs, evil-twin
  and mesh OUI trust detection, a Security tab for filtered Kismet WIDS alerts, and on-demand wired LAN
  scans (Scapy ARP + Nmap TCP ports with IEEE OUI maker and mDNS hostname lookup).
- **Server Health.** CPU, memory, disk, database size and fill forecast, encrypted `age` backups, weekly
  CVE checks, and interface traffic totals for admins and guests, plus **Fault Simulation** (a prompt-based
  walkthrough of how Overview, alerts and self-heal respond to failure scenarios without injecting live data).
- **Secure enrollment & self-healing tunnels.** Agents check in with an Ed25519-signed request, pin the
  server's TLS key, wait for admin approval (or scan a QR code on Android TV), and receive a WireGuard
  tunnel with automatic stale-handshake recovery.
- **Retention built in.** Raw results for 6 months, hourly averages up to 12 months, daily averages
  up to 2 years.
- **Three roles.** `admin` can change settings, run scans and approve devices; `viewer` is read-only;
  `guest` provides a read-only demo session that masks identifying fields (`#`) and expires after 3 minutes idle.

## How it works

```
Remote agents ──pull config / push results──▶ Server ◀──read APIs── Dashboard ◀── Browser
(Pi / Android TV)                        (FastAPI + Postgres)        (React)
```

Remote agents stay on your LAN and only make outbound connections. The dashboard talks to the server
and never to the agents. See [`SPEC.md`](./SPEC.md) for the data model, API surface and design principles.

| Component | Directory | Stack |
|-----------|-----------|-------|
| Server | [`server/`](./server) | FastAPI, SQLAlchemy, Alembic, Postgres 16 |
| Dashboard | [`dashboard/`](./dashboard) | React 19, Vite, TypeScript, TanStack Query, Recharts, Leaflet |
| Landing page | [`site/`](./site) | Static HTML, CSS and JS served at `/welcome/` and `www.homenetwatch.com` |
| Remote agent (Pi) | [`monitor/`](./monitor) | Python 3 standard library, systemd, WireGuard, Kismet, Nmap, Scapy |
| Android TV agent | [`android/`](./android) | Kotlin, WireGuard (`wireguard-android`), bundled `rtnp-send` |
| Tunnel hub | [`wireguard/`](./wireguard) | WireGuard container (`10.7.0.1` / `10.7.1.1`) that agents connect to |

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) with Compose v2.

```bash
git clone https://github.com/evaneyou/homenetwatch.git
cd homenetwatch
./scripts/local-up.sh
```

The script copies `.env.example` to `.env`, starts the stack, waits for the API, and loads demo data.
To do the same by hand:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec server python -m app.seed
```

Open the dashboard at <http://127.0.0.1:3847>.

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `admin-change-me` |
| Viewer | `viewer` | `viewer-change-me` |
| Guest | `guest` | `guest` |

> [!WARNING]
> These are development defaults. Change every secret in `.env` before you expose the stack to
> anything beyond your own machine. See [SECURITY.md](./SECURITY.md).

### Deploy to a server

A production overlay (`docker-compose.prod.yml`) adds automatic HTTPS via Caddy and closes internal ports.
Always deploy with `./scripts/deploy.sh` so `rtnp` stays attached to the `wireguard` network namespace.
The step-by-step guide is in [`docs/deploy.md`](./docs/deploy.md).

### Ports

| Service | Port | Notes |
|---------|------|-------|
| Dashboard | `3847/tcp` | Web UI (internal behind Caddy in production) |
| API | `8847/tcp` | `GET /api/v1/healthz` for liveness (internal behind Caddy in production) |
| Agent API | `8848/tcp` | HTTPS with pinned TLS key, agent enrollment only |
| WireGuard | `51820/udp` | Agent tunnels (`wg0` / `wg1`) |
| RTNP receiver | `8998/udp` | Real-time synthetic voice/video/application UDP flows (`8999/tcp` control on tunnel) |
| Postgres | `55432/tcp` | Development access only |

## Add a Raspberry Pi

1. In the dashboard open **Settings → Remote Agent Onboarding** and register the device's installer code.
2. Copy the agent package to the Pi and install it. Full steps are in
   [`monitor/README.md`](./monitor/README.md).
3. Open the agent's setup page from a PC on the same LAN, enter the server address, device
   name and TLS pin (or setup key), and tap **Approve** in the dashboard when it checks in.

## Add an Android TV (NVIDIA Shield)

An Android TV box can be a Wi-Fi-only remote agent: it runs the voice and video streams and the Speed
Test, not the Pi's HTTP, ping or Wi-Fi scans.

1. Download the APK from the latest **Android agent** run on GitHub Actions (artifact
   `homenetwatch-agent-apk`) and sideload it with `adb install -r app-debug.apk`.
2. Open HomeNetWatch on the TV and scan its QR code with a phone. The onboarding page opens with the
   installer code filled in: name it and tap **Register**. The TV collects its setup key from the server
   automatically; tap **Approve** when it checks in. Without a phone, open the setup address the TV shows
   from a laptop instead.
3. Choose **OK** on the TV's VPN prompt, then turn on the Wi-Fi checks for it in **Settings →
   Real-Time Application Health**.

Full steps and remote-control actions are in [`android/README.md`](./android/README.md).

## Configuration

Settings are read from `.env`. Copy [`.env.example`](./.env.example) and edit it.

| Variable | Purpose |
|----------|---------|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials |
| `DATABASE_URL` | SQLAlchemy URL used by the server |
| `SECRET_KEY` | Signs session cookies and encrypts stored integration secrets. Use a long random value |
| `CORS_ORIGINS` | Comma-separated dashboard origins allowed to call the API |
| `COOKIE_SECURE`, `COOKIE_SAMESITE` | Cookie flags. Set `COOKIE_SECURE=true` behind HTTPS |
| `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD` | Admin account created by the seed |
| `BOOTSTRAP_VIEWER_USERNAME`, `BOOTSTRAP_VIEWER_PASSWORD` | Viewer account created by the seed |
| `BOOTSTRAP_GUEST_USERNAME`, `BOOTSTRAP_GUEST_PASSWORD` | Guest account created by the seed |
| `WIREGUARD_ENDPOINT` | `host:port` that agents use to reach the WireGuard hub |
| `VITE_API_BASE_URL` | API address the dashboard calls |

## Development

```bash
docker compose up -d db                       # Postgres only

cd server
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8847

cd ../dashboard
npm ci
npm run dev
```

### Tests

```bash
cd server && uv run pytest -q          # needs the database above, migrated and seeded
./scripts/test-server.sh               # or run them in a container against a separate hns_test database
cd dashboard && npm run build          # type-check and production build
```

CI runs the same checks on every push and pull request, plus the Android agent unit tests and APK build.
See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Repository layout

```
server/        FastAPI app, Alembic migrations, seed data, tests
dashboard/     React + Vite + TypeScript UI
site/          Static landing page served at /welcome/ and www.homenetwatch.com
monitor/       Raspberry Pi agent package (setup page, health, checks, tunnel watchdog, installer)
android/       Android TV agent (Kotlin app, QR onboarding, tunnel, voice stream, Speed Test)
wireguard/     WireGuard hub container
deploy/        Caddy config for the production overlay (docker-compose.prod.yml)
docs/          Deployment guide
scripts/       Local helpers (deploy, bring-up, server tests, encrypted backup, GeoIP update, version bump)
SPEC.md        Architecture, API and design principles
CHANGELOG.md   Release history
VERSION        Current project version
```

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Dashboard-first slice: server, seed, core UI | Done |
| 1 | Server hardening: Ed25519 enrollment, QR pairing, issue & correlation engine, KPI & Gemini RCA | Done |
| 2 | Agents: Raspberry Pi (dual-link HTTP, ping, MTR, Speed Test, RTNP flows, tunnel self-heal) & Android TV | Done |
| 3 | Airwaves: dual-radio scans (iw / Kismet), trust and evil-twin, Security tab (WIDS filters + LAN scan) | Done |
| 4 | Cloud deployment with Caddy TLS, security headers, Server Health, and encrypted age backups | Done |

Details and architecture notes are in [`SPEC.md`](./SPEC.md).

## Third-party services

The dashboard, server, and agents contact a few external services. Path, voice/video, and throughput
tests go to your own server over the WireGuard tunnel.

- **Map tiles:** Esri ArcGIS basemap tiles, loaded by your browser. Respect Esri's attribution and terms.
- **Location search:** the dashboard sends location queries to [Photon](https://photon.komoot.io/) (OpenStreetMap data).
- **Internet checks:** agents request well-known sites such as Google, YouTube, Microsoft, Zoom and
  Netflix to measure reachability. Edit the list in `server/app/internet_tests.py`.
- **Telegram Bot API (optional):** when configured in **Settings → Alerts**, the server sends alert and
  recovery messages to your Telegram chat.
- **Google Gemini API (optional):** when an API key is configured in **Settings → Configure Gemini AI**,
  admins can run on-demand KPI root-cause analysis and Fault Simulation walkthroughs.
- **DB-IP City Lite (local):** `./scripts/update-geoip.sh` downloads a local `.mmdb` file so guest visit
  locations are resolved on the server without sending visitor IP addresses to a third party.

## Versioning

The project follows [Semantic Versioning](https://semver.org/) and is pre-1.0, so minor releases may
include breaking changes. The version is in [`VERSION`](./VERSION) and history is in
[`CHANGELOG.md`](./CHANGELOG.md). The Pi agent (`0.8.8`) and Android TV agent (`0.4.1`) carry their own
versions, reported to the server.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](./CONTRIBUTING.md) and the
[Code of Conduct](./CODE_OF_CONDUCT.md). Report vulnerabilities privately as described in
[SECURITY.md](./SECURITY.md).

## License

Released under the [MIT License](./LICENSE).
