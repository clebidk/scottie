#!/bin/bash
# Cycle 108: install (or remove) the hourly automatic pipeline,
# crons/meta-pull.sh, in the CURRENT user's crontab. Run it as the deploy
# user on prod, never as root:
#
#   crons/install-cron.sh [tenant]            add or replace the line
#   crons/install-cron.sh --remove [tenant]   take the line out
#
# The line it writes (minute 7 of every hour; meta-pull.sh runs the venv's
# .venv/bin/harness and appends to tenants/<tenant>/runs/meta-pull.log):
#
#   7 * * * * <repo>/crons/meta-pull.sh <tenant>
#
# Any other meta-pull.sh line for the same tenant (for example the older ":15"
# line from docs/META-INGEST.md) is replaced, so the job never runs twice an
# hour. Every other crontab line is kept as it is.
set -euo pipefail

REMOVE=0
if [ "${1:-}" = "--remove" ]; then
  REMOVE=1
  shift
fi
TENANT="${1:-peak-saunas}"
REPO="${HARNESS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
JOB="$REPO/crons/meta-pull.sh"
LINE="7 * * * * $JOB $TENANT"

if [ "$(id -u)" = "0" ]; then
  echo "run this as the deploy user, not root" >&2
  exit 1
fi
[ -x "$JOB" ] || { echo "$JOB is missing or not executable" >&2; exit 1; }

current="$(crontab -l 2>/dev/null || true)"
kept="$(printf '%s\n' "$current" | grep -v -E "crons/meta-pull\.sh $TENANT\$" | sed '/^$/d' || true)"
if [ "$REMOVE" = "1" ]; then
  printf '%s\n' "$kept" | sed '/^$/d' | crontab -
  echo "removed the meta-pull line for $TENANT; crontab now:"
else
  { [ -n "$kept" ] && printf '%s\n' "$kept"; printf '%s\n' "$LINE"; } | crontab -
  echo "installed: $LINE"
  echo "crontab now:"
fi
crontab -l 2>/dev/null || true
