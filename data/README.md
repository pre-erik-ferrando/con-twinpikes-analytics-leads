# `data` Folder

Local client data. **Not versioned** (`.gitignore`'s `data/*`): ask for the files. Suggested layout:

```
data/
├── inputs/        TWP's inputs Excel (frozen version), customer extracts per country, cockpit CSVs
├── geo/           official boundaries as GeoJSON (BKG VG250, Statistik Austria, swisstopo, Statbel, CBS)
└── client_docs/   anything else the client sends
```

Nothing reads this folder implicitly: every file is passed to a `leadgen.py` command
(`load-config`, `load-customers`, `load-cockpit`, `load-geo`, `import-list`), which keeps it as received
in `fld_landinglgn` before mapping it.
