# `docs` Folder

- **`how_it_works.md`**: a Spanish explainer of the whole system, end to end: [how_it_works.md](how_it_works.md).
- **`outline/`**: the documentation, in Spanish, published to Outline one way: edit it here, never in
  Outline. Start at `outline/proyecto_lead_generator.md`. The pages go under «Proyecto Lead Generator», in
  «Proyectos y desarrollo» of the client's collection, shared with the Insight Generator;
  `outline/outline.toml` says which file goes under which page. `python docs/outline/sync.py --dry-run`
  previews them, and with `OUTLINE_API_TOKEN` set it publishes.
- **`spec/`**: the client's specification: definition v3.3 (functional + technical, 20 Oct 2026) and the
  technical review of v3.2.
- **`tests/`**: the no-DB guards on the docs (links that resolve, no private IPs), run by `./run_tests.sh`.

The engine's reference, in English, lives next to its code: [`dwh/transf_py/leadgen/README.md`](../dwh/transf_py/leadgen/README.md).
