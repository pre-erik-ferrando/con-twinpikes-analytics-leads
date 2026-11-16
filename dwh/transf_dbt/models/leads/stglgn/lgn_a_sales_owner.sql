{{ config(materialized='view') }}
-- Current Salesforce owner per subregion; subregion_code NULL is the country's default owner.
SELECT
    src.pk_country                                                        AS country,
    src.pk_subregion_code                                                 AS subregion_code,
    argMax(src.des_owner_first_name, (src.inserted_at, src.updated_by))   AS owner_first_name,
    argMax(src.des_owner_last_name, (src.inserted_at, src.updated_by))    AS owner_last_name,
    argMax(src.val_active, (src.inserted_at, src.updated_by))             AS is_active
FROM {{ source('writeback', 'biz_cfg_sales_owner') }} AS src
GROUP BY src.pk_country, src.pk_subregion_code
HAVING is_active = 1
