-- ANP fuel prices: raw and staging layers. Idempotent, runs on every DAG run.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;

-- The original files are kept compressed on disk (RAW_DIR), not in the
-- database: they are large, already compressed and never change, so keeping
-- them out of the daily pg_dump keeps backups small. This table records
-- where each one came from and its checksum.
CREATE TABLE IF NOT EXISTS raw.anp_file (
    source_key     text PRIMARY KEY,      -- e.g. ca:2016-S1, glp:2026-08
    file_group     text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    url            text        NOT NULL,
    sha256         text        NOT NULL,  -- of the bytes as downloaded
    size_bytes     bigint      NOT NULL,
    stored_path    text        NOT NULL,  -- relative to RAW_DIR, .csv.gz
    rows_read      integer,
    rows_skipped   integer,               -- rows without a usable price or date
    downloaded_at  timestamptz NOT NULL
);

-- Weekly aggregates per municipality and product, one set per source file.
-- A week that crosses a month boundary appears in two monthly files, so the
-- partial sums are kept per source and combined (weighted) in the marts.
CREATE TABLE IF NOT EXISTS staging.anp_fuel_price_weekly (
    source_key    text           NOT NULL,
    week_start    date           NOT NULL,   -- Sunday
    state         char(2)        NOT NULL,
    municipality  text           NOT NULL,
    product       text           NOT NULL,
    unit          text           NOT NULL,
    samples       integer        NOT NULL,
    price_sum     numeric(16, 4) NOT NULL,
    price_min     numeric(10, 4) NOT NULL,
    price_max     numeric(10, 4) NOT NULL,
    loaded_at     timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, state, municipality, product, source_key)
);

CREATE INDEX IF NOT EXISTS anp_fuel_price_weekly_source_idx
    ON staging.anp_fuel_price_weekly (source_key);
