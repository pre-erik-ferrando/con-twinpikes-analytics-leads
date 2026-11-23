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

    # The score computed in SQL (lgn_f2_score) equals the engine's (score_out, via lgn_f_score), lead by lead
    py = {(b, p): (round(float(t), 2), r, [float(x) for x in bd]) for b, p, t, r, bd in ch.client.query(
        "SELECT business_id, profile, total, rating, breakdown FROM fld_bizlgn.lgn_f_score").result_rows}
    sql = {(b, p): (round(float(t), 2), r, [float(x) for x in bd]) for b, p, t, r, bd in ch.client.query(
        "SELECT business_id, profile, total, rating, breakdown FROM fld_bizlgn.lgn_f2_score").result_rows}
    assert py, "the flow scored nothing"
    assert sql == py


def test_sql_score_equals_python_on_every_bucket(ch):
    """40 synthetic German leads built to reach every bucket of every factor, with German configuration of
    their own (brand list, zones, a review minimum of 5 so the reference is computed, not provisional).
    Python scores them; the SQL view must give the same total, Rating and bucket per factor, lead by lead."""
    from leadgen.domain import SCORING_BUCKETS
    from leadgen.normalize import registrable_domain
    from leadgen.state import RunContext, business_views
    from leadgen.steps.common import current_web, score_businesses, scoring_context

    typed = {
        "biz_cfg_brand": [{"country": "DE", "brand": "DEPUMP", "owner": "COMPETITOR", "brand_group": 1},
                          {"country": "DE", "brand": "DECHEM", "owner": "COMPETITOR", "brand_group": 2},
                          {"country": "DE", "brand": "DEROBOT", "owner": "COMPETITOR", "brand_group": 3}],
        "biz_cfg_strategic_zone": [{"country": "DE", "admin_level": "SUBREGION", "admin_code": c, "admin_name": c,
                                    "quadrant": q, "model_version": "test"}
                                   for c, q in [("D1", "ATTACK"), ("D2", "ACTIVATE"), ("D3", "DEFEND"), ("D4", "OPTIMIZE")]],
        "biz_cfg_run_parameter": [{"country": "DE", "parameter": "review_reference_min_n", "value": "5",
                                   "value_type": "INT"}],
    }
    seed_loader.load(ch, {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                                        for r in rows]) for t, rows in typed.items()},
                     "test-de", "test", "synthetic-de")
    builders = {"wixsite.com", "jimdofree.com", "jimdosite.com"}
    websites = [None, "https://www.firm{i}.de/", "https://firm{i}.wixsite.com/pool", "https://www.facebook.com/firm{i}",
                "https://shop.firm{i}.de/"]
    subregions = ["D1", "D2", "D3", "D4", None, "D9"]
    reviews = [None, 0, 1, 3, 8, 15, 40, 120]
    brands = [[], ["DEPUMP"], ["DECHEM"], ["DEROBOT"], ["DEROBOT", "DECHEM"], ["SPECK"], ["DEPUMP", "SPECK"]]
    types = ["BUILDER_INSTALLER_SHOP", "MAINTENANCE_OR_PREFAB", "WEAK_UNVERIFIED", None]
    crawl = ["OK", "NO_WEBSITE", "SOCIAL_ONLY", "UNREACHABLE", None]
    places, links, webs, matches = [], [], [], []
    for i in range(40):
        bid, pid = 9_000_000 + i, f"de-p{i}"
        site = websites[i % 5]
        site = site.format(i=i) if site else None
        places.append({"run_id": "synthetic-de", "place_id": pid, "country": "DE", "name": f"Firm {i}",
                       "name_key_ae": f"firm {i}", "name_key_a": f"firm {i}", "website": site,
                       "domain": registrable_domain(site, builders) if site else None,
                       "phone_e164": None if i % 5 == 0 else f"+4930{1000 + i}", "subregion_code": subregions[i % 6],
                       "reviews_count": reviews[i % 8], "is_closed_permanently": 0, "is_closed_temporarily": 0})
        links.append({"place_id": pid, "business_id": bid, "country": "DE", "link_reason": "NEW", "run_id": "synthetic-de"})
        webs.append({"run_id": "synthetic-de", "business_id": bid, "country": "DE", "profile": "POOL_SPECIALIST",
                     "validation": ["VALID", "VALID", "REVIEW", "VALID", "NOT_VALID"][i % 5],
                     "vat_check_status": "NOT_CHECKED", "company_type": types[i % 4], "detected_brands": brands[i % 7],
                     # moduli chosen so the factors do not move in step (every bucket is reached)
                     "has_ecommerce": [1, 0, None][i % 3], "crawl_status": crawl[(i // 5) % 5],
                     "contact_name": "Max" if i % 3 == 0 else None, "email": "a@b.de" if i % 4 != 2 else None,
                     "phone_e164": "+49301234" if i % 6 == 0 else None})
        if i % 7 == 0:                                   # customers: not part of the review reference
            matches.append({"run_id": "synthetic-de", "business_id": bid, "country": "DE", "match_pass": "CAPTURE",
                            "origin": "AUTO", "outcome": "CUSTOMER", "signal": "PHONE"})
    ch.insert("place_norm_out", places)
    ch.insert("business_link_out", links)
    ch.insert("customer_match_out", matches)
    ch.insert("web_validation_out", webs)

    ids = {9_000_000 + i for i in range(40)}
    ctx = RunContext(ch, "run_synthetic_de", "ENRICH", "DE", "POOL_SPECIALIST", "test", snapshot_id="snap_synthetic_de")
    cfg = ctx.config()
    sc = scoring_context(ctx, cfg)
    assert not sc.ref.is_provisional                     # the reference is computed from these leads
    score_businesses(ctx, cfg, business_views(ch, "DE", ids), current_web(ch, "DE", "POOL_SPECIALIST", ids), sc)

    r = _dbt("run", "--select", "+lgn_f2_score", "+lgn_f_score", "+lgn_f3_score_component")
    assert r.returncode == 0, r.stdout[-6000:]
    q = ch.client.query
    py = {(b, p): (round(float(t), 2), rt, [float(x) for x in bd]) for b, p, t, rt, bd in q(
        "SELECT business_id, profile, total, rating, breakdown FROM fld_bizlgn.lgn_f_score WHERE country = 'DE'").result_rows}
    sql = {(b, p): (round(float(t), 2), rt, [float(x) for x in bd]) for b, p, t, rt, bd in q(
        "SELECT business_id, profile, total, rating, breakdown FROM fld_bizlgn.lgn_f2_score WHERE country = 'DE'").result_rows}
    assert len(py) == 32 and sql == py                   # 40 leads, 8 NOT_VALID never scored
    pyb = {(b, f): bk for b, f, bk in q("SELECT business_id, factor, bucket_code FROM fld_bizlgn.lgn_f3_score_component "
                                       "WHERE country = 'DE'").result_rows}
    sqlb = {(b, f): bk for b, f, bk in q("SELECT business_id, factor, bucket_code FROM fld_bizlgn.lgn_f2_score_component "
                                        "WHERE country = 'DE'").result_rows}
    assert sqlb == pyb
    seen = {(f, bk) for (_, f), bk in sqlb.items()}
    missing = [(f, bk) for f, bks in SCORING_BUCKETS.items() for bk in bks if (f, bk) not in seen]
    assert not missing, f"buckets the synthetic leads never reach: {missing}"
    ratings = {rt for _, rt, _ in sql.values()}
    assert ratings == {"HOT", "WARM", "COLD"}, ratings
