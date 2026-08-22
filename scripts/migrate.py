"""Container startup helper.

Runs `alembic upgrade head` against the configured database. Alembic is
the only thing that touches schema in a deployed environment.

Idempotent: safe to run on every container start.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    subprocess.check_call(["alembic", "upgrade", "head"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
