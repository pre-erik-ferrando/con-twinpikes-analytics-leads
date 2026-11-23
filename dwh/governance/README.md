# governance/

Database governance: databases, roles and `GRANT`s, one folder per database.

```
governance/
├── schemas/
│   ├── fld_landinglgn/     landing (written by the engine: files, actor items, pages, model calls)
│   ├── fld_srclgn/         src views
│   ├── fld_stglgn/         stg views (current configuration)
│   ├── fld_crplgn/         crp views (business, place, customer, enrichment)
│   ├── fld_bizlgn/         configuration, Intelek writeback, engine outputs, lgn_v_* consumption views
│   └── fld_opslgn/         run control: runs, snapshots, queries, model batches, cost, exports
├── migrations/             hand-run changes to tables that already hold data (dated, run once, in order)
└── users/                  user creation
```

Each database folder has `create_schema_roles.sql` (database + development role `r_<db>` + read role
`r_<db>_read_all`) and `grant_roles.sql` (assignment to users; placeholders `<usr_…>`).

## ⚠️ None of this is run from this repo

The ClickHouse instance is not administered by this team (reached through an OpenVPN tunnel; whoever
operates it grants permissions). These files are **the request**, in SQL so what is needed is
unambiguous. **The `fld_` prefix is the permission's scope** ([PROPUESTA] in the definition, DT.1.3):
the GRANT has to be requested before the first `python leadgen.py init`. Changing it is not a code
change only: `LEADGEN_DB_PREFIX` (ddl.py), the `vars` of `dbt_project.yml` and every `+schema` follow it.
