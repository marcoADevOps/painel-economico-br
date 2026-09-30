-- Marts: analysis-ready tables for the dashboard, built only from staging.
-- Idempotent; each table is fully rebuilt by its own SQL file.

CREATE SCHEMA IF NOT EXISTS marts;

-- One row per month: policy rate, FX and national inflation side by side.
CREATE TABLE IF NOT EXISTS marts.monthly_indicators (
    month           date PRIMARY KEY,   -- first day of the month
    selic_target    numeric(10, 4),     -- Selic target on the last day with data (% p.a.)
    ptax_avg        numeric(12, 4),     -- average PTAX sell rate (BRL per USD)
    ptax_close      numeric(12, 4),     -- PTAX on the last business day with data
    ipca_monthly    numeric(10, 4),     -- IPCA Brazil, monthly change (%)
    ipca_12m        numeric(10, 4),     -- IPCA Brazil, 12-month change (%)
    real_rate_12m   numeric(10, 4),     -- ex-post real rate: (1+selic)/(1+ipca_12m)-1 (%)
    updated_at      timestamptz NOT NULL
);

-- IPCA by metropolitan area / capital (and Brazil), one row per month.
CREATE TABLE IF NOT EXISTS marts.ipca_by_region (
    month          date NOT NULL,
    level          text NOT NULL,       -- SIDRA territorial level (N1, N6, N7)
    level_name     text NOT NULL,
    locality_id    text NOT NULL,
    locality_name  text NOT NULL,
    ipca_monthly   numeric(10, 4),
    ipca_12m       numeric(10, 4),
    updated_at     timestamptz NOT NULL,
    PRIMARY KEY (month, level, locality_id)
);

-- Unemployment rate by quarter for Brazil, major regions and states.
CREATE TABLE IF NOT EXISTS marts.unemployment_by_region (
    quarter_start  date NOT NULL,       -- first day of the quarter
    quarter        text NOT NULL,       -- e.g. 2026-Q2
    level          text NOT NULL,       -- SIDRA territorial level (N1, N2, N3)
    level_name     text NOT NULL,
    locality_id    text NOT NULL,
    locality_name  text NOT NULL,
    rate           numeric(6, 2) NOT NULL,  -- % of the labour force
    updated_at     timestamptz NOT NULL,
    PRIMARY KEY (quarter_start, level, locality_id)
);
