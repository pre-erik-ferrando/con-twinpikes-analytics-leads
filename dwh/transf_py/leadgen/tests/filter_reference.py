"""Python reference of the relevance filter (DT.4.4), for the tests only. The filter the engine uses is
the dbt view lgn_f_rule_evaluation (SQL); tests_db checks that the view decides exactly as this does,
and the in-memory store of the no-DB suite (ReferenceStore) decides with this.

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

from dataclasses import dataclass

from ..config import Config
from ..normalize import normalize_category, normalize_text
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


class ReferenceStore(MemoryStore):
    """The in-memory store with the filter's Python reference in place of the SQL view. `decide` forces
    a decision, for tests that need a business included in one profile and not in another."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.forced: dict[tuple[int, str | None], str] = {}

    def decide(self, business_id: int, profile: str | None, decision: str) -> None:
        self.forced[(business_id, profile)] = decision

    def filter_decisions(self, country, profile, business_ids=None, any_profile=False):
        out = reference_decisions(self, country, profile, business_ids, any_profile)
        for (b, p), decision in self.forced.items():
            if (any_profile or p == profile) and (business_ids is None or b in set(business_ids)):
                out[(b, p)] = {**out.get((b, p), {c: None for c in FILTER_COLUMNS}), "business_id": b, "profile": p,
                               "decision": decision, "is_closed_temporarily": 0}
        return out
