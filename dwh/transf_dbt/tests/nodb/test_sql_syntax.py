"""Syntax gate: every model and every singular test parses in the ClickHouse dialect."""
import os
import pytest
import sqlglot
from sqlglot.errors import ParseError

from _sqlutil import REPO, sql_files, strip_jinja


@pytest.mark.parametrize("path", sql_files(), ids=lambda p: os.path.relpath(p, REPO))
def test_parses_in_clickhouse(path):
    sql = strip_jinja(open(path).read())
    try:
        sqlglot.parse_one(sql, dialect="clickhouse")
    except ParseError as e:
        first = str(e).splitlines()[0]
        pytest.fail(f"{os.path.relpath(path, REPO)} does not parse in ClickHouse: {first}")
