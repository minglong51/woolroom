#!/usr/bin/env bash
# Pull the live woolroom SQLite DB off the Fly machine and store it locally.
#
# Why not `fly ssh sftp get`: the SSH tunnel requires a working WireGuard
# peer, which has been intermittent on this account (TLS handshake failures
# from time to time). `fly machine exec` goes through Fly's gateway API
# instead and works reliably, but the slim container has no `sqlite3`
# binary, so we use python to base64-encode the file inline.
#
# Usage:
#   scripts/backup.sh                  # one-shot pull to ~/.local/state/woolroom-admin/
#   scripts/backup.sh /path/to/dir     # one-shot pull to a custom dir
#
# Recommended: add to crontab for weekly/monthly archive beyond Fly's
# 5-day snapshot retention. See DEPLOYMENT.md for the runbook.

set -euo pipefail

APP="${WOOLROOM_FLY_APP:-woolroom}"
OUTDIR="${1:-$HOME/.local/state/woolroom-admin}"
mkdir -p "$OUTDIR"

# Get the running machine ID (first started machine in the app).
MACHINE_ID=$(fly machine list --app "$APP" --json \
  | python3 -c "import json,sys; ms=json.load(sys.stdin); print(next(m['id'] for m in ms if m['state']=='started'))")

if [ -z "$MACHINE_ID" ]; then
  echo "no started machine for app $APP" >&2
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
OUT="$OUTDIR/prod-woolroom.db.bak.$TS"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "[backup] pulling /data/woolroom.db from machine $MACHINE_ID via base64-over-exec..."
fly machine exec "$MACHINE_ID" \
  "python3 -c \"import base64, sys; sys.stdout.write(base64.b64encode(open('/data/woolroom.db','rb').read()).decode())\"" \
  --app "$APP" > "$TMP"

base64 -d -i "$TMP" -o "$OUT"

SIZE=$(wc -c < "$OUT" | tr -d ' ')
echo "[backup] wrote $OUT ($SIZE bytes)"

# Quick sanity check via Python (no sqlite3 cli on macOS either, sometimes).
python3 - <<PYEOF
import sqlite3, sys
c = sqlite3.connect("$OUT")
tables = sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"))
print(f"[backup] tables in pulled DB: {tables}")
c.execute("PRAGMA quick_check").fetchone()
print("[backup] integrity check ok")
PYEOF
