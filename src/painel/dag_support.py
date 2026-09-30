"""Pieces shared by the DAG files (imports Airflow lazily where possible)."""

from __future__ import annotations

from contextlib import closing
from datetime import timedelta

from airflow.sdk import Param

from painel.alerts import notify_task_failure

TIMEZONE = "America/Sao_Paulo"
DW_CONN_ID = "painel_dw"

DEFAULT_ARGS = {
    "owner": "painel",
    # Waits of ~2, 4 and 8 min: a persistent failure is alerted in ~15 min.
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "on_failure_callback": notify_task_failure,
}

WINDOW_PARAMS = {
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
}


def dw_connection():
    """Warehouse connection that is closed on exit.

    closing(): a psycopg connection's own context manager does not close it.
    """
    from airflow.providers.postgres.hooks.postgres import PostgresHook

    return closing(PostgresHook(postgres_conn_id=DW_CONN_ID).get_conn())


def run_window(lookback_days: int) -> dict[str, str]:
    """Resolve the run's date window inside a task (see painel.windows)."""
    import pendulum
    from airflow.sdk import get_current_context
    from airflow.sdk.exceptions import AirflowFailException

    from painel.windows import InvalidWindowError, resolve_window

    context = get_current_context()
    # Manual runs in Airflow 3 may have no logical_date; fall back to run_after.
    run_date = context.get("logical_date") or context["dag_run"].run_after
    run_day = pendulum.instance(run_date).in_timezone(TIMEZONE).date()
    try:
        start, end = resolve_window(context["params"], run_day, lookback_days)
    except InvalidWindowError as exc:
        # User error: fail at once (no retries) so the alert arrives immediately.
        raise AirflowFailException(str(exc)) from exc
    return {"start": start.isoformat(), "end": end.isoformat()}
