{{ config(materialized='view') }}
-- One real business (DT.4.2): the master listing (most filled fields, then most reviews) governs name,
-- address, coordinates and website; categories are the union; reviews the maximum, with that listing's
-- rating. Closed only if every listing is.
SELECT
    bp.business_id                                                            AS business_id,
    any(p.country)                                                            AS country,
    argMax(p.name, (p.n_filled, coalesce(p.reviews_count, 0)))                AS name,
    argMax(p.address, (p.n_filled, coalesce(p.reviews_count, 0)))             AS address,
    argMax(p.street, (p.n_filled, coalesce(p.reviews_count, 0)))              AS street,
    argMax(p.city, (p.n_filled, coalesce(p.reviews_count, 0)))                AS city,
    argMax(p.postal_code, (p.n_filled, coalesce(p.reviews_count, 0)))         AS postal_code,
    argMax(p.latitude, (p.n_filled, coalesce(p.reviews_count, 0)))            AS latitude,
    argMax(p.longitude, (p.n_filled, coalesce(p.reviews_count, 0)))           AS longitude,
    argMax(p.website, (p.n_filled, coalesce(p.reviews_count, 0)))             AS website,
    argMax(p.domain, (p.n_filled, coalesce(p.reviews_count, 0)))              AS domain,
    argMax(p.phone_e164, (p.n_filled, coalesce(p.reviews_count, 0)))          AS phone_e164,
    argMax(p.phone_type, (p.n_filled, coalesce(p.reviews_count, 0)))          AS phone_type,
    argMax(p.subregion_code, (p.n_filled, coalesce(p.reviews_count, 0)))      AS subregion_code,
    argMax(p.municipality_code, (p.n_filled, coalesce(p.reviews_count, 0)))   AS municipality_code,
    argMax(p.owner_description, (p.n_filled, coalesce(p.reviews_count, 0)))   AS owner_description,
    groupUniqArrayArray(p.categories)                                         AS categories,
    max(p.reviews_count)                                                      AS reviews_count,
    argMax(p.rating, coalesce(p.reviews_count, 0))                            AS rating,
    min(p.is_closed_permanently)                                              AS is_closed_permanently,
    max(p.is_closed_temporarily)                                              AS is_closed_temporarily,
    groupUniqArray(p.phone_e164)                                              AS phones,
    count()                                                                   AS n_listings,
    min(p.first_seen_at)                                                      AS first_seen_at,
    max(p.last_seen_at)                                                       AS last_seen_at,
    toUInt8(bp.business_id IN (
        SELECT s.business_id FROM {{ source('engine', 'merge_suspect_out') }} AS s))  AS is_merge_suspect
FROM {{ ref('lgn_d_business_place') }} AS bp
INNER JOIN {{ ref('lgn_d_place') }} AS p ON p.place_id = bp.place_id
GROUP BY bp.business_id
