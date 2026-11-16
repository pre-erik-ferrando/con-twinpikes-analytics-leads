-- Every lead has one of DF.4's six states (lgn_v_lead.des_status). Rows returned = failures.
SELECT pk_business_id, pk_profile, des_status
FROM {{ ref('lgn_v_lead') }}
WHERE des_status NOT IN ('Found', 'Excluded by engine', 'To review', 'Rejected', 'Approved', 'Published')
