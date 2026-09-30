"""Marts: analysis-ready tables rebuilt from staging.

Runs whenever either source DAG (bcb_sgs, ibge_sidra) publishes a staging
update (Airflow assets, OR condition): a failing source does not hold back the
other one, and a full rebuild takes seconds. Each table is rebuilt in a single
transaction (TRUNCATE + INSERT), so readers never see a half-built table and
re-running is always safe.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

from painel.dag_support import DEFAULT_ARGS, STAGING_BCB, STAGING_IBGE, TIMEZONE, dw_connection

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "marts"
MARTS = ("monthly_indicators", "ipca_by_region", "unemployment_by_region")

log = logging.getLogger(__name__)


def _run_sql(name: str) -> None:
    from painel.warehouse import run_sql_file

    with dw_connection() as conn:
        run_sql_file(conn, SQL_DIR / f"{name}.sql")


@dag(
    dag_id="marts",
    description="Rebuild marts (monthly indicators, IPCA and unemployment by region)",
    schedule=STAGING_BCB | STAGING_IBGE,
    start_date=pendulum.datetime(2026, 9, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["marts", "transform"],
    doc_md=__doc__,
)
def marts():
    @task
    def ensure_tables() -> None:
        _run_sql("ddl")

    @task
    def build(name: str) -> int:
        _run_sql(name)
        with dw_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM marts.{name}")  # name from MARTS only
            rows = cur.fetchone()[0]
        log.info("marts.%s rebuilt: %s rows", name, rows)
        return rows

    ensure_tables() >> build.expand(name=list(MARTS))


marts()
