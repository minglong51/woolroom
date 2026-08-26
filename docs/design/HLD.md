# woolroom — High-Level Design

**Refreshed:** 2026-08-25 (rule-driven engine boundary; first public baseline 2026-08-20)

woolroom is a self-hostable shared ambient pet: one quiet animal in a small
room, kept by two people. A rule-driven brain (mood drift, memory, seeded
outings, a phrasebook) keeps the pet alive when nobody is looking and costs
zero inference spend by default; an opt-in LLM lane narrates richer
utterances behind a validator and a per-pet daily budget. There are no
scores, streaks, meters, accounts, payments, or hosted service — the rig has
no surface for them, and that refusal is architectural, not a setting.

v1 is pair-shaped by design: `HOUSEHOLD_SIZE` is pinned to 2 at config
validation. One pet, the same soul on every screen.

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
                         │   sanitize (allowlist SVG) · lint        │
                         └──────────────────────────────────────────┘
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
- **Packs** (`app/packs/`, `packs/`) — pack format v1: species/quirk/phrase/
  voice packs as YAML + one SVG per species, loaded from local directories
  named by `PACK_PATHS` at boot. Data, never code. See docs/packs.md.
- **Storage** (`app/storage/`, `migrations/`) — SQLAlchemy async over
  aiosqlite; alembic owns schema in deployed environments.
- **Scheduler** (`app/scheduler/`) — APScheduler in-process jobs: mood
  drift, daily outing, anniversary, busy-mode expiry, demo self-play.
- **Eval** (`app/eval/`, `scripts/eval.py`) — corpus-driven harness for the
  LLM lane: prompt → LLM → validator, persisted per run.

## Trust boundaries and gates

- **Pack boundary (the big one).** Packs are data, never code; the loader
  validates every pack fail-closed at boot (manifest, confinement, size
  caps, allowlist SVG sanitization, phrase/quirk/voice shape, fx-vocab
  version, registry collisions) and refuses boot on any violation. There is
  no runtime install, remote fetch, or hot reload — every byte served is a
  byte the host chose. A known-bad-pack kill-list is a designed loader gate
  that lands before any remote-install path exists.
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

One container: `Dockerfile` + `scripts/docker-entrypoint.sh` (litestream
restore/replicate only when object-storage secrets exist, else plain
uvicorn). `fly.toml` + `litestream.yml` are the ready fly.io template with
continuous SQLite backup to the host's own bucket. All configuration is
environment variables (`app/config.py`, documented in `.env.example`).

## What this design refuses

Gamification surfaces · accounts/marketplace/payments/hosted service · code
in packs · generic (uncapped or anonymous) N-human rooms · runtime pack
install in v1. These are load-bearing, not backlog.
