{{ config(materialized='view') }}
-- Current brands (biz_cfg_brand): last row per key, active only; country NULL = the common list, used by a
-- country without a list of its own (config.brands).
SELECT
    src.pk_country                                                        AS country,
    src.pk_brand                                                          AS brand,
    argMax(src.des_owner, (src.inserted_at, src.updated_by))              AS owner,
    argMax(src.val_brand_group, (src.inserted_at, src.updated_by))        AS brand_group,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_brand') }} AS src
GROUP BY src.pk_country, src.pk_brand
HAVING is_active = 1
