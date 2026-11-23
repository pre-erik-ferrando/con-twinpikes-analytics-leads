{{ config(materialized='view') }}
-- A Fluidra customer of the latest extract of its subsidiary (DT.5.1.3). Test records stay out.
-- `country` is the ADDRESS country: the match uses every customer with an address in the lead's country.
SELECT
    c.country, c.subsidiary_code, c.customer_code, c.customer_name, c.name_key_ae, c.name_key_a, c.street,
    c.postal_code, c.city, c.phone_e164, c.vat_id, c.register_id, c.email, c.corporate_email_domain,
    c.customer_class, c.sales_rep_name, c.latitude, c.longitude, c.coordinate_precision, c.extract_id
FROM {{ source('engine', 'customer_norm_out') }} AS c
WHERE c.is_test_record = 0
  AND c.extract_id IN (
      SELECT argMax(f.file_id, f.loaded_at)
      FROM {{ source('landing', 'upload_file') }} AS f
      WHERE f.kind = 'CUSTOMER_EXTRACT'
      GROUP BY f.subsidiary_code)
