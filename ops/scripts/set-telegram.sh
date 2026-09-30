#!/usr/bin/env bash
# Stores the Telegram bot token and chat id in /opt/painel/.env.
# The token is read without echo and never passed on a command line.
set -euo pipefail

read -rsp "Cole o token do bot e aperte Enter: " TOKEN
echo
if [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  echo "Isso não parece um token do BotFather (formato 123456:ABC...)."
  exit 1
fi

# The token goes to curl through a config file on stdin, not argv.
RESPONSE=$(printf 'url = "https://api.telegram.org/bot%s/getUpdates"\n' "$TOKEN" | curl -s -K -)
CHAT_ID=$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data.get("ok"):
    print("ERR:" + data.get("description", "resposta inválida"))
    sys.exit()
chats = [u[k]["chat"]["id"] for u in data["result"] for k in ("message", "my_chat_member") if k in u]
print(chats[-1] if chats else "")
')

if [[ "$CHAT_ID" == ERR:* ]]; then
  echo "O Telegram recusou o token: ${CHAT_ID#ERR:}"
  exit 1
fi
if [[ -z "$CHAT_ID" ]]; then
  echo "Nenhuma mensagem encontrada. Mande uma mensagem para o bot no Telegram e rode de novo."
  exit 1
fi

sudo -u gh-runner sed -i '/^TELEGRAM_/d' /opt/painel/.env
printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TOKEN" "$CHAT_ID" | sudo -u gh-runner tee -a /opt/painel/.env >/dev/null
echo "OK: token e chat id $CHAT_ID gravados em /opt/painel/.env"
