# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The repository version lives in [`VERSION`](./VERSION). The Pi agent in `monitor/`
reports its own `AGENT_VERSION` so it can be updated independently of the server.

## [Unreleased]

### Security

- **Old-style agent keys are no longer accepted:** every agent key is now stored as SHA-256, so the bcrypt lookup
  for keys from before enrollment is removed. An agent still on such a key must be enrolled again.
- **Dependency and secret scans in CI:** a new Security scans workflow audits the server's locked Python packages
  and the dashboard's shipped npm packages for known advisories, and scans the whole git history for committed
  secrets with gitleaks. It runs on every pull request, on `main` and weekly. An optional `.pre-commit-config.yaml`
  runs the secret scan and blocks `.env`, key and keystore files before a commit.
- **Dashboard file server: patched `compression`:** `serve` 14.2.7 bundles `compression` 1.8.1, which can leak memory
  when a response is closed early (GHSA-vc2v-76pw-4v95). It is overridden to 1.8.2.
- **Old-style agent keys are upgraded on use:** an agent set up before enrollment has its key stored as a bcrypt
  hash, so every request from it cost a bcrypt check. The first time such a key is verified, the server now stores
  it as SHA-256 like an enrollment key (only when the key is at least 32 characters; a shorter one stays behind
  bcrypt).
- **The API runs as a non-root user:** `server` and `agent-api` ran as root (with CHOWN, DAC_OVERRIDE and FOWNER),
  so a bug in the API could read or change any file in its volumes, including the WireGuard hub's private key. They
  now run as `hns` (uid 10001) with no capabilities. A one-shot `state-init` service gives the WireGuard, RTNP and
  TLS volumes to that user before they start, keeping the hub's private key and `wg0.conf` root-only. The RTNP
  receiver gets its keys through its group, `hoststat` copies the backup list so the API no longer mounts
  `./backups`, and `scripts/backup.sh` and the restore steps read the WireGuard state through the hub.
- **A separate key for each purpose:** `SECRET_KEY` itself signed sessions, made CSRF tokens, protected installer
  codes and encrypted the stored Telegram and Gemini secrets. Each purpose now uses its own key derived from it
  with HKDF (`server/app/keys.py`). The new `SECRET_KEY_PREVIOUS` makes rotation safe: while it holds the old
  value, old sessions, CSRF tokens and installer codes stay valid, and the stored secrets are re-encrypted under
  the new key at startup. Before, rotating the key lost those secrets. Deploying this signs everyone out once;
  stored secrets carry over.
- **Security events are audited:** every non-guest sign-in (with its address), failed sign-in, sign-in block, and
  change to the Telegram, Gemini, web service, stability and internet service settings now writes an
  `audit_events` row. Failures are written only until the address or account is blocked, and the block once, so a
  flood can't fill the table. With Telegram alerts set up, an admin sign-in from an address not seen before sends
  a message.
- **Traces go to the address the server checked:** for a trace typed into the dashboard, the server checked that
  the name resolved to public addresses, then the agent resolved it again, so a name that changed its answer (DNS
  rebinding) could have pointed the trace into the home network. The agent now gets the public IPv4 address the
  server resolved (migration `0054_trace_address`); the name is shown in the dashboard only. A name with no public
  IPv4 address is refused.
- **CI actions pinned to commits:** every GitHub Action is pinned by full commit SHA (Dependabot keeps them current)
  and `cargo-ndk` by version. The Android signing key moves to an `android-signing` environment that only `main`
  builds use, so a hijacked tag or a branch build can't sign an update for the TV.
- **Request size limits:** sign-in usernames are capped at 64 characters and passwords at 256, and Caddy refuses API
  request bodies over 1 MB. Before, an anonymous request could hand the server megabytes to parse and hash.
- **Old-style agent keys need the device name:** a key from before enrollment is stored as a bcrypt hash. An unknown
  key without an `X-Monitor-Name` header made the server run bcrypt against every such agent; now only the named
  device's hash is checked, and a key without a name is refused.
- **Crash details stay out of the stored log:** a failed request is logged in `system_logs` (readable in the
  dashboard) with the error type and a request id only. The exception text goes to the container log under that id.
- **Pi agent 0.8.12:** a trace or ping target must start with a letter or digit, and `--` is passed before it, so a
  target can never be read as a `ping` or `mtr` option.
- **Sessions can be ended:** a session cookie held only the user id, so a copied admin cookie stayed valid for seven
  days, even after signing out or a new password. Each account now has a session version that every cookie carries.
  Signing out ends all of that account's sessions on every device (the shared guest account excepted), a new
  password from the seed does the same, and `python -m app.sessions revoke <username>` (or `--all`) does it from the
  server. Migration `0053_session_version`; existing cookies keep working until one of those happens.
- **Gemini actions need the CSRF token:** Fault Simulation (`POST /server-health/fault-simulation`) and Root Cause
  (`POST /kpi/root-cause`) spend Gemini quota and store results, but skipped the CSRF check every other admin action
  has. Another site under the same domain could have made a signed-in admin's browser run them.
- **API docs are off:** `/docs`, `/redoc` and `/openapi.json` listed every route and schema. They are off unless
  `API_DOCS=1` is set for development.
- **The API no longer mounts the host:** the server container ran as root with the whole host filesystem and
  `/proc` mounted read-only, so any file-read bug or code execution in the API would have exposed every secret on
  the machine (SSH keys, other projects' `.env`, every Docker volume). A new `hoststat` container with no network
  and one capability copies only what Server Health shows into the `hostinfo` volume. `server` and `agent-api`
  now drop all capabilities but three needed for their own state files, run read-only, and can't gain privileges.
- **Images build from the lockfiles:** the server image installed the newest release of every dependency instead of
  what `uv.lock` pins, so production could run code CI never tested. It now installs from `uv.lock` with hash checks;
  the dashboard uses `npm ci` and a pinned `serve` (`dashboard/serve/`); LibreSpeed checks the release file's SHA-256;
  base images are pinned by digest, and Dependabot proposes updates.
- **Guests see sample software and processes in Server Health:** the public guest could read the host's OS, kernel,
  running programs, home folder names and every package with a known vulnerability. Guests now get a fixed sample for
  those parts; the CPU, memory, disk, traffic and database charts stay real. The admin view is unchanged.
- **The agent port serves enrollment only:** port 8848 is public so agents at home can enroll, but it ran the whole
  API, including sign-in and every admin route, and the API docs. It now serves only `/api/v1/agents/*`
  (`AGENT_API_ONLY=1` in `docker-compose.yml`); the dashboard API stays behind Caddy.
- **Sign-in limit can't be dodged with a forged header:** `X-Forwarded-For` is used only when the request comes from
  a trusted proxy (`TRUSTED_PROXIES`, default `caddy`). Anyone else is limited by their own address, so a client that
  writes a new address on every try no longer gets unlimited guesses.
- **Per-account sign-in limit:** besides 8 failures per address, an account name allows 30 failed sign-ins per
  15 minutes from all addresses together. The public guest account is exempt, so it can't be locked for visitors.
- **Guests no longer see Wi-Fi names, BSSIDs or home addresses inside sentences:** guest redaction hid these only
  under known field names, so they still reached guests in issue titles ("Possible evil twin <SSID> on <BSSID>"), airwave
  test names and log messages. Every other string a guest receives is now searched for MAC addresses, private and
  CGNAT addresses, the agents' own addresses, and the Wi-Fi names, places and AP labels this installation knows; only
  those parts become `#`. Path View's public hops stay visible as before.

### Changed

- Home page redesign: a summary sentence with the overall mood, agents reporting, open issues and the time of the last check; each agent's Wi-Fi, Wired, Web and Apps tile now shows a live number (round trip, sites up, jitter) and a one-hour trend line from the continuous streams; a Lately list of the most recent issues; shortcuts to Path View, Airwaves, Issues, KPI Dashboard and Server Health. The characters, background and trend lines are animated, and all motion is off when the device asks for reduced motion.
- **The Cloudflare Speed Test runs on the clock, as often as every 15 minutes (Pi agent 0.8.17):** the shortest
  interval is 15 minutes, down from 30. The agent runs the test at whole intervals past the hour (every 15 minutes
  means :00, :15, :30 and :45), where it used to run a set time after the last run and drift later each time. After a
  refusal it skips to the first such time past the wait Cloudflare asked for.
- **KPI internet speed comes from the Cloudflare Speed Test:** an hour and link with a Cloudflare reading uses it,
  since it measures the line and not the HomeNetWatch server. An hour without one, such as the Wi-Fi link or any hour
  before that test existed, keeps the Bandwidth Test's figures. Determine Root Cause is given both tests' readings.
- **Health rows show internet availability, not the Bandwidth Test:** the row under the Cloudflare reading on an
  agent's Wired view, the only row on its Wi-Fi view, and the two summaries on the Internet Connection page are now
  **Internet Availability**: the share of the minute-by-minute ping checks that got an answer, with the latest ping
  and loss, and ticks coloured from the ping and HTTP path checks. They no longer need the Bandwidth Test, which can
  be turned off under Settings > Internet Service: each run filled the line for about 40 seconds and showed up as
  failed samples on the real-time flows. The connection diagram shows the Cloudflare speed on the wired link and the
  ping alone on Wi-Fi. Past Bandwidth Test readings stay on the charts.
- **The Cloudflare Speed Test is gentler (Pi agent 0.8.16):** it filled the line with parallel connections for 8
  seconds each way, about 500 MB a run on a fast line, and Cloudflare refused it (HTTP 429) for hours afterwards. It
  now makes a few transfers one at a time, small ones first, with a pause between them, the way Cloudflare's own page
  does: about 130 MB down and 40 MB up. The reading is the typical speed of the largest transfers, so download reads
  a little lower than before on a fast line. After a refusal the agent waits the time Cloudflare asks for, plus five
  minutes, before it tries again.
- **Health rows are a row of ticks with a time axis:** the solid status bar on the Internet Connection and Real-Time
  Application Health rows is now 48 separate ticks running from the start of the selected range to now, with the
  start, midpoint and "now" labelled underneath. A row where everything passed no longer looks like a plain green
  line. When one tick covers several samples it takes the worst status, so a short failure still shows. The gap between
  ticks is the same whole number of screen pixels everywhere; spare pixels widen the first ticks by one pixel each.

- **KPI valid working hours (6:00 AM – 12:00 AM EST, 7 days a week):** KPI availability, packet drop, time offline, outages, reporting gaps and daily health scores now count only measurements taken during valid working hours from 6:00 AM to 12:00 AM Eastern Time (`America/New_York`), 7 days a week; overnight hours (12:00 AM – 6:00 AM ET) are excluded. Migration `0055_kpi_valid_hours` and the KPI backfill purge historical off-hour rows from `kpi_hourly` and recompute `kpi_daily` by Eastern date so all past dates reflect the working-hours window. The KPI Dashboard header, verdict banner, Daily KPI History and footer explicitly state the valid hours.
- **Landing page redesign:** `site/` has a new look and message (enterprise infrastructure principles applied to a
  home network), a moving radar background that follows the pointer, clickable system layers, an advanced-features
  grid and a new Security section. Still plain HTML, CSS and JS under the existing CSP; `enter.html` is unchanged.
- **Guest view: hidden values are `#` and blurred:** identifying values in a guest's view (home and LAN addresses,
  the city, device and wireless names) are still replaced letter by letter with `#` on the server, so the real value
  never reaches the browser, and the dashboard now also blurs them so a guest can see that something is hidden. The
  server marks each hidden value with a leading word joiner (U+2060). A saved place keeps its state and country
  readable, and Path View keeps the public hops, networks and BGP details visible and hides only the source (home) hop.
- **Live dashboard opens already signed in as the guest:** the landing page's **Open live dashboard** button goes to
  `/welcome/enter.html`, which signs the visitor in as the guest from the same site (`POST /api/v1/auth/guest/session`,
  accepted only when the request's `Origin` is that site) and opens the dashboard. `GET /api/v1/auth/guest` redirects
  there. Sign-in limits and guest-visit recording are unchanged.
- **Landing page:** it now leads with the platform and the written operating rules the coding agents built it from,
  and the author section has a portrait and a short leadership line.
- **Speed Test link buttons:** Wired or Wi-Fi is greyed out when the selected device does not have that link (for
  example Wired on an Android agent), instead of being offered and then refused.
- **Remote Probe Status dialog:** a compact, phone-friendly inspector, with Pi-only details left out for an Android
  agent.
- **Documentation refresh:** README, SPEC, CLAUDE.md, CONTRIBUTING, SECURITY, the deploy guides and the Pi, Android
  and dashboard READMEs now match the code: installer-code enrollment, the Android agent, current agent versions
  (Pi 0.8.11, Android 0.5.0), every dashboard route, the API route groups, the guest blur, and the UDP 8998 allowlist
  timer, which the deploy guides now install. The Oracle guide's clone command and update steps are fixed.
- **Path View BGP from your provider:** when a public route collector sees a route to the destination through the agent's own provider, the BGP view shows that route from the provider onward, so it starts where the trace does. Otherwise it shows the most common route and says no collector sees one through the provider. Repeated (prepended) networks count once.
- **Location fields in order:** a remote agent's Location section asks for Country, then State/Province, then City, so the state list matches the country before the city is typed.
- **Bandwidth Test variance goes up to 100%:** the cap was 50%, and a higher value was quietly saved as 50%. At 100% no drop in speed turns the Bandwidth Test amber, so its alerts are off; a test that gets no answer in either direction is still red. The Internet Service page shows the 0–100% range, and the API rejects values above 100 instead of changing them.
- **Logo:** the house now sits in a rounded square, centred on the wordmark, on the dashboard and the landing page.
- **Real-Time Application Health is shorter:** the Voice G.723.1, Market data, Fortnite and Call of Duty presets and the
  Games group are gone, and so is the Timed runs list at the bottom of the page (each preset still has **Run once**).
  A continuous or pending run of a removed preset is stopped when the server starts or the page loads, the removed
  presets can no longer be started, and their stored results stay readable. The Network Test (stress run) keeps its own
  G.723.1 and Market data options.

### Added

- **Cloudflare Speed Test (Pi agent 0.8.13):** a second scheduled speed test that measures the wired link of a
  Raspberry Pi agent against `speed.cloudflare.com`, outside the tunnel, so the HomeNetWatch server's own bandwidth
  does not limit the reading. It runs every 60 minutes by default (30 minutes at the fastest, set under Settings >
  Internet Service), raises no alerts and stays out of the Overview and the KPI figures. Its download and upload
  show as a second chart on the wired Internet Connection view. The Bandwidth Test and the Speed Test to the server
  are unchanged. When Cloudflare refuses a run, the agent sends no result (migration `0056_cloudflare_speed`).
  Agent 0.8.14 reports the round trip Cloudflare measured for the connection; 0.8.13 timed each request, which added
  the time Cloudflare takes to build its answer.
- **Wired Network Health shows the Cloudflare reading first:** on a Raspberry Pi agent's Wired view, the first row is
  **Internet Connection to Cloudflare**: the newest Cloudflare Speed Test reading with the time it was measured. The
  Bandwidth Test to the server sits below it as **Internet Connection to Dashboard**. The charts follow the same order
  and names. Wi-Fi and Android agents are unchanged.

- **Server-only sites stay out of the repository:** `deploy/Caddyfile` imports `deploy/local/*.caddy` and the
  production overlay mounts that folder, and an optional `docker-compose.local.yml` can be added to `COMPOSE_FILE`.
  Git ignores both, so extra sites and services on the same server no longer have to be edited into tracked files
  (which blocked `git pull`). See the deploy guide, Server-only sites and services.
- **Android agent updates itself, with the owner's OK:** each time the app starts (or **Restart agent** is chosen) it
  checks for a newer version, asks on the TV, downloads it, checks its SHA-256, package and version, and hands it to
  Android's installer, which asks once more. CI releases each higher `versionCode` built on `main` as `android-v<version>`
  and points `latest.json` on the `android-latest` release at it. The TV screen shows the app version. Android agent
  0.5.0 (includes the µ-law profile from 0.4.1).

### Fixed

- **Measurement flows no longer lose a packet every minute:** the UDP 8998 allowlist was rebuilt step by step every 60
  seconds, and for a few milliseconds its address set was empty while the chain still ended in `drop`. Every running
  voice, video and remote desktop flow lost a packet in that gap. `scripts/rtnp-allowlist.py` now leaves the filter
  alone when the peer addresses have not changed and otherwise replaces it in one nft transaction. It also keeps the
  filter as it is when WireGuard cannot be asked for its peers.
- **A deploy no longer restarts every agent:** `scripts/deploy.sh` recreated the RTNP receiver on every full deploy.
  That cut the control connection of the running voice, video and remote desktop flows, the server then asked each
  agent to restart, and the restarted agent ran all of its tests again at once. The receiver is now recreated only
  when the WireGuard container really was recreated.
- **The Cloudflare Speed Test keeps its schedule across an agent restart (Pi agent 0.8.15):** a test that runs at most
  every 30 minutes saves the time of its last run in `schedule.json` beside the agent's config, so a restart does not
  run it again early. Cloudflare refuses runs that come too often.
- **Caddyfile changes reach Caddy:** `deploy/Caddyfile` is mounted into the container as a single file, and
  `git pull` replaces the file, so the running container kept the old copy and neither a deploy nor `caddy reload`
  applied a change. `scripts/deploy.sh` now compares the two copies after every deploy and, when they differ,
  validates the new file in a throwaway container and recreates caddy. An invalid file leaves caddy as it was.
- **Maps no longer cover open menus:** a dropdown opened above a map (for example State/Province in a remote agent's Location) shows in full instead of being hidden behind the map.
- **Daily KPI history rows by month:** the KPI history heatmap now renders one row per calendar month (up to 12 months
  at a time) with up to 31 day columns (`1`–`31`), instead of 7 weekday rows across week columns.
- **Raspberry Pi Wi-Fi throughput & Kismet capture isolation (agent 0.8.11):** restores default `librespeed-cli`
  multi-stream upload sizing, standard `AC_BE` hardware A-MPDU aggregation on `brcmfmac` (`rtnp_bind.c`), and full
  MTU `fq_codel` quantum on `wlan0` after single-stream `256 KiB` uploads and `AC_VO` (`0xB8`) tagging reduced Wi-Fi
  upload throughput and overflowed the `brcmfmac` `AC_VO` firmware ring, while keeping `radio.py` survey isolation
  and Kismet `1/sec` channel hopping.
- **Raspberry Pi Wi-Fi stability and multicore tuning (agent 0.8.10):** `update.sh`, `install.sh`, `restore_tunnel.sh`,
  and the `hnw-wifi-tune.service` boot unit now run `tune_wifi.sh` to unmanage `hnwradio*` and `p2p-dev-*` in
  NetworkManager (stopping `wpa_supplicant` background scan clashes against Kismet's capture radio), pin the active
  5 GHz Wi-Fi connection's band and BSSID so the single-radio `brcmfmac` adapter does not roam onto 2.4 GHz or run
  periodic multi-band roaming scans, move the `mmc1` SDIO Wi-Fi interrupt off `CPU0` onto `CPU1`, enable multicore
  RPS/RFS across `CPU1–CPU3`, and set `fq_codel` (`quantum 300`) on `wg0`, `wg1`, and `wlan0`.
- The stress test now names the A-law codec **Voice G.711 A-law**, matching the voice profile list.

## [0.6] - 2026-10-30

### Added

- **Voice G.711 µ-law profile:** a two-way G.711 µ-law (payload type 0) voice check next to the existing A-law one, which is now named **Voice G.711 A-law** (same slug, so running checks and history are unchanged). The Pi agent (0.8.8), the Android agent (0.4.1) and the server accept the `voip-g711` preset, one-way and two-way. Voice profiles are ordered A-law, µ-law, G.729, G.723.1.

- **Android onboarding without the phone reaching the TV:** registering from the TV's QR link now stores its pairing token (hashed, migration `0052_installer_pairing`), and the TV collects its setup key and name itself from `POST /api/v1/agents/pairing` on the public site. The **Send setup key to this device** step is gone, so onboarding works from any browser and any network (Chrome on Android refused to open the TV's home-network page). Android agent 0.4.0.

- **One-scan Android onboarding:** the Android agent's TV screen shows a QR code. Scanning it opens Remote Agent Onboarding with the installer code filled in; after Register, the waiting card has **Send setup key to this device**, which hands the key and name to the device over the home network (only to a private-network setup page from the QR link) and brings the phone back to approve. Nothing is typed or copied on the TV.
- **KPI report for an Android agent:** with an Android agent selected, the report keeps to what it measures: Voice, Video and Packet drop (Wi-Fi only, no Wired split) plus the voice, video and packet-drop charts. The Internet, Web, Time offline, Reporting gaps, Latency and Speed cards, and the internet, offline, latency, speed, Wired vs Wi-Fi and outage panels are hidden, since the Android agent runs none of those checks. Its daily health score is worked out from the voice stream and packet drop instead of being left blank. "All sensors" is unchanged.

- **Device type on the Home page:** each remote agent card shows a small hardware drawing and its platform under the name (Raspberry Pi · Linux, or NVIDIA Shield · Android). An Android agent's card shows only Wi-Fi and Apps instead of untested Wired and Web tiles, and its Wi-Fi tile follows its voice stream over Wi-Fi. `/api/v1/summary` monitors now include `platform`.

- **Setup key on registration:** registering an installer code now also shows a Setup key (the server address and the TLS key pins in one string, with a Copy button). A device with a setup page, such as the Android agent, takes that one string instead of a server command's output. The dashboard API container now mounts the TLS key volume read-only to build it. The Pi's two-field setup page is unchanged.
- **Android agent support on the server:** a remote agent can check in with `platform: android` (signed with the other check-in fields) and one WireGuard key. It can send the name its owner typed during setup (signed with the other check-in fields), and that name becomes the sensor's name when approved. It enrolls through the same installer code and approval as a Pi, gets a single tunnel, and is not assigned the Pi's internet checks, so it adds no gaps to the KPI. New `monitors.platform`, `enrollment_checkins.platform` and `enrollment_checkins.device_name` columns (migration 0051); every existing agent stays `pi`.
- **Android agent:** `android/` holds a Kotlin app that makes an Android TV box such as the NVIDIA Shield a Wi-Fi-only remote agent. It enrolls like a Pi (installer code, signed check-in with `platform: android`, admin approval), with one-scan QR onboarding from the TV screen. Once approved it brings up one WireGuard tunnel (Android asks once on the TV), sends health reports, runs the voice and video streams with the bundled `rtnp-send` (built for arm64 and x86 by `android/build-sender.sh`), runs the Speed Test, restarts silent flows like the Pi, and starts by itself after a reboot or update. A GitHub Actions workflow builds the APK as a downloadable artifact.
- **Gemini AI Root Cause Analysis on KPI Dashboard:** an on-demand **Determine Root Cause** button on the KPI Dashboard compiles the period's bandwidth test log, synthetic application state transitions, path/web failures, and alert history, and queries Google Gemini to report when the degradation started, the primary culprit, a chronological timeline, supporting evidence, and recommended actions. Configure the encrypted API key in Settings › Configure Gemini AI (`GET`/`PUT`/`DELETE /api/v1/settings/gemini`, `POST /api/v1/kpi/root-cause`). Migration `0050_gemini_settings`.
- **Hierarchical alert correlation & bandwidth drop alerts:** Telegram alerts now enforce a strict top-down root-cause hierarchy (`pi-offline` > shared WAN outage / ISP bandwidth collapse / WAN path degraded > single-link fault > measurement tunnel down > individual application failure). When an upstream ISP upload/download drop hits both Wired and Wi-Fi, a single `net-bandwidth:{monitor}:wan` alert is sent and subordinate path/web/application alerts are suppressed.
- **LAN scan names devices:** each host in Airwaves Security › Scans now shows its maker (from the MAC's IEEE OUI prefix, using the table that ships with Nmap), the name it announces over mDNS, and the service behind each open port. Randomized MACs are flagged as `Private address`. Migration `0049_lan_scan_hostname`. Agent **0.8.5**.
- **Tunnel-down alerts and auto-restore:** when a WireGuard handshake goes stale on an online Pi, a `pi` alert names the Wired or Wi-Fi **tunnel** (not an internet outage). Enable Telegram and Notify Pi in Settings to receive it. Agent **0.8.4** watches `10.7.0.1` / `10.7.1.1` and restarts `wg-quick@wg0` / `wg1` through `/opt/monitor/restore_tunnel.sh` (sudoers drop-in).

### Fixed

- **Remote desktop and other non-RTP flows report a real round trip (agent 0.8.7, Android agent):** without RTP there is no RTCP round trip, so the dashboard showed the receiver's one-way delay, which compares the agent's clock with the server's. On the Pi that was about half the real figure; on the Shield, whose clock ran 2 s behind, it read about 2,000 ms. Agents now pass `--meter rtt`: the receiver reflects each packet (same size, and UDP 8998 only accepts the WireGuard peers' addresses) and the agent times the round trip on its own clock. Remote desktop on the Pi moves from about 49 ms to about 95 ms, well inside its 300 ms limit. A profile still cannot set the meter itself.

- **Pi tunnel self-heal never ran (agent 0.8.7):** `tunnel_watch` checked for `/etc/wireguard/wgN.conf`, which the `monitor` user cannot read (the directory is root-only), so every 30-second check stopped with `Permission denied` and a dead Wi-Fi or wired tunnel was never restored. It now asks systemd whether the `wg-quick@wgN` unit is enabled instead.

- **Android agents are tested over Wi-Fi only:** Speed Test and Real-Time Application Health no longer offer Wired (or Both) for an Android agent, and Network Test leaves it out, since the Android agent does not run stress tests. The server refuses those requests too: a Wired speed test or synthetic run for an Android agent gets 400, and so does any Network Test.

- **One SRTP key for every remote agent:** the voice receiver (RTNP 0.2.1) reads exactly one SRTP key and refuses to start with more, but the server gave each agent its own, so a second agent (the first Android agent) lost every voice packet and the next receiver restart would have stopped the voice and video checks for all agents. Every agent now shares one key, as they already share the control key. A server with per-agent keys keeps the oldest, which is the one its running receiver loaded, so the first agent and the receiver carry on unchanged; only that key is left in the receiver's key folder.
- **Agent enrollment port served plain HTTP:** the TLS flags for the agent API were on a separate line in `docker-compose.yml` and were dropped, so port 8848 answered plain HTTP and an agent that pins the server's TLS key could not enroll. The command is now one line and the port serves HTTPS.
- **Wi-Fi measurement tunnel self-heal after ISP recovery (agent 0.8.6):** when home internet comes back but `wg1` stays dead, Overview was blaming local Wi-Fi, and the agent waited up to 15 minutes between restores even though a restore during the outage had pinned a stale default route. Overview now labels that case as a measurement tunnel fault. The agent re-checks every 30s, retries within about a minute once the access plane is up again, fully stops/starts `wg-quick` so PostUp re-pins routes, and bounces both tunnels on a probe restart.
- **Bandwidth Test variance cap and Real-Time Application Health correlation:** `librespeed_variance_pct` is now capped at 50% (default 25%) so a 100% variance setting can no longer set the throughput floor to 0 Mbps and mask a severe upload collapse as `ok`. Ingest now refreshes `Test.stable_status` for `librespeed`, and `/api/v1/synthetic/health` correlates collapsed upload/download throughput as the root cause on the Real-Time Application Health banner.

- **KPI internet vs tunnel:** `icmp-path` / `http-path` target the WireGuard peer (`10.7.x.1`), not the open internet. Internet availability, outage minutes, and the outage list no longer count a path fail when a public web check on the same link still succeeds. Access-plane correlation for alerts uses public web only. Rebuild affected KPI hours after deploy.
- **KPI / Apps diagnosis:** public web proves the access plane. If web stays healthy on a link while voice/video over the tunnel fail, diagnosis is `tunnel` (measurement tunnel or receiver), not local Wi-Fi/Ethernet or internet down. Internet-outage alerts also skip when recent web samples on that link still succeed.

### Changed

- Home page: Android agent cards keep the same two-column check button width as Raspberry Pi cards.
- Landing page (www): interactive 5-layer system topology, updated for v0.6 (Android TV agent, 160+ PRs, 250+ tests).
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

[0.6]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.6
[0.5]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.5
[0.4]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.4
[0.3]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.3
[0.2]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.2
[0.1]: https://github.com/evaneyou/homenetwatch/releases/tag/v0.1
