"""Loads into the Postgres warehouse. Every write is an upsert by natural key.

Only plain DB-API calls (execute/executemany) are used: the Postgres provider
returns a psycopg 3 connection when it is installed, psycopg2 otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path

UPSERT_RAW = """
    INSERT INTO raw.bcb_sgs_response (series_code, window_start, window_end, payload, loaded_at)
    VALUES (%s, %s, %s, %s::jsonb, now())
    ON CONFLICT (series_code, window_start, window_end)
    DO UPDATE SET payload = EXCLUDED.payload, loaded_at = now()
"""

EXISTS_RAW = """
    SELECT 1 FROM raw.bcb_sgs_response
    WHERE series_code = %s AND window_start = %s AND window_end = %s
"""

SELECT_RAW = """
    SELECT payload
    FROM raw.bcb_sgs_response
    WHERE series_code = %s AND window_start >= %s AND window_end <= %s
    ORDER BY loaded_at, window_start
"""

UPSERT_STAGING = """
    INSERT INTO staging.bcb_sgs_observation (series_code, series_name, ref_date, value, loaded_at)
    VALUES (%s, %s, %s, %s, now())
    ON CONFLICT (series_code, ref_date) DO UPDATE
    SET series_name = EXCLUDED.series_name, value = EXCLUDED.value, loaded_at = now()
    WHERE staging.bcb_sgs_observation.value IS DISTINCT FROM EXCLUDED.value
       OR staging.bcb_sgs_observation.series_name IS DISTINCT FROM EXCLUDED.series_name
"""


def run_sql_file(conn, path: Path) -> None:
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def save_raw(conn, series_code: int, window_start: date, window_end: date, payload: list) -> None:
    with conn.cursor() as cur:
        cur.execute(UPSERT_RAW, (series_code, window_start, window_end, json.dumps(payload)))
    conn.commit()


def raw_window_exists(conn, series_code: int, window_start: date, window_end: date) -> bool:
    with conn.cursor() as cur:
        cur.execute(EXISTS_RAW, (series_code, window_start, window_end))
        return cur.fetchone() is not None


def read_raw_payloads(conn, series_code: int, start: date, end: date) -> list[list]:
    with conn.cursor() as cur:
        cur.execute(SELECT_RAW, (series_code, start, end))
        # jsonb comes back already decoded with both psycopg 3 and psycopg2
        return [row[0] for row in cur.fetchall()]


def dedupe_by_date(rows: Iterable[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """Keep one value per date (the last seen), so one upsert never hits a key twice."""
    return sorted(dict(rows).items())


def upsert_staging(
    conn, series_code: int, series_name: str, rows: Iterable[tuple[date, Decimal]]
) -> int:
    values = [
        (series_code, series_name, ref_date, value) for ref_date, value in dedupe_by_date(rows)
    ]
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_STAGING, values)
    conn.commit()
    return len(values)


# --- IBGE SIDRA -----------------------------------------------------------------

UPSERT_IBGE_RAW = """
    INSERT INTO raw.ibge_sidra_response
        (dataset, table_id, period_start, period_end, payload, loaded_at)
    VALUES (%s, %s, %s, %s, %s::jsonb, now())
    ON CONFLICT (dataset, table_id, period_start, period_end)
    DO UPDATE SET payload = EXCLUDED.payload, loaded_at = now()
"""

SELECT_IBGE_RAW = """
    SELECT payload
    FROM raw.ibge_sidra_response
    WHERE dataset = %s AND table_id = %s AND period_start = %s AND period_end = %s
"""

UPSERT_IBGE_STAGING = """
    INSERT INTO staging.ibge_sidra_observation (
        dataset, variable_id, variable_name, unit, category_id, territorial_level,
        locality_id, locality_name, period, ref_date, value, table_id, loaded_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (dataset, variable_id, category_id, territorial_level, locality_id, period)
    DO UPDATE SET
        variable_name = EXCLUDED.variable_name,
        unit = EXCLUDED.unit,
        locality_name = EXCLUDED.locality_name,
        ref_date = EXCLUDED.ref_date,
        value = EXCLUDED.value,
        table_id = EXCLUDED.table_id,
        loaded_at = now()
    WHERE (staging.ibge_sidra_observation.value, staging.ibge_sidra_observation.table_id,
           staging.ibge_sidra_observation.locality_name)
        IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.table_id, EXCLUDED.locality_name)
"""


def save_ibge_raw(
    conn, dataset: str, table_id: int, period_start: int, period_end: int, payload: list
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            UPSERT_IBGE_RAW, (dataset, table_id, period_start, period_end, json.dumps(payload))
        )
    conn.commit()


def read_ibge_raw(
    conn, dataset: str, table_id: int, period_start: int, period_end: int
) -> list | None:
    with conn.cursor() as cur:
        cur.execute(SELECT_IBGE_RAW, (dataset, table_id, period_start, period_end))
        row = cur.fetchone()
    return row[0] if row else None


def upsert_ibge_staging(conn, dataset: str, table_id: int, observations: Iterable) -> int:
    """Upsert painel.ibge.Observation rows; duplicates by natural key keep the last one."""
    unique = {
        (o.variable_id, o.category_id, o.territorial_level, o.locality_id, o.period): o
        for o in observations
    }
    values = [
        (
            dataset, o.variable_id, o.variable_name, o.unit, o.category_id, o.territorial_level,
            o.locality_id, o.locality_name, o.period, o.ref_date, o.value, table_id,
        )
        for o in unique.values()
    ]
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_IBGE_STAGING, values)
    conn.commit()
    return len(values)


# --- ANP fuel prices -------------------------------------------------------------

SELECT_ANP_FILE = """
    SELECT sha256, stored_path,
           (SELECT count(*) FROM staging.anp_fuel_price_weekly s WHERE s.source_key = f.source_key)
    FROM raw.anp_file f
    WHERE source_key = %s
"""

UPSERT_ANP_FILE = """
    INSERT INTO raw.anp_file (
        source_key, file_group, period_start, period_end, url, sha256, size_bytes,
        stored_path, downloaded_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (source_key) DO UPDATE SET
        url = EXCLUDED.url,
        sha256 = EXCLUDED.sha256,
        size_bytes = EXCLUDED.size_bytes,
        stored_path = EXCLUDED.stored_path,
        downloaded_at = now()
"""

INSERT_ANP_STAGING = """
    INSERT INTO staging.anp_fuel_price_weekly (
        source_key, week_start, state, municipality, product, unit,
        samples, price_sum, price_min, price_max, loaded_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
"""


def get_anp_file(conn, source_key: str) -> dict | None:
    """sha256, stored_path and number of staging rows of a registered file."""
    with conn.cursor() as cur:
        cur.execute(SELECT_ANP_FILE, (source_key,))
        row = cur.fetchone()
    conn.rollback()
    if row is None:
        return None
    return {"sha256": row[0], "stored_path": row[1], "staging_rows": row[2]}


def register_anp_file(conn, source, sha256: str, size_bytes: int, stored_path: str) -> None:
    """Record a downloaded painel.anp.SourceFile in raw.anp_file."""
    with conn.cursor() as cur:
        cur.execute(
            UPSERT_ANP_FILE,
            (
                source.key, source.group, source.period_start, source.period_end,
                source.url, sha256, size_bytes, stored_path,
            ),
        )
    conn.commit()


def replace_anp_staging(conn, source_key: str, result) -> int:
    """Swap the staging rows of one source file for a new painel.anp.ParseResult.

    Delete + insert in one transaction: re-running is idempotent and readers
    never see the source half loaded.
    """
    values = [
        (source_key, week, state, municipality, product, unit,
         agg.samples, agg.price_sum, agg.price_min, agg.price_max)
        for (week, state, municipality, product, unit), agg in result.aggregates.items()
    ]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM staging.anp_fuel_price_weekly WHERE source_key = %s", (source_key,)
        )
        if values:
            cur.executemany(INSERT_ANP_STAGING, values)
        cur.execute(
            "UPDATE raw.anp_file SET rows_read = %s, rows_skipped = %s WHERE source_key = %s",
            (result.rows, result.skipped, source_key),
        )
    conn.commit()
    return len(values)
