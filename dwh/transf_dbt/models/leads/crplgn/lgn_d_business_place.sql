{{ config(materialized='view') }}
-- Listing -> business: the first link of each place_id stands (never relinked), then the alias.
SELECT
    l.place_id                                        AS place_id,
    coalesce(al.surviving_id, l.business_id)          AS business_id,
    l.business_id                                     AS original_business_id,
    l.link_reason                                     AS link_reason,
    l.linked_at                                       AS linked_at
FROM (
    SELECT
        b.place_id                                    AS place_id,
        argMin(b.business_id, b.inserted_at)          AS business_id,
        argMin(b.link_reason, b.inserted_at)          AS link_reason,
        min(b.inserted_at)                            AS linked_at
    FROM {{ source('engine', 'business_link_out') }} AS b
    GROUP BY b.place_id
) AS l
LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = l.business_id
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
