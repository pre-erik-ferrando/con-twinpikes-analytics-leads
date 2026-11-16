{{ config(materialized='view') }}
-- One actor item as received, typed (DT.1.5.2). The engine reads the same JSON in Python
-- (apify.item_fields); this view is for audit and lineage. categoryName is never used (DT.3.2).
SELECT
    g.run_id                                                     AS run_id,
    g.query_id                                                   AS query_id,
    g.place_id                                                   AS place_id,
    JSONExtractString(g.item_json, 'title')                      AS title,
    JSONExtractString(g.item_json, 'address')                    AS address,
    JSONExtractString(g.item_json, 'city')                       AS city,
    JSONExtractString(g.item_json, 'postalCode')                 AS postal_code_raw,
    JSONExtractString(g.item_json, 'website')                    AS website,
    JSONExtractString(g.item_json, 'phoneUnformatted')           AS phone_raw,
    JSONExtract(g.item_json, 'location', 'lat', 'Nullable(Float64)') AS latitude,
    JSONExtract(g.item_json, 'location', 'lng', 'Nullable(Float64)') AS longitude,
    JSONExtract(g.item_json, 'categories', 'Array(String)')     AS categories,
    JSONExtract(g.item_json, 'reviewsCount', 'Nullable(UInt32)') AS reviews_count,
    JSONExtract(g.item_json, 'totalScore', 'Nullable(Float32)')  AS rating,
    toUInt8(JSONExtractBool(g.item_json, 'permanentlyClosed'))   AS is_closed_permanently,
    toUInt8(JSONExtractBool(g.item_json, 'temporarilyClosed'))   AS is_closed_temporarily,
    g.loaded_at                                                  AS loaded_at
FROM {{ source('landing', 'gmaps_place') }} AS g
