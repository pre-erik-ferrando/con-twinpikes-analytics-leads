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
- Outlines come from the sources the spec names: Statistik Austria (AT, see below), BKG VG250 for
  Germany and swisstopo for Switzerland (not done).
- A state sent as many touching districts (Styria: 13) is untested against Apify.
- District codes must match TWP's. Statistik Austria's 601 = Graz(Stadt) matches the spec's example; the
  full list is not checked against TWP's inputs Excel (we don't have it). The spec has this as [PENDIENTE TWP].
- Vienna is in the file twice: as 900 Wien(Stadt) and as its 23 city districts, 901-923, which overlap 900.
  We don't know which TWP uses. Load only one set, or a business in Vienna may get either code.
- It changes how spec DT.3.2 sends the area: tell TWP.

## 2026-11-18 · Austrian district outlines

Statistik Austria's politische Bezirke (2026 edition, 117 entries: 93 districts, Vienna as a whole and its
23 city districts), downloaded as in [onboarding.md](../docs/outline/onboarding.md) and loaded with
`load-geo`. Simplified to about 100 m they go from 1.2 million points to 30,000, a median of 288 per entry.
What capture sends: Graz(Stadt) 112 points (3 KB request), Styria as its 13 districts 4,816 points (93 KB).
The places from the free-text test and from the official-outline test all fall in Graz(Stadt) and in no
other district.
Graz-Umgebung surrounds Graz, so its outline has a hole: anything reading these outlines must handle holes.
