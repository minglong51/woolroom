# woolroom — High-Level Design

**Refreshed:** 2026-09-02 (installable core and private card-overlay boundary)

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
through its thin runtime adapter. The root wheel exposes the stable
`woolroom.create_app()` composition API, packages the browser and Alembic
resources, and accepts one trusted `CatalogOverlayProvider` plus an optional
`AuthNamespace`. Stock direct hosting uses the empty provider and Woolroom
cookie namespace; a private consumer can preserve its existing signed-cookie
names and salts while installing an adapter without copying the core application.

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
                         │  packs/loader ◀── public PACK_PATHS      │
                         │       │                                  │
                         │       └──▶ woolpack.validate_pack        │
                         │   register validated public data/assets  │
                         │                                          │
 private site adapter ──▶│  CatalogOverlayProvider                 │
 (trusted code / DB)     │   owner_card · guest_card               │
                         │       └──▶ pet-bound PetCardV1 allowlist │
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
- **Public pack integration** (`app/packs/`, `packs/`) — the application adapter
  derives a `PackEnvironment` from the live engine vocabularies, asks
  `woolpack` to validate each `PACK_PATHS` directory, then alone mutates the
  runtime species, phrase, quirk, voice, and client-asset registries. The
  shipped Pebble pack remains the repository's runtime example. `PACK_PATHS`
  is public by contract: its merged voice and assets remain static,
  guest-readable distribution content.
- **Composition API** (`woolroom/`) — stable public import surface,
  provider lifecycle/subjects, the default empty provider, validated auth
  namespace, and packaged Alembic revisions. A trusted provider may own
  database access but receives only the active owner or pinned-guest subject
  and returns one card-shaped projection; it never receives or mutates the
  public registries.
- **Woolpack distribution** (`packages/woolpack/`) — standalone pack-format
  v1 validator, SVG sanitizer, authoring lint, static render board, and
  scaffold CLI (`woolpack new|render|lint`), plus the versioned `PetCardV1`
  browser-projection contract. Its wheel packages the Pebble
  scaffold and the room CSS needed to author without a woolroom checkout.
  Data in, reports/HTML/data out; it never imports the application.
- **Storage** (`app/storage/`, `woolroom/migrations/`) — SQLAlchemy async over
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
- **Private overlay boundary.** `PACK_PATHS` never carries private site
  content. Deployment-installed provider code may own a database, but the
  core passes it only a narrow subject and revalidates its result as the exact
  versioned `PetCardV1` field set inside a `BoundPetCard`; the binding is
  checked against the requested pet and stripped before serialization. Owner
  and guest cards travel only in dynamic `private, no-store` envelopes. Guest
  lookup starts from the pinned production demo pet and requires the returned
  pet, species, and coat to match; provider errors and extra fields fail
  closed. Private cards never enter
  `CLIENT_VOICE`, `PACK_ASSETS`, `/api/voice`, or `/api/packs`.
- **Auth gates.** Signed-cookie sessions (no passwords); invite-only
  pairing; optional outer site password for private deployments; read-only
  guest mode resolves only one pinned demo pet with private fields stripped
  server-side. Direct hosting uses distinct Woolroom session/site/guest salts
  and cookie names. A composed app may replace all names and salts with one
  validated `AuthNamespace`; pairwise-distinct validation keeps the three
  credential domains separate and preserves an existing deployment's tokens.
  `/admin/*` requires a shared token and is disabled without one. Prod refuses
  to boot with the default secret or a non-metered Anthropic credential.
- **LLM lane.** Off without a key; per-pet daily call cap; the validator
  rejects chatbot-register output, so the fallback phrasebook is always the
  floor. Human message text is wrapped as data, never instructions.
- **Room contract** (`app/room_contract.py`) — the fx-mode vocabulary
  shared by server, client, and packs, versioned (`FX_VOCAB_VERSION`); an
  unknown mode is a loader error, never a silent no-op.
- **Woolpack release boundary.** A dedicated GitHub release workflow builds
  wheel and source artifacts in a read-only job, verifies that the
  `woolpack-v<version>` tag resolves to a commit on `main`, then passes only
  those artifacts to a two-step publish job. PyPI authentication is a
  short-lived OIDC identity scoped to the reviewed `pypi` environment; no
  registry token is stored in the repository or GitHub.

## Deployment

One container: `Dockerfile` installs both local workspace projects, then
`scripts/docker-entrypoint.sh` runs litestream restore/replicate only when
object-storage secrets exist (else plain uvicorn). `fly.toml` +
`litestream.yml` are the ready fly.io template with continuous SQLite backup
to the host's own bucket. All runtime configuration is environment variables
(`app/config.py`, documented in `.env.example`).

CI installs the locked workspace and separately builds both `woolroom` and
`woolpack` distribution formats.
The core wheel smoke pins root/package/dependency version parity, requires the
static client and Alembic resources in the artifact, installs wheel and source
artifacts beside the exact Woolpack wheel, imports the public composition API,
and upgrades a synthetic SQLite database from packaged migrations.
The standalone smoke checks that packaged resources match the canonical
in-repo example/style, package modules contain no `app.*` imports, default and
engine-derived `PackEnvironment` values agree, and isolated wheel and source
installs can scaffold, render, and strict-lint a new pack. Publishing is separate:
GitHub release tags under the Woolpack namespace build and inspect both wheel
and source distributions before the environment-gated Trusted Publishing job
uploads them to PyPI.

## What this design refuses

Gamification surfaces · accounts/marketplace/payments/hosted service · code
or private site content in `PACK_PATHS` · generic (uncapped or anonymous)
N-human rooms · runtime pack install in v1. These are load-bearing, not backlog.
