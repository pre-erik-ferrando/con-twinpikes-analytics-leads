{{ config(materialized='view') }}
-- Current exclusions by name (biz_cfg_title_rule): last row per key, active only.
SELECT
    src.pk_country                                                        AS country,
    src.pk_profile                                                        AS profile,
    src.pk_pattern_normalized                                             AS pattern_normalized,
    argMax(src.des_pattern, (src.inserted_at, src.updated_by))            AS pattern,
    argMax(src.des_match_type, (src.inserted_at, src.updated_by))         AS match_type,
    argMax(src.des_rule_class, (src.inserted_at, src.updated_by))         AS rule_class,
    argMax(src.des_reason_code, (src.inserted_at, src.updated_by))        AS reason_code,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_title_rule') }} AS src
GROUP BY src.pk_country, src.pk_profile, src.pk_pattern_normalized
HAVING is_active = 1
