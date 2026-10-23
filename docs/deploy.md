# Deploy on a server

This guide runs the stack on a Linux server with automatic HTTPS. Any small cloud VM or VPS is enough. You need a domain name you can point at the server, and an SSH key pair.

## What gets exposed

| Port | Protocol | Purpose | Public |
|------|----------|---------|--------|
| 22 | TCP | SSH | Yes (restrict to your IP if you can) |
| 80, 443 | TCP | HTTPS dashboard and API through Caddy | Yes |
| 443 | UDP | HTTP/3 (optional) | Yes |
| 8848 | TCP | Agent enrollment, TLS with a pinned key | Yes |
| 51820 | UDP | WireGuard tunnels from your Pis | Yes |
| 55432, 8847, 3847 | TCP | Postgres, API, dashboard | **No**, internal only |

[`docker-compose.prod.yml`](../docker-compose.prod.yml) closes the last row and adds Caddy.

Open those public ports in the server's firewall. Docker publishes container ports through its own rules. If a port is still unreachable, check the host firewall and allow each port:

```bash
sudo iptables -L INPUT -n --line-numbers
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save
```

Repeat the insert for 443/tcp, 443/udp, 8848/tcp, and 51820/udp.

## 1. Point your domain at the server

Create an **A record** for the name you want (for example `hns.example.com`) pointing at the public IP. If you use Cloudflare, set it to **DNS only** (grey cloud), because Caddy needs to reach Let's Encrypt directly and the agent ports can't go through the proxy.

Wait until `dig +short hns.example.com` returns your IP.

## 2. Install Docker

```bash
ssh <user>@<public-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
exit            # log back in so the group applies
docker compose version      # must be 2.24 or newer
```

## 3. Get the code and configure it

```bash
git clone https://github.com/evaneyou/homenetwatch.git
cd homenetwatch
cp .env.example .env
```

Generate secrets with `openssl rand -hex 32` and edit `.env`:

| Variable | Set it to |
|----------|-----------|
| `DOMAIN` | `hns.example.com` (your name) |
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml` (uncomment it) |
| `SECRET_KEY` | a long random value |
| `POSTGRES_PASSWORD` | a long random value |
| `BOOTSTRAP_ADMIN_PASSWORD` | your admin password |
| `BOOTSTRAP_VIEWER_PASSWORD` | your viewer password |
| `CORS_ORIGINS` | `https://hns.example.com` |
| `COOKIE_SECURE` | `true` |
| `WIREGUARD_ENDPOINT` | `<public-ip>:51820` |

`DATABASE_URL` in the file is ignored in Compose, which builds it from the `POSTGRES_*` values.

Because `COMPOSE_FILE` is set, plain `docker compose` commands in this directory use the production stack. Lock the file down: `chmod 600 .env`.

## 4. Start it

```bash
./scripts/deploy.sh
docker compose exec server python -m app.seed --users-only
```

The `rtnp` image builds from a copy of the [RTN_PackGen](https://github.com/evaneyou/RTN_PackGen)
project in `rtnp/`. It is a separate repository, so clone it there first; it is not
part of this one. Use the same release for the receiver and the Pi `rtnp-send`
(currently `v0.2.1`). 0.2 speaks real SRTP, so a 0.1 sender cannot talk to a 0.2 receiver.
0.2.1 encrypts the return stream of a two-way call with the same key.

```bash
git clone --branch v0.2.1 --depth 1 https://github.com/evaneyou/RTN_PackGen.git rtnp
```

The first build compiles the dashboard and takes a few minutes. Migrations run automatically on start. `--users-only` creates the admin and viewer accounts **without demo data**, and refuses to run with the default passwords. Do not run the plain seed on this server.

Check it:

```bash
docker compose ps
curl https://hns.example.com/api/v1/healthz
```

Then open `https://hns.example.com` and sign in as `admin`. Caddy needs a minute to get its certificate on first start; `docker compose logs caddy` shows progress.

## 5. Connect a Raspberry Pi

1. In the dashboard open **Settings → Remote Agent Onboarding** and add the device.
2. Install the agent on the Pi as described in [`monitor/README.md`](../monitor/README.md).
3. On the Pi's setup page enter the server address `https://hns.example.com`, the device name and key.
4. When you issue tunnels, the Pi connects to `<public-ip>:51820`. After the tunnel is up the agent uses the tunnel address (`http://10.7.0.1:8847`) for its tests.

Print the agent API's TLS key pins and keep them:

```bash
docker compose exec agent-api python -m app.tls pins
```

## 6. Back up

```bash
./scripts/backup.sh
```

This writes a database dump plus the WireGuard and TLS state to `backups/`, keeping the newest 14 of each. Each file is encrypted with [age](https://github.com/FiloSottile/age) to the public key at `~/.config/homenetwatch/backup.pub` (override the path with `BACKUP_PUBKEY`). The private key is not on the server. Server Health shows the time and size of those copies, and does not serve the files. Run it daily from cron and copy `backups/` somewhere else:

```bash
# On a machine other than the server, once:
age-keygen -o backup.key
age-keygen -y backup.key > backup.pub
# Copy only backup.pub to the server's ~/.config/homenetwatch/backup.pub
# Keep backup.key somewhere you can get it back. Without it the copies cannot be read.

crontab -e
# 15 3 * * * cd /home/ubuntu/Home-Net-Watch && ./scripts/backup.sh
```

The TLS and WireGuard archives matter as much as the database: they hold the keys your Pis trust. Without them every Pi has to be re-enrolled.

**Restore** on a fresh server after the stack is up. Decrypt on a machine that has `backup.key`, then load the plaintext into the stack. The database file is gzip inside age. The other two are tar gzip inside age:

```bash
age -d -i backup.key backups/db-<stamp>.sql.gz.age | gunzip | docker compose exec -T db psql -U hns -d hns
age -d -i backup.key backups/tls-<stamp>.tar.gz.age | docker compose exec -T agent-api tar -C /var/lib/hns-tls -xz
age -d -i backup.key backups/wireguard-<stamp>.tar.gz.age | docker compose exec server tar -C /var/lib/hns-wireguard -xz
docker compose restart
```

A copy from before encryption has no `.age` suffix. Restore that one with `gunzip` and `tar` directly.

## Security headers

Caddy sets these on every response (`deploy/Caddyfile`):

| Header | Setting |
| --- | --- |
| `Strict-Transport-Security` | `max-age=$HSTS_MAX_AGE; includeSubDomains`. Starts at 300 seconds. |
| `Content-Security-Policy` | Enforced. Scripts and API calls from this site only; Google Fonts, Esri map tiles and the Photon address search are allowed. |
| `Permissions-Policy` | Camera, microphone, geolocation, payment, USB and similar are off. |

**Raising HSTS.** Browsers remember HSTS for the whole max-age, so check first that `https://$DOMAIN` and every subdomain you use serve valid HTTPS, then set `HSTS_MAX_AGE=31536000` in `.env` and run `docker compose up -d caddy`. `includeSubDomains` makes browsers refuse plain HTTP on every subdomain too.

**If a page breaks under the CSP.** Open the browser console: a line starting "Refused to ..." names the blocked source. Add it to the policy in `deploy/Caddyfile` and run `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`. To log violations without blocking while you investigate, rename the header to `Content-Security-Policy-Report-Only`.

## Guest visit locations

Settings, Guest visits shows where each guest sign-in came from. The lookup reads a local copy of the free
[DB-IP City Lite](https://db-ip.com/db/download/ip-to-city-lite) database, so no address is sent to a third party. Install it once, then refresh it monthly:

```bash
./scripts/update-geoip.sh                      # downloads ./geoip/dbip-city-lite.mmdb (about 130 MB)
# crontab -e, on the 5th of each month:
# 0 4 5 * * cd ~/Home-Net-Watch && ./scripts/update-geoip.sh
```

The server picks up a replaced file on the next request. Without the file the page still works and shows "Unknown". The data is licensed CC BY 4.0 and credited on the page.

## Updating

```bash
cd homenetwatch
git pull
./scripts/deploy.sh
```

Use the script rather than `docker compose up --build -d` on its own. `rtnp` runs
with `network_mode: service:wireguard`, so it has no network of its own. Recreating
the tunnel destroys the namespace `rtnp` is attached to: the container still reports
**Up** while its listener is gone, and every measurement flow dies at launch with
nothing in any log. The script recreates `rtnp` whenever `wireguard` is recreated.
Named services work too: `./scripts/deploy.sh server dashboard`.

## Patching the server

Ubuntu LTS installs security updates by itself (`unattended-upgrades`). Everything else, and the
reboot that a new kernel needs, is done by hand. Plan for a few minutes of downtime: the Pi's
results simply resume afterwards.

1. **Back up first**, because Docker and the tunnel restart: `./scripts/backup.sh`.
2. **See what is waiting**, without installing anything:

   ```bash
   sudo apt-get update
   apt list --upgradable
   cat /var/run/reboot-required.pkgs 2>/dev/null   # present when a reboot is already pending
   ```

3. **Install the updates:**

   ```bash
   sudo NEEDRESTART_MODE=a DEBIAN_FRONTEND=noninteractive apt-get -y \
     -o Dpkg::Options::=--force-confold upgrade
   ```

   When `docker-ce` or `containerd.io` is in the list, Docker restarts and so do all the containers
   (about a minute offline). Containers come back on their own (`restart: unless-stopped`) and the
   database and WireGuard volumes are untouched. Never run `docker volume rm` or `docker compose down -v`.
4. **Reboot when `/var/run/reboot-required` exists:** `sudo systemctl reboot`. A new kernel is only
   used after this. Keep a way to reach the cloud console, because a kernel that fails to boot has to be
   rolled back there by choosing the previous kernel at start-up.
5. **Check afterwards:**
   - `uname -r` shows the new kernel and `/var/run/reboot-required` is gone.
   - `docker compose ps` shows every service up, with `db` and `librespeed` healthy.
   - `docker compose exec wireguard wg show` shows recent handshakes.
   - `https://<domain>/api/v1/healthz` returns 200, and the dashboard loads.
   - From the Pi, `10.7.0.1:8999`, `:8080` and `:8847` accept a connection, and new results arrive
     within a minute or two. If `8999` refuses, `rtnp` lost the tunnel's network namespace (see
     [Updating](#updating)): `docker compose up -d --force-recreate rtnp`.
   - Errors in `docker compose logs server` from the moment of the reboot are expected: the database
     stops before the server. New errors after start-up are not.

A new Ubuntu release (`do-release-upgrade`) is a larger change than any of this. Take a backup and a
cloud snapshot, and do it on its own, not as part of routine patching.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Browser can't connect | Cloud firewall and `sudo iptables -L INPUT -n` |
| Certificate error or Caddy loops | DNS A record resolves to this IP; ports 80 and 443 reachable; Cloudflare set to DNS only; `docker compose logs caddy` |
| Login works but you are signed out immediately | `COOKIE_SECURE=true` requires HTTPS; open the `https://` address, and `CORS_ORIGINS` must match it exactly |
| Dashboard loads but shows API errors | Rebuild after changing `DOMAIN`: `docker compose up --build -d dashboard` |
| Pi can't enroll | Port 8848/TCP open; Pi can reach `https://<domain>`; `WIREGUARD_ENDPOINT` is the public IP and port |
| Pi tunnel never connects | Port 51820/UDP open; `WIREGUARD_ENDPOINT` correct, then re-issue the tunnel |
| Build is killed or very slow | The machine needs more memory, or build on another machine |
| Application checks stop reporting after a deploy | `rtnp` lost the tunnel's network namespace: `docker compose up -d --force-recreate rtnp`. From the Pi, `10.7.0.1:8999` refusing a connection confirms it |
