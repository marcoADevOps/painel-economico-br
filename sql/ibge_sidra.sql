-- IBGE SIDRA: raw and staging layers. Idempotent, runs on every DAG run.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;

-- One row per API request (dataset, table, period range), untouched JSON.
CREATE TABLE IF NOT EXISTS raw.ibge_sidra_response (
    dataset       text        NOT NULL,
    table_id      integer     NOT NULL,
    period_start  integer     NOT NULL,
    period_end    integer     NOT NULL,
    payload       jsonb       NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, table_id, period_start, period_end)
);

-- Clean, typed observations. Locality ids repeat across territorial levels,
-- so the level is part of the natural key. table_id is informational: one
-- dataset (e.g. IPCA) can span tables over time.
CREATE TABLE IF NOT EXISTS staging.ibge_sidra_observation (
    dataset            text           NOT NULL,
    variable_id        integer        NOT NULL,
    variable_name      text           NOT NULL,
    unit               text           NOT NULL,
    category_id        integer        NOT NULL,
    territorial_level  text           NOT NULL,
    locality_id        text           NOT NULL,
    locality_name      text           NOT NULL,
    period             text           NOT NULL,
    ref_date           date           NOT NULL,
    value              numeric(18, 6) NOT NULL,
    table_id           integer        NOT NULL,
    loaded_at          timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, variable_id, category_id, territorial_level, locality_id, period)
);
