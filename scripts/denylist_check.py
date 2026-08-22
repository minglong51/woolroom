#!/usr/bin/env python3
"""Publish gate: no private identifiers in the public tree.

woolroom is a personal project published for anyone to self-host. The
engine, its content, and its docs must carry no private names, places, or
network identifiers. This script is that gate as code: it scans every text
file in the tree and exits 1, printing `file:line` for every hit.

The patterns below ARE the denylist — this file is the one place those
strings may appear, and the scanner excludes itself.

Word boundaries are load-bearing: the name patterns use `\b...\b` so
"grooming", "coming", "humming" and friends do NOT fire. The bare word
"paws" is deliberately NOT denied — it is ordinary cat anatomy and appears
in legitimate prose all over the phrasebook; only the compound forms that
named the predecessor product (`paws.db`, `paws_session`, `window.paws`, …)
are patterns.

Run:  scripts/denylist_check.py [root]     (default: the repo root)
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (label, pattern). All name/place patterns are case-insensitive whole-word.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private person name", re.compile(r"\bming\b", re.IGNORECASE)),
    ("private pet name", re.compile(r"\bmochi\b", re.IGNORECASE)),
    ("private pet name", re.compile(r"\bbella\b", re.IGNORECASE)),
    ("private pet name", re.compile(r"\bbao\b", re.IGNORECASE)),
    ("private timezone", re.compile(r"los_angeles", re.IGNORECASE)),
    # Tailscale CGNAT range 100.64.0.0/10 — a tailnet IP is always private
    # infrastructure.
    (
        "tailscale IP",
        re.compile(r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"),
    ),
    ("private app name", re.compile(r"\bpaws[._-]?(?:db|data|session|ios|git)\b", re.IGNORECASE)),
    ("private app name", re.compile(r"\bwindow\.paws\b", re.IGNORECASE)),
]

# Sanctioned residue: matched strings the gate tolerates, per repo-relative
# path. Each entry needs a reason.
ALLOW: dict[str, frozenset[str]] = {
    # Internal Python identifier for the session-cookie dependency; the
    # external contract is the cookie alias (COOKIE_NAME), so the parameter
    # name is invisible outside this file. Rename is a deliberate decision,
    # not a drive-by.
    "app/api/deps.py": frozenset({"paws_session"}),
}

# Phrase leak guard. The four intent tables in app/data/body_language.py were
# seeded from the private predecessor and carried two of one household's real
# messages. Those are personal content rather than identifiers, so unlike
# PATTERNS above they must NOT be written here in plaintext — a denylist that
# spells out the phrase republishes it. They are pinned as salted digests of the
# classify_message-normalized form, and a hit prints the location only, never
# the line.
PHRASE_SALT = "woolroom-phrase-gate-v1"
PHRASE_DIGESTS: frozenset[str] = frozenset(
    {
        "0843ec353890c983d8d1271107f20208933128fb99ea843dd6de6231ddd3b143",
        "d0020181d0ea2124a8db31f53deb97887a42f87d1367b4a617f9afde5f4fc7d8",
    }
)
_QUOTED = re.compile(r"\"([^\"\n]{1,200})\"|'([^'\n]{1,200})'")


def _phrase_digest(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text).casefold().replace("\u2019", "'")[:200]
    norm = re.sub(r"[^\w']+", " ", norm).strip()
    return hashlib.sha256((PHRASE_SALT + norm).encode()).hexdigest()


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}
SELF = Path(__file__).resolve()


def _is_text(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return False
    return b"\x00" not in chunk


def scan(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.resolve() == SELF:
            continue  # the patterns live here; see the docstring
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if not _is_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        allowed = ALLOW.get(str(rel), frozenset())
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    if match.group(0).casefold() in {a.casefold() for a in allowed}:
                        continue
                    hits.append(f"{rel}:{lineno}: {label}: {line.strip()[:120]}")
            for quoted in _QUOTED.finditer(line):
                value = quoted.group(1) or quoted.group(2) or ""
                if _phrase_digest(value) in PHRASE_DIGESTS:
                    hits.append(f"{rel}:{lineno}: leaked private phrase (digest match)")
    return hits


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO
    hits = scan(root)
    if hits:
        print(f"denylist check FAILED — {len(hits)} private identifier(s) in the tree:")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("denylist check passed — no private identifiers in the tree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
