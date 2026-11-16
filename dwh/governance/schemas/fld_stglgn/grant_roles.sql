-- The engine's user (leadgen.py and dbt) develops and writes; replace the placeholders before sending.
GRANT r_fld_stglgn TO <usr_leadgen>;
-- The lgn_v_* views run with the caller's rights (ClickHouse's default SQL SECURITY INVOKER): Intelek
-- needs to read the layers they read (stg, crp, ops), not only fld_bizlgn.
GRANT r_fld_stglgn_read_all TO <usr_intelek>;
