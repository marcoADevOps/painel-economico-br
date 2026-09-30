from datetime import date
from decimal import Decimal

import pytest

from painel.bcb import (
    BcbApiError,
    fetch_series,
    fetch_series_with_retry,
    parse_observations,
    split_window,
)
from painel.warehouse import dedupe_by_date


class FakeResponse:
    def __init__(self, status_code, body, content_type="application/json"):
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type}

    def json(self):
        import json

        return json.loads(self.text)


class FakeSession:
    """Returns the given responses in order (the last one repeats)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


HTML_ERROR = FakeResponse(200, "<!DOCTYPE html><html>Bad request</html>", "text/html")


# --- split_window -----------------------------------------------------------


def test_split_window_single_chunk():
    assert split_window(date(2026, 9, 1), date(2026, 9, 30)) == [
        (date(2026, 9, 1), date(2026, 9, 30))
    ]


def test_split_window_covers_range_without_gaps_or_overlap():
    start, end = date(2016, 1, 1), date(2026, 9, 30)
    windows = split_window(start, end, max_days=366)

    assert windows[0][0] == start
    assert windows[-1][1] == end
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert (next_start - prev_end).days == 1
    assert all((w_end - w_start).days < 366 for w_start, w_end in windows)


def test_split_window_same_day():
    assert split_window(date(2026, 9, 30), date(2026, 9, 30)) == [
        (date(2026, 9, 30), date(2026, 9, 30))
    ]


def test_split_window_rejects_inverted_range():
    with pytest.raises(ValueError):
        split_window(date(2026, 9, 30), date(2026, 9, 1))


# --- fetch_series -----------------------------------------------------------


def test_fetch_series_sends_brazilian_dates():
    session = FakeSession(FakeResponse(200, '[{"data": "01/09/2026", "valor": "5.1570"}]'))

    payload = fetch_series(session, 1, date(2026, 9, 1), date(2026, 9, 29))

    assert payload == [{"data": "01/09/2026", "valor": "5.1570"}]
    url, params = session.calls[0]
    assert url.endswith("bcdata.sgs.1/dados")
    assert params == {"formato": "json", "dataInicial": "01/09/2026", "dataFinal": "29/09/2026"}


def test_fetch_series_not_found_means_no_data():
    body = '{"erro":{"statusCode":404,"detail":"SGSNegocioException: Value(s) not found"}}'
    session = FakeSession(FakeResponse(404, body))

    assert fetch_series(session, 1, date(2026, 9, 27), date(2026, 9, 27)) == []


def test_fetch_series_html_with_200_is_an_error():
    session = FakeSession(HTML_ERROR)

    with pytest.raises(BcbApiError, match="not JSON"):
        fetch_series(session, 432, date(2026, 9, 1), date(2026, 9, 29))


def test_fetch_series_window_too_large_is_an_error():
    session = FakeSession(FakeResponse(406, '{"error":"janela de no máximo 10 anos"}'))

    with pytest.raises(BcbApiError, match="HTTP 406"):
        fetch_series(session, 1, date(2010, 1, 1), date(2026, 9, 29))


# --- fetch_series_with_retry ------------------------------------------------


def test_retry_recovers_from_transient_html_page():
    ok = FakeResponse(200, '[{"data": "01/09/2026", "valor": "15.00"}]')
    session = FakeSession(HTML_ERROR, HTML_ERROR, ok)
    waits = []

    payload = fetch_series_with_retry(
        session, 432, date(2026, 9, 1), date(2026, 9, 1), wait_seconds=10, sleep=waits.append
    )

    assert payload == [{"data": "01/09/2026", "valor": "15.00"}]
    assert len(session.calls) == 3
    assert waits == [10, 20]


def test_retry_gives_up_after_all_attempts():
    session = FakeSession(HTML_ERROR)

    with pytest.raises(BcbApiError, match="not JSON"):
        fetch_series_with_retry(
            session, 432, date(2026, 9, 1), date(2026, 9, 1), attempts=3, sleep=lambda _: None
        )
    assert len(session.calls) == 3


def test_retry_does_not_repeat_client_errors():
    session = FakeSession(FakeResponse(406, '{"error":"janela de no máximo 10 anos"}'))

    with pytest.raises(BcbApiError, match="HTTP 406"):
        fetch_series_with_retry(
            session, 1, date(2010, 1, 1), date(2026, 9, 29), sleep=lambda _: None
        )
    assert len(session.calls) == 1


# --- parse_observations -----------------------------------------------------


def test_parse_observations_types_values():
    rows = parse_observations(
        [{"data": "29/09/2026", "valor": "5.2198"}, {"data": "30/09/2026", "valor": "15.00"}]
    )
    assert rows == [(date(2026, 9, 29), Decimal("5.2198")), (date(2026, 9, 30), Decimal("15.00"))]


@pytest.mark.parametrize(
    "item",
    [
        {"data": "2026-09-29", "valor": "5.2"},
        {"data": "29/09/2026", "valor": ""},
        {"data": "29/09/2026", "valor": "NaN"},
        {"valor": "5.2"},
    ],
)
def test_parse_observations_rejects_invalid(item):
    with pytest.raises(BcbApiError):
        parse_observations([item])


# --- dedupe_by_date ---------------------------------------------------------


def test_dedupe_keeps_last_value_per_date():
    rows = [
        (date(2026, 9, 29), Decimal("1")),
        (date(2026, 9, 30), Decimal("2")),
        (date(2026, 9, 29), Decimal("3")),
    ]
    assert dedupe_by_date(rows) == [
        (date(2026, 9, 29), Decimal("3")),
        (date(2026, 9, 30), Decimal("2")),
    ]
