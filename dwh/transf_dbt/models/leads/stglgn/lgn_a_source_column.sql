{{ config(materialized='view') }}
-- Current column mapping of the files we receive (biz_cfg_source_column): last row per key, active only.
SELECT
    src.pk_country                                                        AS country,
    src.pk_source_system                                                  AS source_system,
    src.pk_source_column                                                  AS source_column,
    argMax(src.des_target_column, (src.inserted_at, src.updated_by))      AS target_column,
    argMax(src.des_transform, (src.inserted_at, src.updated_by))          AS transform,
    argMax(src.des_value_map, (src.inserted_at, src.updated_by))          AS value_map,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_source_column') }} AS src
GROUP BY src.pk_country, src.pk_source_system, src.pk_source_column
HAVING is_active = 1
