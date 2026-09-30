-- Banco Central SGS: raw and staging layers. Idempotent, runs on every DAG run.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;

-- One row per API request window, with the untouched JSON response.
-- Re-running the same window replaces the row (no duplicates).
CREATE TABLE IF NOT EXISTS raw.bcb_sgs_response (
    series_code   integer     NOT NULL,
    window_start  date        NOT NULL,
    window_end    date        NOT NULL,
    payload       jsonb       NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_code, window_start, window_end)
);

-- Clean, typed observations. Natural key: series + reference date.
CREATE TABLE IF NOT EXISTS staging.bcb_sgs_observation (
    series_code  integer        NOT NULL,
    series_name  text           NOT NULL,
    ref_date     date           NOT NULL,
    value        numeric(18, 6) NOT NULL,
    loaded_at    timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (series_code, ref_date)
);
