# Open issues: who answers what

Questions for TWP and Fluidra: [preguntas_twp.md](preguntas_twp.md) (in Spanish, sent 19 and 24 Nov). Only
what the most recent definition (v3.3, 20 October 2026) leaves open, plus what testing found.

Received: the inputs Excel **v3** (261013) and **v4** (261020), in `docs/source/` (gitignored). Both load.
v4 is v3 with one sentence changed (the export tab now cites the Spain upload format 260610, v2.2 DT.8.5).

## After the first demo (Pool specialist DE/AT/CH)

The demo was sent to TwinPikes; waiting for their feedback.

### TWP / Fluidra

| Missing | Without it | Weight | Question |
|---|---|---|---|
| ~~Customer lists AT, CH, DE~~ received 19 Nov, they load ([findings.md](findings.md)). But AT (29 Sep) and CH (6 Oct) are older than the definition's 7 days: capture blocks unless approved (`--approve-stale-extract`) or they send fresh ones | Current Fluidra customers can show up as new leads | Done; freshness open | 3 |
| Subregion → sales rep (empty in v4) | The Owner column is a placeholder | Low | 2 |
| Salesforce export values: Province format, Country, Lead Status, mandatory Email (Pending in v4) | Our provisional values are used; the file is still produced | Lowest | 1 |
| Filter rules: unclassified Google categories, businesses with no category, "Therme" as a HARD rule, whirlpool shops (sent 24 Nov) | Pool businesses the filter drops today (Carinthia, Styria) | Medium | 1-4 of 24 Nov |

### Us

| Missing | Without it | When |
|---|---|---|
| ~~Where the tables go in the database~~ done 19 Nov: the six `fld_*lgn` databases exist; `init`, seeds, v4, outlines (AT/DE/CH), customer lists (AT/CH/DE) and dbt loaded | — | Done |
| ~~Model: which one, whose key, spending cap~~ OpenAI `gpt-6.2-sol` (20 Nov); real runs in Carinthia (38 businesses, 3 leads) and Styria (12 leads) on 23 Nov ([findings.md](findings.md)) | — | Done |

## Later

- Step 0 (v3.3): the tool reads a SharePoint folder by itself (customer lists, Salesforce status
  files). Which folder, its permissions and how often are [PENDIENTE] (Prenomics, within Fluidra's
  limits). Until then: `load-customers` and `load-sf-status` by hand.
- Salesforce status file: exact headers and values, and who drops it monthly (Fluidra, whoever runs the
  new Salesforce, expected in about three weeks).

- Benelux: BE and NL customer lists (TWP, 19 Nov: "De Benelux aún no lo tengo, está pedido"), their
  district outlines (Statbel, CBS) and the search pilot the technical review proposes.
- Intelek, about their DataApp on the `lgn_v_*` views (to discuss with Intelek in the week of 23 Nov):
  1. Can it write rows to ClickHouse (insert only)? Needed to validate leads, approve costs and edit
     the configuration.
  2. Can a button launch our processes (capture, enrich, export), and how: API call, queue, other?
  3. Can the user download a file the process generates (the Salesforce Excel)?
  4. Can it show maps with zone polygons and lead points?
  5. Which ClickHouse user does it connect with, and can it read views across databases
     (`fld_bizlgn`, `fld_opslgn`)?
- Actor language, to check on the next capture: TWP's category rules are German names only, and capture
  asks the actor for the area's language, so in French or Italian Switzerland Google's categories would
  come back in that language and fail the filter (they'd show in `lgn_v_category_pending`). Fix if so:
  one actor language for all searches (`apify_language`, definition DT.3), terms still in the area's.
  Actor build `0.14.793` worked on 23 Nov; `exploration/apify_smoke.py` checks the current one (free).
- A whole state sent to Apify at once (Bayern would be an 851 KB request). Single districts tested in
  AT (Graz) and DE (Landkreis München) ([findings.md](findings.md)).
- Moutier (2604) moved from Bern to Jura in 2026 and is not in TWP's district list.
- Outline: pages published on 24 Nov (`outline_ids.json`). `outline.toml` links to the Prenomics GitHub
  org, not this repo's current remote (`pre-erik-ferrando`): fine once the repo moves there.
