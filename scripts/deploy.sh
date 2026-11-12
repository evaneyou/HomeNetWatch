#!/usr/bin/env bash
# Rebuild and restart the stack, or just the services named.
#
# rtnp has no network of its own: `network_mode: service:wireguard` borrows the
# tunnel's. Recreating wireguard destroys that namespace, and rtnp keeps running
# with no listener — it still reports "Up" while every measurement flow fails to
# reach its control port on 10.7.0.1:8999. So whenever wireguard is recreated,
# rtnp is recreated after it.
#
# rtnp is recreated only then. Recreating it cuts the control connection of every running measurement flow, and
# the server then asks each agent to restart, so a deploy that left wireguard alone must leave rtnp alone too.
#
# caddy mounts deploy/Caddyfile as a single file. `git pull` (and most editors) replace that file instead of
# rewriting it, and the running container keeps reading the old copy, so neither `up` nor `caddy reload` picks
# up the change. So after every deploy the two copies are compared; when they differ, the new file is checked
# in a throwaway container and caddy is recreated (offline for a few seconds). A broken file is not applied.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -gt 0 ] && printf '%s\n' "$@" | grep -qx server && ! printf '%s\n' "$@" | grep -qx agent-api; then
  set -- "$@" agent-api
fi

wireguard_id() {
  docker compose ps -q wireguard 2>/dev/null | head -n 1
}

wireguard_before=$(wireguard_id || true)

docker compose up --build -d "$@"

wireguard_after=$(wireguard_id || true)
if [ -n "$wireguard_after" ] && [ "$wireguard_before" != "$wireguard_after" ]; then
  echo "wireguard was recreated: recreating rtnp so it rejoins the tunnel"
  docker compose up -d --force-recreate rtnp
fi

refresh_caddy() {
  docker compose ps --status running -q caddy 2>/dev/null | grep -q . || return 0
  local want have out
  want=$(sha256sum deploy/Caddyfile | cut -d' ' -f1)
  have=$(docker compose exec -T caddy sha256sum /etc/caddy/Caddyfile 2>/dev/null | cut -d' ' -f1) || have=""
  [ "$want" = "$have" ] && return 0
  echo "deploy/Caddyfile changed: checking it, then recreating caddy"
  if ! out=$(docker compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile 2>&1); then
    printf '%s\n' "$out" | tail -5 >&2
    echo "deploy/Caddyfile is not valid; caddy keeps running the previous version" >&2
    return 1
  fi
  docker compose up -d --force-recreate --no-deps caddy
}
refresh_caddy

docker compose ps
