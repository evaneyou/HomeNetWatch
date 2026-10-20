#!/usr/bin/env bash
# Back up the database and the key material the agents depend on.
#
# Writes to ./backups (override with BACKUP_DIR), keeps the newest KEEP files of each kind
# (default 14), and creates files readable only by you. Copy ./backups off the server too.
#
# Run from the repository root with the stack up:  ./scripts/backup.sh
# Daily at 03:15 with cron:  15 3 * * * cd /home/ubuntu/Home-Net-Watch && ./scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

dir="${BACKUP_DIR:-backups}"
keep="${KEEP:-14}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dir"

user="$(docker compose exec -T db printenv POSTGRES_USER)"
name="$(docker compose exec -T db printenv POSTGRES_DB)"

echo "Dumping database $name..."
docker compose exec -T db pg_dump -U "$user" -d "$name" --no-owner | gzip > "$dir/db-$stamp.sql.gz"

# WireGuard keys and peers, and the agent API's TLS key and backup pin.
echo "Archiving WireGuard and TLS state..."
docker compose exec -T server tar -C /var/lib/hns-wireguard -cz . > "$dir/wireguard-$stamp.tar.gz"
docker compose exec -T agent-api tar -C /var/lib/hns-tls -cz . > "$dir/tls-$stamp.tar.gz"

# Drop everything but the newest $keep of each kind.
for kind in db wireguard tls; do
  ls -1t "$dir/$kind"-* 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f --
done

echo "Backup written to $dir/ ($stamp)."
