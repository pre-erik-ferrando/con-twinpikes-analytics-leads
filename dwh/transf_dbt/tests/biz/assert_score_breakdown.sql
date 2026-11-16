-- The score never exceeds 37.5 and is the sum of its six factors in the cockpit's order (DT.7.4).
SELECT business_id, profile, total, breakdown
FROM {{ ref('lgn_f_score') }}
WHERE total > 37.5 OR length(breakdown) != 6 OR abs(arraySum(breakdown) - total) > 0.001
