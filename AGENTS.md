# woolroom — agent notes

A self-hostable shared ambient pet: FastAPI + SQLite backend, a vanilla-JS
SVG room client, and a data-only content-pack format. README.md is the
product face; this file is the contributor contract.

## Commands

```sh
uv sync --extra dev                      # environment
.venv/bin/python -m pytest tests -q      # tests — hermetic, no services/keys/network
.venv/bin/python -m ruff check .         # lint (line length 100, py311)
.venv/bin/uvicorn app.main:app --reload  # run locally (zero API keys needed)
.venv/bin/python scripts/pack_lint.py packs/pebble          # pack contract suite
.venv/bin/python scripts/pack_render.py packs/pebble        # visual review board
.venv/bin/python scripts/denylist_check.py                  # publish gate
```

Keep the test suite hermetic: `tests/conftest.py` pins safe env defaults
before anything imports `app.*`; a test that needs the network or a live LLM
does not belong in the suite.

## Layout

- `app/` — the application: `engine/` (mood, aging, outings, quirk
  interpreter — pure deterministic logic), `runtime/` (respond pipeline,
  prompt, validator, scene fx), `data/` (phrase/voice/species/quirk base
  tables — content modules), `packs/` (loader, sanitizer, lint), `api/`
  (REST + WebSocket), `channels/`, `memory/`, `scheduler/`, `storage/`,
  `auth/`, `static/` (the room client), `config.py`, `room_contract.py`.
- `packs/pebble/` — the shipped example content pack (pack format v1).
- `scripts/` — operator/authoring CLIs: `pack_lint.py`, `pack_render.py`,
  `migrate.py`, `eval.py`, `seed_demo_pet.py`, `denylist_check.py`,
  `docker-entrypoint.sh`.
- `migrations/` — alembic. The only thing that touches schema in prod.
- `docs/` — `packs.md` (authoring guide), `design/` (HLD/LLD contract).
- `tests/` — pytest suite, mirrored loosely against `app/`.

## Invariants that outrank taste

- **Packs are data, never code.** No scripting, no CSS, no runtime download.
  New behavior lands in the engine, versioned — never in a pack.
- **Loader gates are fail-closed.** Any gate violation refuses boot; a
  refused pack registers nothing. Never convert a gate refusal into a
  warning or a silent strip outside the sanitizer's documented allowlist.
- **No gamification surface.** No scores, streaks, meters, or notification
  plumbing — the rig deliberately has no place to put them.
- **The fx vocabulary versions.** `app/room_contract.py` owns
  `FX_VOCAB_VERSION`; unknown modes are loader errors, not no-ops.

## Authoring a pack (the loop)

Full contract: [docs/packs.md](docs/packs.md). Short version: copy
`packs/pebble`, rename ids (file stems are ids), edit temperament / coats /
geometry / art / phrases / quirks / voice, then iterate
`pack_render` (eyeball) → `pack_lint` (contract) → boot with
`PACK_PATHS=<dir>` (live). Lint green + render board correct = the pack
works.

## Design docs (HLD/LLD)

`docs/design/HLD.md` and `docs/design/LLD.md` are the architecture contract.
**Where a doc disagrees with the code, the code wins and the doc is stale** —
that is a bug to fix, not a tie to settle in the doc's favor.

A change to mapped code updates the owning doc **in the same change**.
Tiered, so routine work does not churn the docs:

1. **Update the LLD** when a file, function signature, data model, schema,
   event shape, or config/env surface **the LLD names** changes — or when a
   module appears or disappears under an owned path.
2. **Update the HLD** only on a boundary change: a component added or
   retired, a new external dependency, a changed trust boundary or auth
   gate, a changed contract between components, or a subsystem promoted,
   demoted, or absorbed.
3. **Update neither** for bugfixes, styling, tests, or refactors that
   preserve every surface the docs name.

Do not regenerate a doc wholesale to satisfy this; edit the affected
sections and move the `**Refreshed:**` line.

Ownership map — machine-readable, parsed by `tests/test_design_docs.py`.
Add a row when you add a package; `none` means "no design contract,
deliberately". A dir with no row but rows beneath it is a container and is
recursed into, so a package dropped inside one cannot inherit its parent's
coverage.

```design-doc-map
app/         -> docs/design/HLD.md docs/design/LLD.md
migrations/  -> docs/design/LLD.md
packs/       -> docs/design/HLD.md docs/design/LLD.md
scripts/     -> docs/design/HLD.md docs/design/LLD.md
docs/        -> none
tests/       -> none
.github/     -> none
```

`python3 tests/test_design_docs.py` reports drift: per doc, the modules
added or removed under its owned paths since that doc last changed (test
files excluded). The pytest asserts the map is structurally sound; it
deliberately does **not** fail on drift — a doc gate that blocks merges buys
rubber-stamp edits, not maintained docs. Before the first commit exists the
drift report has no history to diff and skips itself; the structural
assertions still run. `python3 tests/test_design_docs.py --audit` lists
tracked modules named in no design doc.
