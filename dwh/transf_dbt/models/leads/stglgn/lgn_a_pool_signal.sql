{{ config(materialized='view') }}
-- Current pool words (biz_cfg_pool_signal): last row per key, active only.
SELECT
    src.pk_country                                                        AS country,
    src.pk_language                                                       AS language,
    src.pk_profile                                                        AS profile,
    src.pk_stem                                                           AS stem,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_pool_signal') }} AS src
GROUP BY src.pk_country, src.pk_language, src.pk_profile, src.pk_stem
HAVING is_active = 1
