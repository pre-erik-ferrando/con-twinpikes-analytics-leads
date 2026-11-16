CREATE DATABASE IF NOT EXISTS fld_landinglgn;

CREATE ROLE IF NOT EXISTS r_fld_landinglgn;
GRANT CREATE, DROP, SELECT, INSERT, ALTER ON fld_landinglgn.* TO r_fld_landinglgn;

CREATE ROLE IF NOT EXISTS r_fld_landinglgn_read_all;
GRANT SELECT ON fld_landinglgn.* TO r_fld_landinglgn_read_all;
