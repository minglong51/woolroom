#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import gzip
import io
import os
import stat
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path


class SdistNormalizationError(RuntimeError):
    pass


def normalize_sdist(path: Path, source_date_epoch: int) -> None:
    if source_date_epoch < 0 or source_date_epoch > 0xFFFFFFFF:
        raise SdistNormalizationError("source date epoch is outside the gzip timestamp range")
    if not path.is_file() or not path.name.endswith(".tar.gz"):
        raise SdistNormalizationError(f"not a source distribution: {path}")

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    names: set[str] = set()
    with tarfile.open(path, "r:gz") as source:
        for original in source.getmembers():
            if original.name in names:
                raise SdistNormalizationError(
                    f"source distribution has a duplicate member: {original.name}"
                )
            names.add(original.name)
            member = copy.copy(original)
            payload = None
            if member.isfile():
                extracted = source.extractfile(original)
                if extracted is None:
                    raise SdistNormalizationError(
                        f"source distribution member could not be read: {original.name}"
                    )
                payload = extracted.read()
                member.size = len(payload)
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = source_date_epoch
            member.pax_headers = {}
            entries.append((member, payload))

    entries.sort(key=lambda entry: entry[0].name)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.TemporaryDirectory(dir=path.parent) as temporary_directory:
        temporary_path = Path(temporary_directory) / path.name
        with temporary_path.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=source_date_epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as target:
                    for member, payload in entries:
                        target.addfile(
                            member,
                            io.BytesIO(payload) if payload is not None else None,
                        )
        os.chmod(temporary_path, original_mode)
        temporary_path.replace(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize source distributions for reproducible release retries."
    )
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("sdists", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        for path in args.sdists:
            normalize_sdist(path, args.source_date_epoch)
    except (OSError, SdistNormalizationError, tarfile.TarError) as exc:
        print(f"sdist normalization refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
