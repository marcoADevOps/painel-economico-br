-- Full rebuild of marts.monthly_indicators (runs in one transaction).
TRUNCATE marts.monthly_indicators;

INSERT INTO marts.monthly_indicators (
    month, selic_target, ptax_avg, ptax_close, ipca_monthly, ipca_12m, real_rate_12m, updated_at
)
WITH bcb AS (
    SELECT date_trunc('month', ref_date)::date AS month, series_name, ref_date, value
    FROM staging.bcb_sgs_observation
),
selic AS (
    SELECT DISTINCT ON (month) month, value AS selic_target
    FROM bcb
    WHERE series_name = 'selic_meta'
    ORDER BY month, ref_date DESC
),
ptax AS (
    SELECT
        month,
        avg(value) AS ptax_avg,
        (array_agg(value ORDER BY ref_date DESC))[1] AS ptax_close
    FROM bcb
    WHERE series_name = 'ptax_venda'
    GROUP BY month
),
ipca AS (
    SELECT
        ref_date AS month,
        max(value) FILTER (WHERE variable_id = 63) AS ipca_monthly,
        max(value) FILTER (WHERE variable_id = 2265) AS ipca_12m
    FROM staging.ibge_sidra_observation
    WHERE dataset = 'ipca'
      AND category_id = 7169          -- índice geral
      AND territorial_level = 'N1'    -- Brasil
      AND locality_id = '1'
    GROUP BY ref_date
),
months AS (
    SELECT month FROM selic
    UNION
    SELECT month FROM ptax
    UNION
    SELECT month FROM ipca
)
SELECT
    month,
    s.selic_target,
    round(p.ptax_avg, 4),
    p.ptax_close,
    i.ipca_monthly,
    i.ipca_12m,
    round(((1 + s.selic_target / 100) / (1 + i.ipca_12m / 100) - 1) * 100, 4),
    now()
FROM months
LEFT JOIN selic s USING (month)
LEFT JOIN ptax p USING (month)
LEFT JOIN ipca i USING (month);
