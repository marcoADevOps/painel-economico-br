-- Full rebuild of marts.unemployment_by_region (runs in one transaction).
TRUNCATE marts.unemployment_by_region;

INSERT INTO marts.unemployment_by_region (
    quarter_start, quarter, level, level_name, locality_id, locality_name, rate, updated_at
)
SELECT
    ref_date,
    extract(year FROM ref_date)::int || '-Q' || extract(quarter FROM ref_date)::int,
    territorial_level,
    CASE territorial_level
        WHEN 'N1' THEN 'Brasil'
        WHEN 'N2' THEN 'Grande Região'
        WHEN 'N3' THEN 'UF'
        ELSE territorial_level
    END,
    locality_id,
    locality_name,
    value,
    now()
FROM staging.ibge_sidra_observation
WHERE dataset = 'desocupacao'
  AND variable_id = 4099;
