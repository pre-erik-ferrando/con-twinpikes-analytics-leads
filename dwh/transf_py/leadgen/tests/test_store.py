"""ClickHouseStore against a fake client: physical names, defaults left to the server, parameters."""
from __future__ import annotations

from leadgen.store import ClickHouseStore


class FakeClient:
    def __init__(self):
        self.inserts, self.queries = [], []

    def insert(self, table, data, column_names):
        self.inserts.append((table, data, column_names))

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))

        class R:
            result_rows = []
        return R()


def test_insert_uses_physical_names_and_leaves_server_defaults_out():
    c = FakeClient()
    ClickHouseStore(c).insert("biz_cfg_rating_band", [{"country": None, "rating": "HOT", "min_score": 30,
                                                       "updated_by": "seed"}])
    table, data, cols = c.inserts[0]
    assert table == "fld_bizlgn.biz_cfg_rating_band"
    assert cols == ["pk_country", "pk_rating", "val_min_score", "updated_by"]       # active, inserted_at: server
    assert data == [[None, "HOT", 30, "seed"]]


def test_null_in_a_defaulted_column_becomes_the_default():
    c = FakeClient()
    ClickHouseStore(c).insert("biz_cfg_search_term", [
        {"country": None, "language": "de", "profile": None, "term": "a", "preselected": 1, "updated_by": "x"},
        {"country": None, "language": "de", "profile": None, "term": "b", "preselected": None, "updated_by": "x"}])
    _, data, cols = c.inserts[0]
    assert [row[cols.index("val_preselected")] for row in data] == [1, 0]


def test_landing_values_are_text():
    c = FakeClient()
    ClickHouseStore(c).insert("llm_request", [{"custom_id": "b1_v3", "business_id": 1, "n_input_tokens_est": 900}])
    _, data, cols = c.inserts[0]
    assert "loaded_at" not in cols and data[0][cols.index("business_id")] == "1"


def test_select_translates_names_and_binds_parameters():
    c = FakeClient()
    ClickHouseStore(c).select("biz_lead_validation", {"business_id": [1, 2], "country": "AT", "comment": None},
                              columns=["business_id", "decision"])
    sql, params = c.queries[0]
    assert sql.startswith("SELECT pk_business_id AS business_id, des_decision AS decision FROM fld_bizlgn.")
    assert "pk_business_id IN %(p0)s" in sql and "des_country = %(p1)s" in sql and "des_comment IS NULL" in sql
    assert params == {"p0": [1, 2], "p1": "AT"}
