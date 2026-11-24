"""ENRICH (review DT.9): crawl, model and second customer match of one capture batch. The score is not
computed here: the dbt view lgn_f2_score computes it in SQL from what this step writes.
Old phase B of the v2.2 run, now launched by a person on an existing batch.

States: REQUESTED -> SNAPSHOT_FROZEN -> [AWAITING_COST_APPROVAL] -> RUNNING -> [AWAITING_BATCHES ->
COLLECTING] -> CLOSED. Resumable at any point without repeating paid work: crawls and model answers
are cached, and every model request is written (llm_request + llm_batch PREPARING) before it is sent.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .. import cost, ddl, llm, prompt, runs
from ..config import precheck
from ..content import Page, prepare
from ..crawl import Crawler, CrawlLimits, CrawlResult
from ..matching import MatchConfig, apply_verdicts, match
from ..normalize import registrable_domain
from ..response import SchemaError, check_answer, check_schema, technical_review
from ..state import RunContext, business_views, customer_index, latest, permanent_exclusions, resume_context, \
    suppressed, is_suppressed, sync_human_decisions
from ..store import ENGINE_VIEWS
from .capture import RunFailed
from .common import (capture_batch_leads, current_web, deciding_matches, facts_from_google, facts_with_web,
                     is_customer, latest_filter, match_row)

NO_CONTENT = {"NO_WEBSITE": "NO_WEBSITE", "SOCIAL_ONLY": "SOCIAL_ONLY", "BLOCKED": "BLOCKED",
              "ROBOTS_DISALLOWED": "BLOCKED", "UNREACHABLE": "UNREACHABLE", "EMPTY": "INSUFFICIENT_EVIDENCE"}


@dataclass
class EnrichRequest:
    capture_batch_id: str
    requested_by: str
    llm_mode: str = "AUTO"
    max_leads: int | None = None
    reread: bool = False


def _fail(ctx, msg):
    ctx.set_state("FAILED", msg)
    raise RunFailed(msg)


# --------------------------------------------------------------------------- selection and estimate
def select_leads(ctx: RunContext, cfg, req: EnrichRequest) -> tuple[list[int], list[int]]:
    """(to_process, reused): included by the filter, not a customer after the first pass, not
    excluded nor suppressed. Already enriched leads are reused (rescored) unless reread is asked."""
    store = ctx.store
    leads = capture_batch_leads(store, req.capture_batch_id)
    filt = latest_filter(store, ctx.country, ctx.profile, leads)
    included = {b for b, r in filt.items() if r["decision"] == "INCLUDE"}
    matches = deciding_matches(store, ctx.country, included)
    excl = permanent_exclusions(store, ctx.country, ctx.profile)
    sup = suppressed(store)
    views = business_views(store, ctx.country, included)
    ok = sorted(b for b in included if not is_customer(matches.get(b)) and b not in excl
                and b in views and not is_suppressed(views[b], sup))
    done = set(current_web(store, ctx.country, ctx.profile, set(ok))) if not req.reread else set()
    to_process = [b for b in ok if b not in done]
    if req.max_leads is not None:
        to_process = to_process[: req.max_leads]
    return to_process, [b for b in ok if b in done]


def estimate(cfg, n: int, mode: str) -> float:
    return cost.enrich_estimate(cfg, n, mode)


def _latest_loaded(rows: list[dict], key: tuple[str, ...]) -> dict[tuple, dict]:
    """latest() for landing tables, whose timestamp is loaded_at."""
    return latest([{**r, "inserted_at": r["loaded_at"]} for r in rows], key)


def _usable(resp: dict | None) -> dict | None:
    """The parsed answer of a response, or None if it failed, is empty or breaks the schema."""
    if not resp or resp.get("status") != "SUCCEEDED" or not resp.get("response_json"):
        return None
    try:
        obj = json.loads(resp["response_json"])
        check_schema(obj)
        return obj
    except (json.JSONDecodeError, SchemaError):
        return None


def _approved_usd(store, run_id: str) -> float | None:
    rows = store.select("biz_run_approval", {"run_id": run_id, "approval_type": "COST"})
    if not rows:
        return None
    return float(max(rows, key=lambda r: r["inserted_at"]).get("approved_amount_usd") or 0) or float("inf")


# --------------------------------------------------------------------------- entry points
def run_enrich(store, provider: llm.Provider, crawler: Crawler | None, req: EnrichRequest,
               vat_checker=None) -> tuple[RunContext, dict]:
    batch = store.select("capture_batch", {"capture_batch_id": req.capture_batch_id})
    if not batch:
        raise ValueError(f"capture batch {req.capture_batch_id} not found")
    b = batch[0]
    ctx = RunContext(store, runs.new_id("run"), "ENRICH", b["country"], b["profile"], req.requested_by,
                     capture_batch_id=req.capture_batch_id, vat_checker=vat_checker)
    ctx.set_state("REQUESTED", request=req.__dict__)
    sync_human_decisions(ctx)
    ctx.freeze_snapshot()
    ctx.set_state("SNAPSHOT_FROZEN")
    cfg = ctx.config()
    pre = precheck(cfg, needs_scoring=True)
    ctx.log += [f"warning: {w}" for w in pre.warnings]
    if pre.blocking:
        _fail(ctx, "precheck: " + "; ".join(pre.blocking))
    missing = store.missing_views(ENGINE_VIEWS)
    if missing:
        _fail(ctx, f"the engine's views {', '.join(missing)} are not in the database: run `python leadgen.py dbt`")
    try:
        prompt.system_template(ctx.country, ctx.profile)
    except (prompt.ProfileNotConfigured, ValueError) as e:
        _fail(ctx, str(e))
    if ctx.profile != "POOL_SPECIALIST" and not req.max_leads:
        _fail(ctx, "max_leads is mandatory for any profile other than POOL_SPECIALIST")

    to_process, reused = select_leads(ctx, cfg, req)
    mode = llm.choose_mode(req.llm_mode, len(to_process), int(cfg.param("llm_direct_max_requests", 50)))
    est = estimate(cfg, len(to_process), mode)
    store.insert("cost_ledger", [{"run_id": ctx.run_id, "provider": "LLM", "kind": "ESTIMATED", "amount_usd": est,
                                  "detail": f"{len(to_process)} requests, mode {mode}"}])
    if cost.needs_approval(est, float(cfg.param("cost_approval_threshold_usd", 10)), None):
        ctx.set_state("AWAITING_COST_APPROVAL", f"estimated {est:.2f} USD for {len(to_process)} leads; "
                                                f"approve COST for run {ctx.run_id} and resume it")
        return ctx, {"run_id": ctx.run_id, "state": ctx.state, "estimate_usd": est, "to_process": len(to_process)}
    ctx.set_state("RUNNING")
    return _process(ctx, cfg, provider, crawler, req, to_process, reused, est)


def resume_enrich(store, provider: llm.Provider, crawler: Crawler | None, run_id: str,
                  vat_checker=None) -> tuple[RunContext, dict]:
    ctx = resume_context(store, run_id)
    ctx.vat_checker = vat_checker
    cfg = ctx.config()
    req = EnrichRequest(**ctx.request)                          # type: ignore[attr-defined]
    if ctx.state == "AWAITING_COST_APPROVAL":
        approved = _approved_usd(store, run_id)
        if approved is None:
            return ctx, {"run_id": run_id, "state": ctx.state, "message": "no COST approval yet"}
        to_process, reused = select_leads(ctx, cfg, req)
        ctx.set_state("RUNNING", f"approved {approved}")
        return _process(ctx, cfg, provider, crawler, req, to_process, reused, approved)
    if ctx.state == "AWAITING_BATCHES":
        return _poll_batches(ctx, cfg, provider)
    return ctx, {"run_id": run_id, "state": ctx.state, "message": "nothing to resume"}


# --------------------------------------------------------------------------- crawl
def _cached_crawl(store, domain: str, ttl_days: float) -> CrawlResult | None:
    rows = [r for r in store.select("web_crawl", {"domain": domain})
            if r.get("crawl_status") in ("OK", "PARTIAL_LIMIT", "PARTIAL_ERROR")]
    if not rows:
        return None
    last = max(rows, key=lambda r: r["loaded_at"])
    if (store.now() - last["loaded_at"]).total_seconds() > ttl_days * 86400:
        return None
    pages = [Page("", p["url"], int(p.get("depth") or 0), p.get("html") or "")
             for p in store.select("web_page", {"crawl_id": last["crawl_id"]})]
    return CrawlResult(domain, last.get("start_url") or "", last.get("final_url"), last.get("final_domain"),
                       last["crawl_status"], last.get("render_method") or "HTTP", pages)


def crawl_business(ctx: RunContext, cfg, crawler: Crawler | None, website: str | None) -> CrawlResult:
    builders = cfg.shared_domains("SITE_BUILDER")
    shared = cfg.shared_domains() - builders
    domain = registrable_domain(website, builders)
    if not domain:
        return CrawlResult("", "", None, None, "NO_WEBSITE", "HTTP")
    if domain in shared:
        return CrawlResult(domain, website or "", None, None, "SOCIAL_ONLY", "HTTP")
    cached = _cached_crawl(ctx.store, domain, float(cfg.param("web_cache_ttl_days", 180)))
    if cached:
        return cached
    if crawler is None:
        return CrawlResult(domain, website or "", None, None, "UNREACHABLE", "HTTP")
    res = crawler.crawl(website)
    crawl_id = runs.new_id("crawl")
    ctx.store.insert("web_crawl", [{"crawl_id": crawl_id, "run_id": ctx.run_id, "domain": domain,
                                    "start_url": res.start_url, "final_url": res.final_url,
                                    "final_domain": res.final_domain, "crawl_status": res.status,
                                    "render_method": res.render_method, "n_pages": len(res.pages),
                                    "n_bytes": res.n_bytes, "limits_json": json.dumps(crawler.lim.__dict__)}])
    ctx.store.insert("web_page", [{"crawl_id": crawl_id, "url": p.url, "depth": p.depth, "html": p.html,
                                   "content_hash": hashlib.sha256(p.html.encode()).hexdigest()[:16]}
                                  for p in res.pages])
    return res


# --------------------------------------------------------------------------- requests
def _process(ctx, cfg, provider, crawler, req, to_process, reused, approved_usd) -> tuple[RunContext, dict]:
    store = ctx.store
    try:
        views = business_views(store, ctx.country, set(to_process))
        index, extracts = customer_index(store, ctx.country)
        mc = MatchConfig.from_config(cfg)
        competitor, fluidra = cfg.brands("COMPETITOR"), cfg.brands("FLUIDRA")
        version = prompt.prompt_version(ctx.country, ctx.profile)
        system = prompt.system_message(ctx.country, ctx.profile, competitor, fluidra)
        blh = prompt.brand_list_hash(competitor, fluidra)
        p = cfg.param
        # Reuse is judged on what _collect will read: the latest request and the latest response per custom_id.
        prev_req = {k[0]: r for k, r in _latest_loaded(
            store.select("llm_request", {"prompt_version": version}), ("custom_id",)).items()}
        prev_ok = {k[0] for k, r in _latest_loaded(
            store.select("llm_response", {"custom_id": list(prev_req)}), ("custom_id",)).items()
                   if _usable(r) is not None} if prev_req else set()

        new_reqs, reused_ids, direct_reviews = [], [], []
        for bid in to_process:
            v = views.get(bid)
            if v is None:
                continue
            m = v.master
            crawl = crawl_business(ctx, cfg, crawler, m.get("website"))
            first = match(facts_from_google(v), index, mc)
            cands = [{"ref": c.ref, "name": c.customer.customer_name, "address": c.customer.street,
                      "postal_code": c.customer.postal_code, "city": c.customer.city_norm,
                      "phone": c.customer.phone_e164, "email_domain": c.customer.corporate_email_domain,
                      "subsidiary_code": c.customer.subsidiary_code, "customer_code": c.customer.customer_code}
                     for c in first.candidates] if first.outcome == "CANDIDATES" else []
            has_content = crawl.status not in NO_CONTENT and crawl.pages
            if not has_content and not cands:
                # REVIEW without the model (DT.6.6)
                direct_reviews.append((bid, crawl, NO_CONTENT.get(crawl.status, "INSUFFICIENT_EVIDENCE")))
                continue
            content = prepare(list(crawl.pages), cfg.page_patterns(), competitor + fluidra,
                p("token_budget_per_page", 4000), p("token_budget_per_domain", 30000), p("char_cap_per_domain", 100000),
                p("brand_snippet_reserve_ratio", 0.10), p("brand_snippets_per_brand", 3), p("brand_snippet_chars", 200)
            ) if has_content else None
            user = prompt.user_message(
                {"name": m.get("name"), "categories": v.categories, "owner_description": m.get("owner_description"),
                 "address": m.get("address"), "country": ctx.country, "website": m.get("website"),
                 "phone": m.get("phone_e164") or m.get("phone_raw")},
                cands, crawl.status, content.content_line if content else "complete",
                [(pg.page_id, pg.signals) for pg in content.pages] if content else [],
                [(x.brand, x.page_id, x.snippet) for x in content.mentions] if content else [],
                [(pg.page_id, pg.url, pg.text) for pg in content.pages] if content else [])
            cid = prompt.custom_id(bid, version)
            meta = {"business_id": bid, "domain": crawl.domain, "final_domain": crawl.final_domain,
                    "crawl_status": crawl.status, "candidates": cands,
                    "pages": {pg.page_id: [pg.url, pg.text] for pg in content.pages} if content else {},
                    "cart_signal": bool(content and any("cart" in pg.signals for pg in content.pages))}
            content_hash = content.content_hash if content else "none"
            ch = prompt.candidates_hash(cands)
            old = prev_req.get(cid)
            if old and cid in prev_ok and (old.get("brand_list_hash"), old.get("candidates_hash"),
                                           old.get("content_hash"), old.get("domain")) == (blh, ch, content_hash,
                                                                                         crawl.domain or None):
                reused_ids.append(cid)          # cached answer: same business, prompt, brands, candidates, content
                continue
            new_reqs.append((llm.LLMRequest(cid, system, user), meta, ch, content_hash))

        mode = llm.choose_mode(req.llm_mode, len(new_reqs), int(p("llm_direct_max_requests", 50)))
        tokens = [prompt_tokens(r.system, r.user) for r, *_ in new_reqs]
        actual = cost.llm_cost(len(tokens), (sum(tokens) // max(1, len(tokens))) if tokens else 0,
                               cost.EST_OUTPUT_TOKENS, mode, cost.Prices.from_config(cfg), cost.EST_SYSTEM_TOKENS)
        threshold = float(p("cost_approval_threshold_usd", 10))
        approved = _approved_usd(store, ctx.run_id)
        if actual > threshold and cost.overrun(actual, approved, threshold, float(p("cost_overrun_ratio", 0.2))):
            ctx.set_state("AWAITING_COST_APPROVAL", f"counted {actual:.2f} USD, above what was approved")
            return ctx, {"run_id": ctx.run_id, "state": ctx.state, "estimate_usd": actual}

        store.insert("llm_request", [{"batch_id": None, "custom_id": r.custom_id, "run_id": ctx.run_id,
                                      "business_id": meta["business_id"], "profile": ctx.profile,
                                      "prompt_version": version, "brand_list_hash": blh, "candidates_hash": ch,
                                      "domain": meta["domain"] or None, "content_hash": chash,
                                      "request_json": json.dumps({"user": r.user, "meta": meta}, ensure_ascii=False),
                                      "n_input_tokens_est": t}
                                     for (r, meta, ch, chash), t in zip(new_reqs, tokens)])
        # cached answers are linked to this run so the collection reads them like fresh ones
        if reused_ids:
            store.insert("llm_request", [{**{k: v for k, v in prev_req[c].items() if k in ddl.LANDING_TABLES[
                "llm_request"].names and k != "loaded_at"},
                                          "run_id": ctx.run_id, "batch_id": "CACHE"} for c in reused_ids])
        for bid, crawl, reason in direct_reviews:
            _write_web(ctx, bid, crawl.domain, crawl.final_domain, crawl.status, None, technical_review(reason), version)

        direct = [bid for bid, *_ in direct_reviews]   # REVIEW without the model: scored like the rest
        reqs = [r for r, *_ in new_reqs]
        max_out = int(p("llm_max_tokens_out", 2000))
        if mode == "DIRECT" or not reqs:
            batch_id = runs.new_id("llmb")
            _batch_row(ctx, provider, batch_id, "DIRECT", "PREPARING", None, reqs)
            results = provider.send_direct(reqs, max_out, int(p("llm_direct_concurrency", 5))) if reqs else []
            _write_responses(ctx, batch_id, results, attempt=1)
            _batch_row(ctx, provider, batch_id, "DIRECT", "ENDED", None, reqs)
            ctx.set_state("COLLECTING")
            return _collect(ctx, cfg, provider, reused, direct)
        for chunk in llm.split_batches(reqs, int(p("llm_batch_max_requests", 2000))):
            batch_id = runs.new_id("llmb")
            _batch_row(ctx, provider, batch_id, "BATCH", "PREPARING", None, chunk)
            external = provider.submit_batch(chunk, max_out)
            _batch_row(ctx, provider, batch_id, "BATCH", "SUBMITTED", external, chunk)
        ctx.set_state("AWAITING_BATCHES", f"{LEADS_TAG}{json.dumps({'reused': reused, 'direct': direct})}")
        return ctx, {"run_id": ctx.run_id, "state": ctx.state, "requests": len(reqs)}
    except RunFailed:
        raise
    except Exception as e:                    # noqa: BLE001
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise


def prompt_tokens(system: str, user: str) -> int:
    from ..content import estimate_tokens
    return estimate_tokens(system) + estimate_tokens(user)


def _batch_row(ctx, provider, batch_id, mode, status, external, reqs):
    ctx.store.insert("llm_batch", [{"batch_id": batch_id, "run_id": ctx.run_id, "provider": provider.name,
                                    "model": provider.model, "mode": mode, "status": status, "external_id": external,
                                    "custom_ids": [r.custom_id for r in reqs], "n_requests": len(reqs)}])


def _write_responses(ctx, batch_id, results: list[llm.LLMResult], attempt: int):
    ctx.store.insert("llm_response", [{"batch_id": batch_id, "custom_id": r.custom_id, "attempt": attempt,
                                       "status": r.status, "stop_reason": r.stop_reason, "response_json": r.text,
                                       "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                                       "cache_read_tokens": r.cache_read_tokens,
                                       "cache_write_tokens": r.cache_write_tokens, "error": r.error} for r in results])


def _poll_batches(ctx, cfg, provider) -> tuple[RunContext, dict]:
    store = ctx.store
    rows = latest(store.select("llm_batch", {"run_id": ctx.run_id}), ("batch_id",))
    pending = [r for r in rows.values() if r["mode"] == "BATCH" and r["status"] == "SUBMITTED"]
    ended = [r for r in pending if provider.batch_ended(r["external_id"])]
    if len(ended) < len(pending):
        return ctx, {"run_id": ctx.run_id, "state": ctx.state, "batches_ended": len(ended),
                     "batches_pending": len(pending) - len(ended)}
    ctx.set_state("COLLECTING")
    for r in pending:
        _write_responses(ctx, r["batch_id"], provider.batch_results(r["external_id"]), attempt=1)
        store.insert("llm_batch", [{**{k: v for k, v in r.items() if k != "inserted_at"}, "status": "ENDED"}])
    msg = next((r.get("message") for r in sorted(store.select("pipeline_run", {"run_id": ctx.run_id}),
                                                   key=lambda r: r["inserted_at"])
                if r["state"] == "AWAITING_BATCHES"), "") or ""
    return _collect(ctx, cfg, provider, *_pending_leads(msg))


LEADS_TAG = "leads: "


def _pending_leads(msg: str) -> tuple[list[int], list[int]]:
    """(reused, direct) saved in the AWAITING_BATCHES message; older runs saved only the reused list."""
    if "reused leads: " in msg:
        return json.loads(msg.split("reused leads: ", 1)[1]), []
    if msg.startswith(LEADS_TAG):
        d = json.loads(msg[len(LEADS_TAG):])
        return d.get("reused", []), d.get("direct", [])
    return [], []


# --------------------------------------------------------------------------- collection
def _write_web(ctx, bid, domain, final_domain, crawl_status, custom_id, ans, version):
    ctx.store.insert("web_validation_out", [{
        "run_id": ctx.run_id, "business_id": bid, "country": ctx.country, "profile": ctx.profile,
        "domain": domain or None, "custom_id": custom_id, "prompt_version": version, "crawl_status": crawl_status,
        "validation": ans.validation, "review_reason": ans.review_reason,
        "validation_reason_text": ans.validation_reason_text, "offering": ans.offering, "pool_focus": ans.pool_focus,
        "company_type": ans.company_type, "legal_name": ans.legal_name, "legal_address": ans.legal_address,
        "vat_id": ans.vat_id, "vat_check_status": ctx.vat_checker.check(ans.vat_id) if ctx.vat_checker else "NOT_CHECKED",
        "register_id": ans.register_id, "register_type": ans.register_type, "register_court": ans.register_court,
        "contact_name": ans.contact_name, "contact_role": ans.contact_role, "email": ans.email,
        "phone_e164": ans.phone_e164, "phone_type": ans.phone_type,
        "phone_source": "WEB" if ans.phone_e164 else None,
        "has_ecommerce": None if ans.has_ecommerce is None else int(ans.has_ecommerce),
        "detected_brands": ans.detected_brands, "fluidra_brands": ans.fluidra_brands,
        "sources_json": json.dumps(ans.sources, ensure_ascii=False), "n_unsupported_claims": ans.n_unsupported_claims,
        "is_indirect_customer": int(bool(ans.fluidra_brands)), "final_domain": final_domain}])


def _collect(ctx, cfg, provider, reused: list[int], direct: list[int] = ()) -> tuple[RunContext, dict]:
    store, p = ctx.store, cfg.param
    try:
        version = prompt.prompt_version(ctx.country, ctx.profile)
        system = prompt.system_message(ctx.country, ctx.profile, cfg.brands("COMPETITOR"), cfg.brands("FLUIDRA"))
        reqs = _latest_loaded(store.select("llm_request", {"run_id": ctx.run_id}), ("custom_id",))
        responses = _latest_loaded(store.select("llm_response", {"custom_id": [k[0] for k in reqs]}), ("custom_id",))
        usable = _usable

        # One retry through the normal API for failed, expired, truncated or invalid answers (DT.6.4).
        retry = []
        for (cid,), r in reqs.items():
            resp = responses.get((cid,))
            already_retried = bool(resp) and int(resp.get("attempt") or 1) >= 2
            if usable(resp) is None and not already_retried:
                retry.append((llm.LLMRequest(cid, system, json.loads(r["request_json"])["user"]),
                              resp.get("status") if resp else None))
        if retry:
            batch_id = runs.new_id("llmb")
            out = []
            for trunc in (True, False):
                group = [rq for rq, st in retry if (st == "TRUNCATED") == trunc]
                if group:
                    mt = int(p("llm_max_tokens_retry", 4000) if trunc else p("llm_max_tokens_out", 2000))
                    out += provider.send_direct(group, mt, int(p("llm_direct_concurrency", 5)))
            _batch_row(ctx, provider, batch_id, "DIRECT", "ENDED", None, [rq for rq, _ in retry])
            _write_responses(ctx, batch_id, out, attempt=2)
            for res in out:
                responses[(res.custom_id,)] = {"status": res.status, "response_json": res.text, "attempt": "2"}

        competitor, fluidra = cfg.brands("COMPETITOR"), cfg.brands("FLUIDRA")
        index, extracts = customer_index(store, ctx.country)
        mc = MatchConfig.from_config(cfg)
        ext = ",".join(sorted(e["extract_id"] for e in extracts.values())) or None
        bids = {int(json.loads(r["request_json"])["meta"]["business_id"]) for r in reqs.values()}
        views = business_views(store, ctx.country, bids)
        legal = cfg.legal_forms()
        for (cid,), r in reqs.items():
            meta = json.loads(r["request_json"])["meta"]
            bid = int(meta["business_id"])
            resp = responses.get((cid,)) or {}
            obj = usable(resp)
            pages = {k: (v[0], v[1]) for k, v in meta["pages"].items()}
            refs = [c["ref"] for c in meta["candidates"]]
            if obj is None:
                ans = technical_review("TECHNICAL")
            else:
                ans = check_answer(obj, pages, ctx.country, competitor, fluidra, refs, meta["cart_signal"])
                if not meta["pages"] and ans.validation == "VALID":
                    ans.validation, ans.company_type = "REVIEW", "WEAK_UNVERIFIED"   # no website content
                    ans.review_reason = NO_CONTENT.get(meta["crawl_status"], "INSUFFICIENT_EVIDENCE")
            _write_web(ctx, bid, meta["domain"], meta["final_domain"], meta["crawl_status"], cid, ans, version)
            # second pass (DT.5.4): web facts + the model's verdicts on the candidates
            if bid in views:
                wv = {"vat_id": ans.vat_id, "register_id": ans.register_id, "phone_e164": ans.phone_e164,
                      "email": ans.email, "final_domain": meta["final_domain"], "legal_name": ans.legal_name,
                      "legal_address": ans.legal_address}
                res = match(facts_with_web(views[bid], wv, legal), index, mc)
                if res.outcome == "CANDIDATES":
                    res = apply_verdicts(res, {c.ref: ans.verdicts.get(_ref_of(meta, c), "UNSURE")
                                               for c in res.candidates})
                store.insert("customer_match_out", [match_row(ctx, res, "ENRICH", ext)])

        _merge_suspects_by_domain(ctx)
        all_ids = bids | set(reused) | set(direct)
        web = current_web(store, ctx.country, ctx.profile, all_ids)
        matches = deciding_matches(store, ctx.country, all_ids)
        # scored = VALID or REVIEW and not a customer: exactly the leads lgn_f2_score computes
        scored = {b for b in all_ids if web.get(b, {}).get("validation") in ("VALID", "REVIEW")
                  and not is_customer(matches.get(b))}
        scores = store.lead_scores(ctx.country, ctx.profile, scored)
        usages = _run_usages(store, ctx.run_id)
        actual = cost.llm_actual_cost(usages, cost.Prices.from_config(cfg))
        n_in = sum(int(u.get("input_tokens") or 0) for u in usages)
        n_cache = sum(int(u.get("cache_read_tokens") or 0) for u in usages)
        n_write = sum(int(u.get("cache_write_tokens") or 0) for u in usages)
        n_out = sum(int(u.get("output_tokens") or 0) for u in usages)
        store.insert("cost_ledger", [{"run_id": ctx.run_id, "provider": "LLM", "kind": "ACTUAL", "amount_usd": actual,
                                      "detail": f"{len(usages)} calls: {n_in} input / {n_cache} cache-read / {n_write} cache-write / "
                                                f"{n_out} output tokens"}])
        processed = bids | set(direct)
        summary = {"run_id": ctx.run_id, "enriched": len(processed), "reused": len(reused), "scored": len(scored),
                   "by_rating": {k: sum(1 for s in scores.values() if s["rating"] == k) for k in ("HOT", "WARM", "COLD")},
                   "review": sum(1 for b in processed if web.get(b, {}).get("validation") == "REVIEW"),
                   "not_valid": sum(1 for b in processed if web.get(b, {}).get("validation") == "NOT_VALID"),
                   "llm_cost_usd": actual}
    except Exception as e:                    # noqa: BLE001
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary


def _run_usages(store, run_id: str) -> list[dict]:
    """Every paid model call of the run (first attempts and retries) with its batch's mode. Cached answers
    reused from earlier runs have no llm_batch row in this run, so they are not counted."""
    modes = {r["batch_id"]: r["mode"] for r in store.select("llm_batch", {"run_id": run_id})}
    if not modes:
        return []
    return [{**r, "mode": modes[r["batch_id"]]} for r in store.select("llm_response", {"batch_id": list(modes)})]


def _ref_of(meta: dict, cand) -> str:
    """The ref the model saw for this customer (the second pass may rank candidates differently)."""
    for c in meta["candidates"]:
        if c["customer_code"] == cand.customer.customer_code and c.get("subsidiary_code") == cand.customer.subsidiary_code:
            return c["ref"]
    return "?"


def _merge_suspects_by_domain(ctx) -> None:
    """Two businesses whose websites end on the same final domain after redirects (DT.4.2)."""
    web = current_web(ctx.store, ctx.country, ctx.profile)
    by_dom: dict[str, list[int]] = {}
    for b, r in web.items():
        if r.get("final_domain"):
            by_dom.setdefault(r["final_domain"], []).append(b)
    have = {(r["business_id"], r.get("other_business_id"))
            for r in ctx.store.select("business_identity_out", {"kind": "MERGE_SUSPECT"})}
    rows = []
    for dom, bs in by_dom.items():
        for b in bs:
            for o in bs:
                if o != b and (b, o) not in have:
                    rows.append({"kind": "MERGE_SUSPECT", "business_id": b, "other_business_id": o, "place_id": None,
                                 "reason": "SAME_FINAL_DOMAIN", "run_id": ctx.run_id})
    ctx.store.insert("business_identity_out", rows)


def limits(cfg) -> CrawlLimits:
    return CrawlLimits.from_config(cfg)
