#!/bin/bash
# Cycle 68: pull new ads from the tenant's Meta ad account into
# tenants/<tenant>/meta_inbox/ (read only; see docs/META-INGEST.md).
# Appends to tenants/<tenant>/runs/meta-pull.log. Not installed anywhere --
# add the crontab line from docs/META-INGEST.md when the token is in place.
# A second copy that starts while one is still running exits at once.
set -euo pipefail
REPO="${HARNESS_REPO:-$HOME/advertorial}"
TENANT="${1:-${HARNESS_TENANT:-peak-saunas}}"
LOG_DIR="$REPO/tenants/$TENANT/runs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/meta-pull.log"
exec 9>"$LOG_DIR/.meta-pull.lock"
flock -n 9 || { echo "$(date -u +%FT%TZ) meta pull already running; skipped" >> "$LOG"; exit 0; }
cd "$REPO"
{
  echo "=== $(date -u +%FT%TZ) harness meta pull --tenant $TENANT"
  status=0
  "$REPO/.venv/bin/harness" meta pull --tenant "$TENANT" || status=$?
  echo "=== exit $status"
} >> "$LOG" 2>&1
exit "${status:-0}"
