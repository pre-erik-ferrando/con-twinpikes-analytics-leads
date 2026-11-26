"""What the run types share: geography, filter and customer match of a set of businesses. The filter and
the score are computed in SQL (dbt views lgn_f_rule_evaluation and lgn_f2_score) and read through the
store (filter_decisions, lead_scores)."""
from __future__ import annotations

import re

from ..config import Config
from ..geo import AdminArea, AreaIndex, polygons_from_geojson
from ..matching import CustomerIndex, LeadFacts, MatchConfig, MatchResult, match, resolve_priority
from ..normalize import email_domain, name_keys, normalize_postal_code, normalize_text
from ..state import BusinessView, RunContext, customer_index, latest


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
def run_filter(ctx: RunContext, business_ids: set[int]) -> dict[int, dict]:
    """The relevance filter (DT.4.4) of these businesses for the run's profile, computed in SQL by the dbt
    view lgn_f_rule_evaluation with the current configuration. The businesses must already be in a capture
    batch of the profile (capture_batch_lead): that is what the view evaluates."""
    return latest_filter(ctx.store, ctx.country, ctx.profile, business_ids)


def latest_filter(store, country: str, profile: str | None, business_ids: set[int] | None = None) -> dict[int, dict]:
    """business_id -> the current filter decision (lgn_f_rule_evaluation) for the profile."""
    return {b: r for (b, _p), r in store.filter_decisions(country, profile, business_ids).items()}


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
    LATEST automatic pass, the LATEST Sales Ops correction (none if it is RETRACTED: Sales Ops reopened the
    lead) and every Salesforce correction."""
    rows = [r for r in store.select("customer_match_out", {"country": country})
            if business_ids is None or r["business_id"] in business_ids]
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["business_id"], []).append(r)
    out = {}
    for bid, rs in by.items():
        manual = [r for r in rs if r["origin"] == "SALESFORCE"]
        human = [r for r in rs if r["origin"] == "HUMAN"]
        last_human = max(human, key=lambda r: r["inserted_at"]) if human else None
        if last_human and last_human["outcome"] != "RETRACTED":
            manual.append(last_human)
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
