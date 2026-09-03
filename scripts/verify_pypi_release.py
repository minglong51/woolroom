#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ReleaseVerificationError(RuntimeError):
    pass


class ReleaseIncompleteError(ReleaseVerificationError):
    pass


def artifact_digests(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        raise ReleaseVerificationError(f"artifact directory does not exist: {directory}")
    artifacts = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and (
            path.name.endswith(".whl")
            or path.name.endswith(".tar.gz")
            or path.name.endswith(".zip")
        )
    )
    if not artifacts:
        raise ReleaseVerificationError(f"artifact directory is empty: {directory}")
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in artifacts
    }


def pypi_release_digests(payload: Mapping[str, Any]) -> dict[str, str]:
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise ReleaseVerificationError("PyPI response has no release file list")
    result: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, Mapping):
            raise ReleaseVerificationError("PyPI response contains an invalid release file")
        filename = item.get("filename")
        digests = item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, Mapping) else None
        if (
            not isinstance(filename, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.casefold())
        ):
            raise ReleaseVerificationError("PyPI response contains invalid artifact metadata")
        normalized_digest = digest.casefold()
        previous = result.setdefault(filename, normalized_digest)
        if previous != normalized_digest:
            raise ReleaseVerificationError(
                f"PyPI returned conflicting digests for artifact {filename!r}"
            )
    return result


def fetch_release_digests(package: str, version: str) -> dict[str, str]:
    url = (
        "https://pypi.org/pypi/"
        f"{quote(package, safe='')}/{quote(version, safe='')}/json"
    )
    request = Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "woolroom-release-verifier/1",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise ReleaseVerificationError(f"PyPI query failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError("PyPI release state could not be read") from exc
    if not isinstance(payload, Mapping):
        raise ReleaseVerificationError("PyPI response is not a JSON object")
    return pypi_release_digests(payload)


def verify_release_state(
    local: Mapping[str, str],
    remote: Mapping[str, str],
    *,
    require_complete: bool,
) -> tuple[str, ...]:
    unexpected = sorted(set(remote) - set(local))
    if unexpected:
        raise ReleaseVerificationError(
            "PyPI has unexpected artifacts for this version: " + ", ".join(unexpected)
        )
    conflicts = sorted(
        filename
        for filename in set(local) & set(remote)
        if local[filename].casefold() != remote[filename].casefold()
    )
    if conflicts:
        raise ReleaseVerificationError(
            "PyPI artifact hashes conflict with this build: " + ", ".join(conflicts)
        )
    missing = tuple(sorted(set(local) - set(remote)))
    if require_complete and missing:
        raise ReleaseIncompleteError(
            "PyPI release is still missing artifacts: " + ", ".join(missing)
        )
    return missing


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that a PyPI release is an exact, hash-matching subset or copy of dist/."
    )
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        local = artifact_digests(args.dist)
    except ReleaseVerificationError as exc:
        print(f"release verification refused: {exc}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + max(0.0, args.wait_seconds)
    while True:
        try:
            remote = fetch_release_digests(args.package, args.version)
            missing = verify_release_state(
                local,
                remote,
                require_complete=args.require_complete,
            )
        except ReleaseIncompleteError as exc:
            if time.monotonic() >= deadline:
                print(f"release verification refused: {exc}", file=sys.stderr)
                return 2
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
            continue
        except ReleaseVerificationError as exc:
            print(f"release verification refused: {exc}", file=sys.stderr)
            return 2
        print(
            f"verified {args.package} {args.version}: "
            f"{len(remote)} matching on PyPI, {len(missing)} left to publish"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
