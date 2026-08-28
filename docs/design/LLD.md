# woolroom — Low-Level Design

**Refreshed:** 2026-08-27 (woolpack Trusted Publishing; standalone woolpack workspace 2026-08-26)

Module-level contract for the public tree. Companion to
[docs/design/HLD.md](HLD.md); pack format details live in
[docs/packs.md](../packs.md). Where this doc and the code disagree, the
code is right and this doc is stale.

## `app/` — application core

- `config.py` — `Settings` (pydantic-settings, `.env`-backed). Every knob
  is an env var: `SECRET_KEY`, `SITE_PASSWORD`, `DATABASE_URL`, `BASE_URL`,
  `HOME_TZ`, `ENV`, LLM lane (`LLM_PROVIDER` disabled/anthropic/ollama,
  model/token/timeout, `LLM_DAILY_CALL_CAP`), memory caps,
  `ADOPT_ALLOWLIST`, `PACK_PATHS` (CSV of local pack dirs),
  household shape (`HOUSEHOLD_SIZE` pinned `!= 2` raises — pair-shaped by
  design; `MAX_ROOMS_PER_HOUSEHOLD`, `QUIRK_PICK_COUNT`), guest mode
  (`GUEST_ACCESS_ENABLED`, `GUEST_PET_ID`), `ADMIN_TOKEN`, `OPEN_SIGNUP`.
  Prod validators: strong secret required, only metered `sk-ant-api` keys,
  guest mode requires a pinned pet.
- `main.py` — FastAPI entry. Lifespan: `load_packs(settings.pack_paths)`
  first (fail-closed; boot refuses on gate violations), boot refusal when
  `SITE_PASSWORD`/guest access is enabled over the default `SECRET_KEY`
  (the gate cookies would be forgeable in any ENV), dev-only
  `create_all` + tolerant column ALTERs (prod schema is alembic-only),
  scheduler start/stop. Outer site-access middleware (site cookie, guest
  cookie allowlist), baseline security headers (`setdefault`, stricter
  per-route policies win), and an auth-failure throttle: failed (401/403)
  attempts on `POST /api/site-access` and `/admin/*` are counted per
  client IP in-process (single-worker by design) — 5 and 20 per 15
  minutes respectively, then 429; successes never count.
  Serves `/` with `INDEX_VOICE` substituted and
  `?v=<APP_VERSION>` cache-busted statics (immutable when the version
  matches, revalidate otherwise); `/api/voice` and `/api/packs` ride the
  same two cache policies. `APP_VERSION` = `GIT_SHA` or newest static mtime.
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
- `aging.py` — compressed timeline: 30 real days = 1 pet year; life stages
  `kitten/young/adult/senior` drive render scale and proportions.
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
  idempotency receipts, the buffer write, mood nudge + quirk effect,
  scene-fx modifier resolution, milestone promotion, `respond()`, and the
  post-commit room broadcasts. Raises `HTTPException` — its 409/422
  shapes are the endpoint's contract.
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
  initial push, and scheduler broadcasts; resolves the guest demo pet.
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
- `voice.py` — server-side copy (milestones, templates, anniversaries,
  room notes, `origin_line`, `SYSTEM_TEMPLATE`, `STAGE_BLURB`) plus
  `CLIENT_VOICE` (served at `/api/voice`) and `INDEX_VOICE` (substituted
  into index.html at serve time). Golden-pinned by `tests/`.
- `species.py` — the species registry: locked temperament, coats, pronoun,
  overlay key per species. Builtin: the cat. Mutated only by the loader at
  boot via `register_species`.
- `quirks_catalog.py` — the eight builtin quirks as plain data (label,
  description, `behavior` rules per channel).

## `app/packs/` — application adapter for pack format v1

- `loader.py` — owns runtime registration, not data validation.
  `pack_environment()` snapshots the live engine contract (fx modes, action/
  spot/condition/pose vocabularies and occupied species/overlay/quirk/coat
  ids) into an immutable `woolpack.PackEnvironment`. `load_pack(path)` calls
  `woolpack.validate_pack(path, environment=pack_environment())`; only after
  that pure, fail-closed call succeeds does it register species, phrase
  overlays, quirks, voice, and client assets. Exposes `LOADED_PACKS`,
  `PACK_ASSETS`, `load_packs(paths)`, and `client_pack_assets()` for the API.
- `sanitize.py` — compatibility re-export of the standalone sanitizer so
  existing application and test imports keep one implementation.
- `lint.py` — compatibility re-export of the standalone authoring checker;
  the package owns `LintFinding`, `LintReport`, and `lint_pack()`.

## `packages/woolpack/` — standalone pack contract and authoring wheel

- `pyproject.toml` — independently buildable `woolpack` 0.1 distribution,
  Python ≥3.11, with only PyYAML as a runtime dependency and console entry
  point `woolpack = woolpack.cli:main`. Its package README supplies the PyPI
  long description and links the owned product page, format guide, source,
  issues, and pack index. Setuptools includes the resource package (room CSS
  plus the Pebble scaffold) in the wheel. Distribution metadata declares
  `MIT AND CC0-1.0` and ships both texts: MIT covers the tool code, while the
  bundled Pebble template declares CC0.
- `src/woolpack/contract.py` — frozen `PackEnvironment`: the engine-owned
  vocabularies and occupied ids against which otherwise standalone pack
  data is checked. `DEFAULT_ENVIRONMENT` mirrors the shipped woolroom
  engine; CI asserts exact equality with `app.packs.loader.pack_environment()`.
- `src/woolpack/validation.py` — the pure fail-closed contract.
  `validate_pack(path, *, environment=DEFAULT_ENVIRONMENT) -> ValidatedPack`
  enforces manifest, confinement/symlink, byte/prose, safe-YAML, SVG,
  species/phrase/quirk/voice, vocabulary, and collision gates without
  importing or mutating the application. `ValidatedPack` carries its
  `PackRecord`, species, overlays, quirks, voice, raw SVGs, and environment
  for the application adapter or authoring tools to consume.
- `src/woolpack/sanitize.py` — stdlib allowlist SVG sanitizer: elements
  outside the allowed set drop with their subtree; `on*`, `href`, and
  `style` containing `url(` are stripped; the root must be one `<g>`/`<svg>`;
  DTD/entity declarations and over-deep nesting fail as `SvgSanitizeError`.
- `src/woolpack/lint.py` — `lint_pack(path) -> LintReport` runs standalone
  validation, then checks rig/eye/palette handles, stray ids, sanitizer
  drops, touch geometry, overlay completeness/sparsity, and voice coverage.
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
  (`/api/adopt`, `/api/room`, `/api/adopt-second`, `/api/second-quirk`),
  pairing (`/api/invite`, `/join/{token}`, `/api/join-pending`,
  `/r/{token}` recovery, `/api/recovery-url` — the login bookmark on
  demand; `/api/me` deliberately does not carry it), `/api/action` (the interaction verb),
  `/api/visit(+end)`, aliases/coat, memory pin/unseen/read, guest
  `/api/guest/scene`. `/api/action` holds the mutation guard and
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
  `pet_state`/`presence` messages; cookie-authed.
- `api/deps.py` — session-cookie user/pet dependencies.
- `auth/session.py` — signed `woolroom_session` cookie (itsdangerous); no
  email, no password.
- `auth/site_access.py` — optional outer password gate (`woolroom_site_access`
  timed cookie) and read-only guest cookie (`woolroom_guest_access`).
- `channels/base.py` — `Channel` protocol (how the pet reaches a human).
- `channels/webapp.py` — the WebApp channel: in-process WS fanout keyed by
  pet_id, plus per-pet mutation guards.

## `app/memory/`, `app/scheduler/`, `app/storage/`, `app/eval/`

- `memory/buffer.py` — rolling window of recent events (no semantic user
  content), action receipts.
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
  site-password page. `style.css` — the room's look + rig animation classes.
  `favicon.svg` — the cat mark. `app.js` — boot glue.
- `js/state.js` + `js/api.js` — Alpine store and REST/WS client calls.
- `js/wool.js` — the scene core: boot, the light-not-dye clock, motion
  primitives and locomotion, touch resolution, presentation reads
  (traces, rig style, poses). Its former siblings, same component
  `this`: `js/woolvisits.js` (the door next door + playdate
  choreography), `js/woolevents.js` (scene-event dedupe, return cues,
  the drain queue and plan runner), `js/woolfx.js` (fx primitives +
  the verb performances).
- `js/figures.js` — figure art + the rig class contract (the builtin cat;
  pack figures satisfy the same contract) and coat palette application.
- `js/sound.js` — WebAudio synth: per-species motifs (the cat voice) and
  room sounds; no audio assets.
- `js/ws.js` — the live channel, reconnects, shared-trace cue derivation
  (mirrors `TRACE_CUE_MAP`; pinned by `tests/test_room_contract.py`).
- `js/presence.js`, `js/memory.js`, `js/quirks.js`, `js/ui.js` — presence
  pill, memory/moments views, quirk pick + adopt flow, misc UI.
- `vendor/` — vendored Alpine; third-party, not covered by this contract.

## `migrations/` — alembic

`env.py` + `versions/`. Alembic is the only thing that touches schema in a
deployed environment; `scripts/migrate.py` runs `alembic upgrade head` at
container start (idempotent). Individual revision files are generated and
carry no independent contract.

## `scripts/` — operator CLIs and checkout-compatible authoring shims

- `pack_new.py`, `pack_lint.py`, `pack_render.py` — thin re-export/
  executable shims over `woolpack.scaffold`, `woolpack.lint`, and
  `woolpack.render`. Existing checkout commands and imports retain their
  interface while the installed `woolpack` console script owns behavior.
- `migrate.py` — `alembic upgrade head` (container startup).
- `docker-entrypoint.sh` — container boot: starts as root only to chown
  the writable locations (`/app` dir non-recursively, `/data` when
  mounted — fly volumes arrive root-owned), then re-execs itself as the
  `app` user (uid 1000) via runuser; litestream restore + replicate only
  when `BUCKET_NAME` is set, config pre-flight (a settings refusal
  prints a one-line remedy instead of a raw validation traceback),
  migrate, single-worker uvicorn. Code stays root-owned read-only.
- `seed_demo_pet.py` — creates the read-only guest-mode demo pet
  (idempotent) and prints its id for `GUEST_PET_ID`.
- `eval.py` — eval harness CLI: `run` / `diff` / `sessions`.
- `denylist_check.py` — publish gate: scans the tree for private
  identifiers (whole-word patterns, Tailscale IP range, private timezone);
  exit 1 with `file:line` on any hit.
- `backup.sh` — pulls the live SQLite DB off the fly machine to local
  storage; exits nonzero unless `PRAGMA quick_check` returns `ok`.
- `smoke-browse.sh` — optional headless visual smoke (requires a locally
  installed browse daemon; not part of CI).

## Workspace, container, and CI integration

- Root `pyproject.toml` — the application keeps its direct `PyYAML`
  declaration, pins the compatible `woolpack==0.1.1`, and declares
  `packages/woolpack` as a uv workspace member/source. `uv.lock` therefore
  resolves the app and tool distribution as one locked local graph while the
  child project remains independently buildable; a child contract-version
  bump must move the application pin deliberately.
- `Dockerfile` — copies the child project metadata and `src/` tree before
  dependency installation, then installs both local editable projects in one
  pip transaction. The runtime image therefore never resolves an unrelated
  registry package named `woolpack`.
- `.github/workflows/ci.yml` — after locked workspace sync, the standalone
  boundary smoke compares the packaged style/template with their canonical
  app/repository copies, AST-checks every package module for forbidden
  `app.*` imports, and asserts default/application `PackEnvironment` parity.
  It then builds and metadata-checks wheel and source distributions, installs
  each into an isolated venv, and exercises `--version`, scaffold, render, and
  strict lint without the woolroom app.
- `.github/workflows/release-woolpack.yml` — a published GitHub release whose
  tag starts with `woolpack-v` checks out the event SHA with full history,
  requires the tag version to match package metadata, and requires that SHA
  to be on `origin/main`. A read-only build job uses pinned release tooling to
  build, metadata-check, and clean-install both distributions. The separate
  `pypi` environment job receives only those artifacts; only this two-step job
  gets OIDC `id-token: write`, and the pinned PyPA publisher action exchanges
  that identity for the short-lived upload credential.

## `packs/pebble/` — the shipped example pack

A pet rock, deliberately minimal: manifest, one species (temperament,
one coat, hitbox geometry, SVG figure), a phrase overlay (six pinned
cells + a complete tiny table — the rest falls through by design), one
quirk (`sunbather`), and `voice.yaml` coat/quirk copy. Lints 13 PASS ·
0 WARN · 0 ERROR and is the byte-for-byte packaged scaffold checked by CI.
