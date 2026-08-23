# woolroom — Low-Level Design

**Refreshed:** 2026-08-22 (recovery URL on demand; earlier today: perform_action extraction, admin split, emit vocabulary, trust seams)

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

## `app/engine/` — pure deterministic logic (no I/O)

- `mood.py` — `MoodState` + pure transitions over two invisible axes:
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

## `app/packs/` — pack format v1 loader

- `loader.py` — `load_packs(paths)` at boot. Gates, all fail-closed with
  named `PackError` subclasses: manifest (exactly `name`, `version`,
  `author`, `license`, `fx_vocab_version ≤ FX_VOCAB_VERSION`), confinement
  (pack root only, symlinks refused), size (256KB/file, 1MB/pack, prose
  caps), SVG sanitize, species/phrase/quirk/voice shape, registry
  collision. Validates fully BEFORE registering; a refused pack registers
  nothing. Exposes `LOADED_PACKS`, `PACK_ASSETS`, `client_pack_assets()`.
- `sanitize.py` — allowlist-only SVG sanitizer, stdlib only: elements
  outside the allowed set dropped with subtree; `on*`, `href`, and
  `style` with `url(` stripped; root must be one `<g>`/`<svg>`;
  DTD/entity declarations refused before parse (entity-expansion memory
  bomb) and over-deep nesting refused — both as named `SvgSanitizeError`s.
- `lint.py` — authoring-side checker (`PASS|WARN|ERROR` findings +
  `exit_code(strict)`): runs the real loader first, then rig class/eye
  state/palette contracts, stray ids, sanitizer-drop mirror, geometry
  sanity, overlay tiny-table completeness and sparsity, voice coverage.
  Never mutates pack or registries.

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
- `js/wool.js` — the scene renderer: poses, fx modes, trace cues, presence.
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

## `scripts/` — operator and authoring CLIs

- `pack_new.py` — scaffolds a pack: copies the example (default
  `packs/pebble`) with the species stems renamed to the new id and the
  manifest's `name`/`author`/`license` rewritten in place. Exists because
  ids come from file stems, so a bare `cp -r` produces a
  `PackCollisionError` at boot.
- `pack_lint.py` — CLI over `app/packs/lint.py`; exit 1 on ERROR (and on
  WARN with `--strict` — registry-CI mode).
- `pack_render.py` — emits one self-contained HTML review board: coats ×
  poses, hitbox overlay against the pettable rect. Loads via the real
  loader.
- `migrate.py` — `alembic upgrade head` (container startup).
- `docker-entrypoint.sh` — container boot: litestream restore + replicate
  only when `BUCKET_NAME` is set, config pre-flight (a settings refusal
  prints a one-line remedy instead of a raw validation traceback),
  migrate, single-worker uvicorn.
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

## `packs/pebble/` — the shipped example pack

A pet rock, deliberately minimal: manifest, one species (temperament,
one coat, hitbox geometry, SVG figure), a phrase overlay (six pinned
cells + a complete tiny table — the rest falls through by design), one
quirk (`sunbather`), and `voice.yaml` coat/quirk copy. Lints 12 PASS ·
1 WARN (optional rig layers) · 0 ERROR; the WARN is pinned by tests.
