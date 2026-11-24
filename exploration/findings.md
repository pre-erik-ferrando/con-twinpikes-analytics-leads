# Findings

Things learned while testing that matter for later decisions. Newest first.

## 2026-11-24 · Import list: one business by name, location as free text

IMPORT_LIST used to send the row's city by name in the structured fields, which returns nothing (see
18 Nov). It now sends `locationQuery` "City, Country" with the structured fields empty and no outline.
Tested with the engine's own input (`apify_smoke.py --run --build 0.14.793 --term "Cranpool Center Graz"
--location "Graz, Austria" --max-places 3`, run THhIJPNf6WUcTiuHW, $0.005): 1 item, Cranpool Center Graz
(8053, Schwimmbeckenbauer), every field the engine reads filled except the owner's description.

The smoke script's field check fails with a build number (`--build 0.14.793`: HTTP 404, the endpoint wants
the build id); the run itself does use that build. Without `--build` it checks the default build.

## 2026-11-24 · Control sets for Germany and Switzerland

Same rules as Austria (below): only entries with a website or phone, phones to E.164 and domains with the
engine's own functions, `MUST_EXCLUDE` when TWP's v4 name exclusions (HARD) catch the name or the
company is a competitor's branch. In `seeds/biz_cfg_control_set.csv`; `python leadgen.py seed` adds them
(it inserts only keys that don't exist yet).

- **Germany, source BSW (179):** bsw-web.de, "Übersicht Schwimmbadbaufachunternehmen", one static list of
  certified pool builders (maybe a subset of all members). One competitor: Desjoyaux Pools Freising (the
  name rule catches it). Branches kept as separate rows. Ortlieb (Umkirch) has no website: its e-mail domain.
- **Switzerland, source AQUASUISSE (60):** aqua suisse (Swiss association for water and pool technology,
  about 150 members); its member map, category "Becken Beschaffung" (62), read through the page's own
  search endpoint. Dropped: Piscines et Spas Arnold (no contact) and Fehlmann's French duplicate.
  `MUST_EXCLUDE MANUFACTURER`: CF Pool & Chemie (Chemoform, in TWP's manufacturers; the name rule does not
  catch it, so coverage shows whether the model does). Chemicals and water-treatment firms (Chemia,
  Bioexam, Fehlmann) stay MUST_INCLUDE, as the Austrian manufacturers did.
- **Switzerland, source DESJOYAUX (7):** the exclusive dealers on desjoyaux.ch (Händlersuche) and their
  sites, all `MUST_EXCLUDE COMPETITOR` (Desjoyaux is a HARD competitor rule in v4). Their company names
  don't say Desjoyaux; their Google names may, and coverage will show it. desjoyauxfribourg.ch did not resolve.
- The coverage figure needs a capture in DE or CH: no run there yet.

## 2026-11-18 · Apify: give the search area as a map outline, not by name

**Problem.** With the area given by name, as spec DT.3.2 asks (`countryCode` + `state`/`county`/`city`),
Apify returns 0 places. It finds the area (Styria, 16,371 km²) and searches all of it, but marks every
distinct place it sees (246) as out of location. Likely an Apify bug (our reading, not confirmed by Apify).

| Test (Poolbau, max 5 places) | Places |
|---|---|
| `state: Steiermark`, detail page on (as the spec asks) | 0 |
| `state: Steiermark`, detail page off | 0 |
| `state: Steiermark`, previous build 0.14.792 | 0 |
| Free text `Graz, Austria`, detail page on | 5 |
| Outline (`customGeolocation`, a rectangle around Graz), detail page on | 5, all in Graz |
| Graz(Stadt)'s official outline, simplified as capture does (112 points), detail page on | 5, all in Graz(Stadt) |

So the cause is naming the area, not the detail page, and not a change between the two builds tested.

**Decision.** Send each search area as its district's outline (`customGeolocation`), built by capture
from the boundaries loaded with `load-geo` (a state is the union of its districts). It keeps the search
inside the area, which is what the spec wants, without free text. Recorded in
[decisiones.md](../docs/outline/decisiones.md). Confirmed with Graz(Stadt)'s real outline (run
VvIgdWt0Ing7M9ehW, $0.025).

**Still open:**
- Outlines come from the sources the spec names: Statistik Austria, BKG VG250 and swisstopo (all
  three loaded, see below).
- A state sent as many touching districts (Styria: 13) is untested against Apify.
- It changes how spec DT.3.2 sends the area: TWP told with the questions (19 Nov).

## 2026-11-23 · Second real run (Styria): the SQL filter and score on new data

First run with the filter and the score computed only in SQL (`cb_a16b28159957`, AT-S-STEIERMARK, terms
Poolbau, Poolbauer, Poolshop, Poolservice). Before it, on the shared database, the SQL filter matched the
old Python filter on every Carinthia business (same decision, stage and rule).

| Step | Result |
|---|---|
| Businesses | 69 (71 listings) |
| Not pool businesses (filter) | 17 |
| Customers, from Google's data | 35 |
| Sent to the website and the model | 17 |
| New leads | 12 (2 HOT, 4 WARM, 6 COLD); 5 of them REVIEW, 1 NOT_VALID |

Cost about $0.51 OpenAI (Apify in cost_ledger). Again most pool businesses are customers (35 of 69 before
the website).

**The 17 exclusions:** OBI stores (3, KA_CHAIN), construction firms with no pool word (4), and one each of
unrelated categories (bar, bus stop, surveyor…). Two are doubtful, both TWP's call (preguntas_twp.md 3-4):
a pool builder (category Schwimmbeckenbauer) whose name has «Therme», a HARD name rule that beats INCLUDE;
and a whirlpool dealer (Whirlpool-Fachhandel, CONDITIONAL) whose name has no pool word: «whirl» does not
start with a pool stem.

## 2026-11-23 · First real run (Carinthia): the filter loses pool businesses with unlisted categories

**Search unit.** Villach Land (207) and Villach Stadt (202) returned 1 business each. Apify's logs: Google
shows the same 30-60 "Poolbau" businesses of the whole region from every point of the grid, and Apify keeps
only those inside the outline (Villach Stadt: 33-37 distinct per term, all but one "out of location"; free
text "Villach" the same). A control in Graz returned 5 inside Graz, so Apify works: the district is too small
for a sparse market. Search by state there.

**Carinthia (AT-S-KARNTEN, first whole-state search): 40 places, 38 businesses.**

| Step | Result |
|---|---|
| Not pool businesses (filter) | 13 |
| Customers, from Google's data | 17 |
| Customers, from the website (3 VAT, 1 phone, 1 model) | 5 |
| New leads | 3 (2 WARM, 1 COLD) |

Cost about $1.40 Apify + $0.50 OpenAI. 22 of the ~25 pool businesses are already Fluidra customers (88%).

**The filter's 13 exclusions** (`run_trace.py cb_66cb495f9b90`): 9 right (5 OBI stores, a holiday flat, 3
construction or paving firms with no pool sign). The other 4 all have "pool" in the name (names left out: they are leads or customers):
- A (pool technology firm): a duplicate listing with no category; its main listing (Schwimmbeckenbauer)
  passed and is a customer. Not lost.
- B: no category, website or phone. Cannot be checked.
- C (Google category Metallbauunternehmen) and D (Hersteller): enriched outside the pipeline
  (`enrich_probe.py`, $0.11): both VALID. C designs, builds and installs stainless steel pools
  (BUILDER_INSTALLER_SHOP): a real lead lost. D makes walk-on pool covers (WEAK_UNVERIFIED, a
  manufacturer): arguable.

Why (definition DT.4.4.1, values from v4's "Google categories" tab): a pool word in the name counts only with
a CONDITIONAL category (rule 5). Their categories are on no list, so no rule fires and rule 8
(DEFAULT_EXCLUDE) drops them before the website is read.

| Rule | C (stainless steel pool builder) | D (pool cover maker) |
|---|---|---|
| 1-3 closed, excluded before, hard name | no | no |
| 4 INCLUDE category | Metallbauunternehmen: not listed | Hersteller: not listed |
| 5 CONDITIONAL category + pool word | pool word yes, category not CONDITIONAL | same |
| 6-7 soft name, EXCLUDE category | no | no |
| 8 nothing fired | **excluded** | **excluded** |

To propose to TWP: classify Metallbauunternehmen and Hersteller as CONDITIONAL (both would then pass rule 5),
and send a business with no category but a pool word in its name to review rather than excluding it (a
change of the definition's rules).

**Cost: OpenAI's cache writes, paid and never read.** The 8 enrichment calls (DIRECT, 5 at a time): 14,112
normal input tokens, **177,511 cache-write tokens** (1.25x input), **0 cache reads**, 2,410 output: $0.50
instead of about $0.41 without caching, about 22% more for nothing. OpenAI cached almost every whole request
(each one's website is unique) and did not reuse even the shared system message, probably because the calls
run in parallel. **Fixed the same day:** by default OpenAI puts an implicit breakpoint at the end of the
request; now the system message goes as a `developer` message with an explicit breakpoint, and
`prompt_cache_options.mode` is `explicit`, plus a `prompt_cache_key` per system message. Checked with two
calls of `openai_smoke.py`: the first wrote 2,061 tokens (the system message only), the second read them and
wrote nothing; $0.0095 then $0.0046, same answer.

**Coverage (`lgn_v_coverage`).** Of the ÖVS members, the Carinthia search found 3, all customers and set aside
correctly. Two Carinthian members were not found: one makes insulation (expected not to show for
"Poolbau"); for the other, a pool systems maker, it is not known whether Google does not return it for these
terms or its listing has another website or phone.

## 2026-11-23 · OpenAI: first real call (gpt-6.2-sol)

`exploration/openai_smoke.py --run`: one invented lead (a pool builder in Villach) with the exact request
`enrich` builds. Status SUCCEEDED, schema OK, 0 claims dropped by the page check. 2,378 input and 367 output
tokens, $0.0084 (estimate $0.024 at most). The answer was right: builder and shop, VAT, contact, email and
phone from the Impressum, Speck and Bayrol as competitor brands and Zodiac as a Fluidra brand; e-commerce
UNKNOWN (a shop is mentioned, no cart). No cache on a first call, as expected.

## 2026-11-20 · Control set for Austria: the ÖVS members

DF.12 measures coverage against known pool businesses (v3.3: associations in DE and AT, builders in CH).
Austria's association is the ÖVS; its members are in the WKO directory (firmen.wko.at, filter
zertifikate=66, one page per state; linked from wko.at/handel/oevs/oevs-mitgliedersuche): about 120
entries counting branches. Since coverage matches on website domain or phone, only the 47 members with
one of them are in `seeds/biz_cfg_control_set.csv` (source OEVS). WKO refused automated requests (403)
halfway, so many contacts come from the result lists, not the detail pages; sole traders are listed under
their trade name.

- Expected: MUST_EXCLUDE when TWP's name exclusions (v4) catch the name: BWT and Peraqua (manufacturer),
  GWT ("Therme": public pool). The rest MUST_INCLUDE, manufacturers among them (Steinbach, Lutz-Jesco,
  ProMinent…): if the model sets them aside, coverage shows it, and TWP can add them to the exclusions.
- 30 of the 47 are already Fluidra customers in Austria (by phone or email domain): the list mostly tests
  the customer match. `lgn_v_coverage` now counts "excluded as a customer" as correct for MUST_INCLUDE.
- Germany (BSW) and Switzerland: done on 24 Nov (see above).

## 2026-11-20 · Definition v3.3: Salesforce status file instead of the cockpit's call results

v3.3 (20 Oct) replaces v3.2. The prompt is unchanged. What changes:
- No manual load of call results: the cockpit loading is gone (`load-cockpit`, its tables and views).
- Salesforce's monthly status file, with its own tables: `sf_status_file`, `sf_status_row` (landing),
  `sf_status_out` (mapped by the engine), `lgn_e_sf_status`, `lgn_d_sf_status` (per lead and month; the
  latest month is current). Statuses CONVERTED, WORKING, NURTURING, DISCARDED; reasons in their own map.
  `load-sf-status FILE`. DISCARDED is a permanent exclusion (`SALESFORCE_EXCLUSION`), with "already a
  customer" also a SALESFORCE customer match, with no customer code; the others leave the lead Published.
  An unknown lead code or a different zone is a warning. Map 3 by zone and month: `lgn_v_map_zones_month`.
- Step 0: automatic import from a SharePoint folder. Not built: the folder is [PENDIENTE] in v3.3, and
  the Insight Generator it names as the model has no such import.
- `seed` now updates the rows its own seeds wrote when a seed changes, and deletes (active = 0) those a
  seed dropped (the cockpit's mapping). Rows from the Excel are recognised through `seed_load` and left
  alone; from now on the Excel stamps `excel:<version>`.
- The cockpit tables already created in ClickHouse (`cockpit_export_file`, `cockpit_export_row`,
  `cockpit_outcome_out`) and the view `lgn_d_cockpit_outcome` were empty and were dropped (20 Nov).

## 2026-11-20 · The project against the four input files (v4 and the three customer lists)

- Loaded and matching: areas and zones, keywords, Google categories, name exclusions, pool words,
  brands (AstralPool and Zodiac kept from our seeds, also Fluidra brands), the three customer lists.
- Matching the code exactly: scoring (every factor and its points, max 37.5), rating bands (Hot ≥ 30,
  Warm ≥ 20), rejection reasons (six codes, with customer number and "company that stays"), and the 24
  Salesforce columns, in order and content (Last Name = contact or company, CHF for Switzerland, Mobile
  only for mobiles, Lead Status New).
- Fixed: the Import list tab names its column `company_name`, which the loader did not read.
- Empty or pending in v4: the sales reps ("Pending Fluidra", blocks the export), the Salesforce values
  marked Pending, market size per district (not used: zone points use the quadrant).
- v4 cites a "Word v3.3", which arrived the same day (`docs/spec`, 20 Oct) and replaces v3.2. The
  Salesforce status file is built as v3.3 describes it (next entry).
- The Customer file tab lists columns the received files don't have (subsidiary, sales_rep_name,
  geo_precision, extract_date): the files are TWP's normalised layout, as DT.5.1 describes, and the
  missing values come from the `load-customers` arguments.

## 2026-11-19 · Where to search for the demo: Villach Land

Fluidra's Austrian customers placed in TWP's districts (1,506 with coordinates), within the 30 districts
TWP's v4 marks ATTACK. Fewest customers: Güssing 1, Villach Land 2, Villach Stadt 3, then several with
6-7 (Klagenfurt Land 7); the most: Graz-Umgebung 54, Baden 56.
Chosen: **Villach Land (207)**, alternative Klagenfurt Land (204). That the lake area around Villach and
Klagenfurt has many private pools is our reading, not data: v4's market size per district is empty.
Güssing is left out: few customers there probably means few pool businesses at all.

## 2026-11-19 · The customer match on real data: every Graz pool business is a customer

The demo (in memory, `demo_lead.py --inputs v4 --customers AT`) on the 5 saved Graz places: 4 pool
businesses pass the filter, and all 4 are Fluidra customers, so nothing is exported.

| Place | Customer in the list | Signal | Pass |
|---|---|---|---|
| A | same company, same Graz address | same phone | capture (free) |
| B | same company, head office elsewhere in Styria | phone on the website | enrichment |
| C | same company, head office in Lower Austria | same VAT | enrichment |
| D | same company, under its legal name, elsewhere in Styria | same VAT | enrichment |

Names left out: they come from Fluidra's customer list (client data). All four were checked by hand
and are real matches; three are branches of companies with their head office elsewhere, which only
the website's phone or VAT reveals. For the demo, an area with fewer established customers is needed:
a new Apify search (cents).

## 2026-11-19 · Fluidra's customer lists (AT, CH, DE)

Three files from TWP, one per country, all in TWP's normalised layout (tab Customers, the same 15
columns), as DT.5.1 describes: AT 1,693 customers (29 Sep), CH 936 (6 Oct), DE 4,672 (18 Nov). They
load with `load-customers` (`TWP_SHEET_AT/CH/DE`); DE needed its own mapping, added as seeds.

- Address country comes as car-plate codes in AT and DE (A, D, B, I…), which the loader now reads. DE's
  file holds 111 customers with an Austrian address and 48 with a Swiss one, as the definition says.
- DE has name, address and coordinates only: no phone, VAT or email, so German leads match on name
  plus postcode or city only.
- CH: 3 rows recognised as test records and set aside.
- AT and CH also have a "Rules" tab: the old n8n scoring (brands, zones by postcode). Not read: the
  inputs Excel and the definition replace it.
- Age: on 19 Nov AT is 51 days old and CH 44, over the 7 days of DT.5, so capture blocks there unless
  the run is approved (`--approve-stale-extract`).
- Fluidra's own companies' VAT or register numbers (asked in question 3) are not in the files.

## 2026-11-19 · German and Swiss district outlines

BKG VG250 (01.01.2026) and swisstopo swissBOUNDARIES3D (2026-01), the sources DT.4.1 names. Both come
as GeoPackages in projected coordinates (UTM 32N, LV95); `leadgen.py geo-convert` turns them into
GeoJSON in lon/lat without a GIS library (commands in [onboarding.md](../docs/outline/onboarding.md)).

- Accuracy: within 6 m of BKG's own lon/lat service (its rounding); swisstopo's reference point exact
  to 1e-5°. Zürich, Bern, Lugano, Genève, Zug, Basel, Neuchâtel and Moutier fall in the right district.
- Germany: all 401 of TWP's Kreise match, Hanau (06415, kreisfrei since 2026) included. VG250 also
  has 33 sea and lake areas with the land codes: only GF = 4 (land) is loaded. BKG's online service
  (WFS) is still at 2025 and lacks Hanau, so the 2026 download is used.
- Switzerland: 134 of TWP's districts are swisstopo districts; the other 9 are cantons without
  districts, coded canton × 100 (Genève 2500), taken from the canton layer. Moutier (2604) moved to
  Jura on 1 Jan 2026 and is not in TWP's list: `load-geo` skips it, so a business there gets no
  district (no zone points, no owner). For TWP.
- Request size (simplified to about 100 m): a German Kreis median 7 KB, max 38 KB; a Swiss district
  median 3 KB. A whole state is large: Bern 55 KB, Bayern 851 KB. Untested against Apify.
- Tested against Apify (19 Nov): Landkreis München (09184) as its outline, Poolbau, max 5. Five places,
  all pool builders, all inside 09184 (Baierbrunn, Hohenbrunn, Grünwald, Oberhaching, Taufkirchen),
  none in the city of München (09162), which the Landkreis surrounds.

## 2026-11-19 · TWP's district codes match Statistik Austria's

Checked against the inputs Excel v3 (261013, "Admin divisions" tab). All 94 Austrian codes are in
Statistik Austria's file, with the same names except "Sankt" vs "St." in 302, 319 and 504.
TWP uses Vienna as 900 only. Statistik Austria's file also has the 23 city districts 901-923, which
overlap 900. `load-geo` now loads only TWP's codes (from `biz_cfg_search_area`, so after `load-config`)
and lists what it skipped: with v3, 94 loaded, 901-923 skipped, none of TWP's missing.
In v3 the "Subregion → sales rep" tab has no rep filled (all 721 rows "Pending Fluidra").
v3's tabs have a title and a description above the header, and brands come as "Group 1 (7.5)" with no
owner column: the loader now reads both (`load-config`, and the demo's `--inputs`).

## 2026-11-18 · Austrian district outlines

Statistik Austria's politische Bezirke (2026 edition, 117 entries: 93 districts, Vienna as a whole and its
23 city districts), downloaded as in [onboarding.md](../docs/outline/onboarding.md) and loaded with
`load-geo`. Simplified to about 100 m they go from 1.2 million points to 30,000, a median of 288 per entry.
What capture sends: Graz(Stadt) 112 points (3 KB request), Styria as its 13 districts 4,816 points (93 KB).
The places from the free-text test and from the official-outline test all fall in Graz(Stadt) and in no
other district.
Graz-Umgebung surrounds Graz, so its outline has a hole: anything reading these outlines must handle holes.
