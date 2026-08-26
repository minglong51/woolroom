# woolroom — High-Level Design

**Refreshed:** 2026-08-26 (standalone woolpack workspace; rule-driven engine boundary 2026-08-25)

woolroom is a self-hostable shared ambient pet: one quiet animal in a small
room, kept by two people. A rule-driven brain (mood drift, memory, seeded
outings, a phrasebook) keeps the pet alive when nobody is looking and costs
zero inference spend by default; an opt-in LLM lane narrates richer
utterances behind a validator and a per-pet daily budget. There are no
scores, streaks, meters, accounts, payments, or hosted service — the rig has
no surface for them, and that refusal is architectural, not a setting.

v1 is pair-shaped by design: `HOUSEHOLD_SIZE` is pinned to 2 at config
validation. One pet, the same soul on every screen.

The repository is also a uv workspace. The root `woolroom` application
depends on its local `packages/woolpack/` member: an independently buildable
authoring and validation distribution that owns the data-only pack contract.
`woolpack` has no `app.*` dependency; the application supplies its current
engine vocabulary as a `PackEnvironment`, then registers the validated data
through its thin runtime adapter.

## Architecture

Single-process FastAPI app; one SQLite file is the entire durable state.
Single uvicorn worker by design — the WebSocket broadcaster lives
in-process.

```
                         ┌──────────────────────────────────────────┐
 browser (static SPA)    │               uvicorn (1 worker)         │
 ┌───────────────┐       │  ┌────────────┐      ┌────────────────┐  │
 │ index.html    │ REST  │  │ api/http   │─────▶│ runtime/respond│  │
 │ js/wool.js    │◀─────▶│  │ api/ws     │      │  ignore? LLM?  │  │
 │ (SVG room,    │  WS   │  └────────────┘      │  validate,     │  │
 │  sound, fx)   │◀─────▶│        │             │  fallback      │  │
 └───────────────┘       │        ▼             └───────┬────────┘  │
                         │  channels/webapp             │ fallback  │
                         │  (in-process fanout)         ▼           │
                         │                        data/body_language│
                         │  engine/ (rule-driven state logic):      │
                         │   mood · aging · outings · quirks        │
                         │  memory/: buffer → moments → core facts  │
                         │  scheduler/: mood drift, daily outing, … │
                         │  storage/ (SQLAlchemy) ──▶ SQLite file   │
                         │                                          │
                         │  packs/loader ◀── PACK_PATHS (boot only) │
                         │       │                                  │
                         │       └──▶ woolpack.validate_pack        │
                         │   register validated data · serve assets │
                         └──────────────────────────────────────────┘

 pack author ──▶ woolpack new/render/lint ──▶ YAML + SVG pack
                       │
                       └── packaged Pebble scaffold + room style
```

## Components

- **Client** (`app/static/`) — dependency-free SPA (vendored Alpine). The
  room is one SVG animated through CSS classes on a rig contract;
  `js/wool.js` renders scene state and trace cues, `js/figures.js` holds the
  builtin cat art, `js/sound.js` synthesizes the voice (WebAudio, no
  assets), `js/ws.js` owns the live channel.
- **API** (`app/api/`) — REST for actions/room admin, one WebSocket for
  scene state and presence. Auth is a signed session cookie; an optional
  outer site password and a read-only guest mode sit in front.
- **Runtime** (`app/runtime/`) — the respond pipeline: ignore check → LLM
  (optional) → output validator → deterministic phrasebook fallback.
  Utterances are rate-limited; body language is not. Also scene fx, shared
  trace cues, playdate visits — all in-process and short-lived.
- **Engine** (`app/engine/`) — rule-driven state logic: two-axis mood
  (arousal × valence, never shown as numbers), compressed-time aging,
  seeded daily outings, and the quirk condition-grammar interpreter.
- **Memory** (`app/memory/`) — three tiers: a rolling buffer of recent
  events, weekly-promoted shared moments, and permanent core facts.
- **Data** (`app/data/`) — content modules: the builtin cat's phrasebook,
  voice/client copy, species registry, quirk catalog. Plain tables; mutated
  only at boot by the pack loader, then frozen.
- **Pack integration** (`app/packs/`, `packs/`) — the application adapter
  derives a `PackEnvironment` from the live engine vocabularies, asks
  `woolpack` to validate each `PACK_PATHS` directory, then alone mutates the
  runtime species, phrase, quirk, voice, and client-asset registries. The
  shipped Pebble pack remains the repository's runtime example.
- **Woolpack distribution** (`packages/woolpack/`) — standalone pack-format
  v1 validator, SVG sanitizer, authoring lint, static render board, and
  scaffold CLI (`woolpack new|render|lint`). Its wheel packages the Pebble
  scaffold and the room CSS needed to author without a woolroom checkout.
  Data in, reports/HTML/data out; it never imports the application.
- **Storage** (`app/storage/`, `migrations/`) — SQLAlchemy async over
  aiosqlite; alembic owns schema in deployed environments.
- **Scheduler** (`app/scheduler/`) — APScheduler in-process jobs: mood
  drift, daily outing, anniversary, busy-mode expiry, demo self-play.
- **Eval** (`app/eval/`, `scripts/eval.py`) — corpus-driven harness for the
  LLM lane: prompt → LLM → validator, persisted per run.

## Trust boundaries and gates

- **Pack boundary (the big one).** Packs are data, never code; the shared
  `woolpack` validator checks every pack fail-closed (manifest, confinement,
  size caps, allowlist SVG sanitization, phrase/quirk/voice shape, fx-vocab
  version, registry collisions). The application passes the engine-derived
  environment and refuses boot on any violation before registering anything.
  There is no runtime install, remote fetch, or hot reload — every byte served
  is a byte the host chose. A known-bad-pack kill-list is a designed loader
  gate that lands before any remote-install path exists.
- **Auth gates.** Signed-cookie sessions (no passwords); invite-only
  pairing; optional outer site password for private deployments; read-only
  guest mode resolves only one pinned demo pet with private fields stripped
  server-side; `/admin/*` requires a shared token and is disabled without
  one. Prod refuses to boot with the default secret or a non-metered
  Anthropic credential.
- **LLM lane.** Off without a key; per-pet daily call cap; the validator
  rejects chatbot-register output, so the fallback phrasebook is always the
  floor. Human message text is wrapped as data, never instructions.
- **Room contract** (`app/room_contract.py`) — the fx-mode vocabulary
  shared by server, client, and packs, versioned (`FX_VOCAB_VERSION`); an
  unknown mode is a loader error, never a silent no-op.

## Deployment

One container: `Dockerfile` installs both local workspace projects, then
`scripts/docker-entrypoint.sh` runs litestream restore/replicate only when
object-storage secrets exist (else plain uvicorn). `fly.toml` +
`litestream.yml` are the ready fly.io template with continuous SQLite backup
to the host's own bucket. All runtime configuration is environment variables
(`app/config.py`, documented in `.env.example`).

CI installs the locked workspace and separately builds the `woolpack` wheel.
The standalone smoke checks that packaged resources match the canonical
in-repo example/style, package modules contain no `app.*` imports, default and
engine-derived `PackEnvironment` values agree, and an isolated wheel install
can scaffold, render, and strict-lint a new pack.

## What this design refuses

Gamification surfaces · accounts/marketplace/payments/hosted service · code
in packs · generic (uncapped or anonymous) N-human rooms · runtime pack
install in v1. These are load-bearing, not backlog.
