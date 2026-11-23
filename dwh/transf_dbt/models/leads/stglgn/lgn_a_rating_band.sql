{{ config(materialized='view') }}
-- Current Rating thresholds (biz_cfg_rating_band): last row per key, active only; country NULL = every country.
SELECT
    src.pk_country                                                        AS country,
    src.pk_rating                                                         AS rating,
    argMax(src.val_min_score, (src.inserted_at, src.updated_by))          AS min_score,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_rating_band') }} AS src
GROUP BY src.pk_country, src.pk_rating
HAVING is_active = 1
