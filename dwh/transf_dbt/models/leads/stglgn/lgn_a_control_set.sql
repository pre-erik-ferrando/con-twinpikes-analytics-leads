{{ config(materialized='view') }}
-- Current acceptance control set (DF.12).
SELECT
    src.pk_country                                                        AS country,
    src.pk_source                                                         AS source,
    src.pk_name                                                           AS name,
    argMax(src.des_domain, (src.inserted_at, src.updated_by))             AS domain,
    argMax(src.des_phone, (src.inserted_at, src.updated_by))              AS phone,
    argMax(src.des_expected, (src.inserted_at, src.updated_by))           AS expected,
    argMax(src.des_expected_reason, (src.inserted_at, src.updated_by))    AS expected_reason,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_control_set') }} AS src
GROUP BY src.pk_country, src.pk_source, src.pk_name
HAVING is_active = 1
