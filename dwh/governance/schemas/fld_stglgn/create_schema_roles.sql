CREATE DATABASE IF NOT EXISTS fld_stglgn;

CREATE ROLE IF NOT EXISTS r_fld_stglgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_stglgn.* TO r_fld_stglgn;

CREATE ROLE IF NOT EXISTS r_fld_stglgn_read_all;
GRANT SELECT ON fld_stglgn.* TO r_fld_stglgn_read_all;
