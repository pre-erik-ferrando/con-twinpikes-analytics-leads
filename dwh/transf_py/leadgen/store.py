"""The storage seam. The engine reads and writes through `Store` with LOGICAL column names; the
ClickHouse store translates to physical (style A) names and types. `MemoryStore` implements the same
interface in memory, so the run steps are tested end to end without a database.

Everything is insert-only (DT.1.2): there is no update and no delete. Inserts are batched, never row by
row. `inserted_at` is the server's (DEFAULT now64(3)); the memory store imitates it.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

from . import ddl


class Store(Protocol):
    def insert(self, table: str, rows: list[dict]) -> None: ...
    def select(self, table: str, where: dict | None = None, columns: list[str] | None = None) -> list[dict]: ...
    def hash64(self, values: list[str]) -> dict[str, int]: ...
    def now(self) -> datetime: ...
    def lead_scores(self, country: str, profile: str | None, business_ids: Iterable[int] | None = None) -> dict[int, dict]: ...
    def missing_views(self, names: Iterable[str]) -> list[str]: ...


# The dbt views the engine reads (built with `python leadgen.py dbt`): the score is computed in SQL.
SCORE_VIEWS = ("lgn_f2_score", "lgn_f2_score_component")


def _prepare(table: ddl.Table, rows: Iterable[dict]) -> list[dict]:
    """Validates closed domains and column names, and resolves defaults the way the server would:
    a column with a DEFAULT that no row gives (inserted_at, loaded_at…) is left out of the insert; a
    NULL in a non-Nullable column with a literal DEFAULT becomes that default (a NULL would be rejected)."""
    rows = list(rows)
    ddl.validate_domains({table.name: rows})
    for r in rows:
        unknown = set(r) - set(table.names)
        if unknown:
            raise KeyError(f"{table.name}: unknown columns {sorted(unknown)}")
    cols = [c for c in table.columns
            if not (c.default is not None and all(r.get(c.name) is None for r in rows))]
    out = []
    for r in rows:
        row = {}
        for c in cols:
            v = r.get(c.name)
            if v is None and c.default is not None and not c.type.startswith("Nullable"):
                v = _default_value(c.default)
            if v is None and "Nullable(" not in c.type:     # ClickHouse rejects it: fail here, in tests too
                raise ValueError(f"{table.name}.{c.name} ({c.type}) is not Nullable: NULL given")
            row[c.name] = v
        out.append(row)
    return out


def _stringify(v):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _default_value(sql_default: str):
    """The Python value of a DDL DEFAULT literal (what the server would store)."""
    if sql_default == "[]":
        return []
    if sql_default.startswith("'") and sql_default.endswith("'"):
        return sql_default[1:-1]
    return int(sql_default) if sql_default.isdigit() else None


class MemoryStore:
    """In-memory Store for tests: inserted_at increases by 1 ms per insert call."""

    def __init__(self, start: datetime | None = None):
        self.tables: dict[str, list[dict]] = {}
        self._clock = start or datetime(2026, 10, 1, tzinfo=timezone.utc)
        self._tick = itertools.count(1)

    def _stamp(self) -> datetime:
        return self._clock + timedelta(milliseconds=next(self._tick))

    def now(self) -> datetime:
        """Later than every row inserted so far, as the server clock would be."""
        return self._stamp()

    def advance(self, **kw) -> None:
        self._clock += timedelta(**kw)

    def insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        t = ddl.ALL_TABLES[table]
        prepared = _prepare(t, rows)
        stamp = self._stamp()
        for r in prepared:
            if "inserted_at" in t.names and r.get("inserted_at") is None:
                r["inserted_at"] = stamp
            for c in t.columns:          # what the server's DEFAULT gives to a column left out
                if r.get(c.name) is None and c.default is not None and c.name not in ("inserted_at", "loaded_at"):
                    r[c.name] = _default_value(c.default)
            if t.db == ddl.DB["landing"]:
                r = {k: (v if k == "loaded_at" else _stringify(v)) for k, v in r.items()}
                r["loaded_at"] = r.get("loaded_at") or stamp
            self.tables.setdefault(table, []).append(r)

    def select(self, table: str, where: dict | None = None, columns: list[str] | None = None) -> list[dict]:
        rows = self.tables.get(table, [])
        if where:
            def ok(r):
                for k, v in where.items():
                    if isinstance(v, (list, tuple, set)):
                        if r.get(k) not in v:
                            return False
                    elif r.get(k) != v:
                        return False
                return True
            rows = [r for r in rows if ok(r)]
        rows = [dict(r) for r in rows]
        if columns:
            rows = [{c: r.get(c) for c in columns} for r in rows]
        return rows

    def hash64(self, values: list[str]) -> dict[str, int]:
        """A deterministic stand-in for ClickHouse's cityHash64, for tests only."""
        return {v: int.from_bytes(hashlib.blake2b(v.encode(), digest_size=8).digest(), "big") for v in values}

    def lead_scores(self, country, profile, business_ids=None) -> dict[int, dict]:
        """No SQL engine here, so no score: it is computed by the dbt view lgn_f2_score and tested on
        ClickHouse (tests_db). The in-memory flow runs without scores."""
        return {}

    def missing_views(self, names) -> list[str]:
        return []


class ClickHouseStore:
    """Store on ClickHouse through clickhouse-connect. Credentials from the DBT_CH_* variables (.env)."""

    def __init__(self, client=None):
        if client is None:
            import clickhouse_connect

            client = clickhouse_connect.get_client(
                host=os.environ["DBT_CH_HOST"], port=int(os.environ.get("DBT_CH_PORT", "8123")),
                username=os.environ.get("DBT_CH_USER", "default"), password=os.environ.get("DBT_CH_PASSWORD", ""),
                connect_timeout=int(os.environ.get("DBT_CH_CONNECT_TIMEOUT", "30")),
                settings={"join_use_nulls": 1})
        self.client = client

    def now(self) -> datetime:
        return self.client.query("SELECT now64(3)").result_rows[0][0]

    def command(self, sql: str):
        return self.client.command(sql)

    def init(self) -> list[str]:
        done = []
        for stmt in ddl.all_ddl():
            self.client.command(stmt)
            done.append(stmt.split("(")[0].split(" AS")[0].strip())
        return done

    def insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        t = ddl.ALL_TABLES[table]
        prepared = _prepare(t, rows)
        names = list(prepared[0])
        landing = t.db == ddl.DB["landing"]
        data = [[(_stringify(r[n]) if landing and n != "loaded_at" else r[n]) for n in names] for r in prepared]
        self.client.insert(t.fqn, data, column_names=[t.physical(n) for n in names])

    def select(self, table: str, where: dict | None = None, columns: list[str] | None = None) -> list[dict]:
        t = ddl.ALL_TABLES[table]
        cols = columns or t.names
        sql = "SELECT " + ", ".join(f"{t.physical(c)} AS {c}" for c in cols) + f" FROM {t.fqn}"
        params, conds = {}, []
        for i, (k, v) in enumerate((where or {}).items()):
            phys = t.physical(k)
            if v is None:
                conds.append(f"{phys} IS NULL")
            elif isinstance(v, (list, tuple, set)):
                params[f"p{i}"] = list(v)
                conds.append(f"{phys} IN %(p{i})s")
            else:
                params[f"p{i}"] = v
                conds.append(f"{phys} = %(p{i})s")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        res = self.client.query(sql, parameters=params)
        return [dict(zip(cols, row)) for row in res.result_rows]

    def lead_scores(self, country, profile, business_ids=None) -> dict[int, dict]:
        """business_id -> the live score (lgn_f2_score: total, rating, breakdown) and the bucket of each
        factor (lgn_f2_score_component), computed in SQL with the current configuration."""
        biz = ddl.DB["biz"]
        cond, params = "country = %(c)s AND profile = %(p)s", {"c": country, "p": profile}
        if business_ids is not None:
            ids = [int(b) for b in business_ids]
            if not ids:
                return {}
            cond += " AND business_id IN %(ids)s"
            params["ids"] = ids
        out = {}
        for b, t, r, bd in self.client.query(f"SELECT business_id, total, rating, breakdown FROM {biz}.lgn_f2_score "
                                             f"WHERE {cond}", parameters=params).result_rows:
            out[int(b)] = {"total": float(t), "rating": r, "breakdown": [float(x) for x in bd], "buckets": {}}
        for b, f, bk in self.client.query(f"SELECT business_id, factor, bucket_code FROM {biz}.lgn_f2_score_component "
                                          f"WHERE {cond}", parameters=params).result_rows:
            if int(b) in out:
                out[int(b)]["buckets"][f] = bk
        return out

    def missing_views(self, names) -> list[str]:
        """Which of these dbt views (database fld_bizlgn) do not exist: `python leadgen.py dbt` builds them."""
        names = list(names)
        rows = self.client.query("SELECT name FROM system.tables WHERE database = %(d)s AND name IN %(n)s",
                                 parameters={"d": ddl.DB["biz"], "n": names}).result_rows
        return sorted(set(names) - {r[0] for r in rows})

    def hash64(self, values: list[str]) -> dict[str, int]:
        if not values:
            return {}
        res = self.client.query("SELECT v, cityHash64(v) FROM (SELECT arrayJoin(%(v)s) AS v)",
                                parameters={"v": list(values)})
        return {v: int(h) for v, h in res.result_rows}
