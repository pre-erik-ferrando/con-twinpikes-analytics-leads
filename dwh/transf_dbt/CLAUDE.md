# dbt project (Lead Generator) — CLAUDE.md

Views only, over sources the engine creates (`python leadgen.py init`). Traps and layout: [README.md](README.md).

- `models/leads/_sources.yml` is generated: change `dwh/transf_py/leadgen/ddl.py`, then
  `cd dwh/transf_py && python -m leadgen.ddl > ../transf_dbt/models/leads/_sources.yml`.
- Columns of style A tables (biz_cfg_*, biz_lead_validation) are physical (`pk_`, `des_`, `val_`, `imp_`);
  the engine's `*_out` tables use logical names. lgn_v_* expose style A names to Intelek.
- A rule decided in Python (filter, match priority, score) is mirrored here only to READ its result;
  if a view re-derives one (lgn_f_customer_match, des_status), keep it equal to the Python
  (steps/common.deciding_matches, state.py) in the same change.
- Every view with a LEFT JOIN ends with `SETTINGS join_use_nulls = 1`.
