CREATE DATABASE IF NOT EXISTS fld_srclgn;

CREATE ROLE IF NOT EXISTS r_fld_srclgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_srclgn.* TO r_fld_srclgn;

CREATE ROLE IF NOT EXISTS r_fld_srclgn_read_all;
GRANT SELECT ON fld_srclgn.* TO r_fld_srclgn_read_all;
