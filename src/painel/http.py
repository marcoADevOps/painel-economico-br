"""HTTP helpers shared by the source extractors."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "painel-economico-br (+https://github.com/marcoADevOps/painel-economico-br)"
REQUEST_TIMEOUT = (10, 60)  # connect, read (seconds)

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """A source API answered with something that is not valid data.

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


def call_with_retry[T](
    fn: Callable[[], T],
    attempts: int = 3,
    wait_seconds: float = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn, repeating it on retryable ApiError with a growing wait.

    Retrying one request here keeps a single bad response from failing (and
    restarting) a task that has already done other work.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ApiError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            delay = wait_seconds * attempt
            log.warning("attempt %s/%s failed (%s); retrying in %ss", attempt, attempts, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")
