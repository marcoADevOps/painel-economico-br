#!/usr/bin/env bash
# Stores the Metabase API key used by ops/metabase/provision.py.
# The key is read without echo and checked against the Metabase API.
set -euo pipefail
read -rsp "Cole a API key do Metabase e aperte Enter: " KEY
echo
if [[ ! "$KEY" =~ ^mb_[A-Za-z0-9+/=_-]+$ ]]; then
  echo "Isso não parece uma API key do Metabase (começa com mb_)."
  exit 1
fi
status=$(printf 'header = "x-api-key: %s"\n' "$KEY" | curl -s -o /dev/null -w "%{http_code}" -K - http://localhost:3000/api/user/current)
if [[ "$status" != "200" ]]; then
  echo "O Metabase recusou a chave (HTTP $status)."
  exit 1
fi
printf 'MB_API_KEY=%s\n' "$KEY" | sudo tee /etc/painel-metabase.env >/dev/null
sudo chmod 600 /etc/painel-metabase.env
echo "OK: chave válida e gravada em /etc/painel-metabase.env"
