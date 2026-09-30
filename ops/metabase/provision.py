#!/usr/bin/env python3
"""Dashboards as code: creates or updates the Metabase questions and dashboards.

Run on the VM (standard library only):
    sudo python3 ops/metabase/provision.py

Reads MB_API_KEY from /etc/painel-metabase.env (written by set-metabase-key.sh)
and talks to Metabase on http://localhost:3000. Idempotent: questions and
dashboards are matched by name inside the "Painel Econômico BR" collection
and updated in place; managed questions no longer in DASHBOARDS are archived.
All queries read the marts schema only (the connection uses metabase_ro).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MB_URL = os.environ.get("MB_URL", "http://localhost:3000")
ENV_FILE = "/etc/painel-metabase.env"
WAREHOUSE_DB = "painel"
COLLECTION = "Painel Econômico BR"
MANAGED = "Gerenciado por ops/metabase/provision.py (não editar pela interface)."


# --- definitions ------------------------------------------------------------------
# Each dashboard: name, description and a list of cards laid out on a 24-column
# grid. A card is either a native SQL question or a text block.


def question(name, sql, display, row, col, width, height, **viz):
    return {"name": name, "sql": sql.strip(), "display": display, "viz": viz,
            "row": row, "col": col, "size_x": width, "size_y": height}


def text(content, row, col, width, height):
    return {"text": content.strip(), "row": row, "col": col, "size_x": width, "size_y": height}


def line(x, metrics=None, series=None):
    viz = {"graph.dimensions": [x] + ([series] if series else []),
           "graph.metrics": metrics or ["valor"]}
    return viz


LATEST_MONTH = "(SELECT max(month) FROM marts.monthly_indicators WHERE {col} IS NOT NULL)"

MACRO = {
    "name": "Indicadores macroeconômicos",
    "description": "Selic, inflação, juro real e câmbio (Banco Central e IBGE).",
    "cards": [
        text("""
# Indicadores macroeconômicos
Selic meta (Copom), IPCA Brasil (IBGE) e dólar PTAX (Banco Central), mês a mês.
O juro real é ex-post: (1 + Selic) / (1 + IPCA 12 meses) − 1.
O IPCA das tabelas usadas começa em 2012.
""", 0, 0, 24, 3),
        question("Selic meta atual (% a.a.)", f"""
SELECT selic_target AS "Selic meta (% a.a.)" FROM marts.monthly_indicators
WHERE month = {LATEST_MONTH.format(col="selic_target")}
""", "scalar", 3, 0, 6, 3),
        question("IPCA 12 meses (%)", f"""
SELECT ipca_12m AS "IPCA 12 meses (%)" FROM marts.monthly_indicators
WHERE month = {LATEST_MONTH.format(col="ipca_12m")}
""", "scalar", 3, 6, 6, 3),
        question("Juro real ex-post atual (%)", f"""
SELECT real_rate_12m AS "Juro real (%)" FROM marts.monthly_indicators
WHERE month = {LATEST_MONTH.format(col="real_rate_12m")}
""", "scalar", 3, 12, 6, 3),
        question("Dólar PTAX (fechamento, R$)", f"""
SELECT ptax_close AS "PTAX (R$)" FROM marts.monthly_indicators
WHERE month = {LATEST_MONTH.format(col="ptax_close")}
""", "scalar", 3, 18, 6, 3),
        question("Selic meta × IPCA 12 meses", """
SELECT month AS "Mês", selic_target AS "Selic meta (% a.a.)", ipca_12m AS "IPCA 12 meses (%)"
FROM marts.monthly_indicators
WHERE month >= DATE '2012-12-01' AND ipca_12m IS NOT NULL
ORDER BY month
""", "line", 6, 0, 24, 8, **line("Mês", ["Selic meta (% a.a.)", "IPCA 12 meses (%)"])),
        question("Juro real ex-post ao longo do tempo (%)", """
SELECT month AS "Mês", real_rate_12m AS "Juro real (%)"
FROM marts.monthly_indicators
WHERE real_rate_12m IS NOT NULL
ORDER BY month
""", "area", 14, 0, 12, 8, **line("Mês", ["Juro real (%)"])),
        question("Dólar PTAX, média mensal (R$)", """
SELECT month AS "Mês", ptax_avg AS "PTAX média (R$)"
FROM marts.monthly_indicators
WHERE ptax_avg IS NOT NULL
ORDER BY month
""", "line", 14, 12, 12, 8, **line("Mês", ["PTAX média (R$)"])),
    ],
}

REGIONS = {
    "name": "Inflação e desemprego por região",
    "description": (
        "IPCA por região metropolitana e capital; desocupação por região e UF (IBGE)."
    ),
    "cards": [
        text("""
# Inflação e desemprego por região
IPCA (índice geral) por região metropolitana e capital pesquisadas pelo IBGE,
e taxa de desocupação da PNAD Contínua (trimestral).
""", 0, 0, 24, 2),
        question("IPCA 12 meses por localidade (último mês)", """
SELECT locality_name AS "Localidade", ipca_12m AS "IPCA 12 meses (%)"
FROM marts.ipca_by_region
WHERE month = (SELECT max(month) FROM marts.ipca_by_region WHERE ipca_12m IS NOT NULL)
  AND ipca_12m IS NOT NULL
ORDER BY ipca_12m DESC
""", "row", 2, 0, 12, 10, **line("Localidade", ["IPCA 12 meses (%)"])),
        question("Desocupação por UF (último trimestre)", """
SELECT locality_name AS "UF", rate AS "Desocupação (%)"
FROM marts.unemployment_by_region
WHERE level = 'N3'
  AND quarter_start = (SELECT max(quarter_start) FROM marts.unemployment_by_region)
ORDER BY rate DESC
""", "row", 2, 12, 12, 10, **line("UF", ["Desocupação (%)"])),
        question("Desocupação: Brasil e Grandes Regiões (%)", """
SELECT quarter_start AS "Trimestre", locality_name AS "Região", rate AS "Desocupação (%)"
FROM marts.unemployment_by_region
WHERE level IN ('N1', 'N2')
ORDER BY quarter_start, locality_name
""", "line", 12, 0, 24, 8, **line("Trimestre", ["Desocupação (%)"], series="Região")),
    ],
}

FUEL = {
    "name": "Combustíveis",
    "description": "Preço médio semanal de revenda por produto, UF e município (ANP).",
    "cards": [
        text("""
# Preços de combustíveis (ANP)
Média semanal (domingo a sábado) dos preços de revenda coletados pela ANP,
ponderada pelo número de coletas. Lacunas da fonte: ago–out/2020,
1º semestre de 2022 (não publicado) e uma semana de set/2022.
""", 0, 0, 24, 3),
        question("Gasolina: preço médio Brasil (última semana)", """
SELECT avg_price AS "Gasolina (R$/l)" FROM marts.fuel_prices_weekly
WHERE level = 'Brasil' AND product = 'GASOLINA'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'Brasil' AND product = 'GASOLINA')
""", "scalar", 3, 0, 8, 3),
        question("Etanol: preço médio Brasil (última semana)", """
SELECT avg_price AS "Etanol (R$/l)" FROM marts.fuel_prices_weekly
WHERE level = 'Brasil' AND product = 'ETANOL'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'Brasil' AND product = 'ETANOL')
""", "scalar", 3, 8, 8, 3),
        question("Diesel S10: preço médio Brasil (última semana)", """
SELECT avg_price AS "Diesel S10 (R$/l)" FROM marts.fuel_prices_weekly
WHERE level = 'Brasil' AND product = 'DIESEL S10'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'Brasil' AND product = 'DIESEL S10')
""", "scalar", 3, 16, 8, 3),
        question("Preço médio Brasil por combustível (R$/l)", """
SELECT week_start AS "Semana", initcap(product) AS "Produto", avg_price AS "Preço médio (R$)"
FROM marts.fuel_prices_weekly
WHERE level = 'Brasil' AND product IN ('GASOLINA', 'ETANOL', 'DIESEL S10')
ORDER BY week_start, product
""", "line", 6, 0, 16, 8, **line("Semana", ["Preço médio (R$)"], series="Produto")),
        question("GLP 13 kg: preço médio Brasil (R$)", """
SELECT week_start AS "Semana", avg_price AS "GLP 13 kg (R$)"
FROM marts.fuel_prices_weekly
WHERE level = 'Brasil' AND product = 'GLP'
ORDER BY week_start
""", "line", 6, 16, 8, 8, **line("Semana", ["GLP 13 kg (R$)"])),
        question("Gasolina por UF (última semana, R$/l)", """
SELECT state AS "UF", avg_price AS "Gasolina (R$/l)"
FROM marts.fuel_prices_weekly
WHERE level = 'UF' AND product = 'GASOLINA'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'UF' AND product = 'GASOLINA')
ORDER BY avg_price DESC
""", "row", 14, 0, 12, 10, **line("UF", ["Gasolina (R$/l)"])),
        question("Gasolina: 10 municípios mais caros (última semana)", """
SELECT municipality AS "Município", state AS "UF", avg_price AS "Preço médio (R$/l)",
       samples AS "Coletas"
FROM marts.fuel_prices_weekly
WHERE level = 'Município' AND product = 'GASOLINA'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'Município' AND product = 'GASOLINA')
ORDER BY avg_price DESC
LIMIT 10
""", "table", 14, 12, 12, 5),
        question("Gasolina: 10 municípios mais baratos (última semana)", """
SELECT municipality AS "Município", state AS "UF", avg_price AS "Preço médio (R$/l)",
       samples AS "Coletas"
FROM marts.fuel_prices_weekly
WHERE level = 'Município' AND product = 'GASOLINA'
  AND week_start = (SELECT max(week_start) FROM marts.fuel_prices_weekly
                    WHERE level = 'Município' AND product = 'GASOLINA')
ORDER BY avg_price ASC
LIMIT 10
""", "table", 19, 12, 12, 5),
    ],
}

DASHBOARDS = [MACRO, REGIONS, FUEL]


# --- Metabase API -------------------------------------------------------------------


def load_api_key() -> str:
    if key := os.environ.get("MB_API_KEY"):
        return key
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line_ in f:
                if line_.startswith("MB_API_KEY="):
                    return line_.split("=", 1)[1].strip()
    except PermissionError:
        sys.exit(f"cannot read {ENV_FILE}: run with sudo")
    sys.exit(f"MB_API_KEY not found (run set-metabase-key.sh to create {ENV_FILE})")


class Metabase:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.url}/api{path}", data=data, method=method,
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            sys.exit(f"{method} {path}: HTTP {exc.code}: {detail}")
        return json.loads(raw) if raw else None

    def warehouse_id(self) -> int:
        databases = self.call("GET", "/database")
        databases = databases.get("data", databases)
        for db in databases:
            dbname = db.get("details", {}).get("dbname")
            if db.get("engine") == "postgres" and dbname == WAREHOUSE_DB:
                return db["id"]
        sys.exit(f'no PostgreSQL connection to database "{WAREHOUSE_DB}" in Metabase')

    def collection_id(self) -> int:
        for collection in self.call("GET", "/collection"):
            if collection.get("name") == COLLECTION and not collection.get("archived"):
                return collection["id"]
        created = self.call("POST", "/collection", {
            "name": COLLECTION,
            "description": "Painéis do projeto painel-economico-br. " + MANAGED,
            "parent_id": None,
        })
        return created["id"]

    def items(self, collection_id: int, model: str) -> list[dict]:
        found = self.call("GET", f"/collection/{collection_id}/items?models={model}")
        return found.get("data", found)


def card_payload(spec: dict, database_id: int, collection_id: int) -> dict:
    return {
        "name": spec["name"],
        "type": "question",
        "description": MANAGED,
        "collection_id": collection_id,
        "display": spec["display"],
        "visualization_settings": spec["viz"],
        "dataset_query": {
            "database": database_id,
            "type": "native",
            "native": {"query": spec["sql"], "template-tags": {}},
        },
    }


def check_unique_names() -> None:
    names = [c["name"] for d in DASHBOARDS for c in d["cards"] if "name" in c]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        sys.exit(f"question names must be unique (they are the matching key): {duplicates}")


def provision(mb: Metabase) -> None:
    check_unique_names()
    database_id = mb.warehouse_id()
    collection_id = mb.collection_id()
    card_items = mb.items(collection_id, "card")
    # First card per name is reused; any other one with the same name is archived.
    existing_cards = {}
    for item in card_items:
        existing_cards.setdefault(item["name"], item)
    existing_dashboards = {item["name"]: item for item in mb.items(collection_id, "dashboard")}
    kept_ids = set()
    failures = []

    for dashboard in DASHBOARDS:
        dashcards = []
        for index, spec in enumerate(dashboard["cards"], start=1):
            layout = {k: spec[k] for k in ("row", "col", "size_x", "size_y")}
            if "text" in spec:
                dashcards.append({
                    "id": -index, "card_id": None, **layout, "parameter_mappings": [],
                    "visualization_settings": {
                        "virtual_card": {"name": None, "display": "text", "archived": False,
                                         "visualization_settings": {}, "dataset_query": {}},
                        "text": spec["text"],
                    },
                })
                continue
            payload = card_payload(spec, database_id, collection_id)
            if spec["name"] in existing_cards:
                card_id = existing_cards[spec["name"]]["id"]
                mb.call("PUT", f"/card/{card_id}", payload)
                action = "updated"
            else:
                card_id = mb.call("POST", "/card", payload)["id"]
                action = "created"
            kept_ids.add(card_id)
            result = mb.call("POST", f"/card/{card_id}/query", {})
            if result.get("status") == "completed":
                print(f"  question {action}: {spec['name']} ({result.get('row_count')} rows)")
            else:
                failures.append(spec["name"])
                print(f"  question {action} but FAILED: {spec['name']}: {result.get('error')}")
            dashcards.append({"id": -index, "card_id": card_id, **layout,
                              "parameter_mappings": [], "visualization_settings": {}})

        if dashboard["name"] in existing_dashboards:
            dashboard_id = existing_dashboards[dashboard["name"]]["id"]
        else:
            dashboard_id = mb.call("POST", "/dashboard", {
                "name": dashboard["name"], "collection_id": collection_id,
            })["id"]
        mb.call("PUT", f"/dashboard/{dashboard_id}", {
            "description": dashboard["description"] + " " + MANAGED,
            "dashcards": dashcards,
            "tabs": [],
        })
        print(f"dashboard: {dashboard['name']} -> {MB_URL}/dashboard/{dashboard_id}")

    for item in card_items:
        if item["id"] not in kept_ids:
            mb.call("PUT", f"/card/{item['id']}", {"archived": True})
            print(f"  question archived (not in DASHBOARDS or duplicate): {item['name']}")

    if failures:
        sys.exit(f"{len(failures)} question(s) failed to run: {failures}")


if __name__ == "__main__":
    provision(Metabase(MB_URL, load_api_key()))
