# HomeNetWatch — SPEC v2

Ops-style monitoring for a home LAN: Raspberry Pi (or Linux PC) and Android TV remote agents run staged connectivity, real-time application and throughput tests and push results to a central FastAPI + Postgres server. A React dashboard reads those APIs only — the remote agent never talks to the browser.

## Topology

| Piece | Where | Role |
|---|---|---|
| **Remote agent** | Home Pi / Linux PC | Runs tests on the wired and Wi-Fi links, pulls config, pushes ingest (Bearer). Airwaves uploads wait in an SQLite spool (5 days, 50 MB) while the server is unreachable. |
| **Android agent** | Android TV (NVIDIA Shield) | Wi-Fi only: voice/video streams and the Speed Test. Same signed enrollment (`platform: android`), one tunnel; the server refuses Wired and Network Test for it. |
| **Central server** | Cloud VM (production: Oracle Cloud Always Free, Ubuntu 24.04 ARM) | Enrollment, config, ingest, issues, alerts, read APIs. FastAPI + Postgres, WireGuard hub, RTNP receiver, LibreSpeed, behind Caddy. |
| **Dashboard** | Served with server | React UI; cookie auth; reads APIs only. |
| **Landing page** | `site/`, at `/welcome/` and `www` | Static description of the platform with a link that opens the dashboard signed in as the guest. |

```
Remote agent ──pull config / push ingest──▶ Server ◀──read APIs── Dashboard ◀── Browser
```

Agents stay on the LAN and make outbound connections only: HTTPS to port 8848 for enrollment, then WireGuard (UDP 51820) for config, results, path checks and the Speed Test. Real-time flow packets go to the server's public address on UDP 8998, outside the tunnel, so they measure the real ISP path; a host timer limits that port to the current WireGuard peer addresses.

## Stack

- **Server:** FastAPI, SQLAlchemy, Alembic, Postgres 16, cookie session auth (`admin` / `viewer` / `guest`)
- **Dashboard:** React + Vite + TypeScript, TanStack Query, **Recharts** (no Grafana in this product slice)
- **Pi agent:** Python 3 standard library, systemd, WireGuard, Kismet, Nmap, Scapy, `rtnp-send` ([RTN_PackGen](https://github.com/evaneyou/RTN_PackGen))
- **Android agent:** Kotlin, `wireguard-android`, bundled `rtnp-send`
- **Dev and production:** Docker Compose (db, server, agent-api, wireguard, rtnp, librespeed, dashboard; Caddy in the production overlay)

## Data model (core)

- `groups` — tenancy / home site
- `monitors` — enrolled remote agents, with `platform` `pi` or `android`
- `monitor_health` — device samples (CPU, memory, disk, temperature, uptime), kept 7 days
- `networks` — wired / Wi-Fi targets under a group
- `tests` — `http`, `dns`, `ping`, `wifi`, … bound to a remote agent and network
- `test_results` — staged timings + metrics + status (`ok` / `degraded` / `fail`)
- Retention for `test_results` and `wifi_observations`: raw for 6 months, hourly averages from 6 to 12 months, daily averages from 12 months to 2 years, then deleted. The server runs this once a day. Issues are kept as written.
- `users` — admin, viewer, and guest with a password hash. A guest can read every page. Changes stay admin-only (enforced by the server), and a guest session ends after 3 minutes idle. `GET /api/v1/auth/guest` opens `/welcome/enter.html`, which signs the visitor in as the guest from the same site. In a guest response the server replaces each letter and digit of an identifying field (addresses, city, provider, host names, interface names, device MACs and wireless names) with `#`, keeping dots, colons and spaces, so the real value never leaves the server; a leading word joiner (U+2060) marks the value and the dashboard blurs it, so a guest can see that something is hidden. A saved place keeps its state and country readable, and Path View hides only the source (home) hop. Traffic-class markings (`tos`, `dscp`) are dropped.
- `issues` — open/resolved problems with optional `root_cause_stage`
- Also: `installer_codes` and `enrollment_checkins` (enrollment), `adhoc_traces`, `speed_tests` / `speed_samples`, `synthetic_profiles` / `synthetic_runs` / `synthetic_samples` and `stress_runs` (RTNP flows), `rtnp_secrets`, `ap_inventory`, `rf_alerts`, `rf_channel_stats`, `rf_clients`, `lan_scan_runs` / `lan_scan_hosts` (Airwaves), `kpi_hourly` / `kpi_daily`, `alert_events` and `telegram_settings`, `gemini_settings`, `guest_visits` (kept a year), `server_samples`, `server_storage`, `server_db_daily`, `server_advisories`, `server_stuck` (Server Health), settings tables, `audit_events` and `system_logs`

## API surface

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
| `GET` | `/api/v1/kpi` | KPI summary, per-link breakdown, hourly/daily series, and rule-based diagnosis |
| `POST` | `/api/v1/kpi/root-cause` | Admin. On-demand Google Gemini LLM root cause analysis over the KPI window's event and metric log. Guests use the same UI control with a labeled demo report instead |
| `GET`/`PUT`/`DELETE` | `/api/v1/settings/gemini` | Admin + CSRF. Encrypted Google Gemini API key and model selection |
| `GET` | `/api/v1/auth/guest` | Public demo link. Redirects to `/welcome/enter.html` |
| `POST` | `/api/v1/auth/guest/session` | Sets the guest session cookie; the `Origin` must be the site serving the request |
| `POST` | `/api/v1/agents/pairing` | Android agent, before setup. Collects its setup key and name after the QR registration |

Other route groups, all under `/api/v1` (the full list with request and response shapes is the OpenAPI page at `/docs`):

| Area | Paths |
|---|---|
| Remote agents | `/monitors`, `/monitors/{id}` (`PATCH` settings, `/restart`, `/tunnel`, `/path`, `/traces`, `/log`), agent-side `/monitors/command`, `/monitors/wifi-scans`, `/monitors/rf-capture`, `/monitors/lan-scan`, `/monitors/speedtest` |
| Speed Test | `/speedtest`, `/speedtest/active`, `/speedtest/history`, `/speedtest/{id}`, `/speedtest/{id}/cancel` |
| Real-Time Application Health | `/synthetic/*` (catalog, profiles, continuous and manual runs, samples, events, health) |
| Network Test | `/stress/*` (start, sessions, live data, stop, report) |
| Airwaves | `/rf/*` (overview, access points and trust, clients, channel snapshot, alerts, security, scan now, LAN scans), `/wifi-scans` |
| KPI | `/kpi`, `/kpi/daily`, `/kpi/root-cause` |
| Server Health | `/server-health`, `/server-health/software`, `POST /server-health/fault-simulation` |
| Settings | `/settings/internet-service`, `/settings/web-services`, `/settings/web-stability`, `/settings/telegram/*`, `/settings/gemini`, `/settings/guest-visits` |
| Logs | `/logs/unified` |

Enrollment / config ETag matrix and webhooks are deferred.

## Dashboard screens

1. **Home** — open-issue severity banner; one card per remote agent with its platform and Web, Wired, Wi-Fi and Apps tiles
2. **Remote agent detail** — device health and admin config (name, report interval, location); per-network tabs; test status; log of results
3. **Test detail** — range picker; stage waterfall; pass/fail strip; RTT/loss charts
4. **Path View** — traceroute grouped into networks, latency by hop, BGP view from the agent's own provider when collectors see one, trace any host (admin)
5. **Speed Test** and **Network Test** — live LibreSpeed test and multi-session stress runs
6. **KPI Dashboard** — availability and quality reports, daily history, CSV, Determine Root Cause
7. **Airwaves** — networks, clients, channels, Security (WIDS alerts, trust, LAN scans)
8. **Issues** and **Server Health**
9. **Settings** — onboarding, per-agent settings, internet service, Real-Time Application Health, web services, Telegram, Gemini, guest visits

Loading / empty states required. Dark theme. Viewer role hides write controls and has no Settings or Server Health. Guest sees every page read-only.

## Phases

| Phase | Scope |
|---|---|
| **0 — Dashboard-first** (done) | Thin server + seed + core UI on Compose. No Pi required. |
| **1 — Server hardening** (done, except the config ETag) | Signed installer-code enrollment, QR pairing, auth polish, issue and correlation engine, Telegram alerts, KPI and Gemini root cause |
| **2 — Remote agent** (done) | Pi package: LAN setup page, enroll, device health, HTTP, ICMP, and MTR against the server at 10.7.0.1 / 10.7.1.1, Bandwidth Test and Speed Test over the tunnel, web-service HTTP checks, RTNP real-time flows, Network Test, tunnel self-heal. Android TV agent: Wi-Fi flows and Speed Test |
| **3 — Wireless / RF** (done) | Airwaves. The link adapter carries traffic. A second radio adapter, preferably a USB MediaTek, runs the scans. With only one adapter, scans wait at least 15 minutes and never run during a test. A new access point of the joined SSID is trusted when it shares the OUI (the first three MAC bytes) with the BSSID the remote agent is associated to, and is labeled a mesh node. A different OUI opens a possible evil-twin issue. Access points already seen are checked again the first time that association is known. Manual trust is left alone. The Security tab (sub-tabs Alerts, Trust, Scans) presents WIDS (Kismet alerts) with severity/type/trusted/search filters, attention items (evil twins and untrusted same-SSID APs), and admin-only on-demand LAN scans: Scapy ARP discovery then Nmap on a fixed common-port set, on the Pi's wired LAN subnet only (never WireGuard). Each host carries its maker (IEEE OUI lookup on the Pi, from Nmap's prefix table) and mDNS name when announced. One scan at a time per agent; results kept 30 days. |
| **4 — Cloud** (done) | Same Compose on Oracle Cloud Always Free (Ampere A1, Ubuntu 24.04) with Caddy TLS, security headers, Server Health and encrypted backups. `docs/deploy.md` covers any VPS |

## Out of scope

A staged test runner, an offline spool for test results (only Airwaves uploads are spooled), Grafana, webhooks, the full enrollment ETag test matrix, Playwright journeys, and inbound Telegram commands.

## Design principles

- Health color is the primary signal (green / amber / red).
- A continuous application check that reports no packets and no round trip, while another check on the same link is still getting through, is a software problem in the monitor. The same is true when every synthetic flow on that link goes silent at once but a path or web check on the link still succeeds: that is the receiver or the sender, not the service. Those results are marked software, left out of the KPI, opened as an issue, and the monitor is asked to restart that test. A blackout of the path and the web checks as well stays a real failure. When only one link is bad and the other is healthy, voice, video, and web availability follow the healthy link, and the bad link is named on its own.
- Access plane vs measurement tunnel: public web HTTP checks leave through the NIC to the open internet. Path checks (`icmp-path` / `http-path`) target the WireGuard peer (`10.7.0.1` / `10.7.1.1`) and need the measurement tunnel; synthetic app flows use the same tunnel. KPI internet availability and outage lists ignore path fails when public web on that link still succeeds (tunnel death ≠ home internet down). Diagnosis and Telegram alerts treat the planes separately: healthy web with a stale handshake or failing tunnel-backed apps is `tunnel`, not local Wi-Fi/Ethernet or ISP down. A stale WireGuard handshake on an online Pi opens a `pi` tunnel-down alert. The Pi agent (0.8.4+) watches tunnel peers and restarts `wg0` / `wg1` via a narrow sudoers rule when they stop answering.
- Server Health, under Issues, is the machine running HomeNetWatch. An admin or a guest sees CPU and the processes using it, memory and the processes holding it, the machine network interface and the app container interface, a Data used table under the network chart (bytes received and sent by the machine over the last hour, 6 hours, 24 hours and 7 days and since the machine started, the app container's own counters since it last started, and a monthly pace of sending from the last 24 hours; the counters are added up across reboots and restarts, and the history is the 7 days of samples kept), disk, and database size, a forecast of when the database would fill the disk, the retention rules, a weekly software and vulnerability check, processes that look stuck, and when the database, WireGuard, and agent TLS backups were last taken. Those copies are encrypted to a public key kept on the server; the private key stays off the machine. Each copy is marked Yes or No in an Encrypted column. The backup files themselves are not served. The API container never mounts the host: the `hoststat` container (no network, read-only) copies the counters, process basics, OS package list and disk measurement into the `hostinfo` volume, and Server Health reads those copies. Fault Simulation (same page) takes a free-text failure scenario and, using the configured Gemini key, explains in detail what Overview, Telegram alerts, logs, and tunnel self-heal would do based on the production alert and correlation rules. Live Gemini queries are admin-only; guests use the same Simulate / Determine Root Cause controls with a short wait, then see a labeled demo walkthrough. It does not inject readings, mutate monitors, or send Telegram. A viewer is sent home.
- Stage waterfall on Test detail is the distinctive moment of the product.
- Charts live in the React app via Recharts — not an external Grafana stack.
- Dense but readable ops UI; real copy; no marketing fluff inside the product.
