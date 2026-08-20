"""Suite-wide hermetic defaults — the tests need NO `.env` and NO env vars.

`app.config.Settings` reads process env (which beats `.env` values in
pydantic-settings' precedence), and most app modules bind settings at
import time, so the defaults are forced here — before any test module
imports `app.*`. Every value is a `setdefault`: a real operator env still
wins when someone deliberately runs the suite against their own setup.

The recipe mirrors the dogfood environment: the ollama provider selected
but pointed at a dead port (connection-refused in milliseconds, so the
respond path exercises its LLM circuit and falls back to the deterministic
phrasebook — tests that want "the LLM answered" monkeypatch
`app.runtime.client.complete`), the site gate open, a throwaway signing
key, and a scratch SQLite database in the system temp dir so no test can
accidentally read or write the repo's real `woolroom.db`. Tests that care
about a setting override it per-test with `monkeypatch.setenv` and a fresh
app reimport, exactly as before.
"""

from __future__ import annotations

import os
import tempfile

_SCRATCH_DB = os.path.join(tempfile.gettempdir(), "woolroom-pytest-scratch.db")

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("SECRET_KEY", "woolroom-test-secret-key-0123456789abcdef")
os.environ.setdefault("SITE_PASSWORD", "")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_SCRATCH_DB}")
os.environ.setdefault("LLM_PROVIDER", "ollama")
# Port 9 (discard) is refused instantly — no DNS, no timeout wait, no model.
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("OLLAMA_MODEL", "woolroom-test")
