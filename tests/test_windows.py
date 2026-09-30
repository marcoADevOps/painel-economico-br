from datetime import date

import pytest

from painel.windows import InvalidWindowError, resolve_window

RUN_DAY = date(2026, 9, 30)


def test_scheduled_run_uses_lookback():
    assert resolve_window({}, RUN_DAY, 10) == (date(2026, 9, 20), RUN_DAY)


def test_manual_run_with_start_only_ends_on_run_day():
    assert resolve_window({"start": "2000-01-01"}, RUN_DAY, 10) == (date(2000, 1, 1), RUN_DAY)


def test_manual_run_with_start_and_end():
    params = {"start": "2020-01-01", "end": "2020-12-31"}
    assert resolve_window(params, RUN_DAY, 10) == (date(2020, 1, 1), date(2020, 12, 31))


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-09-30", "end": "2026-01-01"},
        {"start": "30/09/2026"},
        {"start": "2026-13-01"},
    ],
)
def test_invalid_params_raise_user_error(params):
    with pytest.raises(InvalidWindowError):
        resolve_window(params, RUN_DAY, 10)
