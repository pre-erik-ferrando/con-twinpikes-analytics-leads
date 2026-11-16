{{ config(materialized='view') }}
-- One normalized Google listing (DT.1.5.4): its latest normalization, with first and last appearance.
-- Results accumulate by place_id: Google does not return the same thing twice (DT.3.4).
SELECT
    q.place_id, q.country, q.name, q.name_key_ae, q.name_key_a, q.address, q.street, q.city, q.postal_code,
    q.phone_e164, q.phone_type, q.website, q.domain, q.latitude, q.longitude, q.subregion_code,
    q.municipality_code, q.main_category, q.categories, q.owner_description, q.reviews_count, q.rating,
    q.is_closed_permanently, q.is_closed_temporarily, q.n_filled, q.first_seen_at, q.last_seen_at
FROM (
    SELECT
        p.*,
        -- fields that make a listing the business's master one (identity.pick_master)
        (isNotNull(p.name) + isNotNull(p.address) + isNotNull(p.latitude) + isNotNull(p.longitude)
         + isNotNull(p.website) + isNotNull(p.phone_raw) + isNotNull(p.postal_code_raw) + isNotNull(p.city)
         + isNotNull(p.owner_description))                                                   AS n_filled,
        min(p.inserted_at) OVER (PARTITION BY p.place_id)                                    AS first_seen_at,
        max(p.inserted_at) OVER (PARTITION BY p.place_id)                                    AS last_seen_at,
        row_number() OVER (PARTITION BY p.place_id ORDER BY p.inserted_at DESC)              AS rn
    FROM {{ source('engine', 'place_norm_out') }} AS p
) AS q
WHERE q.rn = 1
