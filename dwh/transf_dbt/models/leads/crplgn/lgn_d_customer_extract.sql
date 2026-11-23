{{ config(materialized='view') }}
-- One customer extract: subsidiary, date, rows and the share of customers with each field (DT.5.1).
-- Signals with no data in an extract are not applied: this view says which ones.
SELECT
    f.file_id                                                     AS extract_id,
    f.country                                                     AS country,
    f.subsidiary_code                                             AS subsidiary_code,
    f.source_system                                               AS source_system,
    toDateOrNull(f.extract_date)                                  AS extract_date,
    f.file_name                                                   AS file_name,
    f.loaded_by                                                   AS loaded_by,
    f.loaded_at                                                   AS loaded_at,
    count(c.customer_code)                                        AS n_customers,
    countIf(c.is_test_record = 1)                                 AS n_test_records,
    round(countIf(isNotNull(c.vat_id)) / nullIf(count(c.customer_code), 0), 3)                 AS share_vat,
    round(countIf(isNotNull(c.phone_e164)) / nullIf(count(c.customer_code), 0), 3)             AS share_phone,
    round(countIf(isNotNull(c.corporate_email_domain)) / nullIf(count(c.customer_code), 0), 3) AS share_domain,
    round(countIf(isNotNull(c.postal_code)) / nullIf(count(c.customer_code), 0), 3)            AS share_postal_code,
    round(countIf(c.coordinate_precision = 'ADDRESS') / nullIf(count(c.customer_code), 0), 3)  AS share_address_coordinates
FROM {{ source('landing', 'upload_file') }} AS f
LEFT JOIN {{ source('engine', 'customer_norm_out') }} AS c ON c.extract_id = f.file_id
WHERE f.kind = 'CUSTOMER_EXTRACT'
GROUP BY f.file_id, f.country, f.subsidiary_code, f.source_system, f.extract_date, f.file_name, f.loaded_by,
         f.loaded_at
SETTINGS join_use_nulls = 1
