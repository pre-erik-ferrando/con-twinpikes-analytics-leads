{{ config(materialized='view') }}
-- Current pool words (biz_cfg_word_list POOL_SIGNAL, normalized): last row per key, active only.
SELECT
    src.pk_country                                                        AS country,
    src.pk_language                                                       AS language,
    src.pk_profile                                                        AS profile,
    src.pk_value                                                          AS stem,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_word_list') }} AS src
WHERE src.pk_list_name = 'POOL_SIGNAL'
GROUP BY src.pk_country, src.pk_language, src.pk_profile, src.pk_value
HAVING is_active = 1
