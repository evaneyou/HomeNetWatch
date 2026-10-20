# HomeNetWatch — SPEC v2

Ops-style monitoring for a home LAN: Raspberry Pi (or Linux PC) remote agents run staged connectivity tests and push results to a central FastAPI + Postgres server. A React dashboard reads those APIs only — the remote agent never talks to the browser.

## Topology

| Piece | Where | Role |
|---|---|---|
| **Remote agent** | Home Pi / Linux PC | Runs tests, pulls config, pushes ingest (Bearer). Offline SQLite spool. |
| **Central server** | PC now → cloud later | Enrollment, config, ingest, issues, read APIs. FastAPI + Postgres. |
| **Dashboard** | Served with server | React UI; cookie auth; reads APIs only. |

```
Remote agent ──pull config / push ingest──▶ Server ◀──read APIs── Dashboard ◀── Browser
```

The Pi stays on the LAN and reaches the server over outbound HTTPS. Only server + dashboard move to the cloud.

## Stack

- **Server:** FastAPI, SQLAlchemy, Alembic, Postgres 16, cookie session auth (`admin` / `viewer` / `guest`)
- **Dashboard:** React + Vite + TypeScript, TanStack Query, **Recharts** (no Grafana in this product slice)
- **Dev:** Docker Compose (db + server + dashboard)

## Data model (core)

- `groups` — tenancy / home site
- `monitors` — enrolled Pi agents (`pi-living-room`, …)
- `monitor_health` — device samples (CPU, memory, disk, temperature, uptime), kept 7 days
- `networks` — wired / Wi-Fi targets under a group
- `tests` — `http`, `dns`, `ping`, `wifi`, … bound to a remote agent and network
- `test_results` — staged timings + metrics + status (`ok` / `degraded` / `fail`)
- Retention for `test_results` and `wifi_observations`: raw for 6 months, hourly averages from 6 to 12 months, daily averages from 12 months to 2 years, then deleted. The server runs this once a day. Issues are kept as written.
- `users` — admin, viewer, and guest with a password hash. A guest can read every page. Changes stay admin-only, and a guest session ends after 3 minutes idle. Identifying fields in a guest response, including coordinates, keep their shape but each letter and digit is replaced with `#` (addresses, place, provider, host name, interface names, and wireless names) so the map cannot plot the house.
- `issues` — open/resolved problems with optional `root_cause_stage`

## API surface (dashboard-first)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/healthz` | Liveness |
| `POST` | `/api/v1/auth/login` | Cookie session |
| `POST` | `/api/v1/auth/logout` | Clear cookie |
| `GET` | `/api/v1/auth/me` | Current user, plus the CSRF token for enrollment actions |
| `POST` | `/api/v1/ingest` | Remote agent Bearer ingest (idempotent by result id) |
| `POST` | `/api/v1/agents/nonce` | Remote agent, HTTPS port 8848 only. One-use nonce for its identity key, valid 60 seconds |
| `POST` | `/api/v1/agents/check-in` | Remote agent, signed. Attaches to a registered installer code and waits for approval |
| `POST` | `/api/v1/agents/poll` | Remote agent, signed. After approval returns the tunnel config and, when needed, a new ingest token |
| `GET` | `/api/v1/enrollment` | Registered installer codes and the remote agents waiting under them |
| `POST` | `/api/v1/enrollment/codes` | Admin + CSRF. Register an installer code for 24 hours, with the remote agent's device name and an optional location |
| `POST` | `/api/v1/enrollment/codes/{id}/approve` | Admin + CSRF. Add as new, or replace an existing remote agent and keep its tunnel address |
| `POST` | `/api/v1/enrollment/codes/{id}/start-over` | Admin + CSRF. Clear check-ins after two devices used one code |
| `DELETE` | `/api/v1/enrollment/codes/{id}` | Admin + CSRF. Remove a registration that was never approved |
| `DELETE` | `/api/v1/monitors/{id}` | Admin + CSRF. Remove a remote agent and its tunnel peer, and release its installer code |
| `POST` | `/api/v1/monitors/health` | Remote agent posts one device-health sample; response is the desired config, including Bandwidth Test and web-service HTTP tests |
| `GET` | `/api/v1/monitors/{id}/device` | Latest device health, 24h chart points, and whether config is applied |
| `GET` | `/api/v1/summary` | Remote agent × network health matrix + open issues |
| `GET` | `/api/v1/results` | Filtered test results |
| `GET` | `/api/v1/metrics` | Time-series metrics for charts |
| `GET` | `/api/v1/issues` | Issue list |
| `GET` | `/api/v1/issues/{id}` | Issue detail |

Enrollment / config ETag matrix and webhooks are deferred.

## Dashboard screens (this slice)

1. **Overview** — open-issue severity banner; remote agent × network health matrix; last test time
2. **Remote agent detail** — device health and admin config (name, report interval); per-network tabs; test status; log of results
3. **Test detail** — range picker; stage waterfall; pass/fail strip; RTT/loss charts
4. **Issues** — open/resolved list + detail (`root_cause_stage`, recent failures)

Loading / empty states required. Dark/light theme when cheap. Viewer role hides write controls.

## Phases

| Phase | Scope |
|---|---|
| **0 — Dashboard-first (this repo slice)** | Thin server + seed + core UI on Compose. No Pi required. |
| **1 — Server hardening** | Full enrollment, config ETag, auth polish, issue engine |
| **2 — Remote agent** | Pi package: LAN setup page, enroll, device health, HTTP, ICMP, and MTR against the server at 10.7.0.1, Bandwidth Test over the tunnel, plus web-service HTTP checks. Other staged tests and the offline spool come later |
| **3 — Wireless / RF** | Airwaves. The link adapter carries traffic. A second radio adapter, preferably a USB MediaTek, runs the scans. With only one adapter, scans wait at least 15 minutes and never run during a test. A new access point of the joined SSID is trusted when it shares the OUI (the first three MAC bytes) with the BSSID the remote agent is associated to, and is labeled a mesh node. A different OUI opens a possible evil-twin issue. Access points already seen are checked again the first time that association is known. Manual trust is left alone. |
| **4 — Cloud** | Same Compose on a small VPS (Hetzner CX / DO droplet) + TLS |

## Out of scope for this slice

Ping, MTR, Wi-Fi test runner, offline spool, Grafana, webhooks, full enrollment ETag test matrix, Playwright journeys.

## Design principles

- Health color is the primary signal (green / amber / red).
- A continuous application check that reports no packets and no round trip, while another check on the same link is still getting through, is a software problem in the monitor. Those results are marked software, left out of the KPI, opened as an issue, and the monitor is asked to restart that test. A blackout of every check on the link stays a real failure.
- Server Health, under Issues, is the machine running HomeNetWatch. An admin sees CPU, memory, the host interface, disk, and database size, a forecast of when the database would fill the disk, the retention rules, a weekly software and vulnerability check, and processes that look stuck. Guests and viewers are sent home.
- Stage waterfall on Test detail is the distinctive moment of the product.
- Charts live in the React app via Recharts — not an external Grafana stack.
- Dense but readable ops UI; real copy; no marketing fluff inside the product.
