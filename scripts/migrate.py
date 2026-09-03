"""Container startup helper.

Runs the fail-closed Woolroom database upgrade against the configured
database. Alembic remains the only thing that touches schema in a deployed
environment.

Idempotent: safe to run on every container start.
"""

from __future__ import annotations

import sys

from app.config import settings
from woolroom import DatabaseBoundaryError, upgrade_database


def main() -> int:
    try:
        upgrade_database(settings.database_url)
    except DatabaseBoundaryError as exc:
        print(f"woolroom migration refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
