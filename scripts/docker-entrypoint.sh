#!/usr/bin/env sh
# woolroom container entrypoint. Two boot shapes, chosen by whether
# litestream replication is configured:
#
#   BUCKET_NAME set (fly.io after `fly storage create`): restore the SQLite
#   DB from the object-storage replica if the volume is empty, migrate, then
#   run uvicorn under `litestream replicate` (continuous WAL backup).
#
#   BUCKET_NAME unset (plain `docker run`): migrate and run uvicorn
#   directly. With no replica configured, litestream stays out of the way.
#
# Single worker either way — the WebSocket broadcaster lives in-process and
# won't fan out across workers.
set -eu

fail_config() {
  echo "woolroom: container configuration refused: $1" >&2
  exit 2
}

WOOLROOM_ASGI_APP="${WOOLROOM_ASGI_APP:-app.main:app}"
case "$WOOLROOM_ASGI_APP" in
  *[!A-Za-z0-9_.:]*|:*|*:|*:*:*)
    fail_config "WOOLROOM_ASGI_APP must be one module:attribute target"
    ;;
esac
case "$WOOLROOM_ASGI_APP" in
  *:*) ;;
  *) fail_config "WOOLROOM_ASGI_APP must be one module:attribute target" ;;
esac

if [ -n "${WOOLROOM_DB_PATH:-}" ]; then
  resolved_db_path="$WOOLROOM_DB_PATH"
elif [ -n "${DATABASE_URL:-}" ]; then
  case "$DATABASE_URL" in
    sqlite+aiosqlite:///*) resolved_db_path=${DATABASE_URL#sqlite+aiosqlite:///} ;;
    *) fail_config "DATABASE_URL must be a file-backed sqlite+aiosqlite URL" ;;
  esac
else
  resolved_db_path=/data/woolroom.db
fi

case "$resolved_db_path" in
  /*) ;;
  *) fail_config "WOOLROOM_DB_PATH must be absolute inside the container" ;;
esac
case "$resolved_db_path" in
  *\?*|*\#*) fail_config "the container SQLite path cannot contain URL query or fragment syntax" ;;
esac

expected_database_url="sqlite+aiosqlite:///$resolved_db_path"
if [ -n "${DATABASE_URL:-}" ] && [ "$DATABASE_URL" != "$expected_database_url" ]; then
  fail_config "DATABASE_URL and WOOLROOM_DB_PATH must identify the same SQLite file"
fi
WOOLROOM_DB_PATH="$resolved_db_path"
DATABASE_URL="$expected_database_url"
export DATABASE_URL WOOLROOM_ASGI_APP WOOLROOM_DB_PATH

# Drop root: fix ownership of the writable locations (fly volumes mount
# root-owned; the default sqlite file lands in /data), then re-exec as the
# app user. Code files stay root-owned read-only. `chown /app` is the
# directory only, not -R — the user may create files there, not edit code.
if [ "$(id -u)" = "0" ]; then
  chown app /app
  if [ -d /data ]; then chown -R app /data; fi
  exec runuser -u app -- "$0" "$@"
fi

# Config pre-flight: surface a settings refusal as a readable remedy instead
# of a raw validation traceback from the migration wrapper.
if ! python -c "import app.config"; then
  echo "" >&2
  echo "woolroom: configuration refused (validation error above)." >&2
  echo "Most common on fly.io: SECRET_KEY unset while ENV=prod. Fix with:" >&2
  echo "  fly secrets set SECRET_KEY=\"\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')\"" >&2
  exit 1
fi

if ! python -c '
import importlib
import os

module_name, attribute_path = os.environ["WOOLROOM_ASGI_APP"].split(":", 1)
target = importlib.import_module(module_name)
for part in attribute_path.split("."):
    target = getattr(target, part)
if not callable(target):
    raise TypeError("ASGI target is not callable")
'; then
  echo "woolroom: WOOLROOM_ASGI_APP could not be imported as a callable." >&2
  exit 1
fi

if [ -n "${BUCKET_NAME:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists "$WOOLROOM_DB_PATH"
fi

# Idempotent for a known revision; unknown and unversioned databases fail closed.
python scripts/migrate.py

set -- uvicorn "$WOOLROOM_ASGI_APP" --host 0.0.0.0 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips='*'

if [ -n "${BUCKET_NAME:-}" ]; then
  exec litestream replicate -exec "$*"
fi
exec "$@"
