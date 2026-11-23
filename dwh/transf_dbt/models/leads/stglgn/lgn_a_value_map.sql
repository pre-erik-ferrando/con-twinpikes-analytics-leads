{{ config(materialized='view') }}
-- Current value translations (biz_cfg_value_map): last row per key, active only.
SELECT
    src.pk_country                                                        AS country,
    src.pk_map_name                                                       AS map_name,
    src.pk_source_value                                                   AS source_value,
    argMax(src.des_target_value, (src.inserted_at, src.updated_by))       AS target_value,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_value_map') }} AS src
GROUP BY src.pk_country, src.pk_map_name, src.pk_source_value
HAVING is_active = 1
