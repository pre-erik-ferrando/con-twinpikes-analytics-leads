"""OpenAI smoke test: one invented lead through the same request the `enrich` step builds, before the first
real run. Writes nothing to any database or file; prints to the terminal only.

    python exploration/openai_smoke.py           # free: builds the request, prints its size and estimated cost
    python exploration/openai_smoke.py --run     # paid (about a cent): one DIRECT call to the configured model,
                                                 # then the same checks enrich runs on the answer

Reads OPENAI_API_KEY from the environment or .env. Model, effort and prices come from the seeds
(biz_cfg_run_parameter); --model overrides the model. The lead is invented (example.com domain).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

from leadgen import prompt, seed_loader  # noqa: E402
from leadgen.llm import LLMRequest, OpenAIProvider  # noqa: E402
from leadgen.response import SchemaError, check_answer, check_schema  # noqa: E402

PAGES = [
    ("P1", "https://www.poolbau-muster.example/impressum",
     "Impressum\nPoolbau Muster GmbH\nSeeuferstraße 12, 9500 Villach, Österreich\nTel. +43 4242 123456\n"
     "E-Mail: office@poolbau-muster.example\nGeschäftsführer: Max Muster\nUID: ATU12345678\n"
     "Firmenbuch: FN 123456a, Landesgericht Klagenfurt"),
    ("P2", "https://www.poolbau-muster.example/",
     "Ihr Poolbauer in Kärnten. Wir planen und bauen Schwimmbecken aus Beton und Edelstahl, sanieren alte "
     "Pools und kümmern uns um die Wartung. Im Shop: Poolchemie, Poolroboter und Ersatzteile.\n"
     "Wir arbeiten mit Speck Pumpen, Bayrol Wasserpflege und Zodiac Poolrobotern."),
]


def env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'") or None
    return None


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--run", action="store_true", help="make the paid call (about a cent)")
    a.add_argument("--model", help="override llm_model from the seeds")
    ns = a.parse_args()

    seeds = seed_loader.read_csv_seeds()
    params = {r["parameter"]: r["value"] for r in seeds["biz_cfg_run_parameter"]}
    model = ns.model or params["llm_model"]
    effort, max_out = params.get("llm_effort") or "low", int(params.get("llm_max_tokens_out") or 2000)
    price_in, price_out = float(params["llm_price_in_per_mtok"]), float(params["llm_price_out_per_mtok"])
    brands = seeds["biz_cfg_brand"] + [
        {"brand": "SPECK", "owner": "COMPETITOR", "brand_group": 1, "aliases": [], "disambiguation_note": None},
        {"brand": "BAYROL", "owner": "COMPETITOR", "brand_group": 1, "aliases": [], "disambiguation_note": None}]
    competitor = [b for b in brands if b["owner"] == "COMPETITOR"]
    fluidra = [b for b in brands if b["owner"] == "FLUIDRA"]

    system = prompt.system_message("AT", "POOL_SPECIALIST", competitor, fluidra)
    user = prompt.user_message(
        {"name": "Poolbau Muster", "categories": ["Schwimmbeckenbauer", "Poolbedarf"], "owner_description": None,
         "address": "Seeuferstraße 12, 9500 Villach, Österreich", "country": "AT",
         "website": "https://www.poolbau-muster.example/", "phone": "+434242123456"},
        [], "OK", "complete", [], [("SPECK", "P2", "Wir arbeiten mit Speck Pumpen")], PAGES)
    req = LLMRequest(prompt.custom_id(1, prompt.prompt_version("AT", "POOL_SPECIALIST")), system, user)
    est_in = (len(system) + len(user)) // 4
    print(f"model {model}, effort {effort}, max_output_tokens {max_out}")
    print(f"request: system {len(system):,} chars, user {len(user):,} chars, about {est_in:,} input tokens")
    print(f"estimated cost, direct: ${(est_in * price_in + max_out * price_out) / 1e6:.4f} at most")
    if not ns.run:
        print("\nDry run. Add --run for the paid call.")
        return 0

    key = env("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY not set (environment or .env)")
    import openai
    prov = OpenAIProvider(model, effort, client=openai.OpenAI(api_key=key))
    r = prov.send_direct([req], max_out, 1)[0]
    cost = (r.input_tokens * price_in + r.cache_read_tokens * float(params["llm_price_cache_read_per_mtok"])
            + r.cache_write_tokens * float(params["llm_price_cache_write_per_mtok"])
            + r.output_tokens * price_out) / 1e6
    print(f"\nstatus {r.status} ({r.stop_reason}); tokens in {r.input_tokens:,} (+{r.cache_read_tokens:,} cached), "
          f"out {r.output_tokens:,}; cost ${cost:.4f}")
    if r.status != "SUCCEEDED":
        print("error:", r.error)
        return 1
    raw = r.parsed()
    if raw is None:
        print("the answer is not valid JSON:\n", (r.text or "")[:500])
        return 1
    try:
        check_schema(raw)
        print("schema: OK")
    except SchemaError as e:
        print("schema: FAILED:", e)
        return 1
    checked = check_answer(raw, {pid: (url, text) for pid, url, text in PAGES}, "AT", competitor, fluidra, [], True)
    print("answer:", json.dumps({k: raw.get(k) for k in ("validation", "validation_reason", "company_type", "offering",
                                                        "legal_name", "vat_id", "contact_name", "email", "phone",
                                                        "ecommerce", "detected_brands", "fluidra_brands")},
                                ensure_ascii=False, indent=1))
    print("claims dropped by the page check:", checked.n_unsupported_claims,
          "| after the check: validation", checked.validation, "| vat", checked.vat_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
