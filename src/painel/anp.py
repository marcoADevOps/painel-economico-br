"""ANP fuel prices (Série Histórica de Preços de Combustíveis): discovery, download, parsing.

Source page: https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/
serie-historica-de-precos-de-combustiveis . Checked on 2026-09-30:
- One row per station, product and collection date; 16 columns, `;`-separated,
  decimal comma, dd/mm/yyyy dates, UTF-8 with BOM. The 2004-2022 semester
  files (`dsas/ca`, `dsas/glp`) have the same columns as the 2023+ monthly
  files (`dsan/{year}`, one per group: gasolina-etanol, diesel-gnv, glp).
- File names are irregular (typos, with/without year, missing extension, one
  semester shipped as .zip), so links are discovered on the page and
  classified by their name instead of being built from a pattern.
- Automotive fuels for 2022 H1 (`ca-2022-01`) are not published.
- The same data is published twice: monthly files (`dsan`, 2023+) and
  consolidated semester files (`dsas`, up to the last closed semester). A
  monthly file is dropped when a semester file covers it, so nothing is
  counted twice; monthly files only fill the current semester.
- The server sometimes cuts large downloads (~85 MB): downloads resume with
  HTTP Range and are checked against Content-Length.
- gov.br runs Plone: `Accept: application/json` is routed to its REST API and
  answered with HTTP 401, so these requests ask for HTML / any type instead.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import logging
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin

import requests

from painel.http import REQUEST_TIMEOUT, ApiError

PAGE_URL = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "serie-historica-de-precos-de-combustiveis"
)
DOWNLOAD_ATTEMPTS = 5
# Never application/json here (see module docstring: Plone answers 401).
PAGE_HEADERS = {"Accept": "text/html"}
FILE_HEADERS = {"Accept": "*/*"}
CHUNK_SIZE = 1024 * 1024

REQUIRED_COLUMNS = (
    "Estado - Sigla",
    "Municipio",
    "Produto",
    "Data da Coleta",
    "Valor de Venda",
    "Unidade de Medida",
)

log = logging.getLogger(__name__)


class AnpError(ApiError):
    """Unexpected page, download or file content from the ANP site."""


# --- discovery ----------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    url: str
    group: str  # ca (all automotive, semesters), gasolina-etanol, diesel-gnv, glp
    period_start: date
    period_end: date  # inclusive

    @property
    def is_semester(self) -> bool:
        return self.period_end.month - self.period_start.month == 5

    @property
    def key(self) -> str:
        if self.is_semester:
            semester = 1 if self.period_start.month == 1 else 2
            return f"{self.group}:{self.period_start.year}-S{semester}"
        return f"{self.group}:{self.period_start:%Y-%m}"


_HREF = re.compile(r'href="([^"]+/shpc/(?:dsas|dsan)/[^"]+)"')
_SEMESTER = re.compile(r"/shpc/dsas/(ca|glp)/[^/]*?(20\d{2})-?(0[12])(?:\.[a-z]+)?$")
_MONTHLY = re.compile(r"/shpc/dsan/(20\d{2})/([^/]+)$")


def _month_end(year: int, month: int) -> date:
    first_next = date(year + month // 12, month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _monthly_group(name: str) -> str | None:
    lowered = name.lower()
    if "glp" in lowered:
        return "glp"
    if "diesel" in lowered or "gnv" in lowered:
        return "diesel-gnv"
    if "gasolina" in lowered or "etanol" in lowered:
        return "gasolina-etanol"
    return None


def classify_link(url: str) -> SourceFile | None:
    """SourceFile for a data file link, or None if the link is not a price file."""
    if match := _SEMESTER.search(url):
        group, year, semester = match.group(1), int(match.group(2)), int(match.group(3))
        start = date(year, 1 if semester == 1 else 7, 1)
        end = date(year, 6, 30) if semester == 1 else date(year, 12, 31)
        return SourceFile(url, group, start, end)
    if match := _MONTHLY.search(url):
        year, name = int(match.group(1)), match.group(2)
        group = _monthly_group(name)
        month_match = re.match(r"(\d{2})-", name) or re.search(r"-(\d{2})(?:\.[a-z]+)?$", name)
        if group is None or month_match is None:
            return None
        month = int(month_match.group(1))
        if not 1 <= month <= 12:
            return None
        return SourceFile(url, group, date(year, month, 1), _month_end(year, month))
    return None


# Semester group -> monthly groups it consolidates.
SEMESTER_COVERS = {"ca": ("gasolina-etanol", "diesel-gnv"), "glp": ("glp",)}


def drop_superseded(files: Iterable[SourceFile]) -> list[SourceFile]:
    """Remove monthly files whose month is already in a semester file."""
    files = list(files)
    covered = {
        (monthly_group, semester.period_start, semester.period_end)
        for semester in files
        if semester.is_semester
        for monthly_group in SEMESTER_COVERS.get(semester.group, ())
    }
    kept = []
    for f in files:
        if not f.is_semester and any(
            g == f.group and start <= f.period_start <= end for g, start, end in covered
        ):
            log.info("%s superseded by a semester file", f.key)
            continue
        kept.append(f)
    return kept


def discover_files(html: str, base_url: str = PAGE_URL) -> list[SourceFile]:
    """Price files linked on the page, one per key (first link wins), without overlaps."""
    found: dict[str, SourceFile] = {}
    for href in _HREF.findall(html):
        source = classify_link(urljoin(base_url, href))
        if source is None:
            log.warning("unrecognized ANP link ignored: %s", href)
            continue
        if source.key in found and found[source.key].url != source.url:
            log.warning("duplicate link for %s ignored: %s", source.key, source.url)
            continue
        found.setdefault(source.key, source)
    return sorted(drop_superseded(found.values()), key=lambda s: (s.period_start, s.group))


def files_in_window(files: Iterable[SourceFile], start: date, end: date) -> list[SourceFile]:
    return [f for f in files if f.period_start <= end and f.period_end >= start]


def fetch_page(session: requests.Session) -> str:
    response = session.get(PAGE_URL, headers=PAGE_HEADERS, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise AnpError(
            f"source page: HTTP {response.status_code}",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    if "/shpc/" not in response.text:
        raise AnpError("source page has no data file links (layout changed?)", retryable=True)
    return response.text


# --- download -----------------------------------------------------------------


def download(session: requests.Session, url: str, target: Path) -> str:
    """Download url to target, resuming cut transfers; returns the SHA-256 of the bytes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    expected: int | None = None
    with open(target, "wb") as out:
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            done = out.tell()
            headers = {**FILE_HEADERS, **({"Range": f"bytes={done}-"} if done else {})}
            try:
                with session.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT) as r:
                    if r.status_code == 404:
                        raise AnpError(f"{url}: HTTP 404")
                    if done and r.status_code == 200:
                        # Server ignored the Range header: start over.
                        out.seek(0)
                        out.truncate()
                        done = 0
                    elif r.status_code not in (200, 206):
                        raise AnpError(f"{url}: HTTP {r.status_code}", retryable=True)
                    if expected is None and r.status_code == 200:
                        length = r.headers.get("Content-Length")
                        expected = int(length) if length else None
                    for chunk in r.iter_content(CHUNK_SIZE):
                        out.write(chunk)
                if expected is None or out.tell() >= expected:
                    break
                log.warning("%s: cut at %s of %s bytes, resuming", url, out.tell(), expected)
            except (requests.ConnectionError, requests.Timeout,
                    requests.exceptions.ChunkedEncodingError) as exc:
                log.warning("%s: attempt %s failed (%s)", url, attempt, type(exc).__name__)
        else:
            raise AnpError(f"{url}: incomplete after {DOWNLOAD_ATTEMPTS} attempts", retryable=True)
        size = out.tell()
    if expected is not None and size != expected:
        raise AnpError(f"{url}: got {size} bytes, expected {expected}", retryable=True)

    digest = hashlib.sha256()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_as_gzip(downloaded: Path, destination: Path) -> None:
    """Store the CSV gzip-compressed; a .zip download is unpacked first."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with open(downloaded, "rb") as f:
            is_zip = f.read(4) == b"PK\x03\x04"
        if is_zip:
            with zipfile.ZipFile(downloaded) as archive:
                members = [m for m in archive.namelist() if m.lower().endswith(".csv")]
                if len(members) != 1:
                    raise AnpError(f"{downloaded.name}: expected one CSV in zip, got {members}")
                with archive.open(members[0]) as src, gzip.open(tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK_SIZE)
        else:
            with open(downloaded, "rb") as src, gzip.open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK_SIZE)
        tmp_path.replace(destination)
    finally:
        tmp_path.unlink(missing_ok=True)


# --- parsing and weekly aggregation --------------------------------------------


def week_start(day: date) -> date:
    """Sunday that starts the week (the ANP survey runs Sunday to Saturday)."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def parse_price(raw: str) -> Decimal | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = Decimal(text.replace(".", "").replace(",", ".") if "," in text else text)
    except InvalidOperation as exc:
        raise AnpError(f"invalid price {raw!r}") from exc
    return value if value.is_finite() else None


@dataclass
class WeeklyAggregate:
    samples: int = 0
    price_sum: Decimal = field(default_factory=Decimal)
    price_min: Decimal | None = None
    price_max: Decimal | None = None

    def add(self, price: Decimal) -> None:
        self.samples += 1
        self.price_sum += price
        self.price_min = price if self.price_min is None else min(self.price_min, price)
        self.price_max = price if self.price_max is None else max(self.price_max, price)


# (week_start, state, municipality, product, unit)
AggregateKey = tuple[date, str, str, str, str]


@dataclass
class ParseResult:
    aggregates: dict[AggregateKey, WeeklyAggregate]
    rows: int
    skipped: int


def _normalize(text: str) -> str:
    return " ".join(text.split()).upper()


def aggregate_rows(rows: Iterable[dict[str, str]]) -> ParseResult:
    aggregates: dict[AggregateKey, WeeklyAggregate] = {}
    total = skipped = 0
    for row in rows:
        total += 1
        price = parse_price(row["Valor de Venda"] or "")
        raw_date = (row["Data da Coleta"] or "").strip()
        if price is None or price <= 0 or not raw_date:
            skipped += 1
            continue
        try:
            day = datetime.strptime(raw_date, "%d/%m/%Y").date()
        except ValueError as exc:
            raise AnpError(f"invalid date {raw_date!r}") from exc
        key = (
            week_start(day),
            row["Estado - Sigla"].strip().upper(),
            _normalize(row["Municipio"]),
            _normalize(row["Produto"]),
            " ".join((row["Unidade de Medida"] or "").split()),
        )
        aggregates.setdefault(key, WeeklyAggregate()).add(price)
    return ParseResult(aggregates, total, skipped)


def _read_rows(text: io.TextIOBase) -> Iterator[dict[str, str]]:
    reader = csv.DictReader(text, delimiter=";")
    columns = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise AnpError(f"missing columns {missing}; got {columns}")
    reader.fieldnames = columns
    yield from reader


def parse_file(path: Path) -> ParseResult:
    """Weekly aggregates of a stored .csv.gz, read as a stream (UTF-8, Latin-1 fallback)."""
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as text:
            return aggregate_rows(_read_rows(text))
    except UnicodeDecodeError:
        log.warning("%s is not UTF-8, reading as Latin-1", path.name)
        with gzip.open(path, "rt", encoding="latin-1", newline="") as text:
            return aggregate_rows(_read_rows(text))
