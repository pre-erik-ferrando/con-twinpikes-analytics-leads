"""Python references of the dbt views the engine reads, for the tests only: the relevance filter
(lgn_f_rule_evaluation), the permanent exclusions (lgn_f_exclusion) and Salesforce's status file
(lgn_d_sf_status_row, lgn_d_sf_status). The engine uses the views (SQL); tests_db checks that each view
answers exactly as its reference here, and the in-memory store of the no-DB suite (ReferenceStore)
answers with these.

The filter (DT.4.4):
Deterministic, on what Google returns, before anything is spent on the model. The website content
validates what passes later (DT.6): two gates, cheap then expensive. When several categories or title
rules fire, the first in alphabetical order (normalized) is the one reported, as in the view.

Order of evaluation (DT.4.4.1), first match decides:
 1. permanently closed                                  -> EXCLUDE (CLOSED)
 2. suppressed, or permanently excluded before          -> EXCLUDE (SUPPRESSED / PREVIOUSLY_EXCLUDED)
 3. HARD title rule                                     -> EXCLUDE (TITLE_RULE, its reason)
 4. any INCLUDE category                                -> INCLUDE  (inclusion beats category exclusion)
 5. any CONDITIONAL category + pool signal in the title
    or the owner's description                          -> INCLUDE
 6. SOFT title rule                                     -> EXCLUDE (TITLE_RULE)
 7. any EXCLUDE category                                -> EXCLUDE (CATEGORY_RULE)
 8. nothing fired                                       -> EXCLUDE (DEFAULT_EXCLUDE)

HVAC needs no pool word (review DT.4.4): step 5 accepts any CONDITIONAL category for that profile; its
protection is the cost cap and max_leads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import Config
from ..normalize import normalize_category, normalize_month, normalize_text
from ..store import FILTER_COLUMNS, MemoryStore

PROFILES_WITHOUT_POOL_SIGNAL = ("HVAC",)


@dataclass
class FilterInput:
    business_id: int
    name: str
    categories: list[str]
    owner_description: str | None = None
    is_closed_permanently: bool = False
    is_closed_temporarily: bool = False
    is_suppressed: bool = False
    previous_exclusion: str | None = None     # reason of a permanent exclusion (lgn_f_exclusion)


@dataclass
class FilterDecision:
    business_id: int
    decision: str                  # INCLUDE | EXCLUDE
    stage: str | None              # DT.4.4.4 stage, None when included
    rule: str | None
    trigger_value: str | None
    is_closed_temporarily: bool
    unknown_categories: list[str]


def title_matches(title_norm: str, pattern_norm: str, match_type: str) -> bool:
    """TOKEN: whole word(s) (default); PREFIX: a word starts with it; SUBSTRING: anywhere (six letters
    or more, enforced by the precheck)."""
    if not pattern_norm:
        return False
    if match_type == "SUBSTRING":
        return pattern_norm in title_norm
    padded = f" {title_norm} "
    if match_type == "PREFIX":
        return f" {pattern_norm}" in padded
    return f" {pattern_norm} " in padded


def has_pool_signal(texts: list[str | None], stems: list[str]) -> str | None:
    """The first stem found at the start of a word in any text, or None."""
    for text in texts:
        words = normalize_text(text).split()
        for stem in stems:
            if any(w.startswith(stem) for w in words):
                return stem
    return None


def evaluate(inp: FilterInput, category_rules: dict[str, str], title_rules: list[dict], pool_stems: list[str],
             profile: str | None = "POOL_SPECIALIST") -> FilterDecision:
    cats = sorted({normalize_category(c) for c in inp.categories if c} - {""})
    unknown = [c for c in cats if c not in category_rules]
    title = normalize_text(inp.name)

    def out(decision, stage=None, rule=None, value=None):
        return FilterDecision(inp.business_id, decision, stage, rule, value, inp.is_closed_temporarily, unknown)

    if inp.is_closed_permanently:
        return out("EXCLUDE", "CLOSED", "CLOSED_PERMANENTLY", None)
    if inp.is_suppressed:
        return out("EXCLUDE", "SUPPRESSED", "SUPPRESSION_LIST", None)
    if inp.previous_exclusion:
        return out("EXCLUDE", "PREVIOUSLY_EXCLUDED", inp.previous_exclusion, None)

    title_rules = sorted(title_rules, key=lambda r: r["pattern_normalized"] or "")
    hard = [r for r in title_rules if r["rule_class"] == "HARD"]
    soft = [r for r in title_rules if r["rule_class"] == "SOFT"]
    for r in hard:
        if title_matches(title, r["pattern_normalized"], r.get("match_type") or "TOKEN"):
            return out("EXCLUDE", "TITLE_RULE", r["reason_code"], r["pattern"])

    included = next((c for c in cats if category_rules.get(c) == "INCLUDE"), None)
    if included:
        return out("INCLUDE", rule="CATEGORY_INCLUDE", value=included)

    conditional = next((c for c in cats if category_rules.get(c) == "CONDITIONAL"), None)
    if conditional:
        if profile in PROFILES_WITHOUT_POOL_SIGNAL:
            return out("INCLUDE", rule="CATEGORY_CONDITIONAL", value=conditional)
        stem = has_pool_signal([inp.name, inp.owner_description], pool_stems)
        if stem:
            return out("INCLUDE", rule="CATEGORY_CONDITIONAL_POOL_SIGNAL", value=f"{conditional} + {stem}")

    for r in soft:
        if title_matches(title, r["pattern_normalized"], r.get("match_type") or "TOKEN"):
            return out("EXCLUDE", "TITLE_RULE", r["reason_code"], r["pattern"])

    excluded = next((c for c in cats if category_rules.get(c) == "EXCLUDE"), None)
    if excluded:
        return out("EXCLUDE", "CATEGORY_RULE", "CATEGORY_EXCLUDE", excluded)
    if conditional:
        return out("EXCLUDE", "DEFAULT_EXCLUDE", "CATEGORY_CONDITIONAL_NO_POOL_SIGNAL", conditional)
    return out("EXCLUDE", "DEFAULT_EXCLUDE", "NO_RULE_FIRED", None)


# --------------------------------------------------------------------------- the store of the no-DB suite
def reference_decisions(store, country: str, profile: str | None, business_ids=None,
                        any_profile: bool = False) -> dict[tuple[int, str | None], dict]:
    """What lgn_f_rule_evaluation returns, computed in Python: the businesses of the capture batches of
    the profile (aliases resolved), with the current configuration."""
    from ..identity import resolve_alias
    from ..state import aliases, business_views, permanent_exclusions, suppressed
    from ..steps.common import subregion_languages

    al = aliases(store)
    pairs: dict[str | None, set[int]] = {}
    for r in store.select("capture_batch_lead", {"country": country}):
        if any_profile or r.get("profile") == profile:
            pairs.setdefault(r.get("profile"), set()).add(resolve_alias(r["business_id"], al))
    raw = {name: store.select(name) for name in _config_tables()}
    sup = suppressed(store)
    out = {}
    for prof, bids in pairs.items():
        if business_ids is not None:
            bids &= {int(b) for b in business_ids}
        cfg = Config.from_rows(raw, country, prof)
        views = business_views(store, country, bids)
        excl = permanent_exclusions(store, country, prof)
        langs = subregion_languages(cfg)
        titles, stems = cfg.title_rules(), cfg.pool_stems()
        for bid, v in views.items():
            from ..state import is_suppressed
            lang = langs.get(v.master.get("subregion_code") or "")
            d = evaluate(FilterInput(bid, v.master.get("name") or "", v.categories, v.master.get("owner_description"),
                                     v.is_closed_permanently, v.is_closed_temporarily, is_suppressed(v, sup),
                                     excl.get(bid)), cfg.category_rules(lang), titles, stems, prof)
            out[(bid, prof)] = {"business_id": bid, "profile": prof, "decision": d.decision, "stage": d.stage,
                                "rule": d.rule, "trigger_value": d.trigger_value,
                                "is_closed_temporarily": int(d.is_closed_temporarily)}
    assert all(set(r) == set(FILTER_COLUMNS) for r in out.values())
    return out


def _config_tables():
    from .. import ddl
    return ddl.CONFIG_TABLES


# --------------------------------------------------------------------------- Salesforce status file (DT.8.4)
def ref_sf_status_rows(store) -> list[dict]:
    """lgn_d_sf_status_row: every row of the status files, mapped with the current configuration."""
    import json

    from ..identity import resolve_alias
    from ..loaders import apply_mapping
    from ..state import aliases, latest

    cfg = Config.from_rows({n: store.select(n) for n in _config_tables()}, None, None)
    files = {f["file_id"]: f for f in store.select("upload_file", {"kind": "SF_STATUS"})}
    if not files:
        return []
    mapping = cfg.source_columns("SF_STATUS")
    maps = {r["map_name"]: cfg.value_map(r["map_name"]) for r in cfg.rows("biz_cfg_value_map")}
    al = aliases(store)
    exported = {r["lead_code"] for r in store.select("export_lead_out")}
    link = latest(store.select("business_link_out"), ("place_id",))
    zones: dict[int, set] = {}
    sub_of = {r["place_id"]: r.get("subregion_code") for r in store.select("place_norm_out")}
    for (pid,), r in link.items():
        zones.setdefault(resolve_alias(r["business_id"], al), set()).add(sub_of.get(pid))
    out = []
    for row in store.select("upload_row", {"file_id": list(files)}):
        c = apply_mapping(json.loads(row["row_json"]), mapping, "", maps)
        code = c.get("lead_code")
        m = re.fullmatch(r"([A-Z]{2})-(\d+)", code or "")
        known = code in exported and m is not None
        bid = resolve_alias(int(m.group(2)), al) if known else None
        issue = None if known and c.get("status") else ("UNMAPPED_STATUS" if known else "UNKNOWN_LEAD_CODE")
        own = zones.get(bid, set()) - {None}
        out.append({"status_file_id": row["file_id"], "row_no": int(row["source_row_id"]),
                    "imported_at": files[row["file_id"]].get("loaded_at"), "lead_code": code, "business_id": bid,
                    "country": m.group(1) if m else None, "sf_lead_id": c.get("sf_lead_id"),
                    "company_name": c.get("company_name"), "status": c.get("status"),
                    "status_month": normalize_month(c.get("status_month")) or "", "subregion_code": c.get("subregion_code"),
                    "discard_reason": c.get("discard_reason"), "issue": issue,
                    "zone_mismatch": int(issue is None and bool(c.get("subregion_code")) and bool(own)
                                         and c["subregion_code"] not in own)})
    return out


def ref_sf_status(store) -> list[dict]:
    """lgn_d_sf_status: per lead and month, the latest import's row without issue; is_current: one per
    business, the latest month, then the latest import, then the lowest lead code."""
    best: dict[tuple, dict] = {}
    for r in ref_sf_status_rows(store):
        if r["issue"] is None:
            k = (r["lead_code"], r["status_month"])
            if k not in best or (str(r["imported_at"]), r["row_no"]) > (str(best[k]["imported_at"]), best[k]["row_no"]):
                best[k] = r
    current: dict[int, tuple] = {}
    for k, r in best.items():
        when, code = (r["status_month"], str(r["imported_at"])), r["lead_code"]
        cur = current.get(r["business_id"])
        if cur is None or when > cur[0] or (when == cur[0] and code < cur[1]):
            current[r["business_id"]] = (when, code, k)
    keep = ("lead_code", "business_id", "country", "status_month", "status", "discard_reason", "subregion_code",
            "sf_lead_id", "company_name", "status_file_id", "imported_at")
    return [{**{c: r[c] for c in keep}, "is_current": int(current[r["business_id"]][2] == k)} for k, r in best.items()]


# --------------------------------------------------------------------------- permanent exclusions (DT.8.1)
def ref_exclusions(store) -> list[dict]:
    """lgn_f_exclusion: HUMAN, the business's current Sales Ops decision is EXCLUDED; SALESFORCE, DISCARDED
    in some month, for good (DT.8.4), with the reason of its latest such month."""
    from ..state import current_validations

    out = []
    for country in sorted({r["country"] for r in store.select("biz_lead_validation")}):
        for bid, v in current_validations(store, country).items():
            if v["decision"] == "EXCLUDED":
                out.append({"business_id": bid, "country": country, "scope": "ALL", "profile": v.get("profile"),
                            "origin": "HUMAN", "reason": v.get("exclusion_reason") or "NOT_VALID",
                            "source_ref": f"validation:{v['inserted_at']}", "excluded_at": v["inserted_at"]})
    discarded: dict[int, dict] = {}
    for r in sorted(ref_sf_status(store), key=lambda r: r["status_month"]):
        if r["status"] == "DISCARDED":
            discarded[r["business_id"]] = r
    for bid, r in discarded.items():
        out.append({"business_id": bid, "country": r["country"], "scope": "ALL", "profile": None,
                    "origin": "SALESFORCE", "reason": r.get("discard_reason") or "DISCARDED",
                    "source_ref": f"sf:{r['status_file_id']}:{r['lead_code']}", "excluded_at": r["imported_at"]})
    return out


REFERENCES = {"lgn_d_sf_status_row": ref_sf_status_rows, "lgn_d_sf_status": ref_sf_status,
              "lgn_f_exclusion": ref_exclusions}


class ReferenceStore(MemoryStore):
    """The in-memory store with the filter's Python reference in place of the SQL view. `decide` forces
    a decision, for tests that need a business included in one profile and not in another."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.forced: dict[tuple[int, str | None], str] = {}

    def decide(self, business_id: int, profile: str | None, decision: str) -> None:
        self.forced[(business_id, profile)] = decision

    def read_view(self, name, where=None):
        rows = REFERENCES[name](self)
        for k, v in (where or {}).items():
            rows = [r for r in rows if (r.get(k) in v if isinstance(v, (list, tuple, set)) else r.get(k) == v)]
        return rows

    def filter_decisions(self, country, profile, business_ids=None, any_profile=False):
        out = reference_decisions(self, country, profile, business_ids, any_profile)
        for (b, p), decision in self.forced.items():
            if (any_profile or p == profile) and (business_ids is None or b in set(business_ids)):
                out[(b, p)] = {**out.get((b, p), {c: None for c in FILTER_COLUMNS}), "business_id": b, "profile": p,
                               "decision": decision, "is_closed_temporarily": 0}
        return out
