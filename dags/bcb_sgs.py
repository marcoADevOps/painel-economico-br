"""Banco Central SGS series (Selic meta, PTAX) -> raw -> staging.

Scheduled runs reprocess a short lookback window ending at the run's logical
date, so late publications and revisions are picked up and catchup fills any
gap after downtime. For historical loads, trigger manually with `start`
(and optionally `end`) in the run parameters.
"""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pendulum
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from painel.alerts import notify_task_failure
from painel.bcb import SERIES

TIMEZONE = "America/Sao_Paulo"
LOOKBACK_DAYS = 10
DW_CONN_ID = "painel_dw"
DDL_FILE = Path(__file__).resolve().parents[1] / "sql" / "bcb_sgs.sql"

log = logging.getLogger(__name__)


def _dw_connection():
    from airflow.providers.postgres.hooks.postgres import PostgresHook

    # closing(): a psycopg2 connection's own context manager does not close it
    return closing(PostgresHook(postgres_conn_id=DW_CONN_ID).get_conn())


@dag(
    dag_id="bcb_sgs",
    description="Banco Central SGS: Selic meta and PTAX into raw and staging",
    # PTAX closes around 13:00 and Copom decisions come out in the evening;
    # the lookback window covers anything published after this run.
    schedule="0 19 * * 1-5",
    start_date=pendulum.datetime(2026, 9, 1, tz=TIMEZONE),
    catchup=True,
    max_active_runs=1,
    default_args={
        "owner": "painel",
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "on_failure_callback": notify_task_failure,
    },
    params={
        "start": Param(
            None,
            type=["null", "string"],
            format="date",
            description="Historical load: first date (YYYY-MM-DD). Empty = lookback window.",
        ),
        "end": Param(
            None,
            type=["null", "string"],
            format="date",
            description="Historical load: last date (YYYY-MM-DD). Empty = today.",
        ),
    },
    tags=["bcb", "sgs", "ingestion"],
    doc_md=__doc__,
)
def bcb_sgs():
    @task
    def ensure_tables() -> None:
        from painel.warehouse import run_sql_file

        with _dw_connection() as conn:
            run_sql_file(conn, DDL_FILE)

    @task
    def resolve_window() -> dict[str, str]:
        context = get_current_context()
        params = context["params"]
        # Manual runs in Airflow 3 may have no logical_date; fall back to run_after.
        run_date = context.get("logical_date") or context["dag_run"].run_after
        run_day = pendulum.instance(run_date).in_timezone(TIMEZONE).date()

        if params.get("start"):
            # Bad parameters are a user error: fail at once (no retries) so the
            # alert arrives immediately.
            try:
                start = date.fromisoformat(params["start"])
                end = date.fromisoformat(params["end"]) if params.get("end") else run_day
            except ValueError as exc:
                raise AirflowFailException(f"invalid start/end parameter: {exc}") from exc
        else:
            end = run_day
            start = end - timedelta(days=LOOKBACK_DAYS)

        if start > end:
            raise AirflowFailException(f"start {start} is after end {end}")
        return {"start": start.isoformat(), "end": end.isoformat()}

    @task
    def extract_to_raw(series_code: int, window: dict[str, str]) -> dict:
        from painel.bcb import build_session, fetch_series_with_retry, split_window
        from painel.warehouse import raw_window_exists, save_raw

        start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
        # Windows ending before this date are settled: if already in raw, a
        # retry or re-triggered historical load skips them instead of
        # downloading everything again. Recent windows are always refreshed.
        settled_before = end - timedelta(days=LOOKBACK_DAYS)
        session = build_session()
        fetched = skipped = 0
        with _dw_connection() as conn:
            for chunk_start, chunk_end in split_window(start, end):
                if chunk_end < settled_before and raw_window_exists(
                    conn, series_code, chunk_start, chunk_end
                ):
                    skipped += 1
                    continue
                payload = fetch_series_with_retry(session, series_code, chunk_start, chunk_end)
                save_raw(conn, series_code, chunk_start, chunk_end, payload)
                fetched += 1
                log.info(
                    "series %s %s..%s: %s rows", series_code, chunk_start, chunk_end, len(payload)
                )
        log.info("series %s: %s windows fetched, %s already in raw", series_code, fetched, skipped)
        # Only a reference goes through XCom; the data stays in the raw table.
        return {"series_code": series_code, **window}

    @task
    def raw_to_staging(ref: dict) -> int:
        from painel.bcb import parse_observations
        from painel.warehouse import read_raw_payloads, upsert_staging

        series = SERIES[ref["series_code"]]
        start, end = date.fromisoformat(ref["start"]), date.fromisoformat(ref["end"])
        with _dw_connection() as conn:
            rows = [
                row
                for payload in read_raw_payloads(conn, series.code, start, end)
                for row in parse_observations(payload)
            ]
            written = upsert_staging(conn, series.code, series.name, rows)
        log.info(
            "%s: %s raw observations, %s unique dates upserted", series.name, len(rows), written
        )
        return written

    window = resolve_window()
    raw_refs = extract_to_raw.partial(window=window).expand(series_code=list(SERIES))
    ensure_tables() >> raw_refs
    raw_to_staging.expand(ref=raw_refs)


bcb_sgs()
