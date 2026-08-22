#!/usr/bin/env python3
"""pack new — scaffold a species pack with every stem already renamed.

Ids come from file stems, so a bare `cp -r packs/pebble packs/mole` leaves
the species id `pebble` inside — which collides with the example the moment
`PACK_PATHS` lists both. This does the copy AND the renames in one step:

    .venv/bin/python scripts/pack_new.py mole
    .venv/bin/python scripts/pack_new.py mole --author yourhandle --license MIT

Copies the source pack (default `packs/pebble`) to `packs/<id>` (or
`--dest`), renames `species/<old>.yaml`, `species/<old>.svg`, and
`phrases/<old>.yaml` to the new id, prefixes every quirk stem with the new
id (quirk ids are global across loaded packs — an unrenamed copy collides
with the example at boot), rewrites the matching `voice.yaml` keys, and
rewrites the manifest's `name`/`author`/`license` lines in place (comments
survive). Then the loop is yours: edit, `pack_render`, `pack_lint`, boot.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Ensure app/ is on the path when run as `python scripts/pack_new.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.packs.loader import QUIRK_ID_RE, SPECIES_ID_RE  # noqa: E402


def _rewrite_manifest(manifest: Path, values: dict[str, str]) -> None:
    """Replace `key: value` lines by key, preserving everything else."""
    lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line else None
        if key in values:
            out.append(f"{key}: {values[key]}\n")
        else:
            out.append(line)
    manifest.write_text("".join(out), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("species_id", help="new species id (lowercase, [a-z][a-z0-9_]{0,15})")
    parser.add_argument("--from", dest="src", default="packs/pebble", help="source pack dir")
    parser.add_argument("--dest", default=None, help="destination dir (default packs/<id>)")
    parser.add_argument("--author", default=None, help="manifest author (default: the id)")
    parser.add_argument("--license", default=None, help="manifest license (default: keep source's)")
    args = parser.parse_args()

    new_id = args.species_id
    if not SPECIES_ID_RE.fullmatch(new_id):
        print(f"error: species id must match {SPECIES_ID_RE.pattern!r}, got {new_id!r}")
        return 2

    src = Path(args.src)
    if not (src / "pack.yaml").is_file():
        print(f"error: {src} is not a pack (no pack.yaml)")
        return 2
    species_yamls = sorted((src / "species").glob("*.yaml"))
    if len(species_yamls) != 1:
        print(f"error: source pack must hold exactly one species, found {len(species_yamls)}")
        return 2
    old_id = species_yamls[0].stem

    dest = Path(args.dest) if args.dest else Path("packs") / new_id
    if dest.exists():
        print(f"error: {dest} already exists; refusing to overwrite")
        return 2

    shutil.copytree(src, dest)
    renamed = []
    for rel in (f"species/{old_id}.yaml", f"species/{old_id}.svg", f"phrases/{old_id}.yaml"):
        old_path = dest / rel
        if old_path.is_file():
            new_path = old_path.with_stem(new_id)
            old_path.rename(new_path)
            renamed.append(str(new_path.relative_to(dest)))

    # Quirk ids are global across every loaded pack, so the copies must not
    # keep the source's ids; namespace them under the new species id and
    # rewrite the voice.yaml keys that reference them.
    quirk_renames: dict[str, str] = {}
    for quirk_path in sorted((dest / "quirks").glob("*.yaml")) if (dest / "quirks").is_dir() else []:
        new_quirk = f"{new_id}_{quirk_path.stem}"[:32]
        if not QUIRK_ID_RE.fullmatch(new_quirk):
            print(f"error: renamed quirk id {new_quirk!r} is invalid; rename it by hand")
            return 2
        quirk_path.rename(quirk_path.with_stem(new_quirk))
        quirk_renames[quirk_path.stem] = new_quirk
        renamed.append(f"quirks/{new_quirk}.yaml")
    voice = dest / "voice.yaml"
    if quirk_renames and voice.is_file():
        lines = voice.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            for old, new in quirk_renames.items():
                if stripped.startswith(f"{old}:"):
                    lines[i] = line.replace(f"{old}:", f"{new}:", 1)
        voice.write_text("".join(lines), encoding="utf-8")

    _rewrite_manifest(
        dest / "pack.yaml",
        {
            "name": new_id,
            "author": args.author or new_id,
            **({"license": args.license} if args.license else {}),
        },
    )

    print(f"created {dest} from {src} (species id {old_id!r} -> {new_id!r})")
    for rel in renamed:
        print(f"  renamed {rel}")
    print("next:")
    print(f"  $EDITOR {dest}/species/{new_id}.yaml   # temperament / coats / geometry")
    print(f"  $EDITOR {dest}/species/{new_id}.svg    # the figure")
    print(f"  .venv/bin/python scripts/pack_render.py {dest}   # SEE it")
    print(f"  .venv/bin/python scripts/pack_lint.py {dest}     # CHECK it")
    print(f"  PACK_PATHS={dest} .venv/bin/uvicorn app.main:app # LIVE with it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
