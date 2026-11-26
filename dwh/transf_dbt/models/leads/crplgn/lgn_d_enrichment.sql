{{ config(materialized='view') }}
-- What the website gave, already checked (DT.6.5): the latest analysis per business and profile, under
-- the surviving business (the latest of its own and its absorbed businesses', as steps.common.current_web).
SELECT
    q.business_id, q.profile, q.country, q.run_id, q.domain, q.final_domain, q.prompt_version, q.crawl_status,
    q.validation, q.review_reason, q.validation_reason_text, q.offering, q.pool_focus, q.company_type,
    q.legal_name, q.legal_address, q.vat_id, q.vat_check_status, q.register_id, q.contact_name, q.contact_role,
    q.email, q.phone_e164, q.phone_type, q.has_ecommerce, q.detected_brands, q.fluidra_brands, q.sources_json,
    q.n_unsupported_claims, q.is_indirect_customer, q.inserted_at AS analysed_at
FROM (
    SELECT w.* REPLACE (coalesce(al.surviving_id, w.business_id) AS business_id),
           row_number() OVER (PARTITION BY coalesce(al.surviving_id, w.business_id), w.profile
                              ORDER BY w.inserted_at DESC) AS rn
    FROM {{ source('engine', 'web_validation_out') }} AS w
    LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = w.business_id
) AS q
WHERE q.rn = 1
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
