{{ config(materialized='view') }}
-- Current run parameters (biz_cfg_run_parameter): last row per key, active only; country NULL = every country.
SELECT
    src.pk_country                                                        AS country,
    src.pk_parameter                                                      AS parameter,
    argMax(src.des_value, (src.inserted_at, src.updated_by))              AS value,
    argMax(src.des_value_type, (src.inserted_at, src.updated_by))         AS value_type,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_run_parameter') }} AS src
GROUP BY src.pk_country, src.pk_parameter
HAVING is_active = 1
