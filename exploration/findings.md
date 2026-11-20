# Findings

Things learned while testing that matter for later decisions. Newest first.

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
  `cockpit_outcome_out`) are empty and no longer used: the admin can drop them.

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
