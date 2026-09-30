"""Banco Central do Brasil SGS API: extraction and parsing.

Endpoint: https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados
Checked against the official catalog (dadosabertos.bcb.gov.br) on 2026-09-30:
- Daily series require dataInicial/dataFinal and accept at most a 10-year window
  (HTTP 406 otherwise, including queries without dates).
- A window with no observations (weekend, holiday) returns HTTP 404
  "Value(s) not found".
- The gateway sometimes answers HTTP 200 with an HTML error page after ~30 s,
  so the body must be validated, not just the status code.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
USER_AGENT = "painel-economico-br (+https://github.com/marcoADevOps/painel-economico-br)"
REQUEST_TIMEOUT = (10, 60)  # connect, read (seconds)

# The API allows 10 years; smaller windows are faster and fail less.
MAX_WINDOW_DAYS = 366

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Series:
    code: int
    name: str
    description: str


SERIES: dict[int, Series] = {
    432: Series(432, "selic_meta", "Taxa de juros - Meta Selic definida pelo Copom (% a.a.)"),
    1: Series(1, "ptax_venda", "Taxa de câmbio - Livre - Dólar americano (venda) - PTAX"),
}


class BcbApiError(RuntimeError):
    """The API answered with something that is not valid series data.

    retryable: the failure looks transient (gateway HTML page, 5xx, 429) and the
    same request may succeed if repeated.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def build_session() -> requests.Session:
    """HTTP session with retries on connection errors, 429 and 5xx."""
    retry = Retry(
        total=3,
        backoff_factor=5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    return session


def split_window(
    start: date, end: date, max_days: int = MAX_WINDOW_DAYS
) -> list[tuple[date, date]]:
    """Split [start, end] (inclusive) into consecutive windows of at most max_days."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    windows = []
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=max_days - 1), end)
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def fetch_series(
    session: requests.Session, code: int, start: date, end: date
) -> list[dict[str, str]]:
    """Return the raw observations of one series in [start, end] (at most 10 years).

    An empty list means the API has no values for the window (HTTP 404).
    """
    params = {
        "formato": "json",
        "dataInicial": start.strftime("%d/%m/%Y"),
        "dataFinal": end.strftime("%d/%m/%Y"),
    }
    response = session.get(BASE_URL.format(code=code), params=params, timeout=REQUEST_TIMEOUT)

    if response.status_code == 404 and "not found" in response.text.lower():
        return []
    if response.status_code != 200:
        raise BcbApiError(
            f"series {code} {start}..{end}: HTTP {response.status_code}: {response.text[:200]}",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise BcbApiError(
            f"series {code} {start}..{end}: response is not JSON "
            f"(content-type {response.headers.get('Content-Type')!r})",
            retryable=True,
        ) from exc

    if not isinstance(payload, list):
        raise BcbApiError(f"series {code} {start}..{end}: unexpected payload {str(payload)[:200]}")
    return payload


def fetch_series_with_retry(
    session: requests.Session,
    code: int,
    start: date,
    end: date,
    attempts: int = 3,
    wait_seconds: float = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, str]]:
    """fetch_series, repeating only this window on transient failures.

    Retrying here keeps one bad response from failing (and restarting) a task
    that has already downloaded many other windows.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fetch_series(session, code, start, end)
        except BcbApiError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            delay = wait_seconds * attempt
            log.warning("attempt %s/%s failed (%s); retrying in %ss", attempt, attempts, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")


def parse_observations(payload: list[dict[str, str]]) -> list[tuple[date, Decimal]]:
    """Convert [{"data": "dd/mm/yyyy", "valor": "1.23"}, ...] into typed rows."""
    rows = []
    for item in payload:
        try:
            ref_date = datetime.strptime(item["data"], "%d/%m/%Y").date()
            value = Decimal(item["valor"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BcbApiError(f"invalid observation {item!r}") from exc
        if not value.is_finite():
            raise BcbApiError(f"invalid value in observation {item!r}")
        rows.append((ref_date, value))
    return rows
