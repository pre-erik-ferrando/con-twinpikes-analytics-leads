"""Identity and deduplication (DT.4.2).

- place_id is the key of a Google Maps listing.
- business_id is fixed: assigned once as cityHash64(place_id) of the first listing of the group and
  never recalculated. The hash comes from ClickHouse (`hash64` is injected) so Python and SQL agree.
- A new listing with an already registered phone inherits that business, only if the name similarity
  (token_sort_ratio) is >= merge_name_similarity_min or the distance is <= merge_max_distance_m; else
  both stay apart and are flagged is_merge_suspect.
- If a listing joins two businesses, the oldest survives and the other becomes an alias.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz.fuzz import token_sort_ratio


@dataclass
class Listing:
    place_id: str
    name_key: str
    phone_e164: str | None
    latitude: float | None
    longitude: float | None
    first_seen_at: str = ""          # ISO; orders the "first listing of the group"


@dataclass
class Business:
    business_id: int
    first_seen_at: str
    place_ids: list[str]
    phones: set[str]
    name_keys: set[str]
    coords: list[tuple[float, float]]


@dataclass
class IdentityResult:
    links: list[dict] = field(default_factory=list)       # place_id -> business_id, with link_reason
    aliases: list[dict] = field(default_factory=list)     # old_id -> surviving_id
    merge_suspects: list[dict] = field(default_factory=list)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _compatible(listing: Listing, biz: Business, min_similarity: float, max_distance_m: float) -> bool:
    if any(token_sort_ratio(listing.name_key, k) >= min_similarity for k in biz.name_keys):
        return True
    if listing.latitude is not None and listing.longitude is not None:
        return any(haversine_m(listing.latitude, listing.longitude, la, lo) <= max_distance_m
                   for la, lo in biz.coords)
    return False


def assign(listings: list[Listing], businesses: list[Business], known_links: dict[str, int],
           hash64: Callable[[str], int], min_similarity: float = 30, max_distance_m: float = 500,
           now: str = "") -> IdentityResult:
    """Links each new listing to a business. `businesses` is the current state (mutated in place),
    `known_links` maps already-linked place_ids to their business (they are never relinked)."""
    by_id = {b.business_id: b for b in businesses}
    by_phone: dict[str, list[Business]] = {}
    for b in businesses:
        for p in b.phones:
            by_phone.setdefault(p, []).append(b)
    out = IdentityResult()

    for li in sorted(listings, key=lambda x: (x.first_seen_at, x.place_id)):
        if li.place_id in known_links:
            continue
        candidates = by_phone.get(li.phone_e164, []) if li.phone_e164 else []
        compatible = [b for b in candidates if _compatible(li, b, min_similarity, max_distance_m)]
        for b in candidates:
            if b not in compatible:
                out.merge_suspects.append({"place_id": li.place_id, "business_id": b.business_id,
                                           "reason": "PHONE_WITHOUT_NAME_OR_DISTANCE"})
        if compatible:
            compatible.sort(key=lambda b: (b.first_seen_at, b.business_id))
            target = compatible[0]
            reason = "PHONE"
            for other in compatible[1:]:       # the listing joins two businesses: the oldest survives
                out.aliases.append({"old_id": other.business_id, "surviving_id": target.business_id,
                                    "reason": "LISTING_JOINS_BUSINESSES", "linked_at": now})
                target.place_ids += other.place_ids
                target.phones |= other.phones
                target.name_keys |= other.name_keys
                target.coords += other.coords
                for p in other.phones:
                    by_phone[p] = [b for b in by_phone.get(p, []) if b is not other]
                    if target not in by_phone[p]:
                        by_phone[p].append(target)
                del by_id[other.business_id]
        else:
            bid = hash64(li.place_id)
            target = Business(bid, li.first_seen_at, [], set(), set(), [])
            by_id[bid] = target
            businesses.append(target)
            reason = "NEW"
        target.place_ids.append(li.place_id)
        target.name_keys.add(li.name_key)
        if li.latitude is not None and li.longitude is not None:
            target.coords.append((li.latitude, li.longitude))
        if li.phone_e164:
            target.phones.add(li.phone_e164)
            if target not in by_phone.setdefault(li.phone_e164, []):
                by_phone[li.phone_e164].append(target)
        known_links[li.place_id] = target.business_id
        out.links.append({"place_id": li.place_id, "business_id": target.business_id, "link_reason": reason,
                          "linked_at": now})
    return out


def resolve_alias(business_id: int, aliases: dict[int, int]) -> int:
    """Follows old_id -> surviving_id chains (validations, exclusions and Salesforce statuses follow the alias)."""
    seen = set()
    while business_id in aliases and business_id not in seen:
        seen.add(business_id)
        business_id = aliases[business_id]
    return business_id


MASTER_FIELDS = ("name", "address", "latitude", "longitude", "website", "phone_raw", "postal_code_raw", "city",
                 "owner_description")


def pick_master(places: list[dict]) -> dict:
    """The listing that governs a business's name, address, coordinates and website: the one with the
    most filled fields, then the most reviews (then place_id, for determinism)."""
    def filled(p):
        return sum(1 for f in MASTER_FIELDS if p.get(f) not in (None, ""))
    return sorted(places, key=lambda p: (-filled(p), -(p.get("reviews_count") or 0), p["place_id"]))[0]
