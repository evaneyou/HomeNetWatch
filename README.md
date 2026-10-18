<p align="center">
  <img src="dashboard/public/homenetwatch.png" alt="HomeNetWatch" width="360" />
</p>

<h1 align="center">HomeNetWatch</h1>

<p align="center">
  Monitor the wired and wireless performance of your home network with a Raspberry Pi.
</p>

<p align="center">
  <a href="https://github.com/evaneyou/homenetwatch/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/evaneyou/homenetwatch/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <img alt="Version" src="https://img.shields.io/badge/version-0.3-informational" />
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange" />
</p>

Small remote agents on a Raspberry Pi (or any Linux PC) run staged connectivity tests from inside
your home and push the results to a central server. A web dashboard shows what is healthy, what is
degraded, and at which stage a problem starts: DNS, TCP, TLS, or the server's first byte.

## Features

- **Staged tests.** HTTP checks broken into DNS, TCP, TLS and time-to-first-byte stages, plus ICMP
  ping, MTR path traces, and Bandwidth Test throughput to the server.
- **Wired and wireless views.** Test over Ethernet and Wi-Fi separately and compare them.
- **Health at a glance.** Green, amber and red status for every remote agent and network, with an
  issue list that records the stage where each failure began.
- **Internet and web-service checks.** Built-in checks against popular services, plus your own
  HTTP targets.
- **Wi-Fi scans.** Nearby networks observed by the agent, with history.
- **Secure enrollment.** Agents check in with a signed request, pin the server's TLS key, wait for
  admin approval, and then receive a WireGuard tunnel to the server.
- **Retention built in.** Raw results for 6 months, hourly averages up to 12 months, daily averages
  up to 2 years.
- **Two roles.** `admin` can change settings and approve devices; `viewer` is read-only.

## How it works

```
Remote agent ──pull config / push results──▶ Server ◀──read APIs── Dashboard ◀── Browser
 (Raspberry Pi)                          (FastAPI + Postgres)        (React)
```

The Pi stays on your LAN and only makes outbound connections. The dashboard talks to the server
and never to the Pi. See [`SPEC.md`](./SPEC.md) for the data model, API surface and phase plan.

| Component | Directory | Stack |
|-----------|-----------|-------|
| Server | [`server/`](./server) | FastAPI, SQLAlchemy, Alembic, Postgres 16 |
| Dashboard | [`dashboard/`](./dashboard) | React 19, Vite, TypeScript, TanStack Query, Recharts, Leaflet |
| Remote agent | [`monitor/`](./monitor) | Python 3 standard library, systemd |
| Tunnel hub | [`wireguard/`](./wireguard) | WireGuard container that the agents connect to |

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

A production overlay adds automatic HTTPS and closes internal ports. The step-by-step guide is in [`docs/deploy.md`](./docs/deploy.md).

### Ports

| Service | Host port | Notes |
|---------|-----------|-------|
| Dashboard | `3847` | Web UI |
| API | `8847` | `GET /api/v1/healthz` for liveness |
| Agent API | `8848` | HTTPS, agent enrollment only |
| WireGuard | `51820/udp` | Agent tunnels |
| Postgres | `55432` | Development access only |

## Add a Raspberry Pi

1. In the dashboard open **Settings → Remote Agent Onboarding** and add a device.
2. Copy the agent package to the Pi and install it. Full steps are in
   [`monitor/README.md`](./monitor/README.md).
3. Open the agent's setup page from a PC on the same LAN and enter the server address, device
   name and key.

## Configuration

Settings are read from `.env`. Copy [`.env.example`](./.env.example) and edit it.

| Variable | Purpose |
|----------|---------|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials |
| `DATABASE_URL` | SQLAlchemy URL used by the server |
| `SECRET_KEY` | Signs session cookies. Use a long random value |
| `CORS_ORIGINS` | Comma-separated dashboard origins allowed to call the API |
| `COOKIE_SECURE`, `COOKIE_SAMESITE` | Cookie flags. Set `COOKIE_SECURE=true` behind HTTPS |
| `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD` | Admin account created by the seed |
| `BOOTSTRAP_VIEWER_USERNAME`, `BOOTSTRAP_VIEWER_PASSWORD` | Viewer account created by the seed |
| `WIREGUARD_ENDPOINT` | `host:port` that agents use to reach the WireGuard hub. Set it to an address your Pis can reach |
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

CI runs the same checks on every push and pull request. See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Repository layout

```
server/        FastAPI app, Alembic migrations, seed data, tests
dashboard/     React + Vite + TypeScript UI
monitor/       Raspberry Pi agent package (setup page, health, checks, installer)
wireguard/     WireGuard hub container
deploy/        Caddy config for the production overlay (docker-compose.prod.yml)
docs/          Deployment guide
scripts/       Local helpers (bring-up, server tests, backup, version bump)
SPEC.md        Architecture, API and phase plan
CHANGELOG.md   Release history
VERSION        Current project version
```

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Dashboard-first slice: server, seed, core UI | Done |
| 1 | Server hardening: enrollment, config ETag, issue engine | In progress |
| 2 | Pi agent: enrollment, device health, HTTP, ping, MTR, Bandwidth Test | In progress |
| 3 | Airwaves: a second radio adapter scans (iw, or Kismet with `--kismet`); the link adapter scans only when it is the only radio, at most every 15 minutes, and never during a test | In progress |
| 4 | Cloud deployment of the same Compose stack with TLS (see docs/deploy.md) | In progress |

Details and out-of-scope items are in [`SPEC.md`](./SPEC.md).

## Third-party services

The dashboard and agents contact a few public services. Review these before you deploy. Path and
throughput tests go to your own server over the WireGuard tunnel.

- **Map tiles:** Esri ArcGIS basemap tiles, loaded by your browser. Respect Esri's attribution and terms.
- **Location search:** the dashboard sends location queries to [Photon](https://photon.komoot.io/) (OpenStreetMap data).
- **Internet checks:** agents request well-known sites such as Google, YouTube, Microsoft, Zoom and
  Netflix to measure reachability. Edit the list in `server/app/internet_tests.py`.

## Versioning

The project follows [Semantic Versioning](https://semver.org/) and is pre-1.0, so minor releases may
include breaking changes. The version is in [`VERSION`](./VERSION) and history is in
[`CHANGELOG.md`](./CHANGELOG.md). The Pi agent carries its own version, reported to the server.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](./CONTRIBUTING.md) and the
[Code of Conduct](./CODE_OF_CONDUCT.md). Report vulnerabilities privately as described in
[SECURITY.md](./SECURITY.md).

## License

Released under the [MIT License](./LICENSE).
