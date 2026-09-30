import gzip
import zipfile
from datetime import date
from decimal import Decimal

import pytest
import requests

from painel import anp
from painel.anp import (
    AnpError,
    SourceFile,
    aggregate_rows,
    classify_link,
    discover_files,
    files_in_window,
    parse_file,
    parse_price,
    store_as_gzip,
    week_start,
)

BASE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc"
HEADER = (
    "Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;"
    "Complemento;Bairro;Cep;Produto;Data da Coleta;Valor de Venda;Valor de Compra;"
    "Unidade de Medida;Bandeira"
)


def csv_row(uf="AC", municipio="RIO BRANCO", produto="GASOLINA", data="03/08/2026",
            valor="7,19", unidade="R$ / litro"):
    return (
        f"N;{uf};{municipio};POSTO;00;RUA;1;;BAIRRO;69900-000;"
        f"{produto};{data};{valor};;{unidade};X"
    )


# --- classify_link: real (irregular) names seen on the ANP page -----------------


@pytest.mark.parametrize(
    ("path", "key", "start", "end"),
    [
        ("dsas/ca/ca-2016-01.csv", "ca:2016-S1", date(2016, 1, 1), date(2016, 6, 30)),
        ("dsas/ca/ca-2022-02.zip", "ca:2022-S2", date(2022, 7, 1), date(2022, 12, 31)),
        ("dsas/glp/precos-semestrais-glp2021-01.csv", "glp:2021-S1",
         date(2021, 1, 1), date(2021, 6, 30)),
        ("dsan/2024/precos-gasolina-etanol-06.csv", "gasolina-etanol:2024-06",
         date(2024, 6, 1), date(2024, 6, 30)),
        ("dsan/2026/02-cados-abertos-preco-gasolina-etanol.csv", "gasolina-etanol:2026-02",
         date(2026, 2, 1), date(2026, 2, 28)),
        ("dsan/2026/06-dados-abertos-precos-2026-06-glp.csv", "glp:2026-06",
         date(2026, 6, 1), date(2026, 6, 30)),
        ("dsan/2026/04-dados-abertos-precos-diesel-gnv", "diesel-gnv:2026-04",
         date(2026, 4, 1), date(2026, 4, 30)),
    ],
)
def test_classify_real_link_names(path, key, start, end):
    source = classify_link(f"{BASE}/{path}")
    assert (source.key, source.period_start, source.period_end) == (key, start, end)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gov.br/anp/pt-br/agenda/agendadirigentesate2020.xlsx",
        f"{BASE}/dsan/2026/metadados.pdf",
        f"{BASE}/dsan/2026/13-dados-abertos-precos-glp.csv",
    ],
)
def test_classify_ignores_other_links(url):
    assert classify_link(url) is None


def test_discover_dedupes_and_sorts():
    html = "".join(
        f'<a href="{BASE}/{p}">x</a>'
        for p in [
            "dsan/2024/precos-glp-06.csv",
            "dsas/ca/ca-2016-01.csv",
            "dsan/2024/precos-glp-06.csv",
            "dsan/2024/precos-glp-6-copia.csv",
        ]
    )
    files = discover_files(html)
    assert [f.key for f in files] == ["ca:2016-S1", "glp:2024-06"]


def test_monthly_files_covered_by_a_semester_file_are_dropped():
    html = "".join(
        f'<a href="{BASE}/{p}">x</a>'
        for p in [
            "dsas/ca/ca-2026-01.csv",
            "dsas/glp/glp-2026-01.csv",
            "dsan/2026/03-dados-abertos-precos-gasolina-etanol.csv",
            "dsan/2026/03-dados-abertos-precos-diesel-gnv.csv",
            "dsan/2026/03-dados-abertos-precos-glp.csv",
            "dsan/2026/08-dados-abertos-precos-2026-08-glp.csv",
            "dsan/2026/08-dados-abertos-precos-2026-08-diesel-gnv.csv",
        ]
    )
    keys = [f.key for f in discover_files(html)]
    assert keys == ["ca:2026-S1", "glp:2026-S1", "diesel-gnv:2026-08", "glp:2026-08"]


def test_files_in_window_uses_overlap():
    files = [
        SourceFile("u1", "ca", date(2021, 7, 1), date(2021, 12, 31)),
        SourceFile("u2", "glp", date(2026, 5, 1), date(2026, 5, 31)),
        SourceFile("u3", "glp", date(2026, 8, 1), date(2026, 8, 31)),
    ]
    picked = files_in_window(files, date(2026, 5, 20), date(2026, 9, 30))
    assert [f.url for f in picked] == ["u2", "u3"]


# --- parsing ----------------------------------------------------------------------


def test_week_starts_on_sunday():
    assert week_start(date(2026, 9, 30)) == date(2026, 9, 27)  # Wednesday
    assert week_start(date(2026, 9, 27)) == date(2026, 9, 27)  # Sunday
    assert week_start(date(2026, 10, 3)) == date(2026, 9, 27)  # Saturday


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7,19", Decimal("7.19")), ("110,00", Decimal("110.00")), ("1.234,56", Decimal("1234.56")),
     (" ", None), ("", None)],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_parse_price_rejects_garbage():
    with pytest.raises(AnpError):
        parse_price("abc")


def test_aggregate_by_week_municipality_and_product():
    rows = [
        dict(zip(HEADER.split(";"), csv_row(valor=v, data=d).split(";"), strict=True))
        for v, d in [("7,00", "03/08/2026"), ("8,00", "05/08/2026"), ("", "05/08/2026"),
                     ("9,00", "10/08/2026")]
    ]
    result = aggregate_rows(rows)

    assert (result.rows, result.skipped) == (4, 1)
    week1 = result.aggregates[(date(2026, 8, 2), "AC", "RIO BRANCO", "GASOLINA", "R$ / litro")]
    assert (week1.samples, week1.price_sum, week1.price_min, week1.price_max) == (
        2, Decimal("15.00"), Decimal("7.00"), Decimal("8.00")
    )
    assert len(result.aggregates) == 2


def _write_gz(path, text, encoding):
    with gzip.open(path, "wb") as f:
        f.write(text.encode(encoding))


def test_parse_file_utf8_with_bom(tmp_path):
    path = tmp_path / "f.csv.gz"
    _write_gz(path, "﻿" + HEADER + "\r\n" + csv_row(municipio="SÃO PAULO", uf="SP") + "\r\n",
              "utf-8")

    result = parse_file(path)

    assert list(result.aggregates)[0][2] == "SÃO PAULO"


def test_parse_file_falls_back_to_latin1(tmp_path):
    path = tmp_path / "f.csv.gz"
    _write_gz(path, HEADER + "\n" + csv_row(municipio="MACEIÓ", uf="AL") + "\n", "latin-1")

    assert list(parse_file(path).aggregates)[0][2] == "MACEIÓ"


def test_parse_file_requires_columns(tmp_path):
    path = tmp_path / "f.csv.gz"
    _write_gz(path, "a;b;c\n1;2;3\n", "utf-8")

    with pytest.raises(AnpError, match="missing columns"):
        parse_file(path)


# --- storage ------------------------------------------------------------------------


def test_store_plain_csv_as_gzip(tmp_path):
    src = tmp_path / "download"
    src.write_bytes(b"a;b\n1;2\n")

    store_as_gzip(src, tmp_path / "out" / "f.csv.gz")

    assert gzip.decompress((tmp_path / "out" / "f.csv.gz").read_bytes()) == b"a;b\n1;2\n"


def test_store_unpacks_zip(tmp_path):
    src = tmp_path / "download"
    with zipfile.ZipFile(src, "w") as archive:
        archive.writestr("ca-2022-02.csv", "a;b\n1;2\n")

    store_as_gzip(src, tmp_path / "f.csv.gz")

    assert gzip.decompress((tmp_path / "f.csv.gz").read_bytes()) == b"a;b\n1;2\n"


# --- download with resume -----------------------------------------------------------


class FakeStream:
    def __init__(self, status, body, headers=None, cut_after=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.cut_after = cut_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        if self.cut_after is None:
            yield self.body
            return
        yield self.body[: self.cut_after]
        raise requests.exceptions.ChunkedEncodingError("connection broken")


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.headers_seen = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.headers_seen.append(headers or {})
        return self.responses.pop(0)


def test_download_resumes_a_cut_transfer(tmp_path):
    body = b"x" * 100
    session = FakeSession(
        FakeStream(200, body, {"Content-Length": "100"}, cut_after=40),
        FakeStream(206, body[40:]),
    )

    digest = anp.download(session, "https://example/f.csv", tmp_path / "f")

    assert (tmp_path / "f").read_bytes() == body
    assert session.headers_seen[1]["Range"] == "bytes=40-"
    assert len(digest) == 64


def test_download_restarts_when_range_is_ignored(tmp_path):
    body = b"y" * 50
    session = FakeSession(
        FakeStream(200, body, {"Content-Length": "50"}, cut_after=10),
        FakeStream(200, body, {"Content-Length": "50"}),
    )

    anp.download(session, "https://example/f.csv", tmp_path / "f")

    assert (tmp_path / "f").read_bytes() == body


def test_requests_never_ask_for_json(tmp_path):
    """gov.br (Plone) answers Accept: application/json with HTTP 401."""
    session = FakeSession(FakeStream(200, b"ok", {"Content-Length": "2"}))
    anp.download(session, "https://example/f.csv", tmp_path / "f")

    class PageSession:
        def get(self, url, headers=None, timeout=None):
            self.headers = headers
            return type("R", (), {"status_code": 200, "text": '<a href="/shpc/x">'})()

    page = PageSession()
    anp.fetch_page(page)

    assert "json" not in session.headers_seen[0]["Accept"]
    assert "json" not in page.headers["Accept"]


def test_download_404_is_not_retried(tmp_path):
    session = FakeSession(FakeStream(404, b""))

    with pytest.raises(AnpError, match="404") as info:
        anp.download(session, "https://example/missing.csv", tmp_path / "f")
    assert not info.value.retryable
