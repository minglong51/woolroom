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

# Drop root: fix ownership of the writable locations (fly volumes mount
# root-owned; the default sqlite file lands in /app), then re-exec as the
# app user. Code files stay root-owned read-only. `chown /app` is the
# directory only, not -R — the user may create files there, not edit code.
if [ "$(id -u)" = "0" ]; then
  chown app /app
  if [ -d /data ]; then chown -R app /data; fi
  exec runuser -u app -- "$0" "$@"
fi

if [ -n "${BUCKET_NAME:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists /data/woolroom.db
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

# Idempotent for a known revision; unknown and unversioned databases fail closed.
python scripts/migrate.py

set -- uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips='*'

if [ -n "${BUCKET_NAME:-}" ]; then
  exec litestream replicate -exec "$*"
fi
exec "$@"
