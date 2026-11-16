{{ config(materialized='view') }}
-- Preview of the Salesforce export (DT.8.5): Approved leads with the fields biz_cfg_export_column can
-- point to. The file itself is written by `leadgen.py export` (steps/export.py, same field names).
SELECT
    l.pk_business_id                                       AS business_id,
    l.pk_profile                                           AS profile,
    l.des_capture_batch_id                                 AS capture_batch_id,
    l.des_lead_code                                        AS lead_code,
    coalesce(l.des_contact_name, l.des_name)               AS last_name,
    l.des_name                                             AS company_google,
    coalesce(o.owner_first_name, d.owner_first_name)       AS owner_first_name,
    coalesce(o.owner_last_name, d.owner_last_name)         AS owner_last_name,
    if(l.des_country = 'CH', 'CHF', 'EUR')                 AS currency,
    l.des_rating                                           AS rating,
    l.val_score                                            AS score,
    l.des_address                                          AS street,
    l.des_city                                             AS city,
    l.des_postal_code                                      AS zip_code,
    l.des_region                                           AS province,
    l.des_country                                          AS country_code,
    l.des_company_type                                     AS company_type,
    l.des_offering                                         AS offering,
    l.des_pool_focus                                       AS pool_focus,
    if(l.des_fluidra_brands != '', concat('Fluidra brands: ', l.des_fluidra_brands, '. ', coalesce(l.des_why, '')),
       l.des_why)                                          AS family_comments,
    l.des_phone                                            AS phone,
    l.des_email                                            AS email,
    l.des_website                                          AS website,
    l.val_has_ecommerce                                    AS has_ecommerce,
    isNull(coalesce(o.owner_last_name, d.owner_last_name)) AS is_missing_owner
FROM {{ ref('lgn_v_lead') }} AS l
LEFT JOIN {{ ref('lgn_a_sales_owner') }} AS o ON o.country = l.des_country AND o.subregion_code = l.des_subregion_code
LEFT JOIN (SELECT * FROM {{ ref('lgn_a_sales_owner') }} WHERE isNull(subregion_code)) AS d ON d.country = l.des_country
WHERE l.des_status = 'Approved'
SETTINGS join_use_nulls = 1
