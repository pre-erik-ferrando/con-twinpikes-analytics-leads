CREATE DATABASE IF NOT EXISTS fld_bizlgn;

CREATE ROLE IF NOT EXISTS r_fld_bizlgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_bizlgn.* TO r_fld_bizlgn;

CREATE ROLE IF NOT EXISTS r_fld_bizlgn_read_all;
GRANT SELECT ON fld_bizlgn.* TO r_fld_bizlgn_read_all;
