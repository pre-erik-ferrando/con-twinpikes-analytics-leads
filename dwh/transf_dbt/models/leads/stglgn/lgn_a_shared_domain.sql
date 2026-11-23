{{ config(materialized='view') }}
-- Current shared domains (biz_cfg_shared_domain: site builders, social networks, directories): last row per
-- key, active only; country NULL = every country.
SELECT
    src.pk_country                                                        AS country,
    src.pk_domain                                                         AS domain,
    argMax(src.des_kind, (src.inserted_at, src.updated_by))               AS kind,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_shared_domain') }} AS src
GROUP BY src.pk_country, src.pk_domain
HAVING is_active = 1
