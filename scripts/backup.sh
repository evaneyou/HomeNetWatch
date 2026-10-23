#!/usr/bin/env bash
# Back up the database and the key material the agents depend on.
#
# Writes to ./backups (override with BACKUP_DIR), keeps the newest KEEP files of each kind
# (default 14), and creates files readable only by you. Each file is encrypted to the public
# key in BACKUP_PUBKEY (default ~/.config/homenetwatch/backup.pub). The private key stays
# off this machine. Copy ./backups off the server too.
#
# Run from the repository root with the stack up:  ./scripts/backup.sh
# Daily at 03:15 with cron:  15 3 * * * cd /home/ubuntu/Home-Net-Watch && ./scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

dir="${BACKUP_DIR:-backups}"
keep="${KEEP:-14}"
pub="${BACKUP_PUBKEY:-$HOME/.config/homenetwatch/backup.pub}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dir"

if ! command -v age >/dev/null 2>&1; then
  echo "age is not installed. Install it before taking a backup." >&2
  exit 1
fi
if [ ! -f "$pub" ]; then
  echo "No backup public key at $pub." >&2
  echo "On another machine: age-keygen -o backup.key && age-keygen -y backup.key > backup.pub" >&2
  echo "Copy only backup.pub here. Keep backup.key off this machine." >&2
  exit 1
fi
if grep -q 'AGE-SECRET-KEY' "$pub"; then
  echo "Refusing to encrypt: $pub contains a private key. Put only the public key on this machine." >&2
  exit 1
fi

# Read plaintext on stdin and write an age file. A failed encrypt leaves nothing in $dir.
encrypt_to() {
  local dest="$1"
  local tmp
  tmp="$(mktemp "$dir/.partial.XXXXXX")"
  if ! age -R "$pub" -o "$tmp"; then
    rm -f "$tmp"
    exit 1
  fi
  chmod 600 "$tmp"
  mv -f "$tmp" "$dest"
}

user="$(docker compose exec -T db printenv POSTGRES_USER)"
name="$(docker compose exec -T db printenv POSTGRES_DB)"

echo "Dumping database $name..."
docker compose exec -T db pg_dump -U "$user" -d "$name" --no-owner | gzip | encrypt_to "$dir/db-$stamp.sql.gz.age"

# WireGuard keys and peers, and the agent API's TLS key and backup pin.
echo "Archiving WireGuard and TLS state..."
docker compose exec -T server tar -C /var/lib/hns-wireguard -cz . | encrypt_to "$dir/wireguard-$stamp.tar.gz.age"
docker compose exec -T agent-api tar -C /var/lib/hns-tls -cz . | encrypt_to "$dir/tls-$stamp.tar.gz.age"

# Drop everything but the newest $keep of each kind.
for kind in db wireguard tls; do
  ls -1t "$dir/$kind"-* 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f --
done

echo "Backup written to $dir/ ($stamp)."
