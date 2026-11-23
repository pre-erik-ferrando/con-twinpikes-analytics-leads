"""Every ref() names a model of the project and every source() a table of models/leads/_sources.yml,
with only columns that table has (sources are generated from the engine's DDL). No DB."""
import os
import re

import pytest

from _sqlutil import REPO, sql_files

SOURCES = os.path.join(REPO, "models", "leads", "_sources.yml")


def _sources() -> dict[tuple[str, str], set[str]]:
    out, group, table = {}, None, None
    for line in open(SOURCES, encoding="utf-8"):
        m = re.match(r"^  - name: (\w+)", line)
        if m:
            group = m.group(1)
            continue
        m = re.match(r"^      - name: (\w+)", line)
        if m:
            table = m.group(1)
            out[(group, table)] = set()
            continue
        m = re.match(r"^          - name: (\w+)", line)
        if m:
            out[(group, table)].add(m.group(1))
    return out


MODELS = {os.path.splitext(os.path.basename(p))[0] for p in sql_files() if "/models/" in p}


@pytest.mark.parametrize("path", sql_files(), ids=lambda p: os.path.relpath(p, REPO))
def test_refs_and_sources_exist(path):
    sql = open(path, encoding="utf-8").read()
    for ref in re.findall(r"ref\(\s*'([^']+)'\s*\)", sql):
        assert ref in MODELS, f"ref('{ref}') is not a model"
    srcs = _sources()
    for group, table in re.findall(r"source\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", sql):
        assert (group, table) in srcs, f"source('{group}', '{table}') is not declared"


@pytest.mark.parametrize("path", [p for p in sql_files() if "/models/" in p], ids=lambda p: os.path.relpath(p, REPO))
def test_qualified_source_columns_exist(path):
    """`alias.column` on an alias bound to a source must be a column of that source."""
    sql = open(path, encoding="utf-8").read()
    srcs = _sources()
    for group, table, alias in re.findall(
            r"\{\{\s*source\('(\w+)',\s*'(\w+)'\)\s*\}\}\s+AS\s+(\w+)", sql):
        cols = srcs[(group, table)]
        for col in set(re.findall(rf"\b{alias}\.(\w+)", sql)):
            assert col in cols, f"{alias}.{col}: {table} has no column {col}"


@pytest.mark.parametrize("path", [p for p in sql_files() if "/models/" in p], ids=lambda p: os.path.relpath(p, REPO))
def test_joins_use_the_plain_hash_join(path):
    """A view with a JOIN ends with SETTINGS … join_algorithm = 'hash'. ClickHouse's default (parallel_hash)
    reserves memory per thread for every join: on a 22-core machine lgn_v_lead used 1.9 GB on a few rows,
    and a few views in parallel exhausted the server; with 'hash', 60 MB. The setting of the outer view
    reaches the views it reads, so Intelek's queries get it too. LEFT JOIN also needs join_use_nulls = 1."""
    sql = open(path, encoding="utf-8").read()
    if not re.search(r"\bJOIN\b", sql):
        return
    last = [line for line in sql.strip().splitlines() if line.strip()][-1]
    assert last.lstrip().startswith("SETTINGS") and "join_algorithm = 'hash'" in last, \
        f"{os.path.relpath(path, REPO)}: end it with SETTINGS join_algorithm = 'hash'"
    if re.search(r"\bLEFT\s+JOIN\b", sql):
        assert "join_use_nulls = 1" in last, f"{os.path.relpath(path, REPO)}: LEFT JOIN needs join_use_nulls = 1"
