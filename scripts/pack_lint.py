#!/usr/bin/env python3
"""pack lint — the authoring-side contract checker for a content pack.

Runs the pack through the REAL loader (`app.packs.load_pack` — every gate;
a refused pack is the first ERROR and stops the run), then the
authoring-quality checks of `app/packs/lint.py`: the wool-rig class
contract on the sanitized figure, what the sanitizer would drop, hitbox
geometry sanity against the room's pettable rect, phrase-overlay shape
(per-cell fall-through is designed; the tiny table swaps whole and is an
ERROR when incomplete), voice coverage, manifest polish.

    .venv/bin/python scripts/pack_lint.py packs/purl [--strict]

One line per check — `PASS|WARN|ERROR <check> — <reason>` — then a
summary. Exit 1 on any ERROR (and on WARN with `--strict`, the CI mode),
0 otherwise. Lint never mutates: the pack dir is only read, and the
loader's registry mutations are restored before it exits. The human
eyeball half of the loop is `scripts/pack_render.py`; the format
reference and authoring loop are `docs/packs.md`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure app/ is on the path when run as `python scripts/pack_lint.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.packs.lint import ERROR, PASS, WARN, lint_pack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="lint a content pack (pack format v1) — the loader gates plus the authoring contract"
    )
    parser.add_argument("pack", type=Path, help="pack directory (e.g. packs/purl)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on WARN as well as ERROR (registry-CI mode)",
    )
    args = parser.parse_args(argv)

    report = lint_pack(args.pack.expanduser())
    for finding in report.findings:
        print(f"{finding.severity:<5} {finding.check} — {finding.message}")

    counts = {sev: report.count(sev) for sev in (PASS, WARN, ERROR)}
    name = f"{report.name} v{report.version}" if report.name else str(args.pack)
    if counts[ERROR]:
        verdict = "FAILS lint"
    elif counts[WARN]:
        verdict = "lints, with warnings"
    else:
        verdict = "lints clean"
    print(f"{name}: {counts[PASS]} PASS · {counts[WARN]} WARN · {counts[ERROR]} ERROR — {verdict}")
    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
