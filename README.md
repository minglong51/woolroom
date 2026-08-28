# woolroom

**Anyone can have a pet that is really theirs on the internet — alive when
nobody is looking, shared with their people, running in a home they own.**

<img src=".github/assets/room-day.png" alt="the room on a weekday morning — biscuit, a marmalade cat, sitting on the rug between two lamps named for the two people who keep the room" />

woolroom is a self-hostable shared ambient pet: one quiet animal in a small
room, kept by two people. It runs on a rule-driven brain — mood drift,
memory, seeded daily outings, a phrasebook keyed to how it actually feels —
so it stays alive when the tab is closed and costs zero inference spend by
default. An optional LLM lane (Anthropic, or a local model via Ollama)
narrates richer utterances; it is opt-in, budget-capped, and the pet is
fully itself without a key. The design rationale is written up in
[a coherent virtual pet without an LLM](https://www.minglongpan.com/writing/a-coherent-virtual-pet-without-an-llm).

**Visit a room right now:** [woolroom-demo.fly.dev](https://woolroom-demo.fly.dev)
— tap *watch the room* to slip in as a read-only guest. No account, nothing
to install; it is a real instance of the engine below, breathing on its own.

There are no scores, streaks, meters, or notifications. That is not a
setting — the rig has no surface for them.

<table>
<tr>
<td width="50%"><img src=".github/assets/room-night.png" alt="the same room after nine at night — dark walls, a moon where the button sun was, the cat asleep, one lamp still warm" /></td>
<td width="50%"><img src=".github/assets/join-loop.gif" alt="twenty seconds of the join: the room waits with one key, the second lamp takes Wren's name, and biscuit greets the hand that just walked in — first-greet and first-pet milestone cards and all" /></td>
</tr>
</table>

*The room keeps its own hours, and it breathes whether or not anyone is
watching. On the right: the second human joining — the other lamp takes
her name, and the cat picks its head up for it.*

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
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
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

### Guest visits

A deployment can open a read-only window on its room: set
`GUEST_ACCESS_ENABLED=true` and pin `GUEST_PET_ID` to a demo pet seeded with
`scripts/seed_demo_pet.py`. Visitors watch a sanitized scene — only the
pinned demo pet is ever resolvable, never a real household's room. The
public demo above is exactly this.

### Limits, by design

One process, one household per instance. The live channel registry is
in-process, the LLM budget cap is per-process, and SQLite has one writer —
so a woolroom scales by giving each household its own small instance, not by
clustering a big one. There is no multi-tenant mode and none planned; a
home is not a platform.

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

Start a pack from any directory without cloning Woolroom or permanently
installing its authoring tools:

```sh
uvx woolpack new mole --author "Your Name" --license MIT
uvx woolpack render packs/mole -o mole-board.html
uvx woolpack lint packs/mole --strict
```

The scaffold copies the example with every file stem already renamed to your
id (stems are ids — a bare copy collides at boot). Render draws every coat in
every pose plus the touch-hitbox overlay; strict lint runs the contract suite
and treats warnings as failures. If lint is green and the board looks right,
the pack is ready for a Woolroom boot test. Woolroom revalidates every
configured pack together, so cross-pack identifier collisions can still refuse
boot. Contributors working inside this checkout can use the equivalent
`scripts/pack_new.py`, `scripts/pack_render.py`, and `scripts/pack_lint.py`
compatibility shims after `uv sync --extra dev`.

<img src=".github/assets/pebble.png" width="300" alt="pebble, the example species, on the room floor — a smooth gray rock with dot eyes and a pale belly" />

Packs live in their authors' own repositories. The community index is
[woolroom-packs](https://github.com/minglong51/woolroom-packs) — one line per
pack, added by PR; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

Maintained-lite. The engine is feature-complete for v1 and under test, but
responses to issues and pack submissions may be slow — days, not hours. If
there is no external pack or issue activity by 2027-03-01, the repo moves to
reference maintenance: a designed state, not a failure. The authoring loop
pays for itself even at zero external packs.

## License

[MIT](LICENSE) — copyright 2026 woolroom contributors.
