"""Failure alerts to Telegram.

Credentials come from the environment (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID),
set in the VM-only .env file. Without them, alerts are logged and skipped so a
missing configuration never breaks a DAG.
"""

from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_ERROR_CHARS = 500


def format_failure_message(
    dag_id: str, task_id: str, run_id: str, try_number: int | None, log_url: str, error: str | None
) -> str:
    """Telegram HTML message with DAG, task and a link to the log."""
    lines = [
        "🔴 <b>Falha no Airflow</b>",
        f"<b>DAG:</b> <code>{html.escape(dag_id)}</code>",
        f"<b>Task:</b> <code>{html.escape(task_id)}</code>",
        f"<b>Run:</b> <code>{html.escape(run_id)}</code>",
    ]
    if try_number:
        lines.append(f"<b>Tentativa:</b> {try_number}")
    if error:
        short = error if len(error) <= MAX_ERROR_CHARS else error[:MAX_ERROR_CHARS] + "…"
        lines.append(f"<b>Erro:</b> <code>{html.escape(short)}</code>")
    lines.append(f'<a href="{html.escape(log_url, quote=True)}">Abrir log</a>')
    return "\n".join(lines)


def send_telegram(text: str, session: requests.Session | None = None) -> bool:
    """Send a message; returns False (and logs) instead of raising on any problem."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram alert skipped: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return False

    http = session or requests
    try:
        response = http.post(
            TELEGRAM_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        # The exception text may contain the URL (and so the token): log the type only.
        log.error("Telegram alert failed: %s", type(exc).__name__)
        return False
    if response.status_code != 200:
        log.error("Telegram alert failed: HTTP %s %s", response.status_code, response.text[:200])
        return False
    return True


def notify_task_failure(context) -> None:
    """on_failure_callback: alert once the task has failed for good (no retries left)."""
    ti = context["ti"]
    mapped = ti.map_index is not None and ti.map_index >= 0
    error = context.get("exception")
    message = format_failure_message(
        dag_id=ti.dag_id,
        task_id=f"{ti.task_id}[{ti.map_index}]" if mapped else ti.task_id,
        run_id=ti.run_id,
        try_number=ti.try_number,
        log_url=ti.log_url,
        error=f"{type(error).__name__}: {error}" if error else None,
    )
    send_telegram(message)
