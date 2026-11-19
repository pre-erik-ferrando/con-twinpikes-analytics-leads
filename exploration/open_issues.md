# Open issues: who answers what

Questions for TWP and Fluidra: [preguntas_twp.md](preguntas_twp.md) (in Spanish, sent 19 Nov). Only
what the most recent definition (v3.2, 14 October 2026) leaves open, plus what testing found.

Received: the inputs Excel **v3** (261013) and **v4** (261020), in `docs/source/` (gitignored). Both load.
v4 is v3 with one sentence changed (the export tab now cites the Spain upload format 260610, v2.2 DT.8.5).

## Before the first demo (Pool specialist DE/AT/CH)

### TWP / Fluidra

| Missing | Without it | Weight | Question |
|---|---|---|---|
| ~~Customer lists AT, CH, DE~~ received 19 Nov, they load ([findings.md](findings.md)). But AT (29 Sep) and CH (6 Oct) are older than the definition's 7 days: capture blocks unless approved (`--approve-stale-extract`) or they send fresh ones | Current Fluidra customers can show up as new leads | Done; freshness open | 3 |
| Subregion → sales rep (empty in v4) | The Owner column is a placeholder | Low | 2 |
| Salesforce export values: Province format, Country, Lead Status, mandatory Email (Pending in v4) | Our provisional values are used; the file is still produced | Lowest | 1 |

### Us

| Missing | Without it | When |
|---|---|---|
| ~~Where the tables go in the database~~ done 19 Nov: the six `fld_*lgn` databases exist; `init`, seeds, v4, outlines (AT/DE/CH), customer lists (AT/CH/DE) and dbt loaded | — | Done |
| Model: which one, whose Anthropic key, spending cap | The model's answers are typed by hand; the next demo (Villach Land, [demo_lead.md](demo_lead.md)) waits for it | This week |

## Later

- Benelux: BE and NL customer lists (TWP, 19 Nov: "De Benelux aún no lo tengo, está pedido"), their
  district outlines (Statbel, CBS) and the search pilot the technical review proposes.
- Find out with Intelek whether their app can launch runs and accept file uploads.
- A whole state sent to Apify at once (Bayern would be an 851 KB request). Single districts tested in
  AT (Graz) and DE (Landkreis München) ([findings.md](findings.md)).
- Moutier (2604) moved from Bern to Jura in 2026 and is not in TWP's district list.
- Outline sync: the collection doesn't exist yet; `outline.toml` links to the Prenomics GitHub org, not
  this repo's current remote.
