# dbt project (Lead Generator) — CLAUDE.md

Views only, over sources the engine creates (`python leadgen.py init`). Traps and layout: [README.md](README.md).

- `models/leads/_sources.yml` is generated: change `dwh/transf_py/leadgen/ddl.py`, then
  `cd dwh/transf_py && python -m leadgen.ddl > ../transf_dbt/models/leads/_sources.yml`.
- Columns of style A tables (biz_cfg_*, biz_lead_validation) are physical (`pk_`, `des_`, `val_`, `imp_`);
  the engine's `*_out` tables use logical names. lgn_v_* expose style A names to Intelek.
- The filter (lgn_f_rule_evaluation) and the score (lgn_f2_*) are decided HERE, the only implementation;
  tests_db checks them against tests/sql_reference.py and golden_scores.json. A rule decided in Python
  (match priority, exclusions, Salesforce mapping) is mirrored here only to READ its result;
  if a view re-derives one (lgn_f_customer_match, des_status), keep it equal to the Python
  (steps/common.deciding_matches, state.py) in the same change.
- Every view with a JOIN ends with `SETTINGS join_algorithm = 'hash'` (the default parallel_hash reserves
  memory per thread: 1.9 GB per query on a few rows), plus `join_use_nulls = 1` with a LEFT JOIN. Enforced by
  tests/nodb/test_project_refs.py.
