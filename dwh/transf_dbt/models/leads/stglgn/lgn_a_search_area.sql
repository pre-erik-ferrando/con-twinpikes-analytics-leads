{{ config(materialized='view') }}
-- Current search areas (biz_cfg_search_area).
SELECT
    src.pk_country                                                        AS country,
    src.pk_area_id                                                        AS area_id,
    argMax(src.des_parent_area_id, (src.inserted_at, src.updated_by))     AS parent_area_id,
    argMax(src.des_level, (src.inserted_at, src.updated_by))              AS level,
    argMax(src.des_admin_code, (src.inserted_at, src.updated_by))         AS admin_code,
    argMax(src.des_area_name, (src.inserted_at, src.updated_by))          AS area_name,
    argMax(src.des_region_name, (src.inserted_at, src.updated_by))        AS region_name,
    argMax(src.des_default_language, (src.inserted_at, src.updated_by))   AS default_language,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_search_area') }} AS src
GROUP BY src.pk_country, src.pk_area_id
HAVING is_active = 1
