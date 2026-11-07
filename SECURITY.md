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

- Set a long random `SECRET_KEY`.
- Change `BOOTSTRAP_ADMIN_PASSWORD` and `BOOTSTRAP_VIEWER_PASSWORD`, then re-run the seed. The guest
  account (`BOOTSTRAP_GUEST_PASSWORD`) is a read-only public demo; set a private password if you do not want one.
- Change `POSTGRES_PASSWORD` and do not publish the Postgres port (`55432`).
- Serve the dashboard and API over HTTPS and set `COOKIE_SECURE=true`.
- Set `CORS_ORIGINS` to the exact origin of your dashboard.
- Keep a copy of the agent API's `backup-key.pem` off the server (see [`server/README.md`](./server/README.md)).
- Restrict the WireGuard UDP port (`51820`) and the agent API port (`8848`) to the networks that need them.
  The agent API port serves only enrollment (`AGENT_API_ONLY=1`); keep that set.
- If Caddy is not the container named `caddy`, set `TRUSTED_PROXIES` to its name or address, or the sign-in
  limit counts every visitor as the proxy's address.
- Install `deploy/rtnp-allowlist.timer` so the public RTNP port (`8998/udp`) accepts only current WireGuard
  peers (see [`docs/deploy.md`](./docs/deploy.md#allow-udp-8998-only-from-your-agents)).

For a server deployment follow [`docs/deploy.md`](./docs/deploy.md); it uses the
production overlay, which closes the database, API and dashboard ports and serves HTTPS.

The demo seed creates sample data and well-known bootstrap accounts. Do not run it against a
production database. On a server use `python -m app.seed --users-only`.
