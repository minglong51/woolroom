# woolroom — Low-Level Design

**Refreshed:** 2026-09-03 (0.3.0 parity and release boundaries)

Module-level contract for the public tree. Companion to
[docs/design/HLD.md](HLD.md); pack format details live in
[docs/packs.md](../packs.md). Where this doc and the code disagree, the
code is right and this doc is stale.

## `app/` — application core

- `config.py` — `Settings` (pydantic-settings, `.env`-backed). Every knob
  is an env var: `SECRET_KEY`, `SITE_PASSWORD`, `DATABASE_URL`, `BASE_URL`,
  `HOME_TZ`, `ENV`, LLM lane (`LLM_PROVIDER` disabled/anthropic/ollama,
  model/token/timeout, `LLM_DAILY_CALL_CAP`), memory caps,
  `ADOPT_ALLOWLIST`, deployment-owned adoption identities
  (`ADOPT_PRIMARY_SPECIES`/`COAT`, `ADOPT_SECONDARY_SPECIES`/`COAT`),
  `PACK_PATHS` (CSV of additional public local pack dirs),
  household shape (`HOUSEHOLD_SIZE` pinned `!= 2` raises — pair-shaped by
  design; `MAX_ROOMS_PER_HOUSEHOLD`, `QUIRK_PICK_COUNT`), guest mode
  (`GUEST_ACCESS_ENABLED`, `GUEST_PET_ID`), `ADMIN_TOKEN`, `OPEN_SIGNUP`.
  Prod validators: strong secret required, only metered `sk-ant-api` keys,
  guest mode requires a pinned pet.
- `main.py` — FastAPI entry. `create_app(overlay_provider=...,
  auth_namespace=..., adoption_defaults=...)` installs the trusted provider
  or a default empty provider and stores the validated auth namespace and
  adoption identities on app state. Lifespan: provider startup/shutdown around
  the app resources; idempotent core dog/pig profile loading followed by
  `load_packs(settings.pack_paths)`, then adoption-default validation against
  the live registry (fail-closed; boot refuses on gate violations), boot refusal when
  `SITE_PASSWORD`/guest access is enabled over the default `SECRET_KEY`
  (the gate cookies would be forgeable in any ENV), dev-only
  `create_all` + tolerant column ALTERs (prod schema is alembic-only),
  scheduler start/stop. Outer site-access middleware (site cookie, guest
  cookie allowlist), baseline security headers (`setdefault`, stricter
  per-route policies win), and an auth-failure throttle: failed (401/403)
  attempts on `POST /api/site-access` and `/admin/*` are counted per
  client IP in-process (single-worker by design) — 5 and 20 per 15
  minutes respectively, then 429; successes never count. Access-page
  continuation accepts only same-origin absolute paths in both the server
  redirect and inline client, and successful site-password authentication
  clears the guest cookie before the browser continues as an owner.
  Serves `/` with `INDEX_VOICE` substituted and
  `?v=<APP_VERSION>` cache-busted statics (immutable when the version
  matches, revalidate otherwise); `/api/voice` and `/api/packs` ride the
  same two cache policies. Those endpoints contain public distribution data
  only. `APP_VERSION` = `GIT_SHA` or newest static mtime.
- `time.py` — shared UTC helpers (naive-UTC storage convention).
- `room_contract.py` — `FX_VOCAB_VERSION` + `FX_MODES` (the only legal
  scene-fx modes) + `TRACE_CUE_MAP` (shared-trace → ambient cue) +
  `QUIRK_EMIT_TYPES` (the only legal quirk emit frame types — emits ride
  the protocol socket, so an open string would let a pack spoof frames).
  Bump the version on any mode add/remove/rename; unknown modes and
  unknown emit types are loader errors.

## `app/engine/` — rule-driven state logic (no storage/network I/O)

- `mood.py` — `MoodState` + bounded transitions over two invisible axes:
  arousal (diurnal curve + interaction bumps) and valence (care consistency
  over ~7 days). Slow transitions, minutes not turns; `pick_animation`.
- `aging.py` — compressed timeline: 30 real days = 1 pet year; species-neutral
  life stages `juvenile/young/adult/senior` drive render scale and proportions.
- `outings.py` — deterministic daily outing fragments (seeded by pet + day;
  same inputs, same fragment on any worker).
- `quirks.py` — the quirk condition-grammar interpreter. Definitions are
  data; this module owns `CONDITION_EVALUATORS` (the whole legal `when`
  vocabulary), `POSE_WRITE_OPS`, and the base pose rule. Four channels:
  pose rig writes, action effects, scheduler effects, emitted events. No
  quirk ids appear here.

## `app/runtime/` — the respond pipeline and live scene state

- `actions.py` — the action orchestration `/api/action` delegates to
  (`ActionIn` + `perform_action`): HMAC-fingerprinted `origin_id`
  idempotency receipts, the buffer write, pet-scoped ignore resolution from
  the prior action's persisted `BufferEvent.meta` outcome (the first
  `HOME_TZ`-local action each day and an action after an ignore always
  engage), mood nudge + quirk effect, scene-fx modifier resolution, milestone
  promotion, `respond()`, and the post-commit room broadcasts. Raises
  `HTTPException` — its 409/422 shapes are the endpoint's contract.
- `respond.py` — the only entry routes call: ignore check (sleeping /
  `ignore_rate`) → LLM attempt → validator → phrasebook fallback;
  utterance rate limit (≤1 per pet per 5 min; `*...*` body lines exempt).
- `client.py` — LLM providers behind one `complete()` surface: Anthropic
  (prompt-cached system block; no key → `None` → fallback) and Ollama
  (OpenAI-compatible endpoint). Every call logged via `llm_log`.
- `prompt.py` — stable per-pet system prompt (species, quirks, core facts,
  scene rules, anti-chatbot rules; human text wrapped as data) + per-turn
  user message (mood, recent buffer).
- `validator.py` — rejects chatbot-slop (banned substrings, >80 chars,
  questions-back, emoji); the phrasebook is the floor.
- `llm_log.py` — best-effort `llm_calls` rows (never raises) plus an
  in-process attempt counter; `calls_today` fails CLOSED (reports at least
  the cap when the DB count is unavailable) so the budget circuit-breaker
  can never fail open on a metered key.
- `pet_state.py` — the one builder of the `pet_state` payload for REST, WS
  initial push, and scheduler broadcasts; resolves the guest demo pet and
  projects guest state through a top-level allowlist plus nested scene/event
  allowlists. A host-side visit adds only its id/role and the visible
  visitor's id/name/species/coat/animation/scale; sibling, household, and
  away-visit details remain private.
- `scene_fx.py` — short-lived in-process scene effects (quirk visibility).
- `shared_trace.py` — turns the partner's recent action into an ambient
  scene cue via `TRACE_CUE_MAP`.
- `visits.py` — playdate visits: one pet in the sibling room, in-process,
  lazily expired; the DB records only the story (buffer events).

## `app/data/` — content modules (tables, not logic)

- `body_language.py` — the fallback phrasebook = the builtin cat's voice:
  `BODY_LANGUAGE`, `ACTION_LANGUAGE`, `PET_SPOT_LANGUAGE` (arousal × valence
  cells), `MESSAGE_TINY_UTTERANCES` (per valence; overlays swap this table
  WHOLE), `MESSAGE_CONTEXT_LANGUAGE`, intent lexicons (en + zh),
  `SPECIES_PHRASE_OVERLAYS` (loader-registered), bucket helpers, `_pick`
  rotation with repeat guard, `classify_message`, `fallback_phrase`.
- `voice.py` — species-neutral server-side copy (milestones, templates,
  anniversaries, room notes, `origin_line`, `SYSTEM_TEMPLATE`, `STAGE_BLURB`) plus
  `CLIENT_VOICE` (served at `/api/voice`) and `INDEX_VOICE` (substituted
  into index.html at serve time). Golden-pinned by `tests/`.
- `species.py` — the species registry: locked temperament, coats, pronoun,
  overlay key per species. Builtin: the cat. Mutated only by the loader at
  boot via `register_species`.
- `quirks_catalog.py` — the eight builtin quirks as plain data (label,
  description, `behavior` rules per channel).

## `app/packs/` — public application adapter for pack format v1

- `loader.py` — owns runtime registration, not data validation.
  `pack_environment()` snapshots the live engine contract (fx modes, action/
  spot/condition/pose vocabularies and occupied species/overlay/quirk/coat
  ids) into an immutable `woolpack.PackEnvironment`. `load_pack(path)` calls
  `woolpack.validate_pack(path, environment=pack_environment())`; only after
  that pure, fail-closed call succeeds does it register species, phrase
  overlays, quirks, voice, and client assets. Exposes `LOADED_PACKS`,
  `PACK_ASSETS`, `load_packs(paths)`, and `client_pack_assets()` for the API.
  `load_core_profiles()` loads the packaged dog and pig profiles once per
  process, verifies complete registry state on repeat lifespans, and leaves
  ordinary `load_pack(s)` collision-strict. Core profiles load before
  `PACK_PATHS`, reserving `dog` and `pig`. Every `PACK_PATHS` entry is public;
  private providers never call this registration path.
- `sanitize.py` — compatibility re-export of the standalone sanitizer so
  existing application and test imports keep one implementation.
- `lint.py` — compatibility re-export of the standalone authoring checker;
  the package owns `LintFinding`, `LintReport`, and `lint_pack()`.

## `packages/woolpack/` — standalone pack contract and authoring wheel

- `pyproject.toml` — independently buildable `woolpack` 0.3.0 distribution,
  Python ≥3.11, with only PyYAML as a runtime dependency and console entry
  point `woolpack = woolpack.cli:main`. Its package README supplies the PyPI
  long description and links the owned product page, format guide, source,
  issues, and pack index. Setuptools includes the resource package (room CSS
  plus the Pebble scaffold) in the wheel. Distribution metadata declares
  `MIT AND CC0-1.0` and ships both texts: MIT covers the tool code, while the
  bundled Pebble template declares CC0. Its PEP 561 marker publishes the
  card and environment types to external type checkers.
- `src/woolpack/contract.py` — frozen `PackEnvironment`: the engine-owned
  vocabularies and occupied ids against which otherwise standalone pack
  data is checked. `DEFAULT_ENVIRONMENT` mirrors the shipped woolroom
  engine; CI asserts exact equality with `app.packs.loader.pack_environment()`.
- `src/woolpack/cards.py` — `PetCardV1`, the exact versioned browser
  projection a trusted site provider may return. `parse_pet_card()` rejects
  missing/extra fields, malformed identifiers, palettes, geometry, and SVG;
  sanitizes and byte-caps the figure; and freezes nested maps.
  `pet_card_payload()` emits a fresh JSON-compatible copy of only the eight
  allowed fields.
- `src/woolpack/validation.py` — the pure fail-closed contract.
  `validate_pack(path, *, environment=DEFAULT_ENVIRONMENT) -> ValidatedPack`
  enforces manifest, confinement/symlink, byte/prose, safe-YAML, SVG,
  species/phrase/quirk/voice, vocabulary, and collision gates without
  importing or mutating the application. `ValidatedPack` carries its
  `PackRecord`, species, overlays, quirks, voice, raw SVGs, and environment
  for the application adapter or authoring tools to consume.
- `src/woolpack/sanitize.py` — stdlib allowlist SVG sanitizer: elements
  outside the allowed set drop with their subtree; event handlers, hrefs,
  Alpine directives, CSS escapes, and URL-bearing values on any attribute
  are stripped while inert v1 attributes remain compatible. The root must
  be one `<g>`/`<svg>`;
  DTD/entity declarations and over-deep nesting fail as `SvgSanitizeError`.
- `src/woolpack/lint.py` — `lint_pack(path) -> LintReport` runs standalone
  validation, then checks rig/eye/palette handles, rejects host-owned
  `.breath`/`.squishg` wrappers inside pack art, and checks stray ids,
  sanitizer drops, touch geometry, overlay completeness/sparsity, and voice coverage.
  `LintReport.exit_code(strict=True)` turns WARN into a registry-CI failure;
  lint reads only and has no runtime registry to restore.
- `src/woolpack/render.py` — `render_board(pack_dir: Path) -> str` validates
  first, then renders coats × poses and a hitbox overlay into one static HTML
  board using the packaged room CSS. `main(argv)` writes the requested file.
- `src/woolpack/scaffold.py` — `main(argv)` copies either a caller-supplied
  source or the packaged Pebble resource, renames every identity-bearing
  species/phrase/quirk stem, rewrites voice references, and refuses to
  overwrite an existing destination.
- `src/woolpack/cli.py` — `main(argv)` handles `--version` and dispatches
  `new`, `render`, and `lint` to the three authoring modules.
- `src/woolpack/resources/` — `style.css` and the complete Pebble template.
  These are deliberate copies of `app/static/style.css` and `packs/pebble`;
  CI byte-compares/diffs them so a source change cannot silently stale a
  standalone wheel.

## `app/api/`, `app/auth/`, `app/channels/`

- `api/http.py` — REST: `/healthz`, `/api/start|logout|me`, adopt flow
  (`/api/adoption-defaults`, `/api/adopt`, `/api/room`,
  `/api/adopt-second`, `/api/second-quirk`),
  pairing (`/api/invite`, `/join/{token}`, `/api/join-pending`,
  `/r/{token}` recovery, `/api/recovery-url` — the login bookmark on
  demand; `/api/me` deliberately does not carry it), `/api/action` (the interaction verb),
  `/api/visit(+end)`, aliases/coat, memory pin/unseen/read, guest
  `/api/guest/scene`. Authenticated `/api/me` and cookie-authorized
  `/api/guest/scene` add a top-level `card` from their distinct provider
  method after validating its pet binding, exact schema, species, and coat;
  null is the direct-hosting default. Pet-scoped `/api/card` safely refreshes
  that projection after realtime identity changes without relying on a
  cross-device active-room pointer. An authenticated caller may request any
  participating pet, including a ceremony-pending one, but this projection
  does not weaken the confirmed-participant checks on room/action routes. A
  guest may request only the pinned pet or the visitor in that pet's current
  host-side playdate; unknown, unrelated, ended, and away-side subjects fail
  before the provider is called. `/api/room` and `/api/coat` return the newly
  bound card with their mutation response. These dynamic card responses are
  `private, no-store`.
  The public four-field adoption-default DTO drives previews and coat choices;
  the server fixes each adoption to its configured species and validates the
  chosen coat within that species. `/api/action` holds the mutation guard and
  delegates to `runtime/actions.py`. Signup is invite-only
  (`OPEN_SIGNUP` off) with one
  designed exception: an empty users table admits the first human — a fresh
  deployment is otherwise unreachable — then the gate closes itself.
  `/api/me` reports the same effective openness.
- `api/admin.py` — `/admin/*` operator routes (token-gated via
  `X-Admin-Token`; empty `ADMIN_TOKEN` disables): user list/delete, pet
  merge/delete, recovery-link regenerate/revoke, llm stats. Split from
  `http.py` so the ops surface and the product surface read apart.
- `api/ws.py` — `/ws` scene socket: initial `pet_state` push, then
  `pet_state`/`presence` messages; reads the app's configured auth namespace.
- `api/deps.py` — session-cookie user/pet dependencies; cookie lookup is
  request/app-scoped rather than a module-import-time alias.
- `auth/session.py` — signed session cookie (itsdangerous); no email or
  password. Helper defaults retain `woolroom_session` compatibility while
  request paths pass the app namespace through signing and verification.
- `auth/site_access.py` — optional outer password gate and read-only guest
  cookie. Default helpers retain the `woolroom_site_access` and
  `woolroom_guest_access` names; composed apps pass their distinct names and
  salts explicitly.
- `channels/base.py` — `Channel` protocol (how the pet reaches a human).
- `channels/webapp.py` — the WebApp channel: in-process WS fanout keyed by
  pet_id, plus per-pet mutation guards.

## `app/memory/`, `app/scheduler/`, `app/storage/`, `app/eval/`

- `memory/buffer.py` — rolling window of recent events (no semantic user
  content), action receipts, and latest-event lookup by event type.
- `memory/moments.py` — 1–2 shared moments promoted per week from buffer.
- `memory/core.py` — permanent facts (names, adoption date, firsts).
- `scheduler/jobs.py` — APScheduler jobs: `mood_drift`, `daily_outing`,
  `anniversary`, busy-mode expiry, demo self-play; broadcasts via the
  channel. Care rate approximated from the last 7 days of buffer.
- `storage/db.py` — async engine/session; SQLite pragmas per connection
  (WAL, foreign keys on).
- `storage/models.py` — tables: `users`, `pets`, `pet_participants`,
  `buffer_events`, `action_receipts`, `moments`, `core_facts`, `outings`,
  `magic_links`, `llm_calls`, `eval_runs`.
- `storage/repo.py` — thin query helpers; keeps SQL out of routes.
- `eval/corpus.py` — YAML corpus loader; `eval/runner.py` — corpus → prompt
  → LLM → validator → `eval_runs`, with session diffs.

## `app/static/` — the room client

- `index.html` — the whole SPA shell (landing/adopt/room/ceremony), with
  `{{VOICE_*}}` placeholders substituted at serve time. `access.html` — the
  guest-first access threshold with invite-aware owner disclosure. `style.css`
  — the room's look + rig animation classes.
  `favicon.svg` — the cat mark. `app.js` — boot glue.
- `js/state.js` + `js/api.js` — Alpine store and REST/WS client calls; the
  reactive private card cache is keyed by pet id and held separately from the
  public packs registry. Boot seeds the active entry, then fetches only a
  ceremony-pending pet and the current host-side visitor; room/coat/state
  changes select, invalidate, refill, or discard entries by exact
  pet/species/coat subject. Auth-context resets advance a cache generation;
  responses captured under an older generation cannot populate the new
  context even when both users can see the same pet. Boot also fetches the
  public adoption-default DTO and seeds primary/secondary coat selection from it.
- `js/wool.js` — the scene core: boot, the light-not-dye clock, motion
  primitives and locomotion, touch resolution, presentation reads
  (traces, rig style, poses). A matching card supplies the active pet's
  pronoun. Its former siblings, same component
  `this`: `js/woolvisits.js` (the door next door + playdate
  choreography), `js/woolevents.js` (scene-event dedupe, return cues,
  the drain queue and plan runner), `js/woolfx.js` (fx primitives +
  the verb performances).
- `js/figures.js` — figure art + the rig class contract (the builtin cat;
  pack figures satisfy the same contract), adoption/ceremony profile previews,
  visitor figures, and coat palette application. A matching cached card is
  resolved as a one-pet ephemeral catalog for the active, ceremony, or visitor
  subject and never merged into the public `/api/packs` map. Initial-adoption
  previews have no persisted pet id and deliberately resolve public art only.
- `js/sound.js` — WebAudio synth: per-species motifs (the cat voice) and
  room sounds; no audio assets.
- `js/ws.js` — the live channel, reconnects, pet-scoped card invalidation and
  visible-card synchronization after realtime coat/visit/household changes,
  stale old-room frame rejection, and
  shared-trace cue derivation (mirrors `TRACE_CUE_MAP`; pinned by
  `tests/test_room_contract.py`).
- `js/presence.js`, `js/memory.js`, `js/quirks.js`, `js/ui.js` — presence
  pill, memory/moments views, species-aware quirk/adoption flow, misc UI.
- `vendor/` — vendored Alpine; third-party, not covered by this contract.

## `woolroom/` — public composition and migration package

- `__init__.py` — stable consumer import: package version,
  `PLUGIN_API_VERSION`, `create_app(overlay_provider=..., auth_namespace=...,
  adoption_defaults=...)`, `AdoptionDefaults`, `AuthNamespace`,
  `BoundPetCard`, provider types, database boundary types/functions, and
  migration path/head/revision helpers for installed-wheel Alembic adoption.
  The package's PEP 561 marker makes this composition surface typed for
  consumers.
- `adoption.py` — frozen `AdoptionDefaults`: primary/secondary species and
  coat pairs, live-registry validation after pack loading, and the exact
  four-field public client projection.
- `auth.py` — frozen cookie/salt namespace for session, site-access, guest,
  and pending-invite flows. Values are bounded safe ASCII; cookie names and
  signing salts must each be pairwise distinct. `DEFAULT_AUTH_NAMESPACE`
  pins direct-hosting behavior.
- `overlay.py` — narrow immutable owner/guest subjects, async provider
  lifecycle/protocol, empty direct-hosting implementation, and the
  parse-again subject-binding boundary. Providers return `BoundPetCard`; core
  verifies and strips its pet id before emitting the exact `PetCardV1` DTO.
  Provider exceptions, unknown card fields, and pet/species/coat mismatches
  fail closed. Plugin API v2 expands owner calls from only the active room to
  any participating pet (including ceremony-pending and playdate
  subjects), and guest calls from only the pin to its current visible visitor;
  subjects convey projection visibility only, never room/action authority.
- `database.py` — installed SQLite boundary and `woolroom-db` CLI.
  `inspect_database()` classifies empty, known-versioned, canonical
  versionless, and unsafe version states without writing. `upgrade_database()`
  permits only empty or exactly-one-known-revision inputs after SQLite
  `quick_check` and core-table foreign-key checks. A known revision is the
  semantic compatibility assertion; versioned provider histories are not
  re-fingerprinted against one public DDL spelling.
  `adopt_database(..., apply=False)` compares a normalized core fingerprint
  and is dry-run-only until `apply=True` stamps the packaged head. The
  fingerprint sorts physical columns and ignores constraint/index/trigger
  names while comparing nullability/types/defaults, ordered primary keys,
  logical indexes/uniques, foreign keys/on-delete, checks, and triggers. Extra
  tables are reported and preserved.
- `migrations/` — packaged Alembic `env.py`, template, and `versions/`.
  `env.py` honors a caller-supplied URL or synchronous SQLAlchemy connection,
  otherwise it resolves `DATABASE_URL` from application settings. Alembic is
  the only thing that touches schema in a deployed environment. Individual
  revision files are generated and carry no independent contract.

## `scripts/` — operator CLIs and checkout-compatible authoring shims

- `pack_new.py`, `pack_lint.py`, `pack_render.py` — thin re-export/
  executable shims over `woolpack.scaffold`, `woolpack.lint`, and
  `woolpack.render`. Existing checkout commands and imports retain their
  interface while the installed `woolpack` console script owns behavior.
- `migrate.py` — invokes the public fail-closed `upgrade_database()` API at
  container startup.
- `docker-entrypoint.sh` — container boot: starts as root only to chown
  the writable locations (`/app` dir non-recursively, `/data` when
  mounted — fly volumes arrive root-owned), then re-execs itself as the
  `app` user (uid 1000) via runuser. It validates a safe
  `WOOLROOM_ASGI_APP=module:attribute` and resolves the absolute
  `WOOLROOM_DB_PATH` (default `/data/woolroom.db`) with a compatible legacy
  `DATABASE_URL`; a mismatch exits before Litestream restore, config
  pre-flight, or migration. The selected ASGI target must import as a callable
  before restore or migration. The one resolved path feeds Litestream and the
  fail-closed migration API, then the selected app runs under exactly one
  uvicorn worker. Code stays root-owned read-only.
- `seed_demo_pet.py` — creates the read-only guest-mode demo pet
  (idempotent) and prints its id for `GUEST_PET_ID`.
- `eval.py` — eval harness CLI: `run` / `diff` / `sessions`.
- `denylist_check.py` — publish gate: scans the tree for private
  identifiers (whole-word patterns, Tailscale IP range, private timezone);
  exit 1 with `file:line` on any hit.
- `normalize_sdist.py` — rewrites release source archives with sorted members,
  fixed ownership, and the tag commit epoch so a retry rebuilds identical bytes.
- `verify_pypi_release.py` — compares local wheel/source hashes with the PyPI
  JSON API, permits only a matching partial release before upload, and requires
  exact completeness afterward.
- `backup.sh` — pulls the live SQLite DB off the fly machine to local
  storage; exits nonzero unless `PRAGMA quick_check` returns `ok`.
- `smoke-browse.sh` — optional headless visual smoke (requires a locally
  installed browse daemon; not part of CI).

## Workspace, container, and CI integration

- Root `pyproject.toml` — the application uses the PEP 639-capable
  pinned `setuptools==84.0.0` build backend and keeps its direct `PyYAML`
  declaration, pins the compatible `woolpack==0.3.0`, packages `app.static`,
  the canonical dog/pig profile data under `app.packs`, and the public
  `woolroom` namespace, and declares
  `packages/woolpack` as a uv workspace member/source. `uv.lock` therefore
  resolves the app and tool distribution as one locked local graph while the
  child project remains independently buildable; a child contract-version
  bump must move the application pin deliberately.
- `Dockerfile` — copies root metadata/readme plus the child project metadata
  and `src/` tree before dependency installation, then installs both local
  editable projects in one pip transaction; it also copies `app` (including
  the canonical profiles) and the public composition/migration package. It
  selects the matching amd64/arm64 Litestream package from Docker's
  `TARGETARCH` and creates `/data` writable by uid 1000 so zero-config Docker
  boot and a named volume share the same database default. The runtime image
  therefore never resolves an unrelated registry package named `woolpack`.
- `.github/workflows/ci.yml` — after locked workspace sync, the standalone
  boundary smoke compares the packaged style/template with their canonical
  app/repository copies, AST-checks every package module for forbidden
  `app.*` imports, and asserts default/application `PackEnvironment` parity.
  It then builds and metadata-checks wheel and source distributions, installs
  each into an isolated venv, and exercises `--version`, scaffold, render, and
  strict lint without the woolroom app. The core distribution smoke builds
  Woolroom wheel/source artifacts beside the exact Woolpack wheel, verifies
  package/dependency version parity and required static/migration/card/auth/
  profile/database files and the `woolroom-db` entry point, checks every source
  and CLI fallback plus contributor-template version pin, installs each core
  artifact in isolation, enters a composed dog/pig app lifespan twice, exercises
  the stable composition API, and upgrades/inspects synthetic SQLite through the
  packaged fail-closed boundary.
- `.github/workflows/release-woolpack.yml` — a published GitHub release whose
  tag starts with `woolpack-v` checks out the event SHA with full history,
  requires the tag version to match package metadata, and requires that SHA
  to be on `origin/main`. A read-only build job uses pinned release tooling to
  build, normalize, metadata-check, and clean-install both distributions. The
  separate `pypi` environment job checks out the tagged verifier and downloads
  only those artifacts; only this two-step job gets OIDC `id-token: write`, and
  the pinned PyPA publisher action exchanges that identity for the short-lived
  upload credential. Only the downloaded artifacts are uploaded. Before a
  retry, exact hashes must describe a subset of the build; after upload, the
  public release must be complete and hash-identical.
- `.github/workflows/release-woolroom.yml` — distinct root trusted publishing
  for `woolroom-v<version>`. The read-only build validates tag/version/main
  ancestry, root/Woolpack parity, and availability of the matching Woolpack
  version on the public index; then it inspects and clean-installs both
  deterministic Woolroom artifacts with an exact locally built Woolpack wheel
  and exercises the installed CLI, app, and migrations. Only the resulting
  Woolroom artifacts are downloaded by the `pypi-woolroom` OIDC publish job,
  which also checks out the tagged verifier; only those artifacts are uploaded,
  with the same hash-matching partial retry and complete-release verification.

## `packs/pebble/` — the shipped example pack

A pet rock, deliberately minimal: manifest, one species (temperament,
one coat, hitbox geometry, SVG figure), a phrase overlay (six pinned
cells + a complete tiny table — the rest falls through by design), one
quirk (`sunbather`), and `voice.yaml` coat/quirk copy. Lints 14 PASS ·
0 WARN · 0 ERROR and is the byte-for-byte packaged scaffold checked by CI.

## `app/packs/profiles/` — packaged public profiles

`dog/` and `pig/` are canonical data-only packs shipped inside the Woolroom
application distribution and Docker image. Both carry temperament, coats,
neutral rig art, complete rotating phrase overlays, and coat labels; both pass
the same strict Woolpack gates as external packs. Lifespan loads them before
operator `PACK_PATHS`, so direct hosts and private consumers can select either
through `AdoptionDefaults` without vendoring public behavior.
