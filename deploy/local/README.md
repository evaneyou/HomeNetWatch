# Server-only Caddy sites

Files here named `*.caddy` are loaded by `deploy/Caddyfile` (`import /etc/caddy/local/*.caddy`) and
are ignored by git, so a site that is not part of HomeNetWatch can run behind the same Caddy without
appearing in this public repository. Only this README is tracked.

One site block per file, for example `deploy/local/example.caddy`:

```caddy
app.example.com {
	encode zstd gzip
	reverse_proxy my-app:8000
}
```

The service it proxies to goes in an untracked `docker-compose.local.yml` at the repository root,
added to `COMPOSE_FILE` in `.env`. See [Server-only sites and services](../../docs/deploy.md#server-only-sites-and-services).

After adding or changing a file, check and reload:

```bash
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```
