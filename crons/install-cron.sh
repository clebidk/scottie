#!/bin/bash
# Cycle 108: install (or remove) the hourly automatic pipeline,
# crons/meta-pull.sh, in the CURRENT user's crontab. Run it as the deploy
# user on prod, never as root:
#
#   crons/install-cron.sh [tenant]            add the line if it is not there
#   crons/install-cron.sh --remove [tenant]   take the tenant's line(s) out
#
# The line it adds (minute 7 of every hour; meta-pull.sh runs the venv's
# .venv/bin/harness and appends to tenants/<tenant>/runs/meta-pull.log):
#
#   7 * * * * <repo>/crons/meta-pull.sh <tenant>
#
# Idempotent: when the crontab already has an active (not commented)
# crons/meta-pull.sh line for the tenant, in any form (other minute, other
# path, a log redirect), nothing is changed. Otherwise the crontab is written
# back exactly as it was, plus the one line. To change the minute of an
# existing line, run --remove first.
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
# an active line that runs crons/meta-pull.sh for this tenant
PATTERN="^[[:space:]]*[^#[:space:]].*crons/meta-pull\.sh[[:space:]]+${TENANT}([[:space:]]|\$)"

if [ "$(id -u)" = "0" ]; then
  echo "run this as the deploy user, not root" >&2
  exit 1
fi
[ -x "$JOB" ] || { echo "$JOB is missing or not executable" >&2; exit 1; }

current="$(crontab -l 2>/dev/null || true)"
existing="$(printf '%s\n' "$current" | grep -E "$PATTERN" || true)"

if [ "$REMOVE" = "1" ]; then
  printf '%s\n' "$current" | { grep -v -E "$PATTERN" || true; } | sed '/^$/d' | crontab -
  echo "removed the meta-pull line(s) for $TENANT"
elif [ -n "$existing" ]; then
  echo "already installed, crontab not changed:"
  printf '%s\n' "$existing"
  if [ "$(printf '%s\n' "$existing" | wc -l)" -gt 1 ]; then
    echo "WARNING: more than one meta-pull line for $TENANT; run --remove, then install again" >&2
  fi
else
  { if [ -n "$current" ]; then printf '%s\n' "$current"; fi; printf '%s\n' "$LINE"; } | crontab -
  echo "installed: $LINE"
fi
echo "crontab now:"
crontab -l 2>/dev/null || true
