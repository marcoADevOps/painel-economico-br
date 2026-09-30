"""IBGE SIDRA (API de agregados v3): extraction and parsing.

Endpoint: https://servicodados.ibge.gov.br/api/v3/agregados/{table}/periodos/{p}/variaveis/{v}
Checked against the table metadata and live queries on 2026-09-30:
- IPCA by region: table 1419 (2012-01..2019-12) and 7060 (2020-01 onward),
  same layout: variable 63 (monthly change, %) and 2265 (12-month change, %),
  classification 315 category 7169 (índice geral); 17 localities: Brazil (N1),
  10 metropolitan areas (N7) and 6 capitals (N6).
- Unemployment: table 4099 (PNAD Contínua, quarterly, 2012 onward), variable
  4099; Brazil (N1), major regions (N2) and states (N3).
- Periods are YYYYMM (monthly) or YYYYQQ (quarterly: 202602 = 2nd quarter).
- Locality ids repeat across levels (Brazil and Norte are both "1"), so the
  territorial level is part of the natural key.
- A period with no data returns [] (HTTP 200); an oversized query returns 500.
- Conventional signs instead of numbers: "-" absolute zero, "X" suppressed,
  ".." not applicable, "..." not available.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

from painel.http import REQUEST_TIMEOUT, ApiError, call_with_retry

BASE_URL = "https://servicodados.ibge.gov.br/api/v3/agregados"

MONTHLY = "monthly"
QUARTERLY = "quarterly"

# Conventional signs: "-" is a real zero, the others mean there is no value.
ZERO_SIGN = "-"
NO_VALUE_SIGNS = frozenset({"X", "..", "..."})


@dataclass(frozen=True)
class SidraTable:
    table_id: int
    first_period: int
    last_period: int | None = None  # None = still being updated


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    periodicity: str
    tables: tuple[SidraTable, ...]
    variables: tuple[int, ...]
    localities: str
    classification: str | None = None


DATASETS: dict[str, Dataset] = {
    "ipca": Dataset(
        name="ipca",
        description="IPCA por região: variação mensal e em 12 meses (%)",
        periodicity=MONTHLY,
        tables=(SidraTable(1419, 201201, 201912), SidraTable(7060, 202001)),
        variables=(63, 2265),
        localities="N1[all]|N7[all]|N6[all]",
        classification="315[7169]",
    ),
    "desocupacao": Dataset(
        name="desocupacao",
        description="Taxa de desocupação, PNAD Contínua trimestral (%)",
        periodicity=QUARTERLY,
        tables=(SidraTable(4099, 201201),),
        variables=(4099,),
        localities="N1[all]|N2[all]|N3[all]",
    ),
}


@dataclass(frozen=True)
class Observation:
    variable_id: int
    variable_name: str
    unit: str
    category_id: int  # 0 when the table has no classification
    territorial_level: str
    locality_id: str
    locality_name: str
    period: str
    ref_date: date
    value: Decimal


class SidraApiError(ApiError):
    """The SIDRA API answered with something that is not valid data."""


# --- periods ------------------------------------------------------------------


def date_to_period(day: date, periodicity: str) -> int:
    if periodicity == MONTHLY:
        return day.year * 100 + day.month
    if periodicity == QUARTERLY:
        return day.year * 100 + (day.month - 1) // 3 + 1
    raise ValueError(f"unknown periodicity {periodicity!r}")


def period_to_date(period: str | int, periodicity: str) -> date:
    """First day of the period (the quarter's first month for quarterly data)."""
    code = int(period)
    year, part = divmod(code, 100)
    if periodicity == MONTHLY and 1 <= part <= 12:
        return date(year, part, 1)
    if periodicity == QUARTERLY and 1 <= part <= 4:
        return date(year, 3 * (part - 1) + 1, 1)
    raise ValueError(f"invalid {periodicity} period {period!r}")


def plan_requests(dataset: Dataset, start: date, end: date) -> list[tuple[int, int, int]]:
    """(table_id, first_period, last_period) requests covering [start, end]."""
    want_from = date_to_period(start, dataset.periodicity)
    want_to = date_to_period(end, dataset.periodicity)
    plan = []
    for table in dataset.tables:
        first = max(want_from, table.first_period)
        last = want_to if table.last_period is None else min(want_to, table.last_period)
        if first <= last:
            plan.append((table.table_id, first, last))
    return plan


# --- fetch --------------------------------------------------------------------


def fetch_table(
    session: requests.Session, dataset: Dataset, table_id: int, first: int, last: int
) -> list[dict]:
    """Raw SIDRA response for one table and period range ([] when there is no data)."""
    url = (
        f"{BASE_URL}/{table_id}/periodos/{first}-{last}"
        f"/variaveis/{'|'.join(str(v) for v in dataset.variables)}"
    )
    params = {"localidades": dataset.localities}
    if dataset.classification:
        params["classificacao"] = dataset.classification
    label = f"table {table_id} {first}-{last}"

    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise SidraApiError(
            f"{label}: HTTP {response.status_code}: {response.text[:200]}",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise SidraApiError(
            f"{label}: response is not JSON "
            f"(content-type {response.headers.get('Content-Type')!r})",
            retryable=True,
        ) from exc
    if not isinstance(payload, list):
        raise SidraApiError(f"{label}: unexpected payload {str(payload)[:200]}")
    return payload


def fetch_table_with_retry(
    session: requests.Session,
    dataset: Dataset,
    table_id: int,
    first: int,
    last: int,
    attempts: int = 3,
    wait_seconds: float = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    return call_with_retry(
        lambda: fetch_table(session, dataset, table_id, first, last),
        attempts=attempts,
        wait_seconds=wait_seconds,
        sleep=sleep,
    )


# --- parse --------------------------------------------------------------------


def parse_value(raw: str) -> Decimal | None:
    """Numeric value, 0 for "-", None for the no-value signs."""
    text = raw.strip()
    if text == ZERO_SIGN:
        return Decimal(0)
    if text in NO_VALUE_SIGNS:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise SidraApiError(f"invalid value {raw!r}") from exc
    if not value.is_finite():
        raise SidraApiError(f"invalid value {raw!r}")
    return value


def parse_sidra(payload: list[dict], periodicity: str) -> tuple[list[Observation], int]:
    """Flatten a SIDRA response into observations; also returns how many had no value."""
    observations: list[Observation] = []
    no_value = 0
    try:
        for variable in payload:
            for result in variable["resultados"]:
                classifications = result.get("classificacoes") or []
                category_id = (
                    int(next(iter(classifications[0]["categoria"]))) if classifications else 0
                )
                for series in result["series"]:
                    locality = series["localidade"]
                    for period, raw_value in series["serie"].items():
                        value = parse_value(raw_value)
                        if value is None:
                            no_value += 1
                            continue
                        observations.append(
                            Observation(
                                variable_id=int(variable["id"]),
                                variable_name=variable["variavel"],
                                unit=variable["unidade"],
                                category_id=category_id,
                                territorial_level=locality["nivel"]["id"],
                                locality_id=str(locality["id"]),
                                locality_name=locality["nome"],
                                period=str(period),
                                ref_date=period_to_date(period, periodicity),
                                value=value,
                            )
                        )
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise SidraApiError(f"unexpected SIDRA structure: {exc!r}") from exc
    return observations, no_value
