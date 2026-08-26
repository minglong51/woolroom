"""Scaffold a species pack with every stem already renamed.

Copies the bundled Pebble template, or a pack supplied with ``--from``, to
``packs/<id>`` (or ``--dest``). Species and phrase assets are renamed to the
new id, quirk ids are namespaced to avoid collisions, matching voice keys are
rewritten, and the manifest name, author, and optional license are updated.
"""

from __future__ import annotations

import argparse
import shutil
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from woolpack.validation import QUIRK_ID_RE, SPECIES_ID_RE

DEFAULT_TEMPLATE = files("woolpack.resources").joinpath("pebble")


def _copy_traversable(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_traversable(child, target)
        elif child.is_file():
            with child.open("rb") as source_file, target.open("wb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)


def _matching_files(source: Traversable, directory: str, suffix: str) -> list[Traversable]:
    root = source.joinpath(directory)
    if not root.is_dir():
        return []
    return sorted(
        (child for child in root.iterdir() if child.is_file() and child.name.endswith(suffix)),
        key=lambda child: child.name,
    )


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


def main(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    parser.add_argument("species_id", help="new species id (lowercase, [a-z][a-z0-9_]{0,15})")
    parser.add_argument(
        "--from",
        dest="src",
        default=None,
        help="source pack directory (default: bundled Pebble template)",
    )
    parser.add_argument("--dest", default=None, help="destination dir (default packs/<id>)")
    parser.add_argument("--author", default=None, help="manifest author (default: the id)")
    parser.add_argument("--license", default=None, help="manifest license (default: keep source's)")
    args = parser.parse_args(argv)

    new_id = args.species_id
    if not SPECIES_ID_RE.fullmatch(new_id):
        print(f"error: species id must match {SPECIES_ID_RE.pattern!r}, got {new_id!r}")
        return 2

    source: Traversable = Path(args.src) if args.src else DEFAULT_TEMPLATE
    source_label = str(source) if args.src else "bundled Pebble template"
    if not source.joinpath("pack.yaml").is_file():
        print(f"error: {source_label} is not a pack (no pack.yaml)")
        return 2
    species_yamls = _matching_files(source, "species", ".yaml")
    if len(species_yamls) != 1:
        print(f"error: source pack must hold exactly one species, found {len(species_yamls)}")
        return 2
    old_id = Path(species_yamls[0].name).stem

    dest = Path(args.dest) if args.dest else Path("packs") / new_id
    if dest.exists():
        print(f"error: {dest} already exists; refusing to overwrite")
        return 2

    _copy_traversable(source, dest)
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

    print(f"created {dest} from {source_label} (species id {old_id!r} -> {new_id!r})")
    for rel in renamed:
        print(f"  renamed {rel}")
    print("next:")
    print(f"  $EDITOR {dest}/species/{new_id}.yaml   # temperament / coats / geometry")
    print(f"  $EDITOR {dest}/species/{new_id}.svg    # the figure")
    print(f"  woolpack render {dest}   # SEE it")
    print(f"  woolpack lint {dest}     # CHECK it")
    return 0


__all__ = ["DEFAULT_TEMPLATE", "main"]
