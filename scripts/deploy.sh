#!/usr/bin/env bash
# Rebuild and restart the stack, or just the services named.
#
# rtnp has no network of its own: `network_mode: service:wireguard` borrows the
# tunnel's. Recreating wireguard destroys that namespace, and rtnp keeps running
# with no listener — it still reports "Up" while every measurement flow fails to
# reach its control port on 10.7.0.1:8999. So whenever wireguard is recreated,
# rtnp is recreated after it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -gt 0 ] && printf '%s\n' "$@" | grep -qx server && ! printf '%s\n' "$@" | grep -qx agent-api; then
  set -- "$@" agent-api
fi

docker compose up --build -d "$@"

if [ "$#" -eq 0 ] || printf '%s\n' "$@" | grep -qx wireguard; then
  echo "wireguard was recreated: recreating rtnp so it rejoins the tunnel"
  docker compose up -d --force-recreate rtnp
fi

docker compose ps
