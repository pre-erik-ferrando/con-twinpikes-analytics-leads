{{ config(materialized='view') }}
-- Current quadrant of every subregion (biz_cfg_strategic_zone): last row per key, active only (DT.2.1).
-- The runs resolve configuration at their own snapshot in Python; these views serve the lgn_v_* views.
SELECT
    src.pk_country                                                        AS country,
    src.pk_admin_level                                                    AS admin_level,
    src.pk_admin_code                                                     AS admin_code,
    argMax(src.des_admin_name, (src.inserted_at, src.updated_by))         AS admin_name,
    argMax(src.des_quadrant, (src.inserted_at, src.updated_by))           AS quadrant,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_strategic_zone') }} AS src
GROUP BY src.pk_country, src.pk_admin_level, src.pk_admin_code
HAVING is_active = 1
