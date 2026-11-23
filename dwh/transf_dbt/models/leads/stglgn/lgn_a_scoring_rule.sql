{{ config(materialized='view') }}
-- Current points per factor and bucket (biz_cfg_scoring_rule): last row per key, active only. country and
-- profile may be NULL (every country / profile): the score keeps the most specific row (config.scoped).
SELECT
    src.pk_country                                                        AS country,
    src.pk_profile                                                        AS profile,
    src.pk_factor                                                         AS factor,
    src.pk_bucket_code                                                    AS bucket_code,
    argMax(src.val_points, (src.inserted_at, src.updated_by))             AS points,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_scoring_rule') }} AS src
GROUP BY src.pk_country, src.pk_profile, src.pk_factor, src.pk_bucket_code
HAVING is_active = 1
