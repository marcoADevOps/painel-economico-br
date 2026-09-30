from datetime import date

import pytest

from painel.quality import (
    ANP_CHECKS,
    BCB_CHECKS,
    IBGE_CHECKS,
    SAMPLE_ROWS,
    Check,
    DataQualityError,
    assert_quality,
    run_checks,
)

AS_OF = date(2026, 9, 30)


class FakeCursor:
    def __init__(self, rows_by_check):
        self.rows_by_check = rows_by_check
        self.executed = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._rows = next(
            (rows for marker, rows in self.rows_by_check.items() if marker in sql), []
        )

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows_by_check=None):
        self.cur = FakeCursor(rows_by_check or {})
        self.rolled_back = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True


CHECKS = (
    Check("dup", "no duplicates", "SELECT 1 -- dup"),
    Check("fresh", "fresh enough", "SELECT 1 WHERE x < %(as_of)s -- fresh"),
)


def test_all_checks_pass_without_violating_rows():
    conn = FakeConnection()

    results = run_checks(conn, CHECKS, AS_OF)

    assert [r.passed for r in results] == [True, True]
    assert conn.rolled_back
    assert_quality(results)  # does not raise


def test_as_of_is_passed_only_to_checks_that_use_it():
    conn = FakeConnection()

    run_checks(conn, CHECKS, AS_OF)

    assert [params for _, params in conn.cur.executed] == [None, {"as_of": AS_OF}]


def test_violations_fail_with_a_readable_summary():
    rows = [("selic_meta", date(2026, 9, 1), 99)] * 7
    conn = FakeConnection({"-- dup": rows})

    results = run_checks(conn, CHECKS, AS_OF)

    assert results[0].violations == 7
    assert len(results[0].sample) == SAMPLE_ROWS
    with pytest.raises(DataQualityError) as info:
        assert_quality(results)
    message = str(info.value)
    assert "1 of 2 data quality checks failed" in message
    assert "dup (no duplicates): 7 rows" in message
    assert "fresh" not in message


@pytest.mark.parametrize("checks", [BCB_CHECKS, IBGE_CHECKS, ANP_CHECKS])
def test_each_source_covers_the_required_check_types(checks):
    kinds = {check.name.rsplit("_", 1)[1] for check in checks}
    assert kinds == {"duplicates", "nulls", "range", "freshness"}


@pytest.mark.parametrize(
    "check", [*BCB_CHECKS, *IBGE_CHECKS, *ANP_CHECKS], ids=lambda c: c.name
)
def test_freshness_is_relative_to_the_run_date_not_now(check):
    assert "now()" not in check.sql.lower()
    assert "current_date" not in check.sql.lower()
    if check.name.endswith("freshness"):
        assert "%(as_of)s" in check.sql
