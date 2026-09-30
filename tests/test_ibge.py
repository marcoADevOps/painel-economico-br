import json
from datetime import date
from decimal import Decimal

import pytest

from painel.ibge import (
    DATASETS,
    MONTHLY,
    QUARTERLY,
    SidraApiError,
    date_to_period,
    fetch_table,
    fetch_table_with_retry,
    parse_sidra,
    parse_value,
    period_to_date,
    plan_requests,
)

IPCA = DATASETS["ipca"]
DESOCUPACAO = DATASETS["desocupacao"]


class FakeResponse:
    def __init__(self, status_code, body, content_type="application/json"):
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Returns the given responses in order (the last one repeats)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


def sidra_payload(level="N1", locality_id="1", series=None, classified=True):
    classifications = (
        [{"id": "315", "nome": "Geral", "categoria": {"7169": "Índice geral"}}]
        if classified
        else []
    )
    locality = {"id": locality_id, "nivel": {"id": level, "nome": "x"}, "nome": f"loc {level}"}
    result = {
        "classificacoes": classifications,
        "series": [
            {"localidade": locality, "serie": series or {"202607": "0.07", "202608": "-0.32"}}
        ],
    }
    return [{"id": "63", "variavel": "IPCA - Variação mensal", "unidade": "%",
             "resultados": [result]}]


# --- periods ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "periodicity", "expected"),
    [
        (date(2026, 9, 30), MONTHLY, 202609),
        (date(2026, 1, 1), MONTHLY, 202601),
        (date(2026, 3, 31), QUARTERLY, 202601),
        (date(2026, 4, 1), QUARTERLY, 202602),
        (date(2026, 12, 31), QUARTERLY, 202604),
    ],
)
def test_date_to_period(day, periodicity, expected):
    assert date_to_period(day, periodicity) == expected


def test_period_to_date():
    assert period_to_date("202608", MONTHLY) == date(2026, 8, 1)
    assert period_to_date("202602", QUARTERLY) == date(2026, 4, 1)
    assert period_to_date(202604, QUARTERLY) == date(2026, 10, 1)


@pytest.mark.parametrize(("period", "periodicity"), [("202613", MONTHLY), ("202605", QUARTERLY)])
def test_period_to_date_rejects_invalid(period, periodicity):
    with pytest.raises(ValueError):
        period_to_date(period, periodicity)


# --- plan_requests ------------------------------------------------------------


def test_plan_splits_ipca_across_its_two_tables():
    assert plan_requests(IPCA, date(2012, 1, 1), date(2026, 9, 30)) == [
        (1419, 201201, 201912),
        (7060, 202001, 202609),
    ]


def test_plan_recent_window_uses_only_current_table():
    assert plan_requests(IPCA, date(2025, 8, 26), date(2026, 9, 30)) == [(7060, 202508, 202609)]


def test_plan_before_first_period_is_empty():
    assert plan_requests(DESOCUPACAO, date(2000, 1, 1), date(2011, 12, 31)) == []


def test_plan_quarterly():
    assert plan_requests(DESOCUPACAO, date(2025, 8, 26), date(2026, 9, 30)) == [
        (4099, 202503, 202603)
    ]


# --- fetch --------------------------------------------------------------------


def test_fetch_builds_sidra_url_and_params():
    session = FakeSession(FakeResponse(200, json.dumps(sidra_payload())))

    fetch_table(session, IPCA, 7060, 202601, 202609)

    url, params = session.calls[0]
    assert url.endswith("/agregados/7060/periodos/202601-202609/variaveis/63|2265")
    assert params == {"localidades": "N1[all]|N7[all]|N6[all]", "classificacao": "315[7169]"}


def test_fetch_without_classification():
    session = FakeSession(FakeResponse(200, "[]"))

    assert fetch_table(session, DESOCUPACAO, 4099, 202601, 202604) == []
    assert "classificacao" not in session.calls[0][1]


def test_fetch_retries_server_errors_then_succeeds():
    session = FakeSession(
        FakeResponse(500, '{"message":"Internal server error"}'), FakeResponse(200, "[]")
    )

    assert fetch_table_with_retry(session, IPCA, 7060, 1, 2, sleep=lambda _: None) == []
    assert len(session.calls) == 2


def test_fetch_client_error_is_not_retried():
    session = FakeSession(FakeResponse(400, "bad request"))

    with pytest.raises(SidraApiError, match="HTTP 400"):
        fetch_table_with_retry(session, IPCA, 7060, 1, 2, sleep=lambda _: None)
    assert len(session.calls) == 1


def test_fetch_html_body_is_retryable_error():
    session = FakeSession(FakeResponse(200, "<html>erro</html>", "text/html"))

    with pytest.raises(SidraApiError, match="not JSON") as info:
        fetch_table(session, IPCA, 7060, 1, 2)
    assert info.value.retryable


# --- parse --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.07", Decimal("0.07")),
        ("-0.32", Decimal("-0.32")),
        ("-", Decimal(0)),
        ("X", None),
        ("..", None),
        ("...", None),
    ],
)
def test_parse_value_handles_conventional_signs(raw, expected):
    assert parse_value(raw) == expected


def test_parse_value_rejects_garbage():
    with pytest.raises(SidraApiError):
        parse_value("abc")


def test_parse_sidra_flattens_and_types():
    observations, no_value = parse_sidra(sidra_payload(), MONTHLY)

    assert no_value == 0
    assert [(o.period, o.ref_date, o.value) for o in observations] == [
        ("202607", date(2026, 7, 1), Decimal("0.07")),
        ("202608", date(2026, 8, 1), Decimal("-0.32")),
    ]
    first = observations[0]
    key = (first.variable_id, first.category_id, first.territorial_level, first.locality_id)
    assert key == (63, 7169, "N1", "1")


def test_parse_sidra_skips_no_value_signs():
    payload = sidra_payload(series={"202601": "6.1", "202602": "...", "202603": "X"})

    observations, no_value = parse_sidra(payload, QUARTERLY)

    assert len(observations) == 1
    assert no_value == 2


def test_parse_sidra_keeps_level_to_tell_localities_apart():
    brasil = sidra_payload(level="N1", locality_id="1", classified=False)
    norte = sidra_payload(level="N2", locality_id="1", classified=False)

    observations, _ = parse_sidra(brasil + norte, MONTHLY)

    keys = {(o.territorial_level, o.locality_id, o.period) for o in observations}
    assert len(keys) == len(observations) == 4
    assert all(o.category_id == 0 for o in observations)


def test_parse_sidra_rejects_unexpected_structure():
    with pytest.raises(SidraApiError, match="structure"):
        parse_sidra([{"id": "63"}], MONTHLY)
