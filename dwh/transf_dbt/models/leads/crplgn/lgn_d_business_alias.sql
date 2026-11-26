{{ config(materialized='view') }}
-- Business absorbed in a merge -> the surviving one (DT.4.2). Validations, exclusions and Salesforce
-- results follow the alias. One level here; the engine follows chains (identity.resolve_alias). Per
-- business the latest ALIAS or UNALIAS counts: UNALIAS (a DUPLICATE rejection reopened) undoes the merge
-- (state.aliases).
SELECT
    a.business_id                                          AS old_id,
    argMax(assumeNotNull(a.other_business_id), a.inserted_at) AS surviving_id,
    argMax(a.reason, a.inserted_at)                        AS reason,
    max(a.inserted_at)                                     AS aliased_at
FROM {{ source('engine', 'business_identity_out') }} AS a
WHERE a.kind IN ('ALIAS', 'UNALIAS')
GROUP BY a.business_id
HAVING argMax(a.kind, a.inserted_at) = 'ALIAS'
