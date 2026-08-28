# Contributing

First, the honest note: **woolroom is maintained-lite.** Issues and pull
requests may wait days, not hours. If the tracker stays quiet, the repo
moves to reference maintenance — that is a designed state, not a failure.
Bug reports are always welcome; for anything larger than a small fix, open
an issue before writing code so nobody spends a weekend on a no.

A few refusals are permanent, so please don't spend effort on them: no
scores, streaks, meters, or notification surfaces; no accounts, marketplace,
payments, or hosted service; no code in content packs.

## Setup and tests

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --extra dev
.venv/bin/python -m pytest tests -q
```

The suite is hermetic: it needs no services, no API keys, and no `.env`
(`tests/conftest.py` pins safe defaults before anything imports `app.*`).
Please keep it that way — a test that needs the network or a live LLM does
not belong in it.

## Code style

[Ruff](https://docs.astral.sh/ruff/) is the only gate (line length 100,
Python 3.11 target):

```sh
.venv/bin/python -m ruff check .
```

CI also runs the test suite, lints the shipped example pack, builds the
Docker image, and runs the publish-gate denylist check
(`scripts/denylist_check.py`).

## Pack contributions

Packs live in **your own repository**, not this one — a pack is shared as a
link, and the loader runs it from any local directory. The community index is
[woolroom-packs](https://github.com/minglong51/woolroom-packs): open a PR
there adding **one line** for your pack. Run the exact pinned render and
strict-lint commands documented by that index, then include the lint output and
review board with your submission. Neither command requires a Woolroom checkout
or permanent Woolpack installation. Contributors changing Woolpack itself can
instead use the equivalent `scripts/pack_lint.py` and `scripts/pack_render.py`
checkout shims after `uv sync --extra dev`. The authoring loop is documented in
[docs/packs.md](docs/packs.md).

## Design docs

`docs/design/HLD.md` and `docs/design/LLD.md` are the architecture contract
for this tree, and `AGENTS.md` carries the machine-readable ownership map.
If your change adds or retires a module, or alters a surface the LLD names
(a file, signature, data model, config/env key), update the owning doc in
the same change. Bugfixes, styling, and tests need no doc churn. Where a doc
and the code disagree, the code is right and the doc is stale — file that as
a bug.
