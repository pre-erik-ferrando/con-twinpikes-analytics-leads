-- The engine's user (leadgen.py and dbt) develops and writes; replace the placeholders before sending.
GRANT r_fld_opslgn TO <usr_leadgen>;
-- Intelek shows the run summary (lgn_v_run_summary reads ops).
GRANT r_fld_opslgn_read_all TO <usr_intelek>;
