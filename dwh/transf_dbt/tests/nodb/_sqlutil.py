"""Resolves dbt's minimal Jinja so models can be parsed with sqlglot, without dbt or ClickHouse."""
import os
import re
import glob
import sqlglot
from sqlglot import exp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/


def sql_files():
    """Every .sql in the project: models + singular tests (recursive: tests live in layer folders)."""
    return sorted(
        glob.glob(os.path.join(REPO, "models", "**", "*.sql"), recursive=True)
        + glob.glob(os.path.join(_TESTS_DIR, "**", "*.sql"), recursive=True)
    )


def strip_jinja(s: str) -> str:
    """Replaces dbt's Jinja with parseable SQL (ref/source -> relation name)."""
    s = re.sub(r"\{#-.*?-#\}", "", s, flags=re.S)
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r"\{%-?.*?-?%\}", "", s, flags=re.S)
    s = re.sub(r"\{\{\s*config\([^}]*\)\s*\}\}", "", s, flags=re.S)
    s = re.sub(r"\{\{\s*ref\(\s*'([^']+)'\s*\)\s*\}\}", r"\1", s)
    s = re.sub(r"\{\{\s*source\(\s*'[^']+'\s*,\s*'([^']+)'\s*\)\s*\}\}", r"\1", s)
    # A var-conditional ref() resolves to its FIRST relation, enough for sqlglot to parse the FROM.
    s = re.sub(r"\{\{[^{}]*?ref\(\s*'([^']+)'\s*\)[^{}]*?\}\}", r"\1", s)
    s = re.sub(r"\{\{[^}]*\}\}", "'X'", s)
    return s.strip()


def model_sql(relpath: str) -> str:
    with open(os.path.join(REPO, relpath)) as fh:
        return strip_jinja(fh.read())


def outer_output_columns(sql_no_jinja: str):
    """Returns (explicit_columns, n_stars) of the outermost SELECT."""
    tree = sqlglot.parse_one(sql_no_jinja, dialect="clickhouse")
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    cols, stars = [], 0
    for e in sel.expressions:
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
            stars += 1
        else:
            cols.append(e.alias_or_name)
    return cols, stars
