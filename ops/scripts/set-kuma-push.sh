#!/usr/bin/env bash
# Stores the Uptime Kuma push URL used by the daily backup, then runs a backup
# so the monitor turns green right away.
set -euo pipefail
read -rp "Cole a Push URL do monitor 'Backup Postgres' e aperte Enter: " URL
URL="${URL%%\?*}"   # drop ?status=up&msg=OK&ping= if it was copied along
if [[ ! "$URL" =~ ^https?://[^/]+/api/push/[A-Za-z0-9]+$ ]]; then
  echo "Isso não parece uma Push URL do Uptime Kuma (http://<host>:3001/api/push/<token>)."
  exit 1
fi
printf 'KUMA_PUSH_URL=%s\n' "$URL" | sudo tee /etc/painel-backup.env >/dev/null
sudo chmod 600 /etc/painel-backup.env
echo "URL gravada. Rodando um backup de teste..."
sudo systemctl start painel-backup.service
sudo journalctl -u painel-backup.service -n 2 --no-pager -o cat
