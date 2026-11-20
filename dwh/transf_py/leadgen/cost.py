"""Cost estimate and approval (DT.9.5, review DT.9).

Estimated before launching: Apify (queries x cap x price with detail page), browser rendering, and the
model (requests x estimated tokens x price of the mode, batch at half price). Above
cost_approval_threshold_usd an ENRICH run waits in AWAITING_COST_APPROVAL until a person approves it
(biz_run_approval, approval_type COST). Before sending, the real token count is compared with what was
approved: more than cost_overrun_ratio above it -> back to AWAITING_COST_APPROVAL.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Prices:
    apify_per_place_usd: float = 0.007          # pay-per-event incl. detail page; ~2 USD per 58 searches
    llm_in_per_mtok: float = 4.0                # code fallback only: the prices are run parameters (llm_price_*)
    llm_out_per_mtok: float = 20.0
    llm_cache_read_per_mtok: float = 0.20
    llm_cache_write_per_mtok: float = 5.0       # 5-minute cache write: 1.25 x input
    batch_discount: float = 0.5
    render_per_page_usd: float = 0.0            # own infrastructure; a placeholder for a paid browser API

    @classmethod
    def from_config(cls, cfg) -> "Prices":
        d = cls()
        return cls(apify_per_place_usd=cfg.param("apify_price_per_place_usd", d.apify_per_place_usd),
                   llm_in_per_mtok=cfg.param("llm_price_in_per_mtok", d.llm_in_per_mtok),
                   llm_out_per_mtok=cfg.param("llm_price_out_per_mtok", d.llm_out_per_mtok),
                   llm_cache_read_per_mtok=cfg.param("llm_price_cache_read_per_mtok", d.llm_cache_read_per_mtok),
                   llm_cache_write_per_mtok=cfg.param("llm_price_cache_write_per_mtok", d.llm_cache_write_per_mtok),
                   batch_discount=cfg.param("llm_batch_discount", d.batch_discount),
                   render_per_page_usd=cfg.param("render_price_per_page_usd", d.render_per_page_usd))


def capture_cost(n_queries: int, max_places: int, prices: Prices) -> float:
    """Upper bound: every query returns its cap."""
    return round(n_queries * max_places * prices.apify_per_place_usd, 2)


def llm_cost(n_requests: int, input_tokens_per_request: int, output_tokens_per_request: int, mode: str,
             prices: Prices, cached_system_tokens: int = 0) -> float:
    if n_requests <= 0:
        return 0.0
    fresh_in = max(0, input_tokens_per_request - cached_system_tokens)
    per = (fresh_in * prices.llm_in_per_mtok + cached_system_tokens * prices.llm_cache_read_per_mtok
           + output_tokens_per_request * prices.llm_out_per_mtok) / 1e6
    total = per * n_requests
    if mode == "BATCH":
        total *= prices.batch_discount
    return round(total, 2)


# Token assumptions of the enrichment estimate (one place: capture and enrich must give the same figure).
EST_SYSTEM_TOKENS = 3500            # the system prompt, read from the prompt cache after the first request
EST_USER_OVERHEAD_TOKENS = 800      # the user message around the website content
EST_OUTPUT_TOKENS = 1200


def enrich_estimate(cfg, n_requests: int, mode: str) -> float:
    """Upper bound of the model cost of enriching n leads: every website fills its token budget."""
    tokens = int(cfg.param("token_budget_per_domain", 30000)) + EST_SYSTEM_TOKENS + EST_USER_OVERHEAD_TOKENS
    return llm_cost(n_requests, tokens, EST_OUTPUT_TOKENS, mode, Prices.from_config(cfg),
                    cached_system_tokens=EST_SYSTEM_TOKENS)


def llm_actual_cost(usages: list[dict], prices: Prices) -> float:
    """What the model calls of a run cost, from the usage the provider returned. Each usage:
    input_tokens (uncached), cache_read_tokens, cache_write_tokens, output_tokens and the mode of its call (BATCH at the
    batch discount). Reused answers from earlier runs are not calls, so they are not here."""
    total = 0.0
    for u in usages:
        c = (int(u.get("input_tokens") or 0) * prices.llm_in_per_mtok
             + int(u.get("cache_read_tokens") or 0) * prices.llm_cache_read_per_mtok
             + int(u.get("cache_write_tokens") or 0) * prices.llm_cache_write_per_mtok
             + int(u.get("output_tokens") or 0) * prices.llm_out_per_mtok) / 1e6
        total += c * (prices.batch_discount if u.get("mode") == "BATCH" else 1.0)
    return round(total, 4)


def needs_approval(estimate_usd: float, threshold_usd: float, approved_usd: float | None) -> bool:
    if estimate_usd <= threshold_usd:
        return False
    return approved_usd is None or estimate_usd > approved_usd


def overrun(actual_usd: float, approved_usd: float | None, threshold_usd: float, ratio: float) -> bool:
    """True when the real count exceeds what was approved (or the threshold, if nothing needed approval)
    by more than `ratio`."""
    base = approved_usd if approved_usd is not None else threshold_usd
    return actual_usd > base * (1 + ratio)
