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
