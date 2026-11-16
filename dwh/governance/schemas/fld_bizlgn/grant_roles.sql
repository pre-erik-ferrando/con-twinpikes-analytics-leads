-- The engine's user (leadgen.py and dbt) develops and writes; replace the placeholders before sending.
GRANT r_fld_bizlgn TO <usr_leadgen>;

-- Intelek reads the consumption views and the configuration, and writes the editable tables (DT.2.7).
GRANT r_fld_bizlgn_read_all TO <usr_intelek>;
GRANT INSERT ON fld_bizlgn.biz_lead_validation TO <usr_intelek>;
GRANT INSERT ON fld_bizlgn.biz_run_approval TO <usr_intelek>;
GRANT INSERT ON fld_bizlgn.biz_suppression_request TO <usr_intelek>;
GRANT INSERT ON fld_bizlgn.biz_run_request TO <usr_intelek>;
-- Configuration edited from Intelek: insert-only, a "delete" is a new row with val_active = 0.
-- One line per table if the instance does not support wildcard table grants (ClickHouse >= 24.x does).
GRANT INSERT ON fld_bizlgn.biz_cfg_* TO <usr_intelek>;
