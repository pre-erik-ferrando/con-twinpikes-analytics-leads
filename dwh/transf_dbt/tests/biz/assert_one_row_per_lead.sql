-- lgn_v_lead has one row per business and profile, also after merges (aliases resolved in every join).
SELECT pk_business_id, pk_profile, count() AS n
FROM {{ ref('lgn_v_lead') }}
GROUP BY pk_business_id, pk_profile
HAVING n > 1
