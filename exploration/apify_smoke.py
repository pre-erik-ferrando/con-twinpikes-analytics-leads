"""Apify smoke test: checks the engine's actor input against the actor's schema, and optionally runs one
tiny capped search. Writes nothing to any database or file; prints to the terminal only.

    python exploration/apify_smoke.py                 # free: field-name check against the actor schema
    python exploration/apify_smoke.py --run           # paid (cents): 1 term x 1 area (default Steiermark), max 5 places, cap $0.50
                                                      # and saves the raw items to tmp/demo/places.json
    python exploration/apify_smoke.py --run --district 601   # the same, sent as Graz(Stadt)'s outline, as capture does

Reads APIFY_TOKEN from the environment or .env. Uses leadgen.apify.actor_input / item_fields, so it
tests the same code the `capture` step uses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

import requests  # noqa: E402

from leadgen import apify, geo  # noqa: E402


def token() -> str:
    if os.environ.get("APIFY_TOKEN"):
        return os.environ["APIFY_TOKEN"]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("APIFY_TOKEN="):
                return line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'")
    sys.exit("APIFY_TOKEN not set (environment or .env)")


def input_schema(tok: str, build: str) -> dict | None:
    """The actor's input schema for a build tag/number (default: 'latest'). Free API call."""
    r = requests.get(f"{apify.API}/acts/{apify.ACTOR_ID}/builds/default" if build == "latest"
                     else f"{apify.API}/acts/{apify.ACTOR_ID}/builds/{build}",
                     params={"token": tok}, timeout=60)
    if r.status_code != 200:
        print(f"  could not read build {build!r}: HTTP {r.status_code}")
        return None
    data = r.json()["data"]
    print(f"  build: {data.get('buildNumber')} (id {data.get('id')}, finished {data.get('finishedAt')})")
    raw = (data.get("actorDefinition") or {}).get("input") or data.get("inputSchema")
    return json.loads(raw) if isinstance(raw, str) else raw


def check_fields(actor_in: dict, schema: dict | None) -> int:
    if not schema:
        print("  no input schema found in the build; field names NOT verified")
        return 1
    props = schema.get("properties") or {}
    unknown = sorted(k for k in actor_in if k not in props)
    print(f"  fields we send: {len(actor_in)}; known to the actor: {len(actor_in) - len(unknown)}")
    if unknown:
        print(f"  ⚠ NOT in the actor's schema (ignored or rejected by Apify): {unknown}")
    required = sorted(k for k in schema.get("required") or [] if k not in actor_in)
    if required:
        print(f"  ⚠ required by the actor but not sent: {required}")
    for k in ("language", "website", "searchMatching", "reviewsSort"):
        enum = (props.get(k) or {}).get("enum")
        if enum and actor_in.get(k) not in enum:
            print(f"  ⚠ {k}={actor_in.get(k)!r} not among the allowed values {enum}")
    if not unknown and not required:
        print("  ✓ every field we send exists in the actor's schema")
    return 1 if unknown or required else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true", help="actually run the actor (costs money)")
    p.add_argument("--term", default="Poolbau")
    p.add_argument("--country", default="AT")
    p.add_argument("--city", default="", help="city polygon only: excludes the suburbs, where most pool firms are")
    p.add_argument("--state", default="Steiermark", help="'' to search by --city or --county instead")
    p.add_argument("--county", default="")
    p.add_argument("--no-detail", action="store_true",
                   help="scrapePlaceDetailPage=false (diagnostic; the engine needs the detail page's fields)")
    p.add_argument("--location", default="",
                   help="free-text locationQuery (e.g. 'Graz, Austria') instead of the structured fields; "
                        "diagnostic only: the engine always sends structured fields")
    p.add_argument("--box", default="",
                   help="search area as a rectangle 'min_lng,min_lat,max_lng,max_lat' sent as a customGeolocation "
                        "polygon instead of the structured fields (e.g. Graz: 15.34,46.99,15.53,47.13)")
    p.add_argument("--district", default="",
                   help="send this district's official outline, simplified as capture does (e.g. 601 = Graz(Stadt))")
    p.add_argument("--geo", default=str(ROOT / "data" / "geo" / "at_bezirke.geojson"),
                   help="boundaries file for --district (download command in docs/outline/onboarding.md)")
    p.add_argument("--code-prop", default="g_id")
    p.add_argument("--language", default="de")
    p.add_argument("--max-places", type=int, default=5)
    p.add_argument("--max-charge-usd", type=float, default=0.50)
    p.add_argument("--build", default="latest")
    p.add_argument("--save", default=str(ROOT / "tmp" / "demo" / "places.json"),
                   help="where to save the raw items with --run ('' to skip); tmp/ is gitignored")
    a = p.parse_args()

    tok = token()
    where = {k: v for k, v in (("state", a.state), ("county", a.county), ("city", a.city)) if v}
    place = ", ".join(where.values()) or a.country
    actor_in = apify.actor_input(a.term, {"countryCode": a.country, **where}, a.language, a.max_places)
    if a.no_detail:
        actor_in["scrapePlaceDetailPage"] = False
    if a.location:
        place = a.location
        actor_in.update({"locationQuery": a.location, "countryCode": "", "state": "", "county": "", "city": "",
                         "postalCode": ""})
    if a.box:
        x0, y0, x1, y1 = (float(v) for v in a.box.split(","))
        place = f"box {a.box}"
        actor_in.update({"customGeolocation": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1],
                                                                                   [x0, y1], [x0, y0]]]},
                         "locationQuery": "", "countryCode": "", "state": "", "county": "", "city": "",
                         "postalCode": ""})
    if a.district:
        feats = json.loads(Path(a.geo).read_text(encoding="utf-8"))["features"]
        f = next((f for f in feats if str(f["properties"].get(a.code_prop)) == a.district), None)
        if f is None:
            sys.exit(f"district {a.district} not in {a.geo}")
        outline = geo.outline_geojson(geo.polygons_from_geojson(f["geometry"]), 0.001)
        place = f"district {a.district} outline ({sum(len(r) for p in outline['coordinates'] for r in p)} points)"
        actor_in = apify.actor_input(a.term, {"customGeolocation": outline}, a.language, a.max_places)
        if a.no_detail:
            actor_in["scrapePlaceDetailPage"] = False

    print(f"1) Field check against {apify.ACTOR_ID}")
    bad = check_fields(actor_in, input_schema(tok, a.build))

    if not a.run:
        print("\nDry run only. Add --run to run one search "
              f"({a.term!r} in {place}, {a.country}; max {a.max_places} places; cap ${a.max_charge_usd:.2f}).")
        return bad

    print(f"\n2) Running {a.term!r} in {place}, {a.country} (max {a.max_places} places, cap ${a.max_charge_usd:.2f})…")
    cap = apify.ApifyCapturer(tok, "" if a.build == "latest" else a.build, poll_s=5, timeout_s=900)
    res = cap.run(actor_in, a.max_charge_usd)
    print(f"  run {res.apify_run_id}: {res.status}, {len(res.items)} items, cost ${res.cost_usd}")

    fields = [apify.item_fields(it) for it in res.items]
    if fields:
        print("\n  field coverage (non-empty / total):")
        for k in fields[0]:
            n = sum(1 for f in fields if f[k] not in (None, "", []))
            print(f"    {k:24} {n}/{len(fields)}")
        print("\n  places:")
        for f in fields:
            print(f"    - {f['name']} | {f['city']} {f['postal_code_raw']} | {f['main_category']} | {f['website']}")
    print(f"\n  saturated: {apify.is_saturated(len(res.items), a.max_places)}")
    if a.save:
        out = Path(a.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res.items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  raw items saved to {out} (input of exploration/demo_lead.py)")
    return 0 if res.status == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
