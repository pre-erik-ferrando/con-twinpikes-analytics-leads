# Demo: from a Google Maps place to a Salesforce lead

`demo_lead.py` runs the real engine in memory (no database). Output in `tmp/demo/` (gitignored):
`places.json` (raw Apify), `stages/NN_<step>/<table>.csv` (rows each step added), `prompts/` and
`answers/` (the model's part), `trace.md` (each place, step by step), `export/` (Salesforce file).

| # | Stage | Table(s) | What happens |
|---|---|---|---|
| 0 | Apify | `places.json` | One term × one area, capped. Raw JSON per place |
| 1 | Landing | `gmaps_run`, `gmaps_place`, `search_query_log` | Input and items stored as received, never edited |
| 1 | Normalize | `place_norm_out` | Phone to E.164, website to domain, name keys, postcode, subregion |
| 1 | Identity | `business_link_out`, `capture_batch_lead` | Place → business; the same company keeps its id across searches |
| 1 | Filter | `rule_evaluation_out`, `category_seen_out` | Google categories and title rules: INCLUDE or EXCLUDE, with the rule that fired |
| 1 | Customer match | `customer_match_out` (CAPTURE) | Existing customers drop out before any spending |
| 2 | Crawl | `web_crawl`, `web_page` | Website pages, up to 50: Impressum and contact first, then about, brands, products and shop |
| 2 | Model | `llm_request`, `llm_response`, `web_validation_out` | VALID / NOT_VALID / REVIEW, company type, legal data, contact, brands, e-commerce; each claim checked against its cited page |
| 2 | Customer match | `customer_match_out` (ENRICH) | Again, with what the website revealed |
| 2 | Score | `score_component_out`, `score_out` | 6 factors (type, brands, zone, digital, contact, reviews) → COLD / WARM / HOT |
| 3 | Sales Ops | `biz_lead_validation` | ACCEPTED or EXCLUDED; the latest row wins |
| 4 | Export | `export_lead_out`, `export_log`, `.xlsx` | Salesforce file; a lead is exported only once |

## Example: one place through every stage

Invented values: the real ones are in `tmp/demo/trace.md` (not committed, as they are leads).

**0. Apify, raw request** (`gmaps_run.input_json`: what the engine sends for one term × one area)

```json
{"searchStringsArray": ["Poolbau"], "locationQuery": "", "countryCode": "", "state": "",
 "county": "", "city": "", "postalCode": "",
 "customGeolocation": {"type": "MultiPolygon", "coordinates": [[[[15.4071, 47.0166], …112 points]]]},
 "language": "de",
 "maxCrawledPlacesPerSearch": 5, "searchMatching": "all", "placeMinimumStars": "", "website": "allPlaces",
 "skipClosedPlaces": false, "scrapePlaceDetailPage": true, "scrapeTableReservationProvider": false,
 "includeWebResults": false, "scrapeDirectories": false, "maxQuestions": 0, "scrapeContacts": false,
 "maximumLeadsEnrichmentRecords": 0, "leadsEnrichmentDepartments": [], "maxReviews": 0,
 "reviewsSort": "newest", "scrapeReviewsPersonalData": false, "maxImages": 0, "scrapeImageAuthors": false,
 "scrapeSocialMediaProfiles": {"facebooks": false, "instagrams": false, "youtubes": false,
                               "tiktoks": false, "twitters": false},
 "allPlacesNoSearchAction": ""}
```

The area goes as its official outline (Graz(Stadt), from `load-geo`), not by name: by name, Apify
discards every place as out of location ([findings.md](findings.md)). The demo's saved places came from
an earlier free-text search (`locationQuery: "Graz, Austria"`); the demo itself uses a rectangle around Graz.

**0. Apify, raw response** (`gmaps_place.item_json`: one item per place; 61 keys, the ones the
engine uses are shown)

```json
{"title": "Poolbau Muster", "categoryName": "Schwimmbeckenbauer",
 "categories": ["Schwimmbeckenbauer", "Poolbedarf"], "description": null,
 "address": "Kärntner Str. 1, 8053 Graz, Österreich", "street": "Kärntner Str. 1", "city": "Graz",
 "postalCode": "8053", "countryCode": "AT", "location": {"lat": 47.04, "lng": 15.41},
 "phone": "+43 316 123456", "phoneUnformatted": "+43316123456",
 "website": "http://www.poolbau-muster.at/", "totalScore": 4.6, "reviewsCount": 40,
 "permanentlyClosed": false, "temporarilyClosed": false, "placeId": "ChIJ…xyz",
 "scrapedAt": "2026-11-17T16:21:05.000Z", "searchString": "Poolbau", "rank": 3,
 "url": "https://www.google.com/maps/search/?api=1&query=…&query_place_id=ChIJ…xyz"}
```

**1. Normalize** (`place_norm_out`)

| place_id | name | phone_e164 | phone_type | domain | postal_code | main_category |
|---|---|---|---|---|---|---|
| ChIJ…xyz | Poolbau Muster | +43316123456 | FIXED | poolbau-muster.at | 8053 | Schwimmbeckenbauer |

**1. Identity** (`business_link_out`)

| place_id | business_id | link_reason |
|---|---|---|
| ChIJ…xyz | 2236…952 | NEW |

**1. Filter** (`rule_evaluation_out`)

| business_id | decision | stage | rule | trigger_value |
|---|---|---|---|---|
| 2236…952 | INCLUDE | | CATEGORY_INCLUDE | schwimmbeckenbauer |

**1. Customer match** (`customer_match_out`)

| business_id | match_pass | outcome | signal |
|---|---|---|---|
| 2236…952 | CAPTURE | NO_EXTRACT | |

**2. Crawl** (`web_crawl`, `web_page`)

| domain | crawl_status | n_pages | pages |
|---|---|---|---|
| poolbau-muster.at | OK | 3 | /impressum (P1), /kontakt (P2), / (P3) |

**2. Model** (`web_validation_out`, some fields)

| validation | validation_reason_text | company_type | offering | contact_name | email | has_ecommerce |
|---|---|---|---|---|---|---|
| VALID | Plans, builds and services pools and supplies chemicals and pool cleaners | BUILDER_INSTALLER_SHOP | BOTH | Max Muster (P1) | office@poolbau-muster.at (P1) | 0 |

**2. Score** (`score_component_out` → `score_out`)

| factor | bucket_code | points |
|---|---|---|
| COMPANY_TYPE | BUILDER_INSTALLER_SHOP | 10 |
| BRANDS | NONE | 0 |
| ZONE | NONE | 0 |
| DIGITAL | OWN_WEBSITE | 2.5 |
| CONTACT | PERSON_EMAIL_PHONE | 5 |
| REVIEWS | ABOVE_P75 | 2.5 |
| **total** | | **20 → WARM** (WARM ≥ 20, HOT ≥ 30) |

**3. Sales Ops** (`biz_lead_validation`)

| business_id | decision | approval_scope | reviewed_by |
|---|---|---|---|
| 2236…952 | ACCEPTED | REST_OF_BATCH | demo |

**4. Export** (Salesforce file, some columns)

| Lead code | Company | Last Name | Email | Phone | Rating | E-Commerce |
|---|---|---|---|---|---|---|
| AT-2236…952 | Poolbau Muster | Max Muster | office@poolbau-muster.at | +43316123456 | Warm | False |

Across all steps: `pipeline_run` (state of each run) and `cost_ledger` (estimated and actual cost).

**Not in the demo:** the configuration in force (`biz_cfg_*`), the dbt views (`lgn_v_lead` and the
others, which need ClickHouse), and TWP's inputs (customer extract, zones, brands, category
rules). Without those, brand and zone points are always 0.
