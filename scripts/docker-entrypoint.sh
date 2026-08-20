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

if [ -n "${BUCKET_NAME:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists /data/woolroom.db
fi

# Idempotent: alembic upgrade head is a no-op on an already-current schema.
python scripts/migrate.py

set -- uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips='*'

if [ -n "${BUCKET_NAME:-}" ]; then
  exec litestream replicate -exec "$*"
fi
exec "$@"
