-- Full rebuild of marts.fuel_prices_weekly (runs in one transaction).
-- staging keeps partial sums per source file (a week can span two monthly
-- files); they are combined here, weighted by samples.
TRUNCATE marts.fuel_prices_weekly;

INSERT INTO marts.fuel_prices_weekly (
    week_start, level, state, municipality, product, unit,
    avg_price, min_price, max_price, samples, updated_at
)
WITH municipal AS (
    SELECT
        week_start, state, municipality, product,
        max(unit) AS unit,
        sum(samples) AS samples,
        sum(price_sum) AS price_sum,
        min(price_min) AS price_min,
        max(price_max) AS price_max
    FROM staging.anp_fuel_price_weekly
    GROUP BY week_start, state, municipality, product
)
SELECT week_start, 'Município', state, municipality, product, unit,
       round(price_sum / samples, 3), price_min, price_max, samples, now()
FROM municipal
UNION ALL
SELECT week_start, 'UF', state, '', product, max(unit),
       round(sum(price_sum) / sum(samples), 3), min(price_min), max(price_max), sum(samples), now()
FROM municipal
GROUP BY week_start, state, product
UNION ALL
SELECT week_start, 'Brasil', '', '', product, max(unit),
       round(sum(price_sum) / sum(samples), 3), min(price_min), max(price_max), sum(samples), now()
FROM municipal
GROUP BY week_start, product;
