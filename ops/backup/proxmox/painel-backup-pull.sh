#!/usr/bin/env bash
# Runs on the Proxmox host: pulls the VM's Postgres backups to local storage.
#
# The VM side only allows read-only rsync of the backup directory for this key
# (authorized_keys forces `rrsync -ro`), and the VM never holds credentials
# for the host. No --delete: wiping the VM's backups never wipes these copies;
# the host applies its own, longer retention.
set -euo pipefail

VM_HOST="${VM_HOST:?set VM_HOST in /etc/painel-backup-pull.env}"
SSH_KEY="${SSH_KEY:-/root/.ssh/painel_backup_pull}"
DEST="${DEST:-/var/backups/painel-vm}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

umask 077
install -d -m 700 "$DEST"

rsync -a --exclude='.partial-*' \
  -e "ssh -i $SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes" \
  "painelbak@${VM_HOST}:./" "$DEST/"

# Every pulled backup must match its checksums.
for dir in "$DEST"/20*/; do
  (cd "$dir" && sha256sum --quiet -c SHA256SUMS) || { echo "checksum mismatch in $dir" >&2; exit 1; }
done

find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$RETENTION_DAYS" \
  -exec rm -rf {} +

latest="$(ls -1d "$DEST"/20*/ 2>/dev/null | tail -1)"
echo "pull ok: $(ls -1d "$DEST"/20*/ | wc -l) backups on host, latest ${latest:-none}"
