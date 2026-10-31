# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The repository version lives in [`VERSION`](./VERSION). The Pi agent in `monitor/`
reports its own `AGENT_VERSION` so it can be updated independently of the server.

## [Unreleased]

### Added

- **One-scan Android onboarding:** the Android agent's TV screen shows a QR code. Scanning it opens Remote Agent Onboarding with the installer code filled in; after Register, the waiting card has **Send setup key to this device**, which hands the key and name to the device over the home network (only to a private-network setup page from the QR link) and brings the phone back to approve. Nothing is typed or copied on the TV.
- **KPI report for an Android agent:** with an Android agent selected, the report keeps to what it measures: Voice, Video and Packet drop (Wi-Fi only, no Wired split) plus the voice, video and packet-drop charts. The Internet, Web, Time offline, Reporting gaps, Latency and Speed cards, and the internet, offline, latency, speed, Wired vs Wi-Fi and outage panels are hidden, since the Android agent runs none of those checks. Its daily health score is worked out from the voice stream and packet drop instead of being left blank. "All sensors" is unchanged.

- **Device type on the Home page:** each remote agent card shows a small hardware drawing and its platform under the name (Raspberry Pi · Linux, or NVIDIA Shield · Android). An Android agent's card shows only Wi-Fi and Apps instead of untested Wired and Web tiles, and its Wi-Fi tile follows its voice stream over Wi-Fi. `/api/v1/summary` monitors now include `platform`.

- **Setup key on registration:** registering an installer code now also shows a Setup key (the server address and the TLS key pins in one string, with a Copy button). A device with a setup page, such as the Android agent, takes that one string instead of a server command's output. The dashboard API container now mounts the TLS key volume read-only to build it. The Pi's two-field setup page is unchanged.
- **Android agent support on the server:** a remote agent can check in with `platform: android` (signed with the other check-in fields) and one WireGuard key. It can send the name its owner typed during setup (signed with the other check-in fields), and that name becomes the sensor's name when approved. It enrolls through the same installer code and approval as a Pi, gets a single tunnel, and is not assigned the Pi's internet checks, so it adds no gaps to the KPI. New `monitors.platform`, `enrollment_checkins.platform` and `enrollment_checkins.device_name` columns (migration 0051); every existing agent stays `pi`.
- **Gemini AI Root Cause Analysis on KPI Dashboard:** an on-demand **Determine Root Cause** button on the KPI Dashboard compiles the period's bandwidth test log, synthetic application state transitions, path/web failures, and alert history, and queries Google Gemini to report when the degradation started, the primary culprit, a chronological timeline, supporting evidence, and recommended actions. Configure the encrypted API key in Settings › Configure Gemini AI (`GET`/`PUT`/`DELETE /api/v1/settings/gemini`, `POST /api/v1/kpi/root-cause`). Migration `0050_gemini_settings`.
- **Hierarchical alert correlation & bandwidth drop alerts:** Telegram alerts now enforce a strict top-down root-cause hierarchy (`pi-offline` > shared WAN outage / ISP bandwidth collapse / WAN path degraded > single-link fault > measurement tunnel down > individual application failure). When an upstream ISP upload/download drop hits both Wired and Wi-Fi, a single `net-bandwidth:{monitor}:wan` alert is sent and subordinate path/web/application alerts are suppressed.
- **LAN scan names devices:** each host in Airwaves Security › Scans now shows its maker (from the MAC's IEEE OUI prefix, using the table that ships with Nmap), the name it announces over mDNS, and the service behind each open port. Randomized MACs are flagged as `Private address`. Migration `0049_lan_scan_hostname`. Agent **0.8.5**.
- **Tunnel-down alerts and auto-restore:** when a WireGuard handshake goes stale on an online Pi, a `pi` alert names the Wired or Wi-Fi **tunnel** (not an internet outage). Enable Telegram and Notify Pi in Settings to receive it. Agent **0.8.4** watches `10.7.0.1` / `10.7.1.1` and restarts `wg-quick@wg0` / `wg1` through `/opt/monitor/restore_tunnel.sh` (sudoers drop-in).

### Fixed

- **Android agents are tested over Wi-Fi only:** Speed Test and Real-Time Application Health no longer offer Wired (or Both) for an Android agent, and Network Test leaves it out, since the Android agent does not run stress tests. The server refuses those requests too: a Wired speed test or synthetic run for an Android agent gets 400, and so does any Network Test.

- **One SRTP key for every remote agent:** the voice receiver (RTNP 0.2.1) reads exactly one SRTP key and refuses to start with more, but the server gave each agent its own, so a second agent (the first Android agent) lost every voice packet and the next receiver restart would have stopped the voice and video checks for all agents. Every agent now shares one key, as they already share the control key. A server with per-agent keys keeps the oldest, which is the one its running receiver loaded, so the first agent and the receiver carry on unchanged; only that key is left in the receiver's key folder.
- **Agent enrollment port served plain HTTP:** the TLS flags for the agent API were on a separate line in `docker-compose.yml` and were dropped, so port 8848 answered plain HTTP and an agent that pins the server's TLS key could not enroll. The command is now one line and the port serves HTTPS.
- **Wi-Fi measurement tunnel self-heal after ISP recovery (agent 0.8.6):** when home internet comes back but `wg1` stays dead, Overview was blaming local Wi-Fi, and the agent waited up to 15 minutes between restores even though a restore during the outage had pinned a stale default route. Overview now labels that case as a measurement tunnel fault. The agent re-checks every 30s, retries within about a minute once the access plane is up again, fully stops/starts `wg-quick` so PostUp re-pins routes, and bounces both tunnels on a probe restart.
- **Bandwidth Test variance cap and Real-Time Application Health correlation:** `librespeed_variance_pct` is now capped at 50% (default 25%) so a 100% variance setting can no longer set the throughput floor to 0 Mbps and mask a severe upload collapse as `ok`. Ingest now refreshes `Test.stable_status` for `librespeed`, and `/api/v1/synthetic/health` correlates collapsed upload/download throughput as the root cause on the Real-Time Application Health banner.

- **KPI internet vs tunnel:** `icmp-path` / `http-path` target the WireGuard peer (`10.7.x.1`), not the open internet. Internet availability, outage minutes, and the outage list no longer count a path fail when a public web check on the same link still succeeds. Access-plane correlation for alerts uses public web only. Rebuild affected KPI hours after deploy.
- **KPI / Apps diagnosis:** public web proves the access plane. If web stays healthy on a link while voice/video over the tunnel fail, diagnosis is `tunnel` (measurement tunnel or receiver), not local Wi-Fi/Ethernet or internet down. Internet-outage alerts also skip when recent web samples on that link still succeed.

### Changed

- Landing page (www): project proof copy and figures brought in line with v0.5 (capabilities, 125 PRs, 205 tests).
- Landing page timeline: the Rust packet generator (RTN_PackGen) is listed as Live, not roadmap; it already runs on the production stack for Voice G.711 and other application checks.

## [0.5] - 2026-10-28

### Added

- **Airwaves Security tab:** next to Channels, with sub-tabs Alerts, Trust and Scans. WIDS (Kismet alerts) with filters for range, severity, trusted-only and search; Trust for evil twins and untrusted same-SSID APs; and on-demand LAN scans (Scapy ARP then Nmap on common TCP ports) on the Pi's wired LAN only, with history. New APIs: filtered `GET /api/v1/rf/alerts`, `GET /api/v1/rf/security`, `POST /api/v1/rf/lan-scan`, `GET /api/v1/rf/lan-scans`, `POST /api/v1/monitors/lan-scan`. Migration `0048_lan_scan`. Agent 0.8.3; install/update pull in `nmap` and `python3-scapy` when missing.
- Server Health, Interface tab: a Data used table under the network chart. It shows the bytes the machine received and sent in the last hour, 6 hours, 24 hours and 7 days and since the machine started, with a total for each. A period the history does not fully cover yet is marked "(so far)". Below the table are the app container's counters since it last started and a monthly pace of sending, worked out from the last 24 hours. The totals add up the counter increases, so a reboot or a container restart does not lose or invent data. The server's `GET /api/v1/server-health` response has a new `traffic` block. It needs no migration.

### Fixed

- Pi agent: the Apps checks no longer stop for the rest of the day. The agent had a 20 GiB daily traffic cap. At the current load (four continuous profiles on two links) the Pi reached it around 15:35 UTC each day, stopped every continuous flow, and the Apps tiles read "Not running" until midnight UTC. The daily cap is removed. The packet-rate limit and the per-run limit stay, and a stopped flow is relaunched on the next sync. The Pi needs the updated agent package (`monitor/README.md`, Update).

### Changed

- New logo: a rounded house with a heartbeat line replaces the house with the magnifying glass. The lettering is unchanged. It is used in the dashboard top bar, the landing page header, the README banner, and as the browser tab icon on the dashboard and the landing page (the dashboard had none).
- Landing page header: the logo no longer squeezes and the links and the "Open the Live system" button no longer wrap on screens narrower than about 1060px. There the sticky header is only the logo and the button, and the section links sit in a small strip below it that scrolls away. On phones the button reads "Live system".
- Landing page figures: updated to 125 merged pull requests, 205 automated tests and 30 fixes as of October 28, and the release line records v0.5.
- Backups are encrypted with age before they are saved. The server keeps the public key only. The private key stays off the machine. Server Health lists Yes or No in an Encrypted column on each copy.
- Application health charts stack round-trip, jitter, and packet loss in three full-width rows on every app check.
- Application checks and Internet Connection add a fourth chart, share of each limit. It keeps only the moments that crossed a limit, and each bar is that metric against its own limit, so 100% is the line and past it is a failure. Internet Connection stacks download, ping, and jitter the same way.
- Remote Probe Status: CPU now shows the average and peak over the last hour instead of one
  sub-second reading that usually read 0.0%. Location and probe facts sit side by side, and four
  vital tiles (CPU, memory, disk, temperature) switch a single 24-hour chart.
- Server Health network chart shows the Oracle machine interface and the app container interface.
- Server Health has a Backup tab with the time and size of each database, WireGuard, and agent TLS copy. The files are not offered for download.
- Server Health CPU tab lists the processes using the machine since the last sample.
- Server Health Memory tab lists the processes holding the most memory.

## [0.4] - 2026-10-20

### Added

- **Server Health:** a kebab item under Issues for an admin. It graphs the server's CPU, memory, host interface, disk, and database, forecasts when the database would fill the disk, lists how long each kind of row is kept, and shows a weekly software and vulnerability check plus processes that look stuck. Migration `0045_server_health`.
- **Software failures stay out of the KPI:** a continuous check that reports no packets and no round trip, while another check on the same link is still getting through, is marked as a software problem. The result log says so, an issue is opened, the KPI leaves those samples out, and the monitor is asked to restart that test (agent 0.8.1). A blackout of every check on the link is still a real failure. Migration `0044_software_impact`.

### Changed

- **Voice label:** the G.711 A-law check is labeled Voice G.711.
- **Server folder name:** the production checkout is now `~/Home-Net-Watch`. The docs, the backup cron example and `rtnp-allowlist.service` use that path, and `COMPOSE_PROJECT_NAME=home-sensor` stays pinned in `.env.example` so the containers and volumes keep their names.
- **Network Test:** the Wired and Wi-Fi choices are one segmented switch, and the button that starts a test matches the Start test button on Speed Test. The remote agent picker stays 200 pixels wide at the right of the title.
- **Speed Test path:** while a test runs, packets ease up to the measured speed and keep moving instead of restarting on each sample. Download flows toward the Pi and upload flows the other way. On a phone the same path is a smaller row that fits the screen.
- **Probe status map:** the state borders are drawn thinner.
- **Path View, network path:** redrawn as one line instead of boxes. Each network is a coloured stretch of the line, its hops rise from it as bars, and the latency added at each handoff sits between the networks (amber at 15 ms or more). When the path does not fit across, it becomes a rail running down the page with one row per hop. The diagram is plain HTML now, not React Flow, so a thumb on a phone scrolls the page instead of being caught by the diagram. Picking a network or a hop works as before.
- **Path View, BGP view:** redone as one checklist read from you at the top to the destination at the bottom, with a rail and arrows showing the direction. Each network is one row, and two columns (Your trace, BGP path) number its place along each path, or show a dash if it is not on that path. A plain sentence at the top says whether the traffic reaches the origin network BGP announces for the prefix; the old "Paths differ" warning is gone because the networks in between normally differ. The React Flow diagram is removed, so the page scrolls on a phone.
- **Path trace check:** a trace that reaches the destination's network but whose last hop drops the trace probes now passes when a ping to the same host gets through, instead of failing every time. The ping was aimed at the tunnel address, which always answers, so the check never noticed the public address was up.
- **Path View header:** the "Traced today at ... · hops · networks" line under the title is gone. The line still appears while tracing and when a trace fails or does not exist yet.
- **Path View, AS number:** the AS badge in the details panel (for example AS7922) stays on one line; a long network name is cut short instead.
- **Top bar buttons:** Back, Home and the three dots are drawn in the same 15 by 15 box with the white, round-ended stroke of the menu icons, so they match each other and the menu. The Back button is a plain chevron without a tail.
- **Landing page figures:** updated to 116 merged pull requests, 172 automated tests and 28 fixes as of October 20, and the release line now says release 0.2 is published on GitHub (0.4 is in the changelog and `VERSION` but has no GitHub release yet).
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

[0.5]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.5
[0.4]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.4
[0.3]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.3
[0.2]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.2
[0.1]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.1
