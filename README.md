# woolroom

**Anyone can have a pet that is really theirs on the internet — alive when
nobody is looking, shared with their people, running in a home they own.**

woolroom is a self-hostable shared ambient pet: one quiet animal in a small
room, kept by two people. It runs on a deterministic brain — mood drift,
memory, seeded daily outings, a phrasebook keyed to how it actually feels —
so it stays alive when the tab is closed and costs zero inference spend by
default. An optional LLM lane (Anthropic, or a local model via Ollama)
narrates richer utterances; it is opt-in, budget-capped, and the pet is
fully itself without a key.

There are no scores, streaks, meters, or notifications. That is not a
setting — the rig has no surface for them.

v1 is the pair: one pet, the same soul on every screen, two humans sharing a
room. No email, no passwords — your person joins by invite link and picks a
name. The data is a SQLite file on your own disk.

## Run it

### Docker

```sh
docker build -t woolroom .
docker run --rm -p 8000:8000 woolroom
```

Then open http://localhost:8000. To keep the pet's data across containers,
give it a volume:

```sh
docker run --rm -p 8000:8000 \
  -v woolroom-data:/data \
  -e DATABASE_URL=sqlite+aiosqlite:////data/woolroom.db \
  woolroom
```

### fly.io

The repo ships a ready template — `fly.toml`, `Dockerfile`, and
`litestream.yml` for continuous SQLite backup to your own object storage:

```sh
fly apps create woolroom
fly volumes create woolroom_data --region sjc --size 1
fly storage create woolroom-litestream
fly deploy
```

### Local development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --extra dev
.venv/bin/uvicorn app.main:app --reload
```

Run the tests with `.venv/bin/python -m pytest tests -q` — the suite is
hermetic: no services, no keys, no network.

Every path above works with zero API keys. All configuration is environment
variables; [.env.example](.env.example) documents each one, including the
optional site-access password for a private deployment.

## The three promises

- **Author a species in a weekend.** A species is a data pack — YAML plus
  one SVG, no engine code. Copy the example, rename, draw, lint, boot.
- **Host in one command.** One container or one `fly deploy`; nothing
  metered, nothing phoning home; the database is a file you can copy.
- **Share by a link.** Your person joins the room through an invite link;
  a species you wrote is shared as a repo link.

## Packs

A pack adds a species — figure, temperament, coats, voice, habits — as data
the loader validates behind fail-closed gates at boot. Packs are data,
never code: no scripting, no CSS, no runtime download.

- The authoring guide is [docs/packs.md](docs/packs.md).
- [packs/pebble](packs/pebble) is the shipped example — a pet rock,
  deliberately minimal.
- `scripts/pack_render.py <pack-dir>` draws the review board (every coat in
  every pose, plus the touch-hitbox overlay); `scripts/pack_lint.py
  <pack-dir>` runs the contract suite. If lint is green and the render board
  looks right, the pack works.

Packs live in their authors' own repositories. A linked-list index of
community packs opens at launch; until then, open an issue with your pack
link — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

Maintained-lite. The engine is feature-complete for v1 and under test, but
responses to issues and pack submissions may be slow — days, not hours. If
there is no external pack or issue activity by 2027-03-01, the repo moves to
reference maintenance: a designed state, not a failure. The authoring loop
pays for itself even at zero external packs.

## License

[MIT](LICENSE) — copyright 2026 woolroom contributors.
