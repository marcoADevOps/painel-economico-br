-- Full rebuild of marts.ipca_by_region (runs in one transaction).
TRUNCATE marts.ipca_by_region;

INSERT INTO marts.ipca_by_region (
    month, level, level_name, locality_id, locality_name, ipca_monthly, ipca_12m, updated_at
)
SELECT
    ref_date,
    territorial_level,
    CASE territorial_level
        WHEN 'N1' THEN 'Brasil'
        WHEN 'N6' THEN 'Capital'
        WHEN 'N7' THEN 'Região metropolitana'
        ELSE territorial_level
    END,
    locality_id,
    max(locality_name),
    max(value) FILTER (WHERE variable_id = 63),
    max(value) FILTER (WHERE variable_id = 2265),
    now()
FROM staging.ibge_sidra_observation
WHERE dataset = 'ipca'
  AND category_id = 7169   -- índice geral
GROUP BY ref_date, territorial_level, locality_id;
