# Security Policy

## Supported versions

This project is pre-1.0. Security fixes are made on the latest release and on `main`.

| Version | Supported |
|---------|-----------|
| Latest release / `main` | Yes |
| Older releases | No |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub:
[Report a vulnerability](https://github.com/evaneyou/homenetwatch/security/advisories/new).

Include what you found, the version or commit, steps to reproduce, and the impact you expect.
You can expect an acknowledgement within a few days. Fixes are released first and disclosed after,
and reporters are credited in the release notes unless they ask not to be.

## Hardening a deployment

The defaults in `.env.example` and `docker-compose.yml` are for local development. Before you
expose any part of this stack beyond your own machine:

- Set a long random `SECRET_KEY`. If it may have leaked, rotate it with `SECRET_KEY_PREVIOUS`
  ([`docs/deploy.md`](./docs/deploy.md#rotating-secret_key)).
- Change `BOOTSTRAP_ADMIN_PASSWORD` and `BOOTSTRAP_VIEWER_PASSWORD`, then re-run the seed. The guest
  account (`BOOTSTRAP_GUEST_PASSWORD`) is a read-only public demo; set a private password if you do not want one.
- Change `POSTGRES_PASSWORD` and do not publish the Postgres port (`55432`).
- Serve the dashboard and API over HTTPS and set `COOKIE_SECURE=true`.
- Set `CORS_ORIGINS` to the exact origin of your dashboard.
- Keep a copy of the agent API's `backup-key.pem` off the server (see [`server/README.md`](./server/README.md)).
- Restrict the WireGuard UDP port (`51820`) and the agent API port (`8848`) to the networks that need them.
  The agent API port serves only enrollment (`AGENT_API_ONLY=1`); keep that set.
- The API containers mount nothing from the host. Server Health reads the machine through the `hoststat`
  container (no network, read-only), which copies only counters, process names, the OS package list and disk
  use into the `hostinfo` volume. Don't add host mounts such as `/` or `/proc` to `server` or `agent-api`:
  read as root, `/proc` alone reaches every file on the machine.
- `server` and `agent-api` run as the image's `hns` user (uid 10001, no host account), with no Linux
  capabilities. The one-shot `state-init` service hands them the WireGuard, RTNP and TLS volumes on every start;
  the WireGuard private key and `wg0.conf` stay root-only, readable by the hub alone. Don't add `user: root` or
  capabilities back to the API services.
- If a session cookie may have been copied, run `docker compose exec server python -m app.sessions revoke <username>`
  (or `--all`). Signing out or setting a new password does the same for that account.
- Sign-ins (with the address), failed sign-ins, sign-in blocks and changes to the alert, Gemini and test settings
  are kept in `audit_events` for the log retention period. Read them with
  `docker compose exec db psql -U hns -d hns -c "select created_at, username, action, detail from audit_events order by created_at desc limit 50"`.
  With Telegram alerts set up, an admin sign-in from an address not seen before also sends a message.
- If Caddy is not the container named `caddy`, set `TRUSTED_PROXIES` to its name or address, or the sign-in
  limit counts every visitor as the proxy's address.
- Install `deploy/rtnp-allowlist.timer` so the public RTNP port (`8998/udp`) accepts only current WireGuard
  peers (see [`docs/deploy.md`](./docs/deploy.md#allow-udp-8998-only-from-your-agents)).

For a server deployment follow [`docs/deploy.md`](./docs/deploy.md); it uses the
production overlay, which closes the database, API and dashboard ports and serves HTTPS.

The demo seed creates sample data and well-known bootstrap accounts. Do not run it against a
production database. On a server use `python -m app.seed --users-only`.
