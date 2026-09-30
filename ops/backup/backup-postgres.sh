#!/usr/bin/env bash
# Daily Postgres backup on the VM (run by painel-backup.timer, as root).
#
# Dumps roles (globals) plus the Airflow metadata and warehouse databases in
# custom format, checks that every dump can be read back, then keeps
# RETENTION_DAYS days. Runs outside Airflow so it works even if Airflow is down.
# Credentials are read from the Postgres container's own environment; nothing
# secret lives in this script.
#
# Optional /etc/painel-backup.env:
#   KUMA_PUSH_URL=https://.../api/push/<token>   # Uptime Kuma push monitor
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/painel}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
CONTAINER="${CONTAINER:-painel-postgres-1}"
BACKUP_GROUP="${BACKUP_GROUP:-painelbak}"
# Original source files (ANP), mirrored incrementally: they never change
RAW_SOURCE="${RAW_SOURCE:-/var/lib/painel/raw}"
# shellcheck disable=SC1091
[[ -f /etc/painel-backup.env ]] && source /etc/painel-backup.env

umask 027
stamp="$(date +%Y-%m-%dT%H%M)"
target="$BACKUP_DIR/$stamp"
work="$BACKUP_DIR/.partial-$stamp"
trap 'rm -rf "$work"' EXIT

install -d -m 750 -g "$BACKUP_GROUP" "$BACKUP_DIR"
mkdir -p "$work"

pg() { docker exec -i "$CONTAINER" "$@"; }

pg sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$work/globals.sql"

read -r -a databases <<< "$(pg sh -c 'echo "$POSTGRES_DB $WAREHOUSE_DB"')"
for db in "${databases[@]}"; do
  pg sh -c "pg_dump -U \"\$POSTGRES_USER\" --format=custom --dbname=$db" > "$work/$db.dump"
  # A dump that cannot be listed is useless: fail now, not on restore day.
  pg pg_restore --list < "$work/$db.dump" > /dev/null
done

(cd "$work" && sha256sum -- * > SHA256SUMS)
chgrp -R "$BACKUP_GROUP" "$work"
mv "$work" "$target"

# Retention: whole backup directories older than RETENTION_DAYS.
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$RETENTION_DAYS" \
  -exec rm -rf {} +

# Raw files: add new ones to the mirror, never delete (no --delete).
if [[ -d "$RAW_SOURCE" ]]; then
  rsync -a --exclude='.tmp/' --chmod=D750,F640 "$RAW_SOURCE/" "$BACKUP_DIR/raw/"
  chgrp -R "$BACKUP_GROUP" "$BACKUP_DIR/raw"
fi

size="$(du -sh "$target" | cut -f1)"
echo "backup ok: $target ($size; ${databases[*]} + globals)"

if [[ -n "${KUMA_PUSH_URL:-}" ]]; then
  curl -fsS -m 15 -o /dev/null "${KUMA_PUSH_URL}?status=up&msg=backup%20ok%20${size}" \
    || echo "warning: Uptime Kuma push failed" >&2
fi
