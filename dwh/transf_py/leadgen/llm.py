"""Sending the analysis requests to the model (DT.6.4), BATCH or DIRECT, same prompt and schema.

Provider behind an interface (`Provider`), chosen with llm_provider (DT.10.1: "Anthropic u OpenAI"):
- Anthropic: Messages API with structured output `output_config.format`, Message Batches at half price.
- OpenAI: Responses API with strict structured output `text.format`, Batch API on /v1/responses at half
  price. Responses are not stored on OpenAI's side (`store: false`): they carry personal data.
The model is a run parameter (llm_model).

Deviations from DT.10.1 imposed by the current models, documented in docs/outline/decisiones.md:
- no `temperature` (sampling parameters are rejected by the current API);
- extended reasoning cannot be switched off on the current Opus: the request runs at effort `low`
  (llm_effort) instead of "without reasoning", so max_tokens must leave room for it.

Write-ahead (no double payment): the caller writes the ops.llm_batch PREPARING row with every custom_id
BEFORE calling submit_batch/send_direct, and a second row with the external id afterwards. A batch is
tagged with our batch id, so a PREPARING row without its SUBMITTED row (a crash in between) is found at
the provider (`find_batch`) instead of being sent again.
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
    def submit_batch(self, reqs: list[LLMRequest], max_tokens: int, tag: str) -> str: ...
    def find_batch(self, tag: str, since_epoch: float) -> str | None: ...
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

    def submit_batch(self, reqs: list[LLMRequest], max_tokens: int, tag: str) -> str:
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

    def find_batch(self, tag: str, since_epoch: float) -> str | None:
        # Message Batches carry no metadata: a batch sent without its SUBMITTED row cannot be told apart, and
        # sending it again could pay twice. Stop and let a person look in the console.
        raise RuntimeError(f"batch {tag} may have reached Anthropic without its id being recorded: check the "
                           "console's Message Batches, then cancel this run")

    def count_tokens(self, req: LLMRequest) -> int:
        p = self._params(req, 1)
        res = self.client.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"])
        return res.input_tokens


class OpenAIProvider:
    """OpenAI Responses API. Same request as Anthropic's: the system message (identical in every request of a
    run: one country, one brand list) and the lead, the output schema in strict mode (every property
    required, optional ones as null, additionalProperties false: OUTPUT_SCHEMA already is). Token counts are
    reported as Anthropic's: input without cache, cache reads and cache writes apart.

    Caching: by default OpenAI puts an implicit breakpoint at the end of the request, so every request was
    written to the cache whole (1.25x input) and never read, since each lead's website is unique (first run,
    exploration/findings.md, 2026-11-23). Here the system message goes as a `developer` message ending in an
    explicit breakpoint and `prompt_cache_options.mode` is `explicit`: only the shared prefix is written,
    and the rest of the run reads it. `prompt_cache_key` (a hash of the system message) routes a run's
    requests to the same cache."""
    name = "OPENAI"
    TERMINAL = ("completed", "failed", "expired", "cancelled")
    TAG_KEY = "leadgen_batch_id"          # batch metadata: our llm_batch id

    def __init__(self, model: str, effort: str = "low", client=None):
        if client is None:
            import openai
            client = openai.OpenAI()                    # OPENAI_API_KEY from the environment
        self.model, self.effort, self.client = model, effort, client

    def _params(self, req: LLMRequest, max_tokens: int) -> dict:
        import hashlib

        return {
            "model": self.model,
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": req.system,
                                                   "prompt_cache_breakpoint": {"mode": "explicit"}}]},
                {"role": "user", "content": [{"type": "input_text", "text": req.user}]},
            ],
            "prompt_cache_options": {"mode": "explicit"},
            "prompt_cache_key": "leadgen-" + hashlib.sha256(req.system.encode()).hexdigest()[:16],
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": self.effort},
            "text": {"format": {"type": "json_schema", "name": "lead_analysis", "schema": OUTPUT_SCHEMA,
                                "strict": True}},
            "store": False,
        }

    @staticmethod
    def _get(obj, key, default=None):
        """Field of an SDK object (direct calls) or of a plain dict (batch output files)."""
        if obj is None:
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    @classmethod
    def _from_response(cls, custom_id: str, resp) -> LLMResult:
        g = cls._get
        usage = g(resp, "usage")
        details = g(usage, "input_tokens_details")
        cached, written = int(g(details, "cached_tokens") or 0), int(g(details, "cache_write_tokens") or 0)
        common = dict(input_tokens=max(0, int(g(usage, "input_tokens") or 0) - cached - written),
                      output_tokens=int(g(usage, "output_tokens") or 0),
                      cache_read_tokens=cached, cache_write_tokens=written)
        status = g(resp, "status")
        text, refused = None, False
        for item in g(resp, "output") or []:
            if g(item, "type") != "message":
                continue
            for part in g(item, "content") or []:
                if g(part, "type") == "output_text":
                    text = (text or "") + (g(part, "text") or "")
                elif g(part, "type") == "refusal":
                    refused = True
        reason = g(g(resp, "incomplete_details"), "reason")
        if refused or reason == "content_filter":
            return LLMResult(custom_id, "REFUSED", stop_reason="refusal", **common)
        if status == "incomplete" and reason == "max_output_tokens":
            return LLMResult(custom_id, "TRUNCATED", stop_reason="max_tokens", **common)
        if status != "completed":
            error = g(g(resp, "error"), "message") or f"status {status}"
            return LLMResult(custom_id, "ERRORED", stop_reason=status, error=str(error), **common)
        return LLMResult(custom_id, "SUCCEEDED", text=text, stop_reason="end_turn", **common)

    def _one(self, req: LLMRequest, max_tokens: int) -> LLMResult:
        import openai as o
        try:
            resp = self.client.responses.create(**self._params(req, max_tokens))
        except o.BadRequestError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"invalid_request: {e}")
        except o.RateLimitError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"rate_limited: {e}")
        except o.APIStatusError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"api_{e.status_code}: {e}")
        except o.APIConnectionError as e:
            return LLMResult(req.custom_id, "ERRORED", error=f"connection: {e}")
        return self._from_response(req.custom_id, resp)

    def send_direct(self, reqs: list[LLMRequest], max_tokens: int, concurrency: int) -> list[LLMResult]:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            return list(pool.map(lambda r: self._one(r, max_tokens), reqs))

    def batch_lines(self, reqs: list[LLMRequest], max_tokens: int) -> bytes:
        """The batch input file: one JSON line per request (custom_id, method, url, body)."""
        return "".join(json.dumps({"custom_id": r.custom_id, "method": "POST", "url": "/v1/responses",
                                   "body": self._params(r, max_tokens)}, ensure_ascii=False) + "\n"
                       for r in reqs).encode("utf-8")

    def submit_batch(self, reqs: list[LLMRequest], max_tokens: int, tag: str) -> str:
        f = self.client.files.create(file=("lead_requests.jsonl", self.batch_lines(reqs, max_tokens)),
                                     purpose="batch")
        batch = self.client.batches.create(input_file_id=f.id, endpoint="/v1/responses", completion_window="24h",
                                           metadata={self.TAG_KEY: tag})
        return batch.id

    def find_batch(self, tag: str, since_epoch: float) -> str | None:
        """The batch sent with this tag, looking back to a day before since_epoch (newest first)."""
        for b in self.client.batches.list(limit=100):        # the SDK pages through older ones
            if (self._get(b, "metadata") or {}).get(self.TAG_KEY) == tag:
                return self._get(b, "id")
            if (self._get(b, "created_at") or 0) < since_epoch - 86400:
                return None
        return None

    def batch_ended(self, external_id: str) -> bool:
        return self.client.batches.retrieve(external_id).status in self.TERMINAL

    def _file_lines(self, file_id: str | None) -> list[dict]:
        if not file_id:
            return []
        content = self.client.files.content(file_id)
        raw = content.text if hasattr(content, "text") else content.read().decode("utf-8")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def parse_batch_line(self, line: dict) -> LLMResult:
        cid = line.get("custom_id")
        err = line.get("error")
        if err:
            code = (err.get("code") if isinstance(err, dict) else None) or "error"
            return LLMResult(cid, "EXPIRED" if code == "batch_expired" else "ERRORED", error=str(code))
        response = line.get("response") or {}
        if int(response.get("status_code") or 0) != 200:
            return LLMResult(cid, "ERRORED", error=f"http_{response.get('status_code')}")
        return self._from_response(cid, response.get("body") or {})

    def batch_results(self, external_id: str) -> list[LLMResult]:
        # Results arrive in any order: keyed by custom_id. Failed and expired requests are in the error file;
        # a request in neither file (a failed or cancelled batch) gets no result and is retried by the caller.
        b = self.client.batches.retrieve(external_id)
        return [self.parse_batch_line(line)
                for line in self._file_lines(b.output_file_id) + self._file_lines(b.error_file_id)]

    def count_tokens(self, req: LLMRequest) -> int:
        p = self._params(req, 1)
        res = self.client.responses.input_tokens.count(model=p["model"], input=p["input"], text=p["text"],
                                                       reasoning=p["reasoning"])
        return res.input_tokens


def make_provider(cfg) -> Provider:
    provider, model = cfg.param("llm_provider"), cfg.param("llm_model")
    if not provider or not model:              # required by the precheck: never a model chosen by the code
        raise ValueError("run parameters llm_provider and llm_model are required in biz_cfg_run_parameter")
    provider = provider.upper()
    if provider == "OPENAI":
        return OpenAIProvider(model=model, effort=cfg.param("llm_effort") or "low")
    if provider != "ANTHROPIC":
        raise NotImplementedError(f"llm_provider={provider}: ANTHROPIC or OPENAI")
    return AnthropicProvider(model=model,
                             effort=cfg.param("llm_effort") or "low",
                             use_fallbacks=bool(cfg.param("llm_fallbacks", True)))


def choose_mode(requested: str, n_requests: int, direct_max: int) -> str:
    """AUTO: up to llm_direct_max_requests (after filter, first match and cache) go DIRECT, else BATCH."""
    if requested in ("BATCH", "DIRECT"):
        return requested
    return "DIRECT" if n_requests <= direct_max else "BATCH"


def split_batches(reqs: list[LLMRequest], max_requests: int, max_bytes: int = 180 * 1024 * 1024) -> list[list[LLMRequest]]:
    """Batches of at most llm_batch_max_requests, with a byte check under the providers' limits (Anthropic
    256 MB per batch, OpenAI 200 MB per input file)."""
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
