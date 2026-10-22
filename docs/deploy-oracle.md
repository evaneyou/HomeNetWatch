# Deploy on Oracle Cloud Always Free

This guide runs the whole stack on one Ampere A1 (ARM) virtual machine in Oracle Cloud's Always
Free tier, with automatic HTTPS. It also works on any other small Linux VPS; skip the Oracle
console steps and follow the rest.

Allow about 45 minutes. You need an Oracle Cloud account, a domain name you can add DNS records
to, and an SSH key pair.

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

## 1. Create the VM

1. Sign up at <https://www.oracle.com/cloud/free/>. Your **home region** is permanent, so pick
   the one closest to you.
2. In the console go to **Compute → Instances → Create instance**.
3. **Image:** Canonical Ubuntu 24.04 (the aarch64 build).
4. **Shape:** change shape to **Ampere → VM.Standard.A1.Flex**. Use **2 OCPU and 12 GB** (or
   1 OCPU and 6 GB). The Always Free allowance is 4 OCPU and 24 GB across all A1 instances, so
   stay inside it.
5. **Networking:** keep the default VCN and make sure **Assign a public IPv4 address** is on.
6. **SSH keys:** upload your public key.
7. **Boot volume:** 50 GB is plenty (the free allowance is 200 GB total).
8. Create the instance. If you get "Out of host capacity", try another availability domain or
   retry later; A1 capacity varies.

Note the public IP address. If the console offers a reserved public IP within your free limits,
use it so the address survives a rebuild.

## 2. Open the network ports

In the console open your instance's subnet and edit its **Security List** (Networking → Virtual
cloud networks → your VCN → Security Lists → Default). Add **ingress rules** with source
`0.0.0.0/0`:

| Protocol | Destination port |
|----------|------------------|
| TCP | 80 |
| TCP | 443 |
| UDP | 443 |
| TCP | 8848 |
| UDP | 51820 |

Docker publishes container ports through its own firewall rules, so these usually need no extra
host configuration. If a port is still unreachable from outside after you add the rule, Oracle's
Ubuntu image may be rejecting it on the host. Check the rules and insert yours above the final
`REJECT` line:

```bash
sudo iptables -L INPUT -n --line-numbers
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT      # repeat for each port, using -p udp where needed
sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save
```

## 3. Point your domain at the server

Create an **A record** for the name you want (for example `hns.example.com`) pointing at the
public IP. If you use Cloudflare, set it to **DNS only** (grey cloud), because Caddy needs to
reach Let's Encrypt directly and the agent ports can't go through the proxy.

Wait until `dig +short hns.example.com` returns your IP.

## 4. Install Docker

```bash
ssh ubuntu@<public-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
exit            # log back in so the group applies
docker compose version      # must be 2.24 or newer
```

## 5. Get the code and configure it

```bash
git clone https://github.com/evaneyou/Home-Sensor.git
cd Home-Net-Watch
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

Because `COMPOSE_FILE` is set, plain `docker compose` commands in this directory use the
production stack. Lock the file down: `chmod 600 .env`.

## 6. Start it

```bash
docker compose up --build -d
docker compose exec server python -m app.seed --users-only
```

The first build compiles the dashboard and takes a few minutes. Migrations run automatically on
start. `--users-only` creates the admin and viewer accounts **without demo data**, and refuses to
run with the default passwords. Do not run the plain seed on this server.

Check it:

```bash
docker compose ps
curl https://hns.example.com/api/v1/healthz
```

Then open `https://hns.example.com` and sign in as `admin`. Caddy needs a minute to get its
certificate on first start; `docker compose logs caddy` shows progress.

## 7. Connect a Raspberry Pi

1. In the dashboard open **Settings → Remote Agent Onboarding** and add the device.
2. Install the agent on the Pi as described in [`monitor/README.md`](../monitor/README.md).
3. On the Pi's setup page enter the server address `https://hns.example.com`, the device name
   and key.
4. When you issue tunnels, the Pi connects to `<public-ip>:51820`. After the tunnel is up the
   agent uses the tunnel address (`http://10.7.0.1:8847`) for its tests.

Print the agent API's TLS key pins and keep them:

```bash
docker compose exec agent-api python -m app.tls pins
```

## 8. Back up

```bash
./scripts/backup.sh
```

This writes a database dump plus the WireGuard and TLS state to `backups/`, keeping the newest
14 of each. Server Health shows the time and size of those copies, and does not serve the files.
Run it daily from cron and copy `backups/` somewhere else (your PC, object storage):

```bash
crontab -e
# 15 3 * * * cd /home/ubuntu/Home-Net-Watch && ./scripts/backup.sh
```

The TLS and WireGuard archives matter as much as the database: they hold the keys your Pis
trust. Without them every Pi has to be re-enrolled.

**Restore** on a fresh server after steps 1 to 6:

```bash
gunzip -c backups/db-<stamp>.sql.gz | docker compose exec -T db psql -U hns -d hns
docker compose cp backups/tls-<stamp>.tar.gz agent-api:/tmp/tls.tar.gz
docker compose exec agent-api tar -C /var/lib/hns-tls -xzf /tmp/tls.tar.gz
docker compose cp backups/wireguard-<stamp>.tar.gz server:/tmp/wg.tar.gz
docker compose exec server tar -C /var/lib/hns-wireguard -xzf /tmp/wg.tar.gz
docker compose restart
```

## 9. Keep the instance from being reclaimed

Oracle may reclaim an Always Free instance that stays idle for 7 days. The definition in
[Oracle's documentation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm) is
CPU (95th percentile) under 20%, network under 20%, and, on A1 shapes, memory under 20%.

- A smaller A1 shape raises your utilization percentages. Check **Observability → Metrics** for
  your instance after a few days to see where you sit.
- Upgrading the account to **Pay As You Go** is widely reported to prevent reclamation, but the
  documentation does not promise it. Set a $1 budget alert if you do, and only create Always Free
  eligible resources.
- Community tools such as NeverIdle and LookBusy generate light load as a last resort.
- Keep the backups from step 8 current either way.

## Updating

```bash
cd Home-Net-Watch
git pull
docker compose up --build -d
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Browser can't connect | Security list rules (step 2), then `sudo iptables -L INPUT -n` |
| Certificate error or Caddy loops | DNS A record resolves to this IP; ports 80 and 443 reachable; Cloudflare set to DNS only; `docker compose logs caddy` |
| Login works but you are signed out immediately | `COOKIE_SECURE=true` requires HTTPS; open the `https://` address, and `CORS_ORIGINS` must match it exactly |
| Dashboard loads but shows API errors | Rebuild after changing `DOMAIN`: `docker compose up --build -d dashboard` |
| Pi can't enroll | Port 8848/TCP open; Pi can reach `https://<domain>`; `WIREGUARD_ENDPOINT` is the public IP and port |
| Pi tunnel never connects | Port 51820/UDP open in the security list; `WIREGUARD_ENDPOINT` correct, then re-issue the tunnel |
| Build is killed or very slow | Not enough memory; use at least 2 OCPU and 12 GB, or build on another machine |
