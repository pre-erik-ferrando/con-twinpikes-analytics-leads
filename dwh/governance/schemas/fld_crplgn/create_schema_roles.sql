CREATE DATABASE IF NOT EXISTS fld_crplgn;

CREATE ROLE IF NOT EXISTS r_fld_crplgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_crplgn.* TO r_fld_crplgn;

CREATE ROLE IF NOT EXISTS r_fld_crplgn_read_all;
GRANT SELECT ON fld_crplgn.* TO r_fld_crplgn_read_all;
