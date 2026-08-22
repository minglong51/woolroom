# Authoring a woolroom pack (pack format v1)

A pack is a directory of YAML + one SVG per species. **Packs are data, never
code** — there is no scripting, no CSS, no runtime download. The loader reads
local directories named by `PACK_PATHS` at boot, validates everything behind
fail-closed gates, and refuses to boot on any violation.

The contract-test suite IS the authoring tool: if `pack_lint` is green and the
`pack_render` board looks right, the pack works.

## The authoring loop

```sh
.venv/bin/python scripts/pack_new.py mole  # 1. copy the example WITH the stems
                                           #    renamed (ids come from file
                                           #    stems — a bare cp collides
                                           #    with pebble at boot)
$EDITOR packs/mole/pack.yaml          # 2. author + license
$EDITOR packs/mole/species/mole.yaml  # 3. temperament / coats / geometry
$EDITOR packs/mole/species/mole.svg   #    the figure art
$EDITOR packs/mole/phrases/mole.yaml  #    its voice (optional but recommended)
$EDITOR packs/mole/quirks/*.yaml      #    its habits (optional)
$EDITOR packs/mole/voice.yaml         #    coat labels + quirk preview copy

.venv/bin/python scripts/pack_render.py packs/mole   # 4. SEE it: coats × poses + hitboxes
.venv/bin/python scripts/pack_lint.py packs/mole     # 5. CHECK it: the contract suite
PACK_PATHS=packs/mole .venv/bin/uvicorn app.main:app   # 6. LIVE with it (boot loads the pack)
```

Iterate: draw → render → lint → boot. `pack_render` is the eyeball (open the
HTML it prints; check every coat in every pose, and the hitbox overlay against
the art). `pack_lint` is the contract (exit 1 on ERROR; `--strict` also exits 1
on WARN — that is registry-CI mode).

## Format reference

```
<pack-dir>/
  pack.yaml          # required manifest
  species/<id>.yaml  # temperament / pronoun / coats / hitbox geometry
  species/<id>.svg   # figure art: ONE <g> fragment, sanitized at load
  phrases/<id>.yaml  # optional phrase overlay for species <id>
  quirks/<id>.yaml   # optional quirk definitions (data, interpreted)
  voice.yaml         # optional CLIENT_VOICE additions (recursive merge)
```

Ids come from **file stems**: `species/mole.yaml` + `species/mole.svg` define
species `mole`. Species/coat ids match `[a-z][a-z0-9_]{0,15}`, quirk ids
`[a-z][a-z0-9_]{0,31}` (sized to the storage columns). All YAML is
`yaml.safe_load`. Hard caps: 256KB per file, 1MB per pack, 500 chars per prose
line (phrase lines, quirk text, temperament copy), 1000 chars per voice string.

### `pack.yaml` — every key required, unknown keys are an ERROR

| key | rule |
|---|---|
| `name` | non-empty string |
| `version` | string or number |
| `author` | non-empty string |
| `license` | non-empty string (SPDX id, e.g. `MIT`) |
| `fx_vocab_version` | int ≤ the engine's `FX_VOCAB_VERSION` (currently **1**) |

There is deliberately **no `description` key in v1** (unknown keys fail the
manifest gate) — the pack's paragraph lives in its README or YAML comments.

### `species/<id>.yaml` — exactly these four keys

- `temperament` — the prompt fodder the LLM lane narrates from:
  `breed_archetype` (short string), `description` (a paragraph),
  `traits` (exactly the six slots `warmth_baseline`, `sociability`, `energy`,
  `curiosity`, `expressiveness`, `stubbornness`, each ≤100 chars),
  `ignore_rate` (0–1: how often the pet ignores an interaction — the
  builtin cat is 0.22).
- `pronoun` — `he` / `she` / `it` (≤16 chars), used in room copy.
- `coats` — a non-empty map of coat id → `{body, belly, point}`, each a
  `#rrggbb` hex. These are the undyed-wool palettes; the first coat is the
  default. Every coat id wants a label in `voice.yaml` (lint warns).
- `geometry` — touch hitboxes in the 400×520 scene frame, same shape as
  figures.js `SPECIES_GEOMETRY`: `earBelow`, `headBelow` (y thresholds),
  `tail: {yAbove, xAbove}`, `belly: {yAbove, xAbove, xBelow}`. A touch with
  `y < earBelow` is an ear touch, `y < headBelow` the head, past the tail
  thresholds the tail, inside the belly rect the belly, else the body. Measure
  against your art; the pettable area is the `#dogzone` rect
  (x 128–272, y 270–458), so a zone threshold outside it is unreachable (lint
  warns; `pack_render`'s hitbox overlay is the eyeball check).

### `species/<id>.svg` — one `<g>` fragment on the wool rig class contract

The figure is injected into the room's SVG verbatim (after sanitization), and
the room animates it through **classes and one singleton id** — the same
contract the builtin cat art keeps (see the figures.js header). Required —
a missing one is a lint ERROR because poses visibly break:

- `.tailg` `.headg` `.earg` — the groups the wag/tilt/flick animations target.
- `#dog-eyes` — exactly once (weird name and all): the gaze binding and pose
  eye-transforms own it, and the playdate-visitor copy is made by stripping
  it. No other `id` attributes anywhere in the figure (lint warns — they can
  collide with the room's own ids).
- The five eye-state subgroups: `.eyes-open` `.eyes-happy` `.eyes-side`
  `.nap-eyes` `.one-eye-eyes`. In the happy/side-eye/sleep poses the room
  *hides* `.eyes-open` and shows the matching group — a missing subgroup
  renders your figure **eyeless** in that pose.
- `.coat` `.cream` `.point` on the shapes each palette slot paints — the
  room sets `--dog-body/--dog-belly/--dog-point` and those classes pick them
  up. A missing slot means that coat color paints nowhere. (Fill attributes
  carry one coat's hexes as the no-CSS fallback, like the builtin art.)

Nice-to-have (lint WARNs if absent): `.brushstreak` (grooming lines),
`.touchfibers` (petting fibers), `.paw-dream` (the sleep paw-twitch),
`.dog-contact` (the ground shadow).

Sanitization is allowlist-only and **strips silently** — lint's
`sanitizer drops` WARN names what would go, so check it when your figure loads
thinner than you drew it: elements survive only if they are
`svg g path circle ellipse rect line polyline polygon title desc` (anything
else, incl. `script`/`foreignObject`/`image`/`use`/`a`, is dropped **with its
subtree**); attributes lose every `on*`, every `href`, and any `style`
containing `url(`. Namespaces collapse to local names. The root must be a
single `<g>` (or `<svg>`).

### `phrases/<id>.yaml` — the species' voice, as an overlay

Optional; targets a species in this pack or a builtin (`cat`). Without
one your species speaks with the builtin voice (lint warns). Tables:

- `body` — ambient fallback lines, nested `arousal (low/med/high) → valence
  (grumpy/neutral/content) → [lines]`.
- `action` — per action (`call feed greet message pet play walk`), same
  nesting.
- `spot` — per pettable spot (`belly body ear head tail`), same nesting.
- `tiny` — utterances, nested `valence → [lines]` only.
- `language:` — top-level, default `"en"` (the v1 language axis).

**Fall-through is per cell and by design** for `body`/`action`/`spot`: any
cell you don't pin serves the base tables, so pin only the cells where your
species sounds like itself (lint reports the pinned/total count; it warns only
when you pin zero `body` cells, since those are the lines heard most).

**`tiny` is the exception: it swaps WHOLE, with no fall-through.** If your
overlay carries a `tiny` table it must pin all three valence buckets, and each
bucket needs at least one speakable (non-`*...*`) line — the message path
indexes `tiny[valence]` directly and filters asterisk body lines out of
utterances. An incomplete tiny table is a runtime crash, so lint ERRORs.
(Overlay present with no `tiny` at all errors the same way.)

All lowercase, body-first, ≤500 chars a line — the room's voice, not a
cartoon's. Read `packs/pebble/phrases/pebble.yaml` for the shape (the
pebble register is a joke; find your own).

### `quirks/<id>.yaml` — habits as data

```yaml
label: Kneader            # short label
description: Kneads the rug in slow presses when content and unbothered.
complexity: low           # low | medium | high
hooks: [on_sprite_render, on_scheduler_tick]   # optional, informational
behavior:                 # channels: pose | action | scheduler | events
  pose:                   #   rules list; every rule ANDs a `when` condition
    - when: {state_in: [sitting], valence_gte: 62, arousal_lt: 45}
      write: {body_lean: 2, head_shift_y: {min: -1}, tail_motion: still}
```

- Condition grammar (`when`, ANDed; `any:` nests ORs): `action_in`,
  `state_in`, `state_not_in`, `valence_gte`, `valence_lt`, `arousal_gte`,
  `arousal_lt`, `old_arousal_lt`, `old_valence_lt`, `enters_state`,
  `fact_day_gate`, `fact_exists`, `any` (the exact set lives in
  `app/engine/quirks.py` `CONDITION_EVALUATORS`; anything outside it is a
  loader error).
- `pose` rules `write` the rig keys of `base_pose_detail()` (`body_lean`,
  `head_shift_y`, `ear_angle`, `eye_style`, `tail_motion`, `focus_target`);
  a bare value sets, `{min: v}` / `{set_if_default: v}` merge.
- `action`/`scheduler` rules carry `text` (the line the pet says) plus
  optional `arousal_delta`/`valence_delta`/`priority`, `fact_updates`
  (`{today}` interpolates), `choices` (weighted pick lists), and `scene_fx`
  (`{mode, duration_ms}` — mode must come from the engine's fx vocabulary
  `FX_MODES` in `app/room_contract.py`: `greet`, `petting`, `zoomie`,
  `warm_spot`, …; an unknown mode is a loader error).
- `events` rules `emit: {type, data}` room events — `type` must come from
  the emit vocabulary `QUIRK_EMIT_TYPES` in `app/room_contract.py`
  (currently just `response`); an unknown type is a loader error, because
  emits ride the same socket as the protocol's own frames.

Quirks are **prose + thresholds over canned fx modes** — choreography itself
is not authorable in v1 (see limits below). Each quirk also wants
preview/mood copy in `voice.yaml` (lint warns).

### `voice.yaml` — client copy additions

Recursively merged into the room's `CLIENT_VOICE` (builtin tables survive).
The two coverage rules lint checks:

```yaml
coat_labels:          # one per coat id in species/<id>.yaml
  dune: dune
quirks:
  previews:           # the adopt-screen habit preview, one per quirk id
    burrower: disappears into the rug pile and re-emerges elsewhere
  moods:              # the one-word mood label, one per quirk id
    burrower: tunneler
```

Without a label the UI shows the raw id; without preview/mood copy the adopt
screens show the generic fallback lines. Both are WARNs, not errors.

## What `pack_lint` checks

`scripts/pack_lint.py <pack-dir> [--strict]` prints one line per check —
`PASS|WARN|ERROR <check> — <reason>` — and a summary. Exit 1 on any ERROR (and
on WARN under `--strict`), 0 otherwise. Lint never mutates: the pack is only
read, and the loader's registry mutations are restored before it exits.

| check | severity | what it catches |
|---|---|---|
| `load` | ERROR | any loader gate refusal (manifest/confinement/size/SVG/species/phrase/quirk/voice/vocab/collision) — runs first and stops the run |
| `manifest` | PASS | names author · license · fx vocab (all loader-required) |
| `rig structure [<id>]` | ERROR | missing `.tailg`/`.headg`/`.earg`; `#dog-eyes` absent or duplicated |
| `rig eye states [<id>]` | ERROR | missing eye-state subgroup → eyeless in that pose |
| `rig palette [<id>]` | ERROR | missing `.coat`/`.cream`/`.point` → a coat slot paints nowhere |
| `rig extras [<id>]` | WARN | missing `.brushstreak`/`.touchfibers`/`.paw-dream`/`.dog-contact` |
| `figure ids [<id>]` | WARN | any `id` other than `#dog-eyes` (collision risk with room ids) |
| `sanitizer drops [<id>]` | WARN | elements/attributes the sanitizer strips from your file |
| `geometry [<id>]` | WARN | empty zones, thresholds outside the 400×520 frame, zones unreachable from the `#dogzone` pettable rect |
| `phrase overlay [<id>]` | WARN | species has no overlay at all (it speaks with the builtin voice) |
| `overlay tiny [<id>]` | ERROR | tiny table missing/incomplete/no-speakable-line → KeyError on the message path (whole-table swap, no fall-through) |
| `overlay sparsity [<id>]` | PASS/WARN | pins N of M cells (fall-through by design); WARN only at zero body cells |
| `voice coats` | WARN | a coat id with no `coat_labels` entry |
| `voice quirks` | WARN | a quirk with no `previews`/`moods` copy |

## v1 limits — honest scope

- **Species/quirk/phrase/voice packs only. No room packs** — the room's
  furniture, lighting, and choreography are not authorable.
- **No custom choreography.** Quirks are prose + thresholds over the canned
  fx modes; a pack cannot mint a new animation, sound, or scene effect.
- **The phrase overlay falls through to the base tables**, which are the
  builtin cat's voice — a sparse overlay means a species that occasionally
  sounds like a cat. Pin the cells that matter (at minimum some `body` cells
  and the full `tiny` table).
- **Geometry is hand-measured** against your art. `pack_render`'s hitbox
  overlay is the source of truth for "does the ear zone sit on the ear"; lint
  only catches zones that cannot work.
- **Boot-time, local dirs only.** Packs load from `PACK_PATHS` at process
  start; there is no runtime install, no remote fetch, no hot reload.
- **One language axis (`language:`), default `en`** — only `en` content ships
  today; the field is the seam, not a promise.
