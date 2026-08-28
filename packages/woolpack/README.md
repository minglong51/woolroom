# Woolpack

Woolpack is the standalone authoring toolkit for
[Woolroom](https://github.com/minglong51/woolroom) content packs. It scaffolds
version 1 packs, renders their coats, poses, and hitboxes to a static review
board, and validates the same fail-closed data contract Woolroom loads at boot.

Packs are local YAML and SVG data. Woolpack does not execute pack code or upload
pack contents.

## Quick start

With Python 3.11+ and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/), run Woolpack
without installing it permanently:

```sh
uvx woolpack new mole --author "Your Name" --license MIT
uvx woolpack render packs/mole -o mole-board.html
uvx woolpack lint packs/mole --strict
```

The scaffold starts from the bundled Pebble template and renames the species,
phrase, and quirk identifiers for you. Edit the generated YAML and SVG between
render and lint runs. Strict lint exits nonzero for either an error or a warning,
which is the bar intended for registry submissions.

The bundled Pebble template declares `CC0-1.0`. If `--license` is omitted, a
new pack inherits its source template's license, so choose the intended license
explicitly when scaffolding original work.

For repeated use, install the command with `uv tool install woolpack`. See the
[pack format reference](https://github.com/minglong51/woolroom/blob/main/docs/packs.md)
for the file contract, SVG rig hooks, phrase overlays, and Woolroom boot command.
The [pack index](https://github.com/minglong51/woolroom-packs) tracks compatible
pack repositories and defines its submission contract.

## Commands

- `woolpack new <species-id>` scaffolds a pack from the bundled template or a
  local pack supplied with `--from`.
- `woolpack render <pack-dir>` validates the pack and writes a self-contained
  HTML review board.
- `woolpack lint <pack-dir>` reports contract and authoring-quality findings;
  `--strict` treats warnings as failures.

Lint checks one pack against Woolpack's bundled format-v1 environment. Woolroom
revalidates all configured packs together at boot, so cross-pack identifier
collisions can still make an individually clean pack fail to load.

Woolpack requires Python 3.11 or newer. Its code is licensed under the MIT
License; the bundled Pebble template is `CC0-1.0`. Both license texts ship with
the distribution.
