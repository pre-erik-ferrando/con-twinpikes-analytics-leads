"""CAPTURE (review DT.9): Google Maps capture, normalization, identity, filter and first customer
match. Creates a capture batch and estimates the cost of enriching it. Old phase A of the v2.2 run.

IMPORT_LIST (review DT.3.5) [PROPUESTA]: one query per row of an own list (name + country, and the city
if given), at most 3 results; the result with the highest name similarity (>= import_match_min_similarity)
or matching phone/domain is kept; below the threshold the row is UNMATCHED, two tied rows AMBIGUOUS.
Matched listings enter a capture batch with source IMPORT and follow the normal flow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from rapidfuzz.fuzz import token_sort_ratio

from .. import apify, cost, llm, runs
from ..config import precheck
from ..geo import outline_geojson
from ..identity import Business, Listing, assign, resolve_alias
from ..normalize import (name_keys, normalize_phone, normalize_postal_code, registrable_domain)
from ..state import (RunContext, business_views, customer_index, extract_age_days, links, aliases,
                     sync_human_decisions)
from .common import area_index, facts_from_google, run_filter, run_match


@dataclass
class CaptureRequest:
    country: str
    profile: str
    area_ids: list[str]
    terms: list[str]
    requested_by: str
    max_places_per_search: int | None = None
    subdivide: bool = False
    import_rows: list[dict] = field(default_factory=list)     # IMPORT_LIST only
    import_file_id: str | None = None
    approve_stale_extract: bool = False                        # audited override of the extract-age block
    approve_cost_usd: float | None = None                      # Apify estimate approved with the request


class RunFailed(Exception):
    pass


def _fail(ctx: RunContext, message: str):
    ctx.set_state("FAILED", message)
    raise RunFailed(message)


def _running_areas(store, exclude_run: str) -> dict[str, list[str]]:
    states = runs.in_progress(runs.current_states(store.select("pipeline_run")))
    out = {}
    for run_id, st in states.items():
        if run_id == exclude_run or st["run_type"] not in ("CAPTURE", "IMPORT_LIST"):
            continue
        first = min(store.select("pipeline_run", {"run_id": run_id}), key=lambda r: r["inserted_at"])
        req = json.loads(first.get("request_json") or "{}")
        out[run_id] = req.get("area_ids", [])
    return out


def start(store, run_type: str, req: CaptureRequest) -> tuple[RunContext, object]:
    """REQUESTED -> SNAPSHOT_FROZEN -> checks. Returns the context and the run's configuration."""
    ctx = RunContext(store, runs.new_id("run"), run_type, req.country, req.profile, req.requested_by,
                     capture_batch_id=runs.new_id("cb"))
    ctx.set_state("REQUESTED", request={k: v for k, v in req.__dict__.items() if k != "import_rows"})
    sync_human_decisions(ctx)
    ctx.freeze_snapshot()
    ctx.set_state("SNAPSHOT_FROZEN")
    cfg = ctx.config()
    pre = precheck(cfg, req.area_ids if run_type == "CAPTURE" else None,
                   req.terms if run_type == "CAPTURE" else None, needs_scoring=False)
    for w in pre.warnings:
        ctx.log.append(f"warning: {w}")
    if pre.blocking:
        _fail(ctx, "precheck: " + "; ".join(pre.blocking))

    conflicts = runs.area_conflicts(req.area_ids, _running_areas(store, ctx.run_id), cfg.search_areas())
    if conflicts:
        c = conflicts[0]
        _fail(ctx, f"area {c.area_id} overlaps {c.other_area_id} of run {c.run_id}, still in progress")

    # Customer extract age (definition v3.3 DT.5): block above customer_extract_max_age_days unless approved.
    _, extracts = customer_index(store, req.country)
    age = extract_age_days(extracts, req.country, store.now())
    max_age = cfg.param("customer_extract_max_age_days", 7)
    warn_age = cfg.param("customer_extract_warn_age_days")       # optional; not in the definition
    if age is None:
        ctx.log.append(f"warning: no customer extract for {req.country}: customer match skipped")
    elif age > max_age:
        # The run id is created here, so the approval can only come with the request itself.
        if not req.approve_stale_extract:
            _fail(ctx, f"customer extract of {req.country} is {age:.0f} days old (> {max_age}); load a new one "
                       f"or run again with --approve-stale-extract")
        store.insert("biz_run_approval", [{"run_id": ctx.run_id, "approval_type": "STALE_CUSTOMER_EXTRACT",
                                           "approved_by": req.requested_by,
                                           "comment": f"customer extract {age:.0f} days old"}])
        ctx.log.append(f"warning: customer extract of {req.country} is {age:.0f} days old; approved by "
                       f"{req.requested_by}")
    elif warn_age is not None and age > warn_age:
        ctx.log.append(f"warning: customer extract of {req.country} is {age:.0f} days old")
    return ctx, cfg


def _queries(cfg, req: CaptureRequest) -> list[tuple[str, dict, str]]:
    """(term, area, language): every selected term x selected area, with the terms in the area's
    language (a configured term in another language is not sent to that area; a free-text term is)."""
    areas = cfg.search_areas()
    configured = {}
    for t in cfg.search_terms():
        configured.setdefault(t["term"], set()).add(t["language"])
    out = []
    for a in req.area_ids:
        area = areas[a]
        langs = [x.strip() for x in (area.get("default_language") or "de").replace("/", ",").split(",") if x.strip()]
        for lang in langs:      # bilingual subregions (Brussels, Verviers): one query per language
            for term in req.terms:
                if term in configured and lang not in configured[term]:
                    continue
                out.append((term, area, lang))
    return out


def area_outlines(store, cfg, country: str, area_ids: list[str], with_children: bool) -> dict[str, dict]:
    """area_id -> the outline sent to Apify (simplified GeoJSON), from the official boundaries in
    reference_geo: the area's own boundary at its level, or else the union of its children's (a STATE is
    its districts). An outline already in actor_fields (BE/NL pilot) wins. Areas without one are left out."""
    areas = cfg.search_areas()
    tol = float(cfg.param("apify_outline_tolerance_deg", 0.001))
    geo = {lvl: {a.admin_code: a.polygons for a in area_index(store, country, lvl).areas}
           for lvl in ("SUBREGION", "MUNICIPALITY")}
    children: dict[str, list[str]] = {}
    for a in areas.values():
        if a.get("parent_area_id"):
            children.setdefault(a["parent_area_id"], []).append(a["area_id"])

    def polygons(area_id: str, depth: int = 0) -> list:
        a = areas[area_id]
        own = geo.get(a.get("level") or "", {}).get(a.get("admin_code"))
        if own or depth > 3:
            return own or []
        return [p for k in children.get(area_id, []) for p in polygons(k, depth + 1)]

    wanted, i = list(area_ids), 0
    while with_children and i < len(wanted):      # every area below the requested ones (--subdivide)
        wanted += [k for k in children.get(wanted[i], []) if k not in wanted]
        i += 1
    out = {}
    for area_id in dict.fromkeys(wanted):
        given = json.loads(areas[area_id].get("actor_fields") or "{}").get("customGeolocation")
        outline = given or outline_geojson(polygons(area_id), tol)
        if outline:
            out[area_id] = outline
    return out


def normalize_items(ctx: RunContext, cfg, items: list[tuple[str, dict]]) -> list[dict]:
    """(query_id, actor item) -> place_norm_out rows (one per place_id, last item wins)."""
    legal = cfg.legal_forms()
    builders = cfg.shared_domains("SITE_BUILDER")
    shared = cfg.shared_domains()
    sub_idx = area_index(ctx.store, ctx.country, "SUBREGION")
    mun_idx = area_index(ctx.store, ctx.country, "MUNICIPALITY")
    rows = {}
    for _qid, item in items:
        f = apify.item_fields(item)
        if not f["place_id"]:
            continue
        ae, a = name_keys(f["name"], legal)
        e164, kind = normalize_phone(f["phone_raw"], ctx.country)
        dom = registrable_domain(f["website"], builders)
        sub = sub_idx.locate(f["longitude"], f["latitude"])
        mun = mun_idx.locate(f["longitude"], f["latitude"])
        rows[f["place_id"]] = {
            "run_id": ctx.run_id, "place_id": f["place_id"], "country": ctx.country, "name": f["name"],
            "name_key_ae": ae, "name_key_a": a, "address": f["address"], "street": f["street"], "city": f["city"],
            "postal_code_raw": None if f["postal_code_raw"] is None else str(f["postal_code_raw"]),
            "postal_code": normalize_postal_code(f["postal_code_raw"], ctx.country),
            "phone_raw": f["phone_raw"], "phone_e164": e164, "phone_type": kind, "website": f["website"],
            "domain": None if dom in shared else dom, "latitude": f["latitude"], "longitude": f["longitude"],
            "subregion_code": sub.admin_code if sub else None, "municipality_code": mun.admin_code if mun else None,
            "main_category": f["main_category"], "categories": f["categories"],
            "owner_description": f["owner_description"],
            "reviews_count": int(f["reviews_count"]) if f["reviews_count"] is not None else None,
            "rating": float(f["rating"]) if f["rating"] is not None else None,
            "is_closed_permanently": int(f["is_closed_permanently"]),
            "is_closed_temporarily": int(f["is_closed_temporarily"]), "scraped_at": f["scraped_at"]}
    ctx.store.insert("place_norm_out", list(rows.values()))
    return list(rows.values())


def link_identities(ctx: RunContext, cfg, places: list[dict]) -> set[int]:
    """Assigns business_id to every new listing (DT.4.2). Returns the businesses of these listings."""
    store = ctx.store
    known = links(store)                         # all countries: a place_id is linked once, ever
    al = aliases(store)
    new = [p for p in places if p["place_id"] not in known]
    # current businesses of the country, rebuilt from their listings
    existing: dict[int, Business] = {}
    if new:
        for bid, v in business_views(store, ctx.country).items():
            existing[bid] = Business(bid, str(v.first_seen_at or ""), list(v.place_ids), set(v.phones),
                                     {v.master.get("name_key_ae") or ""},
                                     [(v.master["latitude"], v.master["longitude"])]
                                     if v.master.get("latitude") is not None else [])
    hashes = store.hash64([p["place_id"] for p in new])
    now = str(store.now())
    res = assign([Listing(p["place_id"], p["name_key_ae"], p["phone_e164"], p["latitude"], p["longitude"], now)
                  for p in new], list(existing.values()), dict(known), lambda pid: hashes[pid],
                 float(cfg.param("merge_name_similarity_min", 30)), float(cfg.param("merge_max_distance_m", 500)),
                 now)
    store.insert("business_link_out", [{"place_id": l["place_id"], "business_id": l["business_id"],
                                        "link_reason": l["link_reason"], "country": ctx.country, "run_id": ctx.run_id}
                                       for l in res.links])
    store.insert("business_alias_out", [{"old_id": a["old_id"], "surviving_id": a["surviving_id"],
                                         "reason": a["reason"], "run_id": ctx.run_id} for a in res.aliases])
    store.insert("merge_suspect_out", [{"business_id": s["business_id"], "other_business_id": None,
                                        "place_id": s["place_id"], "reason": s["reason"], "run_id": ctx.run_id}
                                       for s in res.merge_suspects])
    known.update({l["place_id"]: l["business_id"] for l in res.links})
    al.update({a["old_id"]: a["surviving_id"] for a in res.aliases})
    return {resolve_alias(known[p["place_id"]], al) for p in places}


def evaluate_capture(ctx: RunContext, cfg, business_ids: set[int], source: str, area_ids: list[str],
                     terms: list[str], import_file_id: str | None = None) -> dict:
    """Filter, first customer match (Google data only), capture batch and enrichment estimate."""
    store = ctx.store
    views = business_views(store, ctx.country, business_ids)
    decisions = run_filter(ctx, cfg, views)
    included = [b for b, d in decisions.items() if d.decision == "INCLUDE"]
    matches = run_match(ctx, cfg, [facts_from_google(views[b]) for b in included], "CAPTURE")

    store.insert("capture_batch", [{"capture_batch_id": ctx.capture_batch_id, "run_id": ctx.run_id,
                                    "country": ctx.country, "profile": ctx.profile, "source": source,
                                    "area_ids": area_ids, "terms": terms, "import_file_id": import_file_id,
                                    "snapshot_id": ctx.snapshot_id, "requested_by": ctx.requested_by}])
    store.insert("capture_batch_lead", [{"capture_batch_id": ctx.capture_batch_id, "business_id": b,
                                         "country": ctx.country, "profile": ctx.profile, "run_id": ctx.run_id}
                                        for b in sorted(views)])
    to_enrich = [b for b in included if matches[b].outcome != "CUSTOMER"]
    mode = llm.choose_mode(cfg.param("llm_mode_default", "AUTO") or "AUTO", len(to_enrich),
                           int(cfg.param("llm_direct_max_requests", 50)))
    est = cost.enrich_estimate(cfg, len(to_enrich), mode)
    store.insert("cost_ledger", [{"run_id": ctx.run_id, "provider": "LLM", "kind": "ESTIMATED", "amount_usd": est,
                                  "detail": f"enrichment estimate for {len(to_enrich)} leads of {ctx.capture_batch_id}"}])
    summary = {"capture_batch_id": ctx.capture_batch_id, "found": len(views),
               "excluded_by_filter": len(views) - len(included),
               "excluded_as_customer": sum(1 for b in included if matches[b].outcome == "CUSTOMER"),
               "pending_enrichment": len(to_enrich), "enrichment_estimate_usd": est}
    return summary


def check_capture_cost(ctx: RunContext, cfg, req: CaptureRequest, n_queries: int, max_places: int) -> float:
    """Apify estimate before any call (DT.9.5): every query returns its cap. Above
    cost_approval_threshold_usd the run needs an approval of at least the estimate, given with the request
    (the run id is created here, so it cannot be approved afterwards). Subdividing adds queries on top:
    those stay under the run's spending cap (apify_max_total_charge_usd)."""
    est = cost.capture_cost(n_queries, max_places, cost.Prices.from_config(cfg))
    ctx.store.insert("cost_ledger", [{"run_id": ctx.run_id, "provider": "APIFY", "kind": "ESTIMATED", "amount_usd": est,
                                      "detail": f"{n_queries} queries x {max_places} places"}])
    if cost.needs_approval(est, float(cfg.param("cost_approval_threshold_usd", 10)), req.approve_cost_usd):
        _fail(ctx, f"Apify estimate {est:.2f} USD for {n_queries} queries is above the approval threshold; "
                   f"run again with --approve-cost {est:.2f} (or fewer areas, terms or --max-places)")
    if req.approve_cost_usd is not None:
        ctx.store.insert("biz_run_approval", [{"run_id": ctx.run_id, "approval_type": "COST",
                                               "approved_amount_usd": req.approve_cost_usd,
                                               "approved_by": req.requested_by, "comment": "Apify capture"}])
    return est


def run_capture(store, capturer: apify.Capturer, req: CaptureRequest) -> tuple[RunContext, dict]:
    ctx, cfg = start(store, "CAPTURE", req)
    max_places = int(req.max_places_per_search or cfg.param("default_max_places_per_search", 50))
    queries = _queries(cfg, req)
    outlines = area_outlines(store, cfg, ctx.country, req.area_ids, req.subdivide)
    missing = [a for a in dict.fromkeys(req.area_ids) if a not in outlines]
    if missing:      # by name alone Apify returns nothing: stop before spending
        _fail(ctx, f"no boundary for areas {', '.join(missing)}: load them with load-geo "
                   f"(the search is sent as the area's outline)")
    check_capture_cost(ctx, cfg, req, len(queries), max_places)
    ctx.set_state("RUNNING")
    try:
        cap_usd = float(cfg.param("apify_max_total_charge_usd", 25))
        sweep_passes = int(cfg.param("sweep_max_passes", 3))
        sweep_min_new = float(cfg.param("sweep_min_new_ratio", 0.05))
        children: dict[str, list[dict]] = {}
        for a in cfg.search_areas().values():
            if a.get("parent_area_id"):
                children.setdefault(a["parent_area_id"], []).append(a)
        seen_before = set(r["place_id"] for r in store.select("place_norm_out", {"country": ctx.country},
                                                              columns=["place_id"]))
        items, seen_run, spent = [], set(), 0.0
        queue = [(t, a, lang, 1) for t, a, lang in queries]
        while queue:
            term, area, lang, pass_no = queue.pop(0)
            if spent >= cap_usd:
                ctx.log.append("warning: Apify spending cap reached; remaining queries skipped")
                break
            qid = runs.new_id("q")
            fields = json.loads(area.get("actor_fields") or "{}")
            if area["area_id"] in outlines:
                fields["customGeolocation"] = outlines[area["area_id"]]
            elif not fields.get("customGeolocation"):       # a child queued by --subdivide without a boundary
                ctx.log.append(f"warning: no boundary for {area['area_id']}; skipped")
                continue
            actor_in = apify.actor_input(term, fields, lang, max_places)
            per_query_cap = max(0.5, (cap_usd - spent) / max(1, len(queue) + 1))
            result = capturer.run(actor_in, per_query_cap)
            spent += result.cost_usd or 0.0
            store.insert("gmaps_run", [{"apify_run_id": result.apify_run_id, "run_id": ctx.run_id, "query_id": qid,
                                        "actor_build": capturer.build, "input_json": apify.dumps(actor_in),
                                        "status": result.status, "cost_usd": result.cost_usd,
                                        "n_items": len(result.items), "started_at": result.started_at,
                                        "finished_at": result.finished_at}])
            store.insert("gmaps_place", [{"run_id": ctx.run_id, "apify_run_id": result.apify_run_id, "query_id": qid,
                                          "place_id": it.get("placeId"), "item_json": apify.dumps(it)}
                                         for it in result.items])
            ids = {it.get("placeId") for it in result.items if it.get("placeId")}
            n_new = len(ids - seen_before - seen_run)
            seen_run |= ids
            items += [(qid, it) for it in result.items]
            saturated = apify.is_saturated(len(result.items), max_places)
            kids = children.get(area["area_id"], [])
            at_leaf = saturated and not kids
            if saturated and req.subdivide:
                if kids:
                    queue += [(term, k, lang, 1) for k in kids]
                elif pass_no < sweep_passes and len(ids) and n_new / len(ids) >= sweep_min_new:
                    queue.append((term, area, lang, pass_no + 1))
            if at_leaf:
                ctx.log.append(f"warning: '{term}' saturated at leaf area {area['area_id']}")
            store.insert("search_query_log", [{
                "run_id": ctx.run_id, "query_id": qid, "capture_batch_id": ctx.capture_batch_id,
                "country": ctx.country,
                "profile": ctx.profile, "term": term, "area_id": area["area_id"], "language": lang,
                "max_places": max_places, "pass_no": pass_no, "n_results": len(result.items), "n_new": n_new,
                "is_saturated": int(saturated), "is_saturated_at_leaf": int(at_leaf),
                "apify_run_id": result.apify_run_id}])
        store.insert("cost_ledger", [{"run_id": ctx.run_id, "provider": "APIFY", "kind": "ACTUAL", "amount_usd": spent,
                                      "detail": f"{len(items)} items"}])
        store.insert("landing_meta", [{"run_id": ctx.run_id, "entity": "gmaps_place", "n_rows": len(items)}])
        places = normalize_items(ctx, cfg, items)
        bids = link_identities(ctx, cfg, places)
        summary = evaluate_capture(ctx, cfg, bids, "SEARCH", req.area_ids, req.terms)
    except RunFailed:
        raise
    except Exception as e:                    # noqa: BLE001 - any error ends the run in FAILED with its reason
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary


# --------------------------------------------------------------------------- IMPORT_LIST
def pick_import_match(row: dict, items: list[dict], min_similarity: float, country: str,
                      legal_forms=()) -> tuple[str, dict | None]:
    """('MATCHED', item) | ('UNMATCHED', None) | ('AMBIGUOUS', None)."""
    want_ae, _ = name_keys(row.get("name"), legal_forms)
    want_phone, _ = normalize_phone(row.get("phone"), country)
    want_dom = registrable_domain(row.get("website"))
    scored = []
    for it in items:
        f = apify.item_fields(it)
        ae, _ = name_keys(f["name"], legal_forms)
        sim = token_sort_ratio(want_ae, ae)
        hard = (want_phone and normalize_phone(f["phone_raw"], country)[0] == want_phone) or (
            want_dom and registrable_domain(f["website"]) == want_dom)
        if hard or sim >= min_similarity:
            scored.append((1 if hard else 0, sim, it))
    if not scored:
        return "UNMATCHED", None
    scored.sort(key=lambda x: (-x[0], -x[1]))
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return "AMBIGUOUS", None
    return "MATCHED", scored[0][2]


def run_import_list(store, capturer: apify.Capturer, req: CaptureRequest) -> tuple[RunContext, dict]:
    ctx, cfg = start(store, "IMPORT_LIST", req)
    check_capture_cost(ctx, cfg, req, len(req.import_rows), 3)        # one query per row, 3 results each
    ctx.set_state("RUNNING")
    try:
        min_sim = float(cfg.param("import_match_min_similarity", 85))
        legal = cfg.legal_forms()
        items, outcomes = [], {"MATCHED": 0, "UNMATCHED": [], "AMBIGUOUS": []}
        for row in req.import_rows:
            fields = {"countryCode": req.country, "city": row.get("city") or ""}
            actor_in = apify.actor_input(row["name"], fields, cfg.param("apify_language", "de") or "de", 3)
            res = capturer.run(actor_in, float(cfg.param("apify_max_total_charge_usd", 25)) / max(1, len(req.import_rows)))
            qid = runs.new_id("q")
            store.insert("gmaps_run", [{"apify_run_id": res.apify_run_id, "run_id": ctx.run_id, "query_id": qid,
                                        "actor_build": capturer.build, "input_json": apify.dumps(actor_in),
                                        "status": res.status, "cost_usd": res.cost_usd, "n_items": len(res.items)}])
            store.insert("gmaps_place", [{"run_id": ctx.run_id, "apify_run_id": res.apify_run_id, "query_id": qid,
                                          "place_id": it.get("placeId"), "item_json": apify.dumps(it)} for it in res.items])
            status, item = pick_import_match(row, res.items, min_sim, req.country, legal)
            if status == "MATCHED":
                outcomes["MATCHED"] += 1
                items.append((qid, item))
            else:
                outcomes[status].append(row.get("name"))
        places = normalize_items(ctx, cfg, items)
        bids = link_identities(ctx, cfg, places)
        summary = evaluate_capture(ctx, cfg, bids, "IMPORT", [], [], req.import_file_id)
        summary.update({"import_matched": outcomes["MATCHED"], "import_unmatched": outcomes["UNMATCHED"],
                        "import_ambiguous": outcomes["AMBIGUOUS"]})
    except RunFailed:
        raise
    except Exception as e:                    # noqa: BLE001
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary
