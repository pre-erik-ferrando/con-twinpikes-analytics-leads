# tests/

- `nodb/`: pytest, no dbt and no ClickHouse: every model and test parses in the ClickHouse dialect
  (`test_sql_syntax.py`), every `ref()`/`source()` exists and every `alias.column` on a source is one of
  its columns (`test_project_refs.py`). Run by `./run_tests.sh` and CI.
- `biz/assert_*.sql`: singular dbt tests, run by `dbt test` against ClickHouse. Each returns the
  failing rows; its header says what it guards.

A rule that is checked in Python (engine) is tested in `dwh/transf_py/leadgen/tests/`, not here.
