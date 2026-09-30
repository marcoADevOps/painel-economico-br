"""Date window processed by a DAG run."""

from __future__ import annotations

from datetime import date, timedelta


class InvalidWindowError(ValueError):
    """Bad start/end run parameters (a user error, not worth retrying)."""


def resolve_window(params: dict, run_day: date, lookback_days: int) -> tuple[date, date]:
    """Window for a run: [start, end] from the params, or a lookback ending at run_day.

    Scheduled runs use the lookback, so late publications and revisions are
    reprocessed and catchup fills gaps after downtime. Manual runs may pass
    `start` (and optionally `end`, default run_day) for historical loads.
    """
    if params.get("start"):
        try:
            start = date.fromisoformat(params["start"])
            end = date.fromisoformat(params["end"]) if params.get("end") else run_day
        except ValueError as exc:
            raise InvalidWindowError(f"invalid start/end parameter: {exc}") from exc
    else:
        end = run_day
        start = end - timedelta(days=lookback_days)

    if start > end:
        raise InvalidWindowError(f"start {start} is after end {end}")
    return start, end
