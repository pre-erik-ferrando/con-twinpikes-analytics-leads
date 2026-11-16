"""Table contracts: every table the engine or Intelek writes, in one place.

- Configuration tables biz_cfg_* (Annex A of the definition + the review's changes).
- Tables Intelek writes: biz_lead_validation, biz_run_approval, biz_suppression_request, biz_run_request.
- landing (raw, as received), ops (run control) and the engine's outputs (<step>_out, in biz).

dbt never creates these: a model would drop+create them and lose what was written. `leadgen.py init`
runs `all_ddl()` (CREATE ... IF NOT EXISTS, idempotent) before every run; dbt declares them as sources.

Column names: the code uses LOGICAL names everywhere. Tables Intelek reads or writes (style_a=True)
have PHYSICAL names in "style A" (DT.1.1.3): natural key -> pk_, text -> des_, money -> imp_, the rest
-> val_; inserted_at and updated_by unchanged. `physical()` derives them; store.py translates.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import domain as D

PREFIX = os.environ.get("LEADGEN_DB_PREFIX", "fld")
DB = {layer: f"{PREFIX}_{layer}lgn" for layer in ("landing", "src", "stg", "crp", "biz", "ops")}

NS = "Nullable(String)"
LC = "LowCardinality(String)"
# Annex A writes Nullable(LowCardinality(String)); ClickHouse only accepts the nesting the other way round.
NLC = "LowCardinality(Nullable(String))"
TS = "DateTime64(3)"


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    domain: tuple | None = None       # closed domain, validated on insert and by the precheck
    money: bool = False               # style A: imp_
    default: str | None = None


@dataclass
class Table:
    db: str
    name: str
    columns: list[Col]
    key: tuple[str, ...] = ()         # natural key (argMax resolution for config, pk_ in style A)
    order_by: tuple[str, ...] = ()
    partition_by: str | None = None
    style_a: bool = False
    scoped: bool = False              # config resolved per country/profile (config.scoped)
    comment: str = ""

    @property
    def fqn(self) -> str:
        return f"{self.db}.{self.name}"

    def col(self, name: str) -> Col:
        return next(c for c in self.columns if c.name == name)

    def physical(self, name: str) -> str:
        if not self.style_a or name in ("inserted_at", "updated_by"):
            return name
        c = self.col(name)
        if name in self.key:
            return f"pk_{name}"
        if c.money:
            return f"imp_{name}"
        if "String" in c.type and "Array" not in c.type or c.type.startswith("Array(String"):
            return f"des_{name}"
        return f"val_{name}"

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]


def _cfg(name: str, cols: list[Col], key: tuple[str, ...], comment: str) -> Table:
    common = [Col("country", NLC, D.COUNTRIES), *cols,
              Col("active", "UInt8", default="1"), Col("updated_by", "String"),
              Col("inserted_at", TS, default="now64(3)")]
    full_key = ("country", *key)
    return Table(DB["biz"], name, common, key=full_key, order_by=full_key, style_a=True, scoped=True,
                 comment=comment)


def C(name, type_=NS, domain=None, money=False, default=None):
    return Col(name, type_, domain, money, default)


PROFILE = C("profile", NLC, D.PROFILES)

# --------------------------------------------------------------------------- configuration (Annex A)
CONFIG_TABLES: dict[str, Table] = {t.name: t for t in [
    _cfg("biz_cfg_profile", [C("profile_code", LC, D.PROFILES), C("label", "String"), C("notes")],
         ("profile_code",), "Lead profiles; only active ones can be launched"),
    _cfg("biz_cfg_search_term", [C("language", LC, D.LANGUAGES), PROFILE, C("term", "String"),
                                 C("preselected", "UInt8", default="0"), C("priority", "UInt16", default="100"),
                                 C("notes")],
         ("language", "profile", "term"), "Search terms per country, language and profile (tab Profiles & keywords)"),
    _cfg("biz_cfg_search_area", [C("area_id", "String"), C("parent_area_id"), C("level", LC, D.AREA_LEVELS),
                                 C("subregion_type", NLC), C("admin_code", "String"), C("area_name", "String"),
                                 C("region_name"), C("default_language"), C("actor_fields", "String"),
                                 C("priority", "UInt16", default="100")],
         ("area_id",), "Search areas: hierarchy, actor fields and official code (tab Admin divisions)"),
    _cfg("biz_cfg_category_rule", [C("language", NLC, D.LANGUAGES), PROFILE, C("category", "String"),
                                   C("category_normalized", "String"),
                                   C("classification", LC, D.CATEGORY_CLASSES), C("notes")],
         ("language", "profile", "category_normalized"), "Google categories: INCLUDE, EXCLUDE, CONDITIONAL"),
    _cfg("biz_cfg_title_rule", [PROFILE, C("pattern", "String"), C("pattern_normalized", "String"),
                                C("match_type", LC, D.TITLE_MATCH_TYPES), C("rule_class", LC, D.TITLE_RULE_CLASSES),
                                C("reason_code", LC, D.TITLE_REASONS), C("notes")],
         ("profile", "pattern_normalized"), "Exclusions by name (tab Name exclusions)"),
    _cfg("biz_cfg_pool_signal", [C("language", LC, D.LANGUAGES), PROFILE, C("stem", "String")],
         ("language", "profile", "stem"), "Stems that signal a pool business (tab Pool words)"),
    _cfg("biz_cfg_brand", [C("brand", "String"), C("owner", LC, D.BRAND_OWNERS), C("brand_group", "Nullable(UInt8)"),
                           C("aliases", "Array(String)", default="[]"), C("disambiguation_note"), C("notes")],
         ("brand",), "Competitor brands (groups 1-3) and Fluidra brands (tab Brands)"),
    _cfg("biz_cfg_shared_domain", [C("domain", "String"), C("kind", LC, D.SHARED_DOMAIN_KINDS)],
         ("domain",), "Social networks, site builders, directories, marketplaces, email providers"),
    _cfg("biz_cfg_name_stopword", [C("language", NLC, D.LANGUAGES), C("word", "String")],
         ("language", "word"), "Generic sector words that do not make a name distinctive"),
    _cfg("biz_cfg_page_priority", [C("language", NLC, D.LANGUAGES), C("pattern", "String"),
                                   C("priority", "UInt16", default="100")],
         ("language", "pattern"), "URL path fragments crawled first"),
    _cfg("biz_cfg_matching_rule", [C("signal", LC, D.MATCH_SIGNALS), C("strength", LC, D.SIGNAL_STRENGTHS),
                                   C("threshold", "Nullable(Float32)"), C("notes")],
         ("signal",), "Customer matching signals and thresholds"),
    _cfg("biz_cfg_source_column", [C("source_system", LC), C("entity", LC, D.SOURCE_ENTITIES),
                                   C("subsidiary_code"), C("source_column", "String"), C("target_column", "String"),
                                   C("transform", LC, D.SOURCE_TRANSFORMS), C("value_map"),
                                   C("coordinate_precision", NLC, D.COORDINATE_PRECISIONS)],
         ("source_system", "source_column"), "Column mapping of every input file format"),
    _cfg("biz_cfg_strategic_zone", [C("admin_level", LC, D.AREA_LEVELS), C("admin_code", "String"),
                                    C("admin_name", "String"), C("quadrant", LC, D.QUADRANTS),
                                    C("market_eur", "Nullable(Float64)", money=True),
                                    C("fluidra_share", "Nullable(Float32)"), C("model_version", "String")],
         ("admin_level", "admin_code"), "TWP's quadrant of every subregion (tab Admin divisions, column Zone)"),
    _cfg("biz_cfg_scoring_rule", [PROFILE, C("factor", LC, D.SCORING_FACTORS), C("bucket_code", LC),
                                  C("bucket_description", "String"), C("points", "Decimal(5, 2)"),
                                  C("sort_order", "UInt8", default="0")],
         ("profile", "factor", "bucket_code"), "Points per factor and bucket (tab Scoring)"),
    _cfg("biz_cfg_rating_band", [C("rating", LC, D.RATINGS), C("min_score", "Decimal(5, 2)")],
         ("rating",), "Rating thresholds (>=), increasing from COLD to HOT"),
    _cfg("biz_cfg_validation_reason", [C("reason_code", LC, D.VALIDATION_REASONS), C("label", "String"),
                                       C("requires_customer_code", "UInt8", default="0"),
                                       C("requires_duplicate_of", "UInt8", default="0"),
                                       C("applies_to_profiles", "Array(String)", default="[]")],
         ("reason_code",), "Rejection reasons of the human validation (tab Rejection reasons)"),
    _cfg("biz_cfg_category_of_interest", [C("rule_order", "UInt16"), C("offering", NLC, D.OFFERINGS),
                                          C("company_type", NLC, D.COMPANY_TYPES),
                                          C("pool_focus", NLC, D.POOL_FOCUSES), C("category_of_interest", "String")],
         ("rule_order",), "Business type -> Salesforce Category of Interest; first matching row wins"),
    _cfg("biz_cfg_sales_owner", [C("subregion_code"), C("owner_first_name", "String"),
                                 C("owner_last_name", "String")],
         ("subregion_code",), "Salesforce owner per subregion; subregion_code NULL = country default"),
    _cfg("biz_cfg_export_column", [C("target", LC, D.EXPORT_TARGETS), C("column_order", "UInt16"),
                                   C("column_name", "String"), C("source_kind", LC, D.EXPORT_SOURCE_KINDS),
                                   C("source_value", "String"), C("source_field"), C("default_value"),
                                   C("format", LC, D.EXPORT_FORMATS)],
         ("target", "column_order"), "Columns of the output files (tab Salesforce and MC export)"),
    _cfg("biz_cfg_value_map", [C("map_name", LC), C("source_value", "String"), C("target_value", "String")],
         ("map_name", "source_value"), "Value translations when reading files and exporting"),
    _cfg("biz_cfg_legal_form", [C("form_normalized", "String")],
         ("form_normalized",), "Legal forms removed from names, longest first"),
    _cfg("biz_cfg_control_set", [C("source", LC), C("name", "String"), C("domain"), C("phone"),
                                 C("expected", LC, ("MUST_INCLUDE", "MUST_EXCLUDE")), C("expected_reason")],
         ("source", "name"), "Acceptance control set (DF.12)"),
    _cfg("biz_cfg_run_parameter", [C("parameter", LC), C("value", NS), C("value_type", LC, D.VALUE_TYPES),
                                   C("unit"), C("notes")],
         ("parameter",), "Spending caps, crawl limits, model and other technical parameters (Annex B.22)"),
]}

# --------------------------------------------------------------------------- written by Intelek
_PART = "(ifNull(country, ''), toYYYYMM(inserted_at))"
WRITEBACK_TABLES: dict[str, Table] = {t.name: t for t in [
    Table(DB["biz"], "biz_lead_validation", [
        C("business_id", "UInt64"), C("profile", LC, D.PROFILES), C("country", LC, D.COUNTRIES), C("run_id", "String"),
        C("capture_batch_id"), C("decision", LC, D.VALIDATION_DECISIONS),
        C("exclusion_reason", NLC, D.VALIDATION_REASONS), C("subsidiary_code"), C("customer_code"),
        C("duplicate_of", "Nullable(UInt64)"), C("comment"), C("approval_scope", NLC, D.APPROVAL_SCOPES),
        C("active_filter"), C("reviewed_by", "String"), C("inserted_at", TS, default="now64(3)")],
        key=("business_id", "profile"), order_by=("country", "business_id"), style_a=True,
        comment="Sales Ops decision per lead; current = argMax by (inserted_at, reviewed_by)"),
    Table(DB["biz"], "biz_run_approval", [
        C("run_id", "String"), C("approval_type", LC, D.APPROVAL_TYPES),
        C("approved_amount_usd", "Nullable(Decimal(10, 2))", money=True), C("comment"), C("approved_by", "String"),
        C("inserted_at", TS, default="now64(3)")],
        key=("run_id", "approval_type"), order_by=("run_id",), style_a=True,
        comment="Cost approval or stale customer extract approval"),
    Table(DB["biz"], "biz_suppression_request", [
        C("business_id", "Nullable(UInt64)"), C("place_id"), C("phone"), C("domain"), C("reason", "String"),
        C("requested_by", "String"), C("inserted_at", TS, default="now64(3)")],
        key=(), order_by=("inserted_at",), style_a=True, comment="Businesses that must not be shown again"),
    Table(DB["biz"], "biz_run_request", [
        C("request_id", "String"), C("run_type", LC, D.RUN_TYPES), C("country", LC, D.COUNTRIES), PROFILE,
        C("capture_batch_id"), C("search_terms", "Array(String)", default="[]"),
        C("area_ids", "Array(String)", default="[]"), C("max_places_per_search", "Nullable(UInt16)"),
        C("subdivide", "UInt8", default="0"), C("llm_mode", LC, D.LLM_MODES), C("max_leads", "Nullable(UInt32)"),
        C("requested_by", "String"), C("inserted_at", TS, default="now64(3)")],
        key=("request_id",), order_by=("inserted_at",), style_a=True,
        comment="Run requests, only used if Intelek cannot call the orchestrator"),
]}

# --------------------------------------------------------------------------- landing (as received)
def _landing(name, cols, order_by, comment):
    return Table(DB["landing"], name, [*[C(c) for c in cols], C("loaded_at", TS, default="now64(3)")],
                 order_by=order_by, comment=comment)


LANDING_TABLES: dict[str, Table] = {t.name: t for t in [
    _landing("gmaps_run", ["apify_run_id", "run_id", "query_id", "actor_build", "input_json", "status",
                           "cost_usd", "n_items", "started_at", "finished_at"], ("loaded_at",),
             "One actor call, with the complete input sent and its cost"),
    _landing("gmaps_place", ["run_id", "apify_run_id", "query_id", "place_id", "item_json"], ("place_id", "loaded_at"),
             "One actor item, complete JSON"),
    _landing("customer_extract_file", ["extract_id", "country", "subsidiary_code", "source_system", "extract_date",
                                       "file_name", "loaded_by"], ("loaded_at",), "One customer extract file"),
    _landing("customer_extract_row", ["extract_id", "source_row_id", "row_json"], ("extract_id",),
             "One extract row as received, columns as a JSON map of text"),
    _landing("cockpit_export_file", ["cockpit_file_id", "country", "exported_at", "file_name", "loaded_by"],
             ("loaded_at",), "One complete Market Cockpit CSV"),
    _landing("cockpit_export_row", ["cockpit_file_id", "source_row_id", "lead_code", "row_json"], ("cockpit_file_id",),
             "One cockpit row as received"),
    _landing("import_list_file", ["import_file_id", "country", "profile", "file_name", "loaded_by"], ("loaded_at",),
             "One own list of companies (Import list)"),
    _landing("import_list_row", ["import_file_id", "source_row_id", "row_json"], ("import_file_id",),
             "One row of an own list"),
    _landing("web_crawl", ["crawl_id", "run_id", "domain", "start_url", "final_url", "final_domain", "crawl_status",
                           "render_method", "n_pages", "n_bytes", "limits_json", "started_at", "finished_at"],
             ("domain", "loaded_at"), "One crawl of a domain"),
    Table(DB["landing"], "web_page", [C("crawl_id"), C("url"), C("depth"), C("status_code"), C("content_type"),
                                      Col("html", "Nullable(String) CODEC(ZSTD(3))"), C("text_chars"),
                                      C("content_hash"), C("fetched_at"), C("loaded_at", TS, default="now64(3)")],
          order_by=("crawl_id",), comment="One downloaded page, compressed HTML"),
    _landing("llm_request", ["batch_id", "custom_id", "run_id", "business_id", "profile", "prompt_version",
                             "brand_list_hash", "candidates_hash", "domain", "content_hash", "request_json",
                             "n_input_tokens_est"], ("custom_id",), "The exact request sent to the model"),
    _landing("llm_response", ["batch_id", "custom_id", "attempt", "status", "stop_reason", "response_json",
                              "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                              "error"], ("custom_id",),
             "The complete model response"),
    _landing("reference_geo", ["source", "country", "version", "admin_level", "admin_code", "admin_name",
                               "parent_code", "geometry_json"], ("admin_code",), "Official boundaries (GeoJSON)"),
    _landing("landing_meta", ["run_id", "entity", "n_rows"], ("loaded_at",), "Stamp per entity and run"),
]}

# --------------------------------------------------------------------------- ops (run control)
OPS_TABLES: dict[str, Table] = {t.name: t for t in [
    Table(DB["ops"], "pipeline_run", [C("run_id", "String"), C("run_type", LC, D.RUN_TYPES),
                                      C("state", LC, D.RUN_STATES), C("country", LC), PROFILE, C("capture_batch_id"),
                                      C("snapshot_id"), C("request_json", "String", default="''"), C("message"),
                                      C("requested_by", "String"), C("inserted_at", TS, default="now64(3)")],
          order_by=("run_id", "inserted_at"), comment="One row per state change of every run"),
    Table(DB["ops"], "config_snapshot", [C("snapshot_id", "String"), C("run_id", "String"), C("snapshot_at", TS),
                                         C("table_name", "String"), C("max_inserted_at", f"Nullable({TS})"),
                                         C("inserted_at", TS, default="now64(3)")],
          order_by=("snapshot_id",), comment="Per run and config table, max(inserted_at) at start"),
    Table(DB["ops"], "search_query_log", [C("run_id", "String"), C("query_id", "String"), C("capture_batch_id", "String"),
                                          C("country", LC), PROFILE, C("term", "String"), C("area_id", "String"),
                                          C("language", LC), C("max_places", "UInt16"), C("pass_no", "UInt8"),
                                          C("n_results", "UInt32"), C("n_new", "UInt32"),
                                          C("is_saturated", "UInt8"), C("is_saturated_at_leaf", "UInt8"),
                                          C("apify_run_id"), C("inserted_at", TS, default="now64(3)")],
          order_by=("run_id", "query_id"), comment="Every actor query"),
    Table(DB["ops"], "llm_batch", [C("batch_id", "String"), C("run_id", "String"), C("provider", LC), C("model", "String"),
                                   C("mode", LC, D.LLM_MODES), C("status", LC), C("external_id"),
                                   C("custom_ids", "Array(String)", default="[]"), C("n_requests", "UInt32"),
                                   C("cost_usd", "Nullable(Float64)"), C("inserted_at", TS, default="now64(3)")],
          order_by=("run_id", "batch_id"), comment="Each submission to the model; PREPARING written before the call"),
    Table(DB["ops"], "cost_ledger", [C("run_id", "String"), C("provider", LC), C("kind", LC, ("ESTIMATED", "ACTUAL")),
                                     C("amount_usd", "Float64"), C("detail"), C("inserted_at", TS, default="now64(3)")],
          order_by=("run_id",), comment="Estimated and actual cost per run and provider"),
    Table(DB["ops"], "suppression_list", [C("business_id", "Nullable(UInt64)"), C("place_id"), C("phone"), C("domain"),
                                          C("reason"), C("inserted_at", TS, default="now64(3)")],
          order_by=("inserted_at",), comment="Resolved from biz_suppression_request"),
    Table(DB["ops"], "seed_load", [C("seed_version", "String"), C("table_name", "String"), C("n_rows", "UInt32"),
                                   C("file_name"), C("overwrite", "UInt8", default="0"), C("loaded_by", "String"),
                                   C("inserted_at", TS, default="now64(3)")],
          order_by=("seed_version", "table_name"), comment="Each seed load"),
    Table(DB["ops"], "export_log", [C("export_id", "String"), C("run_id", "String"), C("country", LC),
                                    C("target", LC, D.EXPORT_TARGETS), C("file_name"), C("n_leads", "UInt32"),
                                    C("status", LC, D.EXPORT_STATUSES), C("generated_by", "String"),
                                    C("inserted_at", TS, default="now64(3)")],
          order_by=("export_id", "inserted_at"), comment="Each generated file and its upload confirmation"),
    Table(DB["ops"], "capture_batch", [C("capture_batch_id", "String"), C("run_id", "String"), C("country", LC),
                                       PROFILE, C("source", LC, D.CAPTURE_SOURCES),
                                       C("area_ids", "Array(String)", default="[]"),
                                       C("terms", "Array(String)", default="[]"), C("import_file_id"),
                                       C("snapshot_id", "String"), C("requested_by", "String"),
                                       C("inserted_at", TS, default="now64(3)")],
          order_by=("capture_batch_id",), comment="One capture batch (review: 'lote de captación')"),
    Table(DB["ops"], "capture_batch_lead", [C("capture_batch_id", "String"), C("business_id", "UInt64"),
                                            C("country", LC), PROFILE, C("run_id", "String"),
                                            C("inserted_at", TS, default="now64(3)")],
          order_by=("capture_batch_id", "business_id"), comment="A business belongs to one or more batches"),
]}

# --------------------------------------------------------------------------- engine outputs (biz)
def _out(name, cols, order_by, comment):
    cols = [*cols, C("inserted_at", TS, default="now64(3)")]
    has_country = any(c.name == "country" for c in cols)
    return Table(DB["biz"], name, cols, order_by=order_by, comment=comment,
                 partition_by=_PART if has_country else None)


OUT_TABLES: dict[str, Table] = {t.name: t for t in [
    _out("place_norm_out", [C("run_id", "String"), C("place_id", "String"), C("country", LC), C("name"),
                            C("name_key_ae", "String"), C("name_key_a", "String"), C("address"), C("street"),
                            C("city"), C("postal_code_raw"), C("postal_code"), C("phone_raw"), C("phone_e164"),
                            C("phone_type"), C("website"), C("domain"), C("latitude", "Nullable(Float64)"),
                            C("longitude", "Nullable(Float64)"), C("subregion_code"), C("municipality_code"),
                            C("main_category"), C("categories", "Array(String)", default="[]"),
                            C("owner_description"), C("reviews_count", "Nullable(UInt32)"),
                            C("rating", "Nullable(Float32)"), C("is_closed_permanently", "UInt8"),
                            C("is_closed_temporarily", "UInt8"), C("scraped_at")],
         ("country", "place_id"), "One normalized Google listing per capture"),
    _out("business_link_out", [C("place_id", "String"), C("business_id", "UInt64"), C("country", LC),
                               C("link_reason", LC), C("run_id", "String")],
         ("business_id", "place_id"), "Listing -> business (DT.4.2); never relinked"),
    _out("business_alias_out", [C("old_id", "UInt64"), C("surviving_id", "UInt64"), C("reason", LC),
                                C("run_id", "String")],
         ("old_id",), "Absorbed business -> surviving business"),
    _out("merge_suspect_out", [C("business_id", "UInt64"), C("other_business_id", "Nullable(UInt64)"),
                               C("place_id"), C("reason", LC), C("run_id", "String")],
         ("business_id",), "Businesses to be checked by a person (is_merge_suspect)"),
    _out("customer_norm_out", [C("extract_id", "String"), C("source_row_id", "String"), C("country", LC),
                               C("subsidiary_code"), C("customer_code", "String"), C("customer_name"),
                               C("name_key_ae", "String"), C("name_key_a", "String"), C("street"), C("postal_code"),
                               C("city"), C("phone_e164"), C("vat_id"), C("register_id"), C("email"),
                               C("corporate_email_domain"), C("customer_class"), C("sales_rep_name"),
                               C("latitude", "Nullable(Float64)"), C("longitude", "Nullable(Float64)"),
                               C("coordinate_precision", NLC), C("is_test_record", "UInt8")],
         ("country", "customer_code"), "Customer extract rows mapped and normalized (same functions as leads)"),
    _out("rule_evaluation_out", [C("run_id", "String"), C("snapshot_id", "String"), C("business_id", "UInt64"),
                                 C("country", LC), PROFILE, C("decision", LC, ("INCLUDE", "EXCLUDE")),
                                 C("stage", NLC, D.DISCARD_STAGES), C("rule"), C("trigger_value"),
                                 C("is_closed_temporarily", "UInt8", default="0")],
         ("country", "business_id"), "Relevance filter decision (DT.4.4)"),
    _out("category_seen_out", [C("run_id", "String"), C("country", LC), PROFILE, C("language"), C("category", "String"),
                               C("category_normalized", "String"), C("classification", NLC),
                               C("n_places", "UInt32")],
         ("country", "category_normalized"), "Categories seen per run, with their classification or NULL (new)"),
    _out("customer_match_out", [C("run_id", "String"), C("business_id", "UInt64"), C("country", LC), PROFILE,
                                C("match_pass", LC, D.MATCH_PASSES), C("origin", LC, D.MATCH_ORIGINS),
                                C("outcome", LC, ("CUSTOMER", "CANDIDATES", "NO_MATCH", "NO_EXTRACT")),
                                C("signal", NLC), C("subsidiary_code"), C("customer_code"), C("customer_name"),
                                C("similarity", "Nullable(Float32)"), C("verdict", NLC),
                                C("candidates_json", "String", default="'[]'"),
                                C("n_candidates_dropped", "UInt16", default="0"), C("reason_text"),
                                C("extract_id")],
         ("country", "business_id"), "Customer matching result per pass (DT.5)"),
    _out("web_validation_out", [C("run_id", "String"), C("business_id", "UInt64"), C("country", LC), PROFILE,
                                C("domain"), C("custom_id"), C("prompt_version"), C("crawl_status", NLC),
                                C("validation", LC, D.VALIDATIONS), C("review_reason", NLC, D.REVIEW_REASONS),
                                C("validation_reason_text"), C("offering", NLC, D.OFFERINGS),
                                C("pool_focus", NLC, D.POOL_FOCUSES), C("company_type", NLC, D.COMPANY_TYPES),
                                C("legal_name"), C("legal_address"), C("vat_id"),
                                C("vat_check_status", LC, D.VAT_CHECK_STATUSES), C("register_id"),
                                C("register_type"), C("register_court"), C("contact_name"), C("contact_role"),
                                C("email"), C("phone_e164"), C("phone_type"), C("phone_source", NLC),
                                C("has_ecommerce", "Nullable(UInt8)"),
                                C("detected_brands", "Array(String)", default="[]"),
                                C("fluidra_brands", "Array(String)", default="[]"),
                                C("sources_json", "String", default="'{}'"),
                                C("n_unsupported_claims", "UInt16", default="0"),
                                C("is_indirect_customer", "UInt8", default="0"), C("final_domain")],
         ("country", "business_id"), "Checked model answer per business, run and profile (DT.6.5)"),
    _out("score_component_out", [C("run_id", "String"), C("business_id", "UInt64"), C("country", LC), PROFILE,
                                 C("factor", LC, D.SCORING_FACTORS), C("bucket_code", LC), C("points", "Float64"),
                                 C("input_value"), C("review_reference_version")],
         ("country", "business_id"), "One row per business, run, profile and factor (DT.7.4)"),
    _out("score_out", [C("run_id", "String"), C("snapshot_id", "String"), C("business_id", "UInt64"),
                       C("country", LC), PROFILE, C("total", "Float64"), C("rating", LC, D.RATINGS),
                       C("breakdown", "Array(Float64)", default="[]"), C("review_reference_version")],
         ("country", "business_id"), "Total, Rating and breakdown in the cockpit's order"),
    _out("review_reference_out", [C("country", LC), C("reference_version", "String"), C("p75", "Float64"),
                                  C("median", "Float64"), C("n", "UInt32"), C("is_provisional", "UInt8")],
         ("country", "reference_version"), "Review cut-offs per country (DT.7.3)"),
    _out("exclusion_out", [C("business_id", "UInt64"), C("country", LC), C("scope", LC, ("ALL", "PROFILE")), PROFILE,
                           C("origin", LC, ("HUMAN", "COCKPIT", "ENGINE")), C("reason", "String"), C("source_ref")],
         ("country", "business_id"), "Permanent exclusions by business (DT.8.1)"),
    _out("export_lead_out", [C("export_id", "String"), C("business_id", "UInt64"), C("country", LC), PROFILE,
                             C("capture_batch_id"), C("lead_code", "String"), C("status", LC, D.EXPORT_STATUSES),
                             C("generated_by", "String")],
         ("country", "business_id"), "A lead in an export; Published once its export is UPLOADED"),
    _out("cockpit_outcome_out", [C("cockpit_file_id", "String"), C("lead_code", "String"),
                                 C("business_id", "Nullable(UInt64)"), C("country", LC),
                                 C("status", NLC, D.COCKPIT_STATUSES),
                                 C("not_qualified_reason", NLC, D.COCKPIT_REASONS), C("customer_code"),
                                 C("attempts", "Nullable(UInt16)"), C("potential_entered_eur", "Nullable(Float64)"),
                                 C("potential_indicative_eur", "Nullable(Float64)"),
                                 C("answers_json", "String", default="'{}'"),
                                 C("contact_corrections_json", "String", default="'{}'"), C("last_call_date"),
                                 C("last_comment"), C("exported_at")],
         ("country", "lead_code"), "Cockpit call results, mapped (DT.8.4)"),
]}

ALL_TABLES: dict[str, Table] = {**CONFIG_TABLES, **WRITEBACK_TABLES, **LANDING_TABLES, **OPS_TABLES, **OUT_TABLES}


# --------------------------------------------------------------------------- DDL
def create_table_sql(t: Table) -> str:
    cols = []
    for c in t.columns:
        line = f"    {t.physical(c.name)} {c.type}"
        if c.default is not None:
            line += f" DEFAULT {c.default}"
        cols.append(line)
    order = ", ".join(t.physical(c) for c in t.order_by) if t.order_by else "tuple()"
    nullable_key = any("Nullable" in t.col(c).type for c in t.order_by)
    sql = (f"CREATE TABLE IF NOT EXISTS {t.fqn} (\n" + ",\n".join(cols) + "\n)\nENGINE = MergeTree\n")
    if t.partition_by:
        sql += f"PARTITION BY {t.partition_by}\n"
    sql += f"ORDER BY ({order})\n"
    if nullable_key:
        sql += "SETTINGS allow_nullable_key = 1\n"
    if t.comment:
        sql += "COMMENT '" + t.comment.replace("'", "''") + "'"
    return sql


def current_view_sql(t: Table) -> str:
    """biz_cfg_<name>_current: the current row of each key with active = 1, what Intelek reads (DT.2.1)."""
    # Columns qualified with `src.`: an alias equal to a column name inside an aggregate
    # (argMax(x, …) AS x) is a cyclic alias for ClickHouse.
    key = [f"src.{t.physical(k)} AS {t.physical(k)}" for k in t.key]
    order = "(src.inserted_at, src.updated_by)"
    others = [f"    argMax(src.{t.physical(c)}, {order}) AS {t.physical(c)}" for c in t.names
              if c not in t.key and c != "inserted_at"]
    return (f"CREATE OR REPLACE VIEW {t.db}.{t.name}_current AS\nSELECT\n    "
            + ",\n    ".join(key) + ",\n" + ",\n".join(others)
            + ",\n    max(src.inserted_at) AS inserted_at\n"
            f"FROM {t.fqn} AS src\nGROUP BY {', '.join('src.' + t.physical(k) for k in t.key)}\n"
            f"HAVING {t.physical('active')} = 1")


def all_ddl() -> list[str]:
    stmts = [f"CREATE DATABASE IF NOT EXISTS {db}" for db in DB.values()]
    stmts += [create_table_sql(t) for t in ALL_TABLES.values()]
    stmts += [current_view_sql(t) for t in CONFIG_TABLES.values()]
    return stmts


SOURCE_GROUPS = (("landing", LANDING_TABLES), ("ops", OPS_TABLES), ("engine", OUT_TABLES),
                 ("writeback", {**CONFIG_TABLES, **WRITEBACK_TABLES}))


def sources_yml() -> str:
    """dwh/transf_dbt/models/leads/_sources.yml, generated (tests/test_contracts.py keeps it in sync):
    every table dbt reads and never manages, with its PHYSICAL column names."""
    lines = ["# GENERATED from dwh/transf_py/leadgen/ddl.py by `python -m leadgen.ddl > …/_sources.yml`.",
             "# Do not edit by hand: change ddl.py and regenerate.", "version: 2", "", "sources:"]
    for name, tables in SOURCE_GROUPS:
        db = next(iter(tables.values())).db
        lines += [f"  - name: {name}", f"    schema: {db}", "    tables:"]
        for t in tables.values():
            lines += [f"      - name: {t.name}", f"        description: \"{t.comment}\"", "        columns:"]
            lines += [f"          - name: {t.physical(c)}" for c in t.names]
    return "\n".join(lines) + "\n"


def validate_domains(tables: dict[str, list[dict]]) -> None:
    """Raises ValueError on the first closed-domain value out of its domain."""
    for name, rows in tables.items():
        t = ALL_TABLES.get(name)
        if t is None:
            continue
        for c in t.columns:
            if c.domain is None:
                continue
            for r in rows:
                v = r.get(c.name)
                if v is not None and v not in c.domain:
                    raise ValueError(f"{name}.{c.name}={v!r} is out of its domain ({', '.join(c.domain)})")


if __name__ == "__main__":
    import sys

    sys.stdout.write(sources_yml())
