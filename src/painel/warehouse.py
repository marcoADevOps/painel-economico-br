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
