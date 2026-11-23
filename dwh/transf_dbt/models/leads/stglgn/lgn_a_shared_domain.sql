{{ config(materialized='view') }}
-- Current shared domains (biz_cfg_word_list SHARED_DOMAIN: site builders, social networks, directories):
-- last row per key, active only; country NULL = every country.
SELECT
    src.pk_country                                                        AS country,
    src.pk_value                                                          AS domain,
    argMax(src.des_kind, (src.inserted_at, src.updated_by))               AS kind,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_word_list') }} AS src
WHERE src.pk_list_name = 'SHARED_DOMAIN'
GROUP BY src.pk_country, src.pk_profile, src.pk_language, src.pk_value
HAVING is_active = 1
