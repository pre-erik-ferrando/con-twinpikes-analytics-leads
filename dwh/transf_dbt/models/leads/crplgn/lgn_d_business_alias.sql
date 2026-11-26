{{ config(materialized='view') }}
-- Business absorbed in a merge -> the surviving one (DT.4.2). Validations, exclusions, exports, website
-- analyses, customer matches and Salesforce results follow the alias. Per business the latest ALIAS or
-- UNALIAS counts: UNALIAS (a DUPLICATE rejection reopened) undoes the merge (state.aliases). Chains
-- (A -> B, then B -> C) are followed up to four links, as identity.resolve_alias follows them; a cycle
-- (A -> B -> A) stops where the engine stops.
WITH direct AS (
    SELECT
        a.business_id                                              AS old_id,
        argMax(assumeNotNull(a.other_business_id), a.inserted_at)  AS surviving_id,
        argMax(a.reason, a.inserted_at)                            AS reason,
        max(a.inserted_at)                                         AS aliased_at
    FROM {{ source('engine', 'business_identity_out') }} AS a
    WHERE a.kind IN ('ALIAS', 'UNALIAS')
    GROUP BY a.business_id
    HAVING argMax(a.kind, a.inserted_at) = 'ALIAS'
)
SELECT
    d1.old_id                                                                         AS old_id,
    -- resolve_alias: walk old -> n1 -> n2 ..., stop at the first one with no alias or already visited
    multiIf(isNull(d2.surviving_id) OR d1.surviving_id = d1.old_id, d1.surviving_id,
            isNull(d3.surviving_id) OR d2.surviving_id IN (d1.old_id, d1.surviving_id), d2.surviving_id,
            isNull(d4.surviving_id) OR d3.surviving_id IN (d1.old_id, d1.surviving_id, d2.surviving_id),
                d3.surviving_id,
            d4.surviving_id)                                                          AS surviving_id,
    d1.reason                                                                         AS reason,
    d1.aliased_at                                                                     AS aliased_at
FROM direct AS d1
LEFT JOIN direct AS d2 ON d2.old_id = d1.surviving_id
LEFT JOIN direct AS d3 ON d3.old_id = d2.surviving_id
LEFT JOIN direct AS d4 ON d4.old_id = d3.surviving_id
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
