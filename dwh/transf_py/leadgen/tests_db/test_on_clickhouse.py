"""The engine and the dbt views on a real ClickHouse (run_db_tests.sh locally, job `db` in CI).

The no-DB suite runs every step on an in-memory store; this checks what that store cannot: the DDL, the
inserts and reads through clickhouse-connect (types, NULLs, defaults), and every dbt model and test with
real data. It only runs against a throwaway server: it drops and recreates the fld_*lgn databases.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LEADGEN_TEST_CH") != "1" or os.environ.get("DBT_CH_HOST") != "localhost",
    reason="needs the throwaway ClickHouse of run_db_tests.sh or of CI (it drops the fld_*lgn databases)")

from leadgen import ddl, seed_loader  # noqa: E402
from leadgen.steps.capture import CaptureRequest, run_capture  # noqa: E402
from leadgen.steps.enrich import EnrichRequest, run_enrich  # noqa: E402
from leadgen.steps.export import confirm, run_export  # noqa: E402
from leadgen.steps.maintenance import approve_rest  # noqa: E402
from leadgen.store import ClickHouseStore  # noqa: E402

from ..tests.conftest import SQUARE, FakeCapturer, FakeProvider, _cfg_rows, crawler  # noqa: E402
from ..tests.test_flow import ITEMS  # noqa: E402

DBT_DIR = Path(__file__).resolve().parents[3] / "transf_dbt"


@pytest.fixture(scope="module")
def ch():
    st = ClickHouseStore()
    for db in ddl.DB.values():
        st.command(f"DROP DATABASE IF EXISTS {db}")
    st.init()
    seed_loader.load_csv_seeds(st, "test")
    typed = {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                           for r in rows]) for t, rows in _cfg_rows().items()}
    seed_loader.load(st, typed, "test-excel", "test", "synthetic")
    st.insert("reference_geo", [{"source": "TEST", "country": "AT", "version": "1", "admin_level": "SUBREGION",
                                 "admin_code": "501", "admin_name": "Salzburg(Stadt)",
                                 "geometry_json": json.dumps(SQUARE)}])
    return st


def _dbt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["dbt", *args, "--profiles-dir", "."], cwd=DBT_DIR, env=os.environ.copy(),
                          capture_output=True, text=True)


def test_full_flow_then_every_dbt_model_and_test(ch, tmp_path):
    ctx, cap = run_capture(ch, FakeCapturer(ITEMS),
                           CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501"], ["Poolbau"], "ops", 20))
    assert cap["found"] == 6                                     # as on the in-memory store
    _, enr = run_enrich(ch, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops", llm_mode="DIRECT"))
    assert enr["scored"] >= 1
    approve_rest(ch, ctx.capture_batch_id, "ops")
    _, xs = run_export(ch, ctx.capture_batch_id, "ops", tmp_path)
    assert xs["exported"] >= 1
    confirm(ch, xs["export_id"], "UPLOADED", "ops")

    for args in (("deps",), ("build",)):                          # every model, then every test, with data
        r = _dbt(*args)
        assert r.returncode == 0, f"dbt {' '.join(args)} failed:\n{r.stdout[-6000:]}"

    status = dict(ch.client.query("SELECT des_status, count() FROM fld_bizlgn.lgn_v_lead GROUP BY 1").result_rows)
    assert status.get("Published", 0) == xs["exported"]
    sf = ch.client.query("SELECT count() FROM fld_bizlgn.lgn_v_salesforce_export").result_rows[0][0]
    assert sf >= 0
