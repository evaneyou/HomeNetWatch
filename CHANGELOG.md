# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The repository version lives in [`VERSION`](./VERSION). The Pi agent in `monitor/`
reports its own `AGENT_VERSION` so it can be updated independently of the server.

## [Unreleased]

### Changed

- **Top bar buttons:** Back, Home and the three dots are drawn in the same 15 by 15 box with the white, round-ended stroke of the menu icons, so they match each other and the menu. The Back button is a plain chevron without a tail.
- **Landing page figures:** updated to 109 merged pull requests, 172 automated tests and 25 fixes as of October 19, and the release line now says release 0.2 is published on GitHub (0.3 is in the changelog and `VERSION` but has no GitHub release yet).
- **KPI toolbar:** the jump back to the current day says Today. The "Viewing history" label and the Print / Save PDF button are gone.

## [0.3] - 2026-10-18

### Added

- **Speed Test (kebab menu):** a live 30 second speed test on the Pi against the LibreSpeed server, on the Wired link (the default, to see the most it can do) or Wi-Fi. A latency check, then 15 seconds of download and 15 seconds of upload. A big readout climbs as the speed ramps up, a React Flow picture of the path (Pi, router, provider, internet, speed server) animates with more and faster dots as the speed rises and reverses direction for upload, and two bar graphs show the download and upload speed filling in half second by half second with a dashed line at the steady speed. Results show the steady average, ping and jitter, and recent tests are listed. Only an admin can start a test (one at a time, 20 an hour, with a Stop button); when a guest presses Start test, a simulated test plays instead, labelled as a demo. The Pi agent reports version 0.8.0 and must be updated; it speaks the LibreSpeed protocol itself because the command line tool only prints a final result. Migration `0040_speed_tests`.
- **Network Test:** the "Ready" status next to the title is gone. Running, Stopped, Finished and Agent offline still show.
- **Pi agent, dead stream recovery:** a continuous test stream that loses every packet for 6 intervals (about a minute) is now restarted, like one that reports nothing. Needs the Pi agent update to take effect.
- **Speed Test sensor picker:** same as KPI: it sits at the right of the header and the page switches sensor when you press Display.
- **KPI sensor picker:** it moved to the right of the report header, starts on the sensor being shown (it was blank with one sensor) and changes the report only when you press the new Display button.
- **Sensor picker on KPI and Speed Test:** both pages show a remote-sensor dropdown (same look as Network Test). KPI can show one sensor or all sensors together (kept in the address as `sensor`), and the Speed Test picker is shown even when there is only one sensor.
- **Menu icons:** the Airwaves and Speed Test icons in the menu are drawn slightly larger so they match the weight of the others.
- **Home Airwaves button:** an Airwaves icon button sits to the left of the KPI button in the top right of the Home card, with its wave drawn as large as the KPI bars.
- **Airwaves graph top:** a very strong network (above -22 dBm) no longer has its peak cut off at the top of the channel graph.
- **Airwaves band switch:** the 2.4 and 5 GHz buttons are now a rounded switch with the chosen band filled, and on wide screens the recommendation shrinks to its content and sits at the right instead of stretching. The phone layout is unchanged.
- **Airwaves Channels layout:** the 2.4 and 5 GHz buttons now share a row with a compact recommendation (channel, networks, busiest, strongest), the channel graph uses the full width and is drawn at the width it is given so its text stays readable on a phone, and Networks in range sits below it as a full-width list (tap or hover to spotlight a network). On a phone the recommendation drops under the buttons and the list is one column.
- **Airwaves advice:** the one-line advice at the top of every Airwaves tab said "Your 2.4 GHz band is crowded" even when 5 GHz was selected. It now shows only on the Overview. On Channels the card names the band ("Recommended for 5 GHz"), drops a sentence that repeated its own title and the Busiest channel tile, and the graph labels the shaded zone "Quietest zone" instead of repeating the channel.
- **KPI daily health score:** each day in the Daily KPI history is now coloured by a weighted score from 0 to 100 (internet 60%, voice 20%, video 10%, packet drop 10%) and not by its single worst result. Each service scores 100 at its target and 0 at 99.0% (internet) or 98.0% (voice, video) or 1% packet drop, and in proportion between. Green is 95 or more, amber 80 to 94, red below 80. A service with no data that day shares its weight; with no internet data there is no score. The hover shows the score, and the CSV has a Health score column.
- **KPI reporting gaps:** time with no ping results (the Pi or the server was down) is now its own figure, a Reporting gaps card, a gap column in the daily records, a line in the day's hover details, and a list in a new Outages and gaps panel, including a stretch that is still open. It is not counted as internet downtime, and one stop seen on both links is counted once. Migration `0041_kpi_gaps` fills in the history. Availability charts no longer start below 95% (lower points sit on the bottom edge, and the hover shows the real figure), "Live Â· updated" shows its time zone, and latency and speed each get their own card.
- **KPI Dashboard, clearer:** a figure of exactly 100% (or 0%) now shows without decimals, the targets are stricter (internet 99.95%, voice and video 99.9%, packet drop at or under 0.1%), and the Daily KPI history is a grid of coloured days (green: met every target, amber: close, red: missed, grey: no data). Hover or focus a day for its numbers, click it to open that day, and download the detail as CSV.
- **KPI daily records and history:** each UTC day is now stored as its own permanent record (`kpi_daily`, migration `0039_kpi_daily`), added up from the hourly rows and kept up to date every minute. The week, month and year views are built from those daily records. KPI percentages show two decimals, cut off and never rounded up. A date picker with previous and next day opens any earlier day as 24 hours by hour (or the week, month or year ending that day), and a Daily KPI history table lists the stored days with a date range, quick 30 day, 90 day and 1 year ranges, click-through to a day and a CSV download. New endpoint `GET /api/v1/kpi/daily`; `GET /api/v1/kpi` takes a `day`.
- **KPI Dashboard** (kebab menu, under Network Test): a live executive report of home network health for a day, a week, a month or a year, refreshed every 30 seconds. It shows internet availability (wired and Wi-Fi, to three decimals), voice and video availability, packet drop, time offline and outages, latency and jitter, and internet speed, each against a target and compared with the previous period, with Recharts trend charts, a Wired versus Wi-Fi table, an outage log and a print / save as PDF page. A one-line verdict says whether the service levels are being met. The figures come from a new hourly table, `kpi_hourly` (migration `0038_kpi_hourly`), refreshed every minute and filled in from existing measurements, so long reports stay exact after raw measurements are averaged.
- **Configure Telegram (Settings):** send a Telegram message for serious problems and again when they clear. Covers network outages (the internet down on the wired or Wi-Fi link, sustained ISP link degradation, Wi-Fi security alerts such as evil twins or deauth floods, or many web services unreachable), Pi problems (offline, hot, disk nearly full, agent errors), server problems (disk nearly full, out of database connections, repeated server errors) and failing application checks. The page takes a bot token and chat ID (with a Find my chat button), has a switch for each kind and for recovery notices, sends a test message, and lists recent alerts. The bot token is stored encrypted, never sent back to the browser and kept out of the logs. Each problem is announced once after it has held for a short time, with a limit of 30 messages an hour. If the whole server is down it cannot send an alert. Migration `0037_telegram_alerts`.
- **Guest visits reporting:** sign-ins are stored in their own `guest_visits` table with the place saved at sign-in, and kept for a year (the retention job deletes older rows). The page has 7 day, 30 day, 90 day and 1 year ranges, totals with the change against the previous period, a chart per day (per week for a year), top countries and cities, and a latest list that scrolls and loads more. Visits from an address a Pi reports as its own public address are not stored or shown; existing visits were copied across without them. Migration `0036_guest_visits`.
- **Network Test (stress run):** pick tests (voice call, video conference, remote desktop, market data, round-trip probe, throughput flood) with their options and as many sessions of each as you like, choose Wired, Wi-Fi or both and a run length of up to 5 minutes, and press Run Stress Test. Live charts show throughput, round trip, jitter and loss per link, and a session list shows each session. Tests can be added and sessions removed while it runs, and Stop all ends it. Traffic stays on the physical NIC and the public address; only the control channel uses WireGuard. TCP is not offered because only UDP 8998 is published. The Pi agent reports version 0.7.0 and must be updated to run stress sessions. Scheduling a run and the finished-run report are the next step.
- **Airwaves redesign:** a compact header (agent, refresh, scan), one row of tabs, one-line band stats, a fixed-height alerts list that scrolls, pill filters with search, one-line network rows with signal bars, and a channel graph that keeps the strongest four networks with a See all button. A phone gets a segmented tab bar; a wide screen gets an underline bar, a three-column overview and a two-column channel graph.
- **Guest visits dashboard:** a summary (sign-ins, different addresses, countries, busiest day), sign-ins per day, sign-ins by country, and a country and city for each visit, looked up in a local DB-IP City Lite file. Install it with `scripts/update-geoip.sh`.

### Changed

- Sign-in limits attempts per address and always checks the password hash, so a missing name is not faster than a wrong password.
- A trace name is resolved on the server, and refused when it does not point at a public address.
- Health cleanup, the monitor page, and metrics queries stay bounded to the monitor or a capped series.
- Admin changes send the same CSRF token that delete and trace already required.
- The connection chart has more room above and below so the bottom row is not clipped.
- The landing page matches the live copy, including the decisions and the Telegram roadmap item.
- A guest sees each letter and digit of an identifying field replaced with `#`, so an address still has the shape of an address. The measurements stay visible.
- Auth, settings, agent check-in, and dashboard readings each have their own API module. The paths are unchanged.
- The Pi agent is split into enrollment, health, radio, and the test scheduler. The setup page stays in `agent.py`. The agent reports version 0.6.1.
- The internet path screen keeps the service grid in one file and the detail charts in another.
- The Pi agent reports version 0.6.0 (on-demand host traces, and traces bound to a link run over IPv4). Install it on each Pi to update.
- `monitor/update.sh` replaces the agent code and restarts `monitor`. Enrollment, tunnels, and `/etc/monitor` stay as they are.
- Kismet on the Pi drops idle devices sooner, skips keeping EAPOL frames in RAM, caps the service cgroup, and restarts once a day so the capture helper cannot grow until the Pi reboots. Existing installs can run `sudo ./repair-kismet-memory.sh`.
- Hardened Pi agent sockets, policy routing self-healing, Wi-Fi power save, and atomic state writes.

## [0.2] - 2026-10-14

### Added

- **Network Path Diagram:** an animated React Flow diagram of the path each test takes (direct, or signalling through the tunnel), with hover details, zoom, one merged "Web services" group, and steady-speed pulses that stay visible on dashed lines. The Internet Connection overview diagram is live and drawn the same way.
- **Path View:** the Network path and BGP comparison are React Flow diagrams that fit a phone. The latency chart and hop detail are folded into the path: each network box is selectable, hops show a latency bar, and details sit below.
- **Trace any host:** type a host name or public IP into Path View and the agent traces to it on demand over the chosen link. Admin only, private addresses refused (migration 0034).
- **Zoom:** the real-time application health charts zoom by drag, wheel and double-click.
- Agent reports its Ethernet, Wi-Fi and radio interface names and a real LAN address.
- `scripts/deploy.sh`, which recreates `rtnp` whenever `wireguard` is recreated.
- **Security headers:** HSTS (`HSTS_MAX_AGE`, starting at 300 seconds), an enforced Content-Security-Policy and a Permissions-Policy on every response, set by Caddy. See the Security headers section of `docs/deploy.md`.
- **Landing page:** `site/`, served at `/welcome/` and on the bare domain and `www`: a short description of the app, documents, an author bio and a one-click read-only guest demo.

### Changed

- Path View and the Remote agent pages use a compact layout: one summary line, controls on one row, and the time range on the Web, Wired and Wi-Fi row.
- "File Transfer" is now "Bandwidth Test". The product is named HomeNetWatch.
- A shared monitor tab bar on every monitor page. Home shows Web first and no longer has the Internet chip.
- Several flows can run at once on a link; Wi-Fi measurements are bound to the Wi-Fi interface.
- Rows that open a detail view (Internet Connection and the real-time application checks) show a chevron and a hover highlight. Real-Time Application Health is in its own box.
- A guest who tries a host trace gets the read-only notice instead of an error.

### Removed

- draw.io, the web-service MTR checks, and the Video call check (migration 0032).

## [0.1] - 2026-10-13

First release.

### Added

- **Server:** FastAPI and Postgres, cookie auth for admin and viewer, enrollment with a pinned TLS key, WireGuard tunnels, and retention of raw results.
- **Dashboard:** overview, remote-agent detail, internet path with public traceroute hops and AS numbers, airwaves, issues, and settings. Every page except Home uses the compact layout.
- **Pi agent:** LAN setup page, device health, and HTTP, ping, MTR, Bandwidth Test, and video-call checks.
- **Bandwidth Test:** Rust LibreSpeed backend. The agent calls `/backend/garbage` and `/backend/empty`.
- **Airwaves:** Wi-Fi scans and Kismet capture, with trusted access points and nearby clients.

### Fixed

- The recent normal adapts when a path stays slower and the latest samples agree, so a moved server does not stay "slow" forever.
- A silent last hop on the path trace no longer turns the home tiles red by itself.

### Removed

- iperf3 bandwidth checks. Bandwidth Test is the throughput check.

[0.3]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.3
[0.2]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.2
[0.1]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.1
