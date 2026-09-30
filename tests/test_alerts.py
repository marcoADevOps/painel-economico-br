from types import SimpleNamespace

import requests

from painel import alerts


class FakeHttp:
    def __init__(self, status_code=200, exc=None):
        self.status_code = status_code
        self.exc = exc
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return SimpleNamespace(status_code=self.status_code, text="{}")


def test_message_has_dag_task_and_log_link_escaped():
    text = alerts.format_failure_message(
        dag_id="bcb_sgs",
        task_id="extract_to_raw[0]",
        run_id="scheduled__2026-09-30",
        try_number=4,
        log_url="http://host:8080/dags/bcb_sgs/runs/x?try_number=4&a=b",
        error="BcbApiError: <html> not JSON",
    )
    assert "<code>bcb_sgs</code>" in text
    assert "<code>extract_to_raw[0]</code>" in text
    assert "&lt;html&gt;" in text
    assert 'href="http://host:8080/dags/bcb_sgs/runs/x?try_number=4&amp;a=b"' in text


def test_long_errors_are_truncated():
    text = alerts.format_failure_message("d", "t", "r", 1, "http://x", "e" * 2000)
    assert "e" * alerts.MAX_ERROR_CHARS + "…" in text
    assert "e" * (alerts.MAX_ERROR_CHARS + 1) not in text


def test_send_skips_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    http = FakeHttp()

    assert alerts.send_telegram("hi", session=http) is False
    assert http.calls == []


def test_send_posts_to_bot_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    http = FakeHttp()

    assert alerts.send_telegram("hi", session=http) is True
    url, body = http.calls[0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert body["chat_id"] == "42" and body["parse_mode"] == "HTML"


def test_send_never_raises_and_never_logs_token(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    exc = requests.ConnectionError("https://api.telegram.org/bot123:secret-token/sendMessage")

    assert alerts.send_telegram("hi", session=FakeHttp(exc=exc)) is False
    assert "secret-token" not in caplog.text


def test_callback_marks_mapped_task_index(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_telegram", sent.append)
    ti = SimpleNamespace(
        dag_id="bcb_sgs", task_id="extract_to_raw", map_index=0, run_id="r", try_number=4,
        log_url="http://x",
    )

    alerts.notify_task_failure({"ti": ti, "exception": ValueError("boom")})

    assert "extract_to_raw[0]" in sent[0]
    assert "ValueError: boom" in sent[0]
