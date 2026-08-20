#!/usr/bin/env python3
"""CLI for the woolroom eval harness.

Usage:
    scripts/eval.py run [--corpus PATH] [--label LABEL]
        Run every case in the corpus through the prompt → LLM → validator
        path, persist results to eval_runs, print a summary.

    scripts/eval.py diff <session_a> <session_b>
        Show per-case verdict / latency / response delta between two
        sessions. Useful for "did this prompt edit make things worse?"

    scripts/eval.py sessions
        List the 10 most recent eval sessions.

Real LLM calls are billed; use --label to tag a session so you can find
it later (`scripts/eval.py sessions`). The runner uses the same prompt /
validator code paths as the production response pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure app/ is on the path when run as `python scripts/eval.py` from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CORPUS = REPO_ROOT / "tests" / "eval" / "corpus.yaml"


def _cmd_run(args: argparse.Namespace) -> int:
    from app.eval.corpus import load_corpus
    from app.eval.runner import run_session

    cases = load_corpus(args.corpus)
    print(f"Loaded {len(cases)} case(s) from {args.corpus}")

    summary, results = asyncio.run(run_session(cases, label=args.label))

    print(f"\nSession {summary.eval_session} ({summary.label or 'unlabeled'})")
    print(
        f"  cases={summary.n_cases}  accepted={summary.accepted}  rejected={summary.rejected}  "
        f"errors={summary.errors}  mean_latency={summary.mean_latency_ms:.0f}ms"
    )
    print()
    print(f"  {'case_id':32s} {'status':10s} {'verdict':10s} {'ms':>6s}")
    print(f"  {'-'*32} {'-'*10} {'-'*10} {'-'*6}")
    for r in results:
        print(
            f"  {r.case_id:32s} {r.status:10s} {r.validator_verdict:10s} {r.latency_ms:>6d}"
        )
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    from app.eval.runner import diff_sessions

    deltas = asyncio.run(diff_sessions(args.session_a, args.session_b))
    changed = [d for d in deltas if d.changed]

    print(f"\nDiff: {args.session_a}  →  {args.session_b}")
    print(f"  cases compared: {len(deltas)}    changed: {len(changed)}")
    print()
    print(
        f"  {'case_id':32s} {'verdict':24s} {'ms (a→b)':14s}  Δms"
    )
    print(f"  {'-'*32} {'-'*24} {'-'*14}  ----")
    for d in deltas:
        verdict_col = f"{d.before_verdict} → {d.after_verdict}"
        ms_col = f"{d.before_latency_ms} → {d.after_latency_ms}"
        delta_ms = d.after_latency_ms - d.before_latency_ms
        marker = "*" if d.changed else " "
        print(f"{marker} {d.case_id:32s} {verdict_col:24s} {ms_col:14s}  {delta_ms:+d}")

    # Per-case excerpt diffs for changed cases.
    if changed:
        print("\nResponse diffs (changed cases only):")
        for d in changed:
            print(f"\n  --- {d.case_id} ---")
            print(f"  before: {d.before_excerpt!r}")
            print(f"  after:  {d.after_excerpt!r}")
    return 0


def _cmd_sessions(args: argparse.Namespace) -> int:
    from sqlalchemy import select, func
    from app.storage.db import SessionLocal
    from app.storage.models import EvalRun

    async def _go():
        async with SessionLocal() as session:
            q = (
                select(
                    EvalRun.eval_session,
                    EvalRun.label,
                    func.count(EvalRun.id),
                    func.min(EvalRun.ts),
                    func.max(EvalRun.ts),
                )
                .group_by(EvalRun.eval_session, EvalRun.label)
                .order_by(func.max(EvalRun.ts).desc())
                .limit(10)
            )
            rows = (await session.execute(q)).all()
        return rows

    rows = asyncio.run(_go())
    print(f"\n{'session':22s} {'label':24s} {'cases':>5s}  first_ts")
    print(f"{'-'*22} {'-'*24} {'-'*5}  {'-'*20}")
    for sess, label, n, first_ts, _last_ts in rows:
        print(f"{sess:22s} {(label or ''):24s} {n:>5d}  {first_ts}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="woolroom eval harness CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the corpus through the response pipeline")
    p_run.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p_run.add_argument("--label", type=str, default=None)
    p_run.set_defaults(func=_cmd_run)

    p_diff = sub.add_parser("diff", help="Diff two sessions case-by-case")
    p_diff.add_argument("session_a")
    p_diff.add_argument("session_b")
    p_diff.set_defaults(func=_cmd_diff)

    p_sessions = sub.add_parser("sessions", help="List recent eval sessions")
    p_sessions.set_defaults(func=_cmd_sessions)

    args = parser.parse_args()

    # The CLI imports SQLAlchemy and the prompt module, both of which read
    # app.config — but the env var defaults are fine for offline reads only
    # if the user already has a working venv pointed at the local DB.
    if "DATABASE_URL" not in os.environ:
        os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./woolroom.db")
    if "SECRET_KEY" not in os.environ:
        os.environ.setdefault("SECRET_KEY", "eval-cli-no-cookies")
    if "ANTHROPIC_API_KEY" not in os.environ:
        os.environ.setdefault("ANTHROPIC_API_KEY", "")
    if "BASE_URL" not in os.environ:
        os.environ.setdefault("BASE_URL", "http://localhost:8000")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
