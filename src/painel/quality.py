"""Data quality checks on the staging tables.

Each check is a SQL query that returns the offending rows: no rows = pass.
The source DAGs run them after loading staging and before publishing the
staging asset, so bad data never reaches the marts. Freshness is measured
against the run's reference date (`as_of`), never against "now".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

log = logging.getLogger(__name__)

SAMPLE_ROWS = 5


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    sql: str  # may use the %(as_of)s parameter; returns violating rows


@dataclass(frozen=True)
class CheckResult:
    check: Check
    violations: int
    sample: list[tuple]

    @property
    def passed(self) -> bool:
        return self.violations == 0


class DataQualityError(Exception):
    """At least one check found violations."""


BCB_CHECKS = (
    Check(
        "bcb_duplicates",
        "one row per series and date",
        """
        SELECT series_code, ref_date, count(*)
        FROM staging.bcb_sgs_observation
        GROUP BY series_code, ref_date
        HAVING count(*) > 1
        """,
    ),
    Check(
        "bcb_nulls",
        "no nulls in required columns",
        """
        SELECT series_code, ref_date
        FROM staging.bcb_sgs_observation
        WHERE value IS NULL OR series_name IS NULL OR ref_date IS NULL
        """,
    ),
    Check(
        "bcb_range",
        "Selic target within 0..50 % p.a.; PTAX within 0.5..20 BRL/USD",
        """
        SELECT series_name, ref_date, value
        FROM staging.bcb_sgs_observation
        WHERE (series_name = 'selic_meta' AND value NOT BETWEEN 0 AND 50)
           OR (series_name = 'ptax_venda' AND value NOT BETWEEN 0.5 AND 20)
        """,
    ),
    Check(
        "bcb_freshness",
        "latest Selic target and PTAX at most 7 days older than the run date",
        """
        SELECT s.series_name, max(o.ref_date) AS latest
        FROM (VALUES ('selic_meta', 7), ('ptax_venda', 7)) AS s(series_name, max_age_days)
        LEFT JOIN staging.bcb_sgs_observation o USING (series_name)
        GROUP BY s.series_name, s.max_age_days
        HAVING max(o.ref_date) IS NULL
            OR max(o.ref_date) < %(as_of)s::date - s.max_age_days
        """,
    ),
)

IBGE_CHECKS = (
    Check(
        "ibge_duplicates",
        "one row per dataset, variable, category, level, locality and period",
        """
        SELECT dataset, variable_id, category_id, territorial_level, locality_id, period, count(*)
        FROM staging.ibge_sidra_observation
        GROUP BY dataset, variable_id, category_id, territorial_level, locality_id, period
        HAVING count(*) > 1
        """,
    ),
    Check(
        "ibge_nulls",
        "no nulls in required columns",
        """
        SELECT dataset, variable_id, territorial_level, locality_id, period
        FROM staging.ibge_sidra_observation
        WHERE value IS NULL OR ref_date IS NULL OR locality_name IS NULL
        """,
    ),
    Check(
        "ibge_range",
        "IPCA monthly -5..10 %, IPCA 12 months -10..100 %, unemployment 0..40 %",
        """
        SELECT dataset, variable_id, territorial_level, locality_name, period, value
        FROM staging.ibge_sidra_observation
        WHERE (dataset = 'ipca' AND variable_id = 63 AND value NOT BETWEEN -5 AND 10)
           OR (dataset = 'ipca' AND variable_id = 2265 AND value NOT BETWEEN -10 AND 100)
           OR (dataset = 'desocupacao' AND variable_id = 4099 AND value NOT BETWEEN 0 AND 40)
        """,
    ),
    Check(
        "ibge_freshness",
        # ref_date is the first day of the period: IPCA comes out ~10 days after
        # the month ends (age up to ~70 days), PNAD ~45 days after the quarter
        # ends (age up to ~225 days).
        "latest Brazil IPCA at most 80 days old, unemployment at most 240 days",
        """
        SELECT s.dataset, max(o.ref_date) AS latest
        FROM (VALUES ('ipca', 80), ('desocupacao', 240)) AS s(dataset, max_age_days)
        LEFT JOIN staging.ibge_sidra_observation o
               ON o.dataset = s.dataset AND o.territorial_level = 'N1'
        GROUP BY s.dataset, s.max_age_days
        HAVING max(o.ref_date) IS NULL
            OR max(o.ref_date) < %(as_of)s::date - s.max_age_days
        """,
    ),
)


ANP_CHECKS = (
    Check(
        "anp_duplicates",
        "one row per week, municipality, product and source file",
        """
        SELECT week_start, state, municipality, product, source_key, count(*)
        FROM staging.anp_fuel_price_weekly
        GROUP BY week_start, state, municipality, product, source_key
        HAVING count(*) > 1
        """,
    ),
    Check(
        "anp_nulls",
        "no nulls in required columns and at least one sample per row",
        """
        SELECT source_key, week_start, state, municipality, product
        FROM staging.anp_fuel_price_weekly
        WHERE samples IS NULL OR samples <= 0 OR price_sum IS NULL
           OR price_min IS NULL OR price_max IS NULL OR unit IS NULL
        """,
    ),
    Check(
        # The weekly average is checked (not min/max): single-station typos in
        # the source cannot be fixed here and must not block the pipeline.
        "anp_range",
        "weekly average within R$ 0.50..20 (liquid fuels, CNG) or R$ 20..300 (LPG 13 kg)",
        """
        SELECT source_key, week_start, state, municipality, product,
               round(price_sum / samples, 3) AS avg_price
        FROM staging.anp_fuel_price_weekly
        WHERE price_min > price_max
           OR (product = 'GLP' AND price_sum / samples NOT BETWEEN 20 AND 300)
           OR (product <> 'GLP' AND price_sum / samples NOT BETWEEN 0.5 AND 20)
        """,
    ),
    Check(
        # Monthly files come out about a month after the period ends.
        "anp_freshness",
        "latest week at most 100 days older than the run date",
        """
        SELECT max(week_start) AS latest
        FROM staging.anp_fuel_price_weekly
        HAVING max(week_start) IS NULL OR max(week_start) < %(as_of)s::date - 100
        """,
    ),
)


def run_checks(conn, checks: tuple[Check, ...], as_of: date) -> list[CheckResult]:
    results = []
    with conn.cursor() as cur:
        for check in checks:
            params = {"as_of": as_of} if "%(as_of)s" in check.sql else None
            cur.execute(check.sql, params)
            rows = cur.fetchall()
            result = CheckResult(check, len(rows), [tuple(r) for r in rows[:SAMPLE_ROWS]])
            level = logging.INFO if result.passed else logging.ERROR
            log.log(
                level, "check %s (%s): %s violations", check.name, check.description,
                result.violations,
            )
            results.append(result)
    # Checks only read; end the transaction so the connection is left clean.
    conn.rollback()
    return results


def failure_summary(results: list[CheckResult]) -> str:
    failed = [r for r in results if not r.passed]
    lines = [f"{len(failed)} of {len(results)} data quality checks failed:"]
    for r in failed:
        lines.append(
            f"- {r.check.name} ({r.check.description}): {r.violations} rows, e.g. {r.sample}"
        )
    return "\n".join(lines)


def assert_quality(results: list[CheckResult]) -> None:
    if any(not r.passed for r in results):
        raise DataQualityError(failure_summary(results))
