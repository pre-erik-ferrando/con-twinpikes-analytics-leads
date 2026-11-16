"""Sending the analysis requests to the model (DT.6.4), BATCH or DIRECT, same prompt and schema.

Provider behind an interface (`Provider`); implemented: Anthropic (Messages API with structured output
`output_config.format`, and the Message Batches API at half price). The model is a run parameter
(llm_model, [PENDIENTE] in the definition).

Deviations from DT.10.1 imposed by the current models, documented in docs/outline/decisiones.md:
- no `temperature` (sampling parameters are rejected by the current API);
- extended reasoning cannot be switched off on the current Opus: the request runs at effort `low`
  (llm_effort) instead of "without reasoning", so max_tokens must leave room for it.

Write-ahead (no double payment): the caller writes the ops.llm_batch PREPARING row with every custom_id
BEFORE calling submit_batch/send_direct, and a second row with the external id afterwards.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from .prompt import OUTPUT_SCHEMA


@dataclass
class LLMRequest:
    custom_id: str
    system: str
    user: str


@dataclass
class LLMResult:
    custom_id: str
    status: str                  # SUCCEEDED | ERRORED | EXPIRED | CANCELED | TRUNCATED | REFUSED
    text: str | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    error: str | None = None

    def parsed(self) -> dict | None:
        if self.status != "SUCCEEDED" or not self.text:
            return None
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return None


class Provider(Protocol):
    name: str
    model: str

    def send_direct(self, reqs: list[LLMRequest], max_tokens: int, concurrency: int) -> list[LLMResult]: ...
    def submit_batch(self, reqs: list[LLMRequest], max_tokens: int) -> str: ...
    def batch_ended(self, external_id: str) -> bool: ...
    def batch_results(self, external_id: str) -> list[LLMResult]: ...
    def count_tokens(self, req: LLMRequest) -> int: ...


class AnthropicProvider:
    name = "ANTHROPIC"

    def __init__(self, model: str, effort: str = "low", use_fallbacks: bool = True, client=None):
        import anthropic

        self.model, self.effort, self.use_fallbacks = model, effort, use_fallbacks
        self.client = client or anthropic.Anthropic()
        self._anthropic = anthropic

    def _params(self, req: LLMRequest, max_tokens: int) -> dict:
        # The system message is identical in every request of a run (one country, one brand list):
        # marked cacheable. Everything variable is in the user message.
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": req.user}],
            "output_config": {"effort": self.effort,
                              "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        }

    @staticmethod
    def _from_message(custom_id: str, msg) -> LLMResult:
        usage = msg.usage
        common = dict(stop_reason=msg.stop_reason, input_tokens=usage.input_tokens or 0,
                      output_tokens=usage.output_tokens or 0,
                      cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                      cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if msg.stop_reason == "refusal":
            return LLMResult(custom_id, "REFUSED", **common)
        if msg.stop_reason == "max_tokens":
            return LLMResult(custom_id, "TRUNCATED", **common)
        text = next((b.text for b in msg.content if b.type == "text"), None)
        return LLMResult(custom_id, "SUCCEEDED", text=text, **common)

    def _one(self, req: LLMRequest, max_tokens: int) -> LLMResult:
        a = self._anthropic
        try:
            if self.use_fallbacks:
                # Server-side fallback on a policy decline (not available on the Batches API).
                msg = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                       fallbacks="default", **self._params(req, max_tokens))
            else:
                msg = self.client.messages.create(**self._params(req, max_tokens))
        except a.BadRequestError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"invalid_request: {e}")
        except a.RateLimitError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"rate_limited: {e}")
        except a.APIStatusError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"api_{e.status_code}: {e}")
        except a.APIConnectionError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"connection: {e}")
        return self._from_message(req.custom_id, msg)

    def send_direct(self, reqs: list[LLMRequest], max_tokens: int, concurrency: int) -> list[LLMResult]:
        # The SDK retries 429/5xx with growing waits (max_retries); concurrency is capped here.
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            return list(pool.map(lambda r: self._one(r, max_tokens), reqs))

    def submit_batch(self, reqs: list[LLMRequest], max_tokens: int) -> str:
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        batch = self.client.messages.batches.create(requests=[
            Request(custom_id=r.custom_id, params=MessageCreateParamsNonStreaming(**self._params(r, max_tokens)))
            for r in reqs])
        return batch.id

    def batch_ended(self, external_id: str) -> bool:
        return self.client.messages.batches.retrieve(external_id).processing_status == "ended"

    def batch_results(self, external_id: str) -> list[LLMResult]:
        out = []
        # Results arrive in any order: keyed by custom_id, never by position.
        for r in self.client.messages.batches.results(external_id):
            kind = r.result.type
            if kind == "succeeded":
                out.append(self._from_message(r.custom_id, r.result.message))
            elif kind == "errored":
                out.append(LLMResult(r.custom_id, "ERRORED", error=str(r.result.error.type)))
            else:
                out.append(LLMResult(r.custom_id, kind.upper()))
        return out

    def recent_batches(self, limit: int = 20) -> list[dict]:
        """For recovery of a PREPARING row with no external id: recent batches with their size."""
        return [{"id": b.id, "created_at": str(b.created_at), "status": b.processing_status,
                 "total": sum(getattr(b.request_counts, k) for k in ("processing", "succeeded", "errored",
                                                                       "canceled", "expired"))}
                for b in self.client.messages.batches.list(limit=limit)]

    def count_tokens(self, req: LLMRequest) -> int:
        p = self._params(req, 1)
        res = self.client.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"])
        return res.input_tokens


def make_provider(cfg) -> Provider:
    provider = (cfg.param("llm_provider") or "ANTHROPIC").upper()
    if provider != "ANTHROPIC":
        raise NotImplementedError(f"llm_provider={provider}: only ANTHROPIC is implemented")
    return AnthropicProvider(model=cfg.param("llm_model") or "claude-opus-5-5",
                             effort=cfg.param("llm_effort") or "low",
                             use_fallbacks=bool(cfg.param("llm_fallbacks", True)))


def choose_mode(requested: str, n_requests: int, direct_max: int) -> str:
    """AUTO: up to llm_direct_max_requests (after filter, first match and cache) go DIRECT, else BATCH."""
    if requested in ("BATCH", "DIRECT"):
        return requested
    return "DIRECT" if n_requests <= direct_max else "BATCH"


def split_batches(reqs: list[LLMRequest], max_requests: int, max_bytes: int = 200 * 1024 * 1024) -> list[list[LLMRequest]]:
    """Batches of at most llm_batch_max_requests, with a byte check under the provider's limit (256 MB)."""
    out, cur, size = [], [], 0
    for r in reqs:
        n = len(r.system.encode()) + len(r.user.encode()) + 2048
        if cur and (len(cur) >= max_requests or size + n > max_bytes):
            out.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        out.append(cur)
    return out
