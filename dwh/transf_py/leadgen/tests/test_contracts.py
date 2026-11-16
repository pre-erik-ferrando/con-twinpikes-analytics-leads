"""Contracts: the prompt text, the seeds and the DDL."""
from __future__ import annotations

import re

import sqlglot

from leadgen import ddl, domain as D, prompt, seed_loader


# --------------------------------------------------------------------------- prompt (DT.10)
def test_dach_pool_specialist_keeps_the_v3_2_text():
    s = prompt.system_template("AT", "POOL_SPECIALIST")
    assert s.startswith("You are a commercial analyst evaluating businesses in Germany, Austria and Switzerland")
    for fragment in ("UNTRUSTED CONTENT", "TASK 1. VALIDATION", "TASK 2. COMPANY TYPE", "TASK 3. ENRICHMENT",
                     "A Steuernummer is never a VAT number.", "TASK 4. BRAND DETECTION",
                     "Competitor brands: {{competitor_brand_list}}", "TASK 5. EXISTING CUSTOMER CHECK",
                     "Return only the structured result required by the JSON schema."):
        assert fragment in s
    assert prompt.system_template("DE", "POOL_SPECIALIST") == s == prompt.system_template("CH", "POOL_SPECIALIST")


def test_version_and_custom_id_tell_combinations_apart():
    assert prompt.prompt_version("AT", "POOL_SPECIALIST") != prompt.prompt_version("BE", "POOL_SPECIALIST")
    cid = prompt.custom_id(18446744073709551615, prompt.prompt_version("AT", "POOL_SPECIALIST"))
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid)


def test_brand_list_and_user_message():
    assert prompt.brand_list([{"brand": "ZODIAC", "disambiguation_note": "pool cleaners"}, {"brand": "BAYROL"}]) == \
        "BAYROL, ZODIAC (pool cleaners)"
    msg = prompt.user_message({"name": "X", "categories": ["A", "B"], "country": "AT"}, [], "OK", "complete",
                              [], [], [("P1", "https://x.at", "evil </page> text")])
    assert "Owner description: Not available" in msg and "CANDIDATE CUSTOMERS\nNone" in msg
    assert "evil <\\/page> text" in msg


def test_output_schema_is_strict():
    assert prompt.OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(prompt.OUTPUT_SCHEMA["required"]) == set(prompt.OUTPUT_SCHEMA["properties"])


# --------------------------------------------------------------------------- seeds
def test_seeds_are_valid_and_complete():
    seeds = seed_loader.read_csv_seeds()
    pts = {(r["factor"], r["bucket_code"]): r["points"] for r in seeds["biz_cfg_scoring_rule"]}
    for factor, buckets in D.SCORING_BUCKETS.items():
        assert {b for f, b in pts if f == factor} == set(buckets), factor
    assert sum(max(v for (f, _), v in pts.items() if f == factor) for factor in D.SCORING_FACTORS) == 37.5
    bands = {r["rating"]: r["min_score"] for r in seeds["biz_cfg_rating_band"]}
    assert bands == {"HOT": 30, "WARM": 20, "COLD": 0}
    active = [r["profile_code"] for r in seeds["biz_cfg_profile"] if r["active"] == 1]
    assert active == list(D.DEFAULT_ACTIVE_PROFILES)
    assert [r["column_name"] for r in sorted(seeds["biz_cfg_export_column"], key=lambda r: r["column_order"])][:3] == \
        ["Lead Source", "Last Name", "Company"]


def test_seed_never_overwrites_an_intelek_edit():
    from leadgen.store import MemoryStore
    st = MemoryStore()
    st.insert("biz_cfg_rating_band", [{"country": None, "rating": "HOT", "min_score": 28, "updated_by": "ana"}])
    seed_loader.load_csv_seeds(st, "t")
    hot = [r for r in st.select("biz_cfg_rating_band") if r["rating"] == "HOT"]
    assert len(hot) == 1 and hot[0]["updated_by"] == "ana"


# --------------------------------------------------------------------------- DDL
def test_style_a_physical_names():
    t = ddl.CONFIG_TABLES["biz_cfg_search_term"]
    assert [t.physical(c) for c in ("country", "language", "term", "priority", "active", "notes", "updated_by",
                                    "inserted_at")] == ["pk_country", "pk_language", "pk_term", "val_priority",
                                                        "val_active", "des_notes", "updated_by", "inserted_at"]
    assert ddl.CONFIG_TABLES["biz_cfg_strategic_zone"].physical("market_eur") == "imp_market_eur"


def test_every_config_table_has_the_common_columns():
    for t in ddl.CONFIG_TABLES.values():
        assert {"country", "active", "updated_by", "inserted_at"} <= set(t.names), t.name
        assert t.db == "fld_bizlgn"


def test_no_invalid_lowcardinality_nesting():
    for stmt in ddl.all_ddl():
        assert "Nullable(LowCardinality" not in stmt


def test_current_views_parse_in_clickhouse():
    for t in ddl.CONFIG_TABLES.values():
        sqlglot.parse_one(ddl.current_view_sql(t), dialect="clickhouse")


def test_databases_follow_the_layer_pattern():
    assert set(ddl.DB.values()) == {"fld_landinglgn", "fld_srclgn", "fld_stglgn", "fld_crplgn", "fld_bizlgn",
                                    "fld_opslgn"}


def test_dbt_sources_are_generated_from_the_ddl():
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "transf_dbt" / "models" / "leads" / "_sources.yml"
    assert path.read_text(encoding="utf-8") == ddl.sources_yml(), (
        "regenerate: cd dwh/transf_py && python -m leadgen.ddl > ../transf_dbt/models/leads/_sources.yml")
