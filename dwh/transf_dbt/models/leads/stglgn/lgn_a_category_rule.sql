{{ config(materialized='view') }}
-- Current classification of Google categories (biz_cfg_category_rule).
SELECT
    src.pk_country                                                        AS country,
    src.pk_language                                                       AS language,
    src.pk_profile                                                        AS profile,
    src.pk_category_normalized                                            AS category_normalized,
    argMax(src.des_classification, (src.inserted_at, src.updated_by))     AS classification,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_category_rule') }} AS src
GROUP BY src.pk_country, src.pk_language, src.pk_profile, src.pk_category_normalized
HAVING is_active = 1
