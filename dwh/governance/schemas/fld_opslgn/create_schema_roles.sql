CREATE DATABASE IF NOT EXISTS fld_opslgn;

CREATE ROLE IF NOT EXISTS r_fld_opslgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_opslgn.* TO r_fld_opslgn;

CREATE ROLE IF NOT EXISTS r_fld_opslgn_read_all;
GRANT SELECT ON fld_opslgn.* TO r_fld_opslgn_read_all;
