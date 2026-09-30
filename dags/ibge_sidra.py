"""IBGE SIDRA (IPCA by region, unemployment) -> raw -> staging.

Scheduled runs reprocess the last ~13 months (IPCA is monthly, PNAD
quarterly), so late releases and revisions are picked up and catchup fills
any gap after downtime. For historical loads (data starts in 2012), trigger
manually with `start` (and optionally `end`) in the run parameters.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

from painel.dag_support import (
    DEFAULT_ARGS,
    STAGING_IBGE,
    TIMEZONE,
    WINDOW_PARAMS,
    dw_connection,
    run_window,
)
from painel.ibge import DATASETS

LOOKBACK_DAYS = 400
DDL_FILE = Path(__file__).resolve().parents[1] / "sql" / "ibge_sidra.sql"

log = logging.getLogger(__name__)


@dag(
    dag_id="ibge_sidra",
    description="IBGE SIDRA: IPCA by region and unemployment rate into raw and staging",
    # IBGE releases at 09:00; runs are cheap (one request per table), so a
    # weekday schedule picks up each release on the same morning.
    schedule="0 10 * * 1-5",
    start_date=pendulum.datetime(2026, 9, 1, tz=TIMEZONE),
    catchup=True,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    params=WINDOW_PARAMS,
    tags=["ibge", "sidra", "ingestion"],
    doc_md=__doc__,
)
def ibge_sidra():
    @task
    def ensure_tables() -> None:
        from painel.warehouse import run_sql_file

        with dw_connection() as conn:
            run_sql_file(conn, DDL_FILE)

    @task
    def resolve_window() -> dict[str, str]:
        return run_window(LOOKBACK_DAYS)

    @task
    def extract_to_raw(dataset_name: str, window: dict[str, str]) -> dict:
        from painel.http import build_session
        from painel.ibge import fetch_table_with_retry, plan_requests
        from painel.warehouse import save_ibge_raw

        dataset = DATASETS[dataset_name]
        start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
        requests_plan = plan_requests(dataset, start, end)
        session = build_session()
        with dw_connection() as conn:
            for table_id, first, last in requests_plan:
                payload = fetch_table_with_retry(session, dataset, table_id, first, last)
                save_ibge_raw(conn, dataset.name, table_id, first, last, payload)
                log.info("%s table %s %s-%s: %s variables", dataset.name, table_id, first, last,
                         len(payload))
        # Only references go through XCom; the data stays in the raw table.
        return {"dataset": dataset.name, "requests": requests_plan}

    @task
    def raw_to_staging(ref: dict) -> int:
        from painel.ibge import parse_sidra
        from painel.warehouse import read_ibge_raw, upsert_ibge_staging

        dataset = DATASETS[ref["dataset"]]
        written = 0
        with dw_connection() as conn:
            for table_id, first, last in ref["requests"]:
                payload = read_ibge_raw(conn, dataset.name, table_id, first, last)
                if payload is None:
                    raise RuntimeError(f"raw row missing for {dataset.name} {table_id} {first}")
                observations, no_value = parse_sidra(payload, dataset.periodicity)
                count = upsert_ibge_staging(conn, dataset.name, table_id, observations)
                written += count
                log.info(
                    "%s table %s %s-%s: %s observations upserted, %s without value",
                    dataset.name, table_id, first, last, count, no_value,
                )
        return written

    @task(outlets=[STAGING_IBGE])
    def publish_staging(written: list[int]) -> int:
        """Single asset event per run, after every dataset is in staging."""
        total = sum(written)
        log.info("staging.ibge_sidra_observation updated: %s rows upserted", total)
        return total

    window = resolve_window()
    raw_refs = extract_to_raw.partial(window=window).expand(dataset_name=list(DATASETS))
    ensure_tables() >> raw_refs
    publish_staging(raw_to_staging.expand(ref=raw_refs))


ibge_sidra()
