"""What the run types share: geography, filter and customer match of a set of businesses. The score is
computed in SQL (dbt view lgn_f2_score) and read with store.lead_scores."""
from __future__ import annotations

import re

from ..config import Config
from ..filtering import FilterDecision, FilterInput, evaluate
from ..geo import AdminArea, AreaIndex, polygons_from_geojson
from ..matching import CustomerIndex, LeadFacts, MatchConfig, MatchResult, match, resolve_priority
from ..normalize import email_domain, name_keys, normalize_category, normalize_postal_code, normalize_text
from ..state import (BusinessView, RunContext, business_views, customer_index, is_suppressed, latest,
                     permanent_exclusions, suppressed)


# --------------------------------------------------------------------------- geography
def area_index(store, country: str, level: str) -> AreaIndex:
    rows = store.select("reference_geo", {"country": country, "admin_level": level})
    latest_rows = latest([{**r, "inserted_at": r.get("loaded_at")} for r in rows], ("admin_code",))
    areas = []
    for r in latest_rows.values():
        try:
            polys = polygons_from_geojson(r["geometry_json"])
        except (ValueError, TypeError, KeyError):
            continue
        areas.append(AdminArea(country, level, r["admin_code"], r.get("admin_name") or "", r.get("parent_code"), polys))
    return AreaIndex(areas)


def subregion_languages(cfg: Config) -> dict[str, str]:
    """subregion admin_code -> its first default language (Google category names arrive in it)."""
    out = {}
    for a in cfg.search_areas().values():
        if a.get("level") == "SUBREGION" and a.get("default_language"):
            out[a["admin_code"]] = a["default_language"].replace("/", ",").split(",")[0].strip()
    return out


# --------------------------------------------------------------------------- filter
def run_filter(ctx: RunContext, cfg: Config, views: dict[int, BusinessView]) -> dict[int, FilterDecision]:
    sup = suppressed(ctx.store)
    excl = permanent_exclusions(ctx.store, ctx.country, ctx.profile)
    langs = subregion_languages(cfg)
    rules_by_lang: dict[str | None, dict] = {}
    titles, stems = cfg.title_rules(), cfg.pool_stems()
    out, rows, seen_cats = {}, [], {}
    for bid, v in views.items():
        lang = langs.get(v.master.get("subregion_code") or "")
        if lang not in rules_by_lang:
            rules_by_lang[lang] = cfg.category_rules(lang)
        d = evaluate(FilterInput(bid, v.master.get("name") or "", v.categories, v.master.get("owner_description"),
                                 v.is_closed_permanently, v.is_closed_temporarily, is_suppressed(v, sup),
                                 excl.get(bid)),
                     rules_by_lang[lang], titles, stems, ctx.profile)
        out[bid] = d
        rows.append({"run_id": ctx.run_id, "snapshot_id": ctx.snapshot_id, "business_id": bid, "country": ctx.country,
                     "profile": ctx.profile, "decision": d.decision, "stage": d.stage, "rule": d.rule,
                     "trigger_value": d.trigger_value, "is_closed_temporarily": int(d.is_closed_temporarily)})
        for raw in v.categories:
            # keyed by the normalized form; the first raw spelling is kept for display
            seen_cats.setdefault((lang, normalize_category(raw)), [raw, 0])[1] += 1
    ctx.store.insert("rule_evaluation_out", rows)
    cat_rows = []
    for (lang, norm), (raw, n) in seen_cats.items():
        cat_rows.append({"run_id": ctx.run_id, "country": ctx.country, "profile": ctx.profile, "language": lang,
                         "category": raw, "category_normalized": norm,
                         "classification": rules_by_lang[lang].get(norm), "n_places": n})
    ctx.store.insert("category_seen_out", cat_rows)
    return out


def latest_filter(store, country: str, profile: str | None, business_ids: set[int] | None = None) -> dict[int, dict]:
    rows = [r for r in store.select("rule_evaluation_out", {"country": country})
            if r.get("profile") == profile and (business_ids is None or r["business_id"] in business_ids)]
    return {k[0]: v for k, v in latest(rows, ("business_id",)).items()}


# --------------------------------------------------------------------------- customer match
_PC_CITY = re.compile(r"\b(?:[A-Z]{1,2}-)?(\d{4,5}|\d{4}\s?[A-Z]{2})\s+([^\d,]+)")


def postal_city_from_address(address: str | None, country: str) -> tuple[str | None, str | None]:
    """Postal code and city out of a written address (the legal_address the website publishes)."""
    if not address:
        return None, None
    m = None
    for m in _PC_CITY.finditer(address):
        pass
    if not m:
        return None, None
    return normalize_postal_code(m.group(1), country), normalize_text(m.group(2)) or None


def facts_from_google(v: BusinessView) -> LeadFacts:
    m = v.master
    return LeadFacts(v.business_id, v.country, m.get("name_key_ae") or "", m.get("name_key_a") or "",
                     m.get("postal_code"), normalize_text(m.get("city")) or None, set(v.phones), set(v.domains),
                     latitude=m.get("latitude"), longitude=m.get("longitude"))


def facts_with_web(v: BusinessView, wv: dict, legal_forms: list[str]) -> LeadFacts:
    """Second pass (DT.5.4): Google data plus what the website gave (VAT, register, phone and email
    domain published, legal name and legal address)."""
    f = facts_from_google(v)
    if wv.get("vat_id"):
        f.vat_ids.add(wv["vat_id"])
    if wv.get("register_id"):
        f.register_ids.add(wv["register_id"])
    if wv.get("phone_e164"):
        f.phones.add(wv["phone_e164"])
    if email_domain(wv.get("email")):
        f.domains.add(email_domain(wv["email"]))
    if wv.get("final_domain"):
        f.domains.add(wv["final_domain"])
    if wv.get("legal_name"):
        ae, a = name_keys(wv["legal_name"], legal_forms)
        f.name_key_ae, f.name_key_a = ae or f.name_key_ae, a or f.name_key_a
    pc, city = postal_city_from_address(wv.get("legal_address"), v.country)
    f.postal_code, f.city_norm = pc or f.postal_code, city or f.city_norm
    return f


def match_row(ctx: RunContext, res: MatchResult, match_pass: str, extract_ids: str | None) -> dict:
    c = res.customer
    return {"run_id": ctx.run_id, "business_id": res.business_id, "country": ctx.country, "profile": ctx.profile,
            "match_pass": match_pass, "origin": "AUTO", "outcome": res.outcome,
            "signal": res.signal, "subsidiary_code": c.subsidiary_code if c else None,
            "customer_code": c.customer_code if c else None, "customer_name": c.customer_name if c else None,
            "similarity": res.similarity, "verdict": res.verdict, "candidates_json": res.candidates_json(),
            "n_candidates_dropped": res.n_candidates_dropped, "reason_text": res.reason_text,
            "extract_id": extract_ids}


def run_match(ctx: RunContext, cfg: Config, facts: list[LeadFacts], match_pass: str,
              index: CustomerIndex | None = None, extracts: dict | None = None) -> dict[int, MatchResult]:
    if index is None and extracts is None:
        index, extracts = customer_index(ctx.store, ctx.country)
    mc = MatchConfig.from_config(cfg)
    ext = ",".join(sorted(e["extract_id"] for e in (extracts or {}).values())) or None
    out = {f.business_id: match(f, index, mc) for f in facts}
    ctx.store.insert("customer_match_out", [match_row(ctx, r, match_pass, ext) for r in out.values()])
    return out


def deciding_matches(store, country: str, business_ids: set[int] | None = None) -> dict[int, dict]:
    """business_id -> the match row that decides (human/Salesforce > hard > model > soft), taken among the
    LATEST automatic pass and every manual correction."""
    rows = [r for r in store.select("customer_match_out", {"country": country})
            if business_ids is None or r["business_id"] in business_ids]
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["business_id"], []).append(r)
    out = {}
    for bid, rs in by.items():
        manual = [r for r in rs if r["origin"] in ("HUMAN", "SALESFORCE")]
        auto = [r for r in rs if r["origin"] == "AUTO"]
        last_auto = [max(auto, key=lambda r: r["inserted_at"])] if auto else []
        out[bid] = resolve_priority(manual + last_auto)
    return out


def is_customer(row: dict | None) -> bool:
    return bool(row) and row["outcome"] == "CUSTOMER"


def current_web(store, country: str, profile: str | None, business_ids: set[int] | None = None) -> dict[int, dict]:
    rows = [r for r in store.select("web_validation_out", {"country": country})
            if r.get("profile") == profile and (business_ids is None or r["business_id"] in business_ids)]
    return {k[0]: v for k, v in latest(rows, ("business_id",)).items()}


def capture_batch_leads(store, capture_batch_id: str) -> set[int]:
    return {r["business_id"] for r in store.select("capture_batch_lead", {"capture_batch_id": capture_batch_id})}
