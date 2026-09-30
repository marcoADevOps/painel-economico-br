"""ANP fuel prices (station level) -> raw files -> weekly staging by municipality.

The source page is read on every run to discover the data files (their names
are irregular). Scheduled runs look at files covering the last ~4 months:
recent ones are downloaded again to catch republications, older ones already
stored are skipped. For historical loads (the project keeps 2016 onward),
trigger manually with `start` (and optionally `end`).

The original files are kept gzip-compressed on disk under RAW_DIR, recorded in
raw.anp_file with their SHA-256. At most two files are processed at a time so
the 4 GB VM is not overloaded by the large semester files (~85 MB each).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

from painel.dag_support import (
    DEFAULT_ARGS,
    STAGING_ANP,
    TIMEZONE,
    WINDOW_PARAMS,
    check_quality,
    dw_connection,
    run_window,
)

LOOKBACK_DAYS = 120
# Files whose period ended within this many days of the window end may still be
# republished by the ANP, so they are always downloaded again.
REFRESH_DAYS = 60
MAX_PARALLEL_FILES = 2
RAW_DIR = Path(os.environ.get("RAW_DIR", "/opt/airflow/raw"))
DDL_FILE = Path(__file__).resolve().parents[1] / "sql" / "anp_precos.sql"

log = logging.getLogger(__name__)


def _stored_path(source_key: str) -> str:
    group, period = source_key.split(":")
    return f"anp/{group}/{group}_{period}.csv.gz"


@dag(
    dag_id="anp_precos",
    description="ANP fuel prices: weekly averages by municipality and product",
    # Monthly files come out irregularly; a weekly check is enough.
    schedule="0 11 * * 1",
    start_date=pendulum.datetime(2026, 9, 1, tz=TIMEZONE),
    catchup=True,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    params=WINDOW_PARAMS,
    tags=["anp", "combustiveis", "ingestion"],
    doc_md=__doc__,
)
def anp_precos():
    @task
    def ensure_tables() -> None:
        from painel.warehouse import run_sql_file

        with dw_connection() as conn:
            run_sql_file(conn, DDL_FILE)

    @task
    def resolve_window() -> dict[str, str]:
        return run_window(LOOKBACK_DAYS)

    @task
    def discover(window: dict[str, str]) -> list[dict]:
        from painel.anp import discover_files, fetch_page, files_in_window
        from painel.http import build_session, call_with_retry

        session = build_session()
        html = call_with_retry(lambda: fetch_page(session))
        start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
        files = files_in_window(discover_files(html), start, end)
        log.info("%s ANP files cover %s..%s", len(files), start, end)
        return [
            {
                "key": f.key,
                "url": f.url,
                "group": f.group,
                "period_start": f.period_start.isoformat(),
                "period_end": f.period_end.isoformat(),
                "refresh_after": (end - timedelta(days=REFRESH_DAYS)).isoformat(),
            }
            for f in files
        ]

    @task(max_active_tis_per_dagrun=MAX_PARALLEL_FILES)
    def extract_to_raw(file: dict) -> dict:
        from painel.anp import SourceFile, download, store_as_gzip
        from painel.http import build_session
        from painel.warehouse import get_anp_file, register_anp_file

        source = SourceFile(
            file["url"], file["group"],
            date.fromisoformat(file["period_start"]), date.fromisoformat(file["period_end"]),
        )
        stored = _stored_path(source.key)
        with dw_connection() as conn:
            known = get_anp_file(conn, source.key)
            settled = source.period_end < date.fromisoformat(file["refresh_after"])
            if settled and known and (RAW_DIR / known["stored_path"]).is_file():
                log.info("%s already stored, skipping download", source.key)
                return {"key": source.key, "stage": known["staging_rows"] == 0}

            tmp_dir = RAW_DIR / ".tmp"
            downloaded = tmp_dir / stored.replace("/", "_").removesuffix(".gz")
            try:
                sha256 = download(build_session(), source.url, downloaded)
                size = downloaded.stat().st_size
                changed = known is None or known["sha256"] != sha256
                if changed or not (RAW_DIR / stored).is_file():
                    store_as_gzip(downloaded, RAW_DIR / stored)
            finally:
                downloaded.unlink(missing_ok=True)
            register_anp_file(conn, source, sha256, size, stored)
        log.info("%s: %.1f MB, %s", source.key, size / 1e6, "changed" if changed else "unchanged")
        return {"key": source.key, "stage": changed or known["staging_rows"] == 0}

    @task(max_active_tis_per_dagrun=MAX_PARALLEL_FILES)
    def raw_to_staging(ref: dict) -> int:
        from painel.anp import parse_file
        from painel.warehouse import replace_anp_staging

        if not ref["stage"]:
            log.info("%s unchanged and already in staging", ref["key"])
            return 0
        result = parse_file(RAW_DIR / _stored_path(ref["key"]))
        with dw_connection() as conn:
            written = replace_anp_staging(conn, ref["key"], result)
        log.info(
            "%s: %s rows read, %s without price/date, %s weekly aggregates",
            ref["key"], result.rows, result.skipped, written,
        )
        return written

    @task
    def quality_checks(written: list[int], window: dict[str, str]) -> int:
        """Gate: bad staging data fails here and is never published to the marts."""
        from painel.quality import ANP_CHECKS

        check_quality(ANP_CHECKS, as_of=window["end"])
        return sum(written)

    @task(outlets=[STAGING_ANP])
    def publish_staging(total: int) -> int:
        """Single asset event per run, after every file is in staging."""
        log.info("staging.anp_fuel_price_weekly updated: %s rows written", total)
        return total

    window = resolve_window()
    files = discover(window)
    raw_refs = extract_to_raw.expand(file=files)
    ensure_tables() >> raw_refs
    publish_staging(quality_checks(raw_to_staging.expand(ref=raw_refs), window))


anp_precos()
