"""Offline eval harness for the woolroom LLM response pipeline.

The point: when you edit a system prompt or validator, you want to ask
"did this change make things worse?" as a query rather than a vibe.
This module lets you run a fixed corpus of (pet state, action) cases
through the same prompt → LLM → validator path that prod uses, record
every result to `eval_runs`, and diff one session against another.

Key surfaces:

- `corpus.load_corpus(path)` — read a YAML corpus file.
- `runner.run_session(...)` — run every case, persist to `eval_runs`,
  return the session id and a summary.
- `runner.diff_sessions(a, b)` — show per-case verdict / latency /
  response deltas between two sessions.
"""
