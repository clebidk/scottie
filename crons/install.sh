#!/usr/bin/env bash
set -euo pipefail

# Install harness systemd --user units for one tenant.
# Usage: install.sh [--with-review] <tenant> [repo-dir]
#
# Renders each .service/.timer template in this directory, substituting
# @@TENANT@@ and @@REPO@@, and writes the result into the user unit
# directory (systemd calls this %h/.config/systemd/user, i.e. ~/.config/
# systemd/user). Never uses sudo and never installs a system unit.
#
# This script only renders units and reloads the daemon. It never enables
# or starts a TIMER itself — it prints the enable command for you to run.
#
# Cycle 26: harness-review.service (the reviewer web app, `harness serve`) is
# different from every other unit here -- it's a long-running server with no
# matching .timer, not a scheduled oneshot job -- so it is only rendered, and
# only enabled+started, when --with-review is given. Left out of the
# generic *.service/*.timer loop below for exactly that reason.

usage() {
  echo "Usage: $0 [--with-review] <tenant> [repo-dir]" >&2
  exit 1
}

WITH_REVIEW=0
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--with-review" ]; then
    WITH_REVIEW=1
  else
    ARGS+=("$arg")
  fi
done
[ "${#ARGS[@]}" -ge 1 ] || usage
TENANT="${ARGS[0]}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${ARGS[1]:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

installed_timers=()

for template in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer; do
  [ -f "$template" ] || continue
  base="$(basename "$template")"
  [ "$base" = "harness-review.service" ] && continue
  name="${base%.*}"
  ext="${base##*.}"
  unit_name="harness-${name}@${TENANT}.${ext}"
  dest="${UNIT_DIR}/${unit_name}"

  sed -e "s|@@TENANT@@|${TENANT}|g" -e "s|@@REPO@@|${REPO}|g" "$template" > "$dest"
  echo "Installed ${dest}"

  if [ "$ext" = "timer" ]; then
    installed_timers+=("$unit_name")
  fi
done

systemctl --user daemon-reload
echo "Ran: systemctl --user daemon-reload"
echo

echo "To enable a schedule, run:"
for unit in "${installed_timers[@]}"; do
  echo "  systemctl --user enable --now ${unit}"
done

if [ -f "${UNIT_DIR}/harness-worker@${TENANT}.service" ]; then
  echo
  echo "Cycle 69: the listicle site's job worker (docs/LISTICLE-SITE.md) is a"
  echo "long-running service, not a timer. To start it:"
  echo "  systemctl --user enable --now harness-worker@${TENANT}.service"
fi

echo
echo "NOTE: no timer was enabled or started by this script. Nothing runs on"
echo "a schedule until you run one of the commands above yourself."

if [ "$WITH_REVIEW" = "1" ]; then
  review_template="${SCRIPT_DIR}/harness-review.service"
  review_unit="harness-review@${TENANT}.service"
  review_dest="${UNIT_DIR}/${review_unit}"
  sed -e "s|@@TENANT@@|${TENANT}|g" -e "s|@@REPO@@|${REPO}|g" "$review_template" > "$review_dest"
  echo
  echo "Installed ${review_dest}"
  if systemctl --user status >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now "$review_unit"
    echo "Ran: systemctl --user enable --now ${review_unit}"
  else
    echo "systemctl --user is not available on this host (no user session/lingering)."
    echo "Start it directly instead, still bound to 127.0.0.1:"
    echo "  nohup ${REPO}/.venv/bin/harness serve --tenant ${TENANT} --port 4870 >>${REPO}/tenants/${TENANT}/runs/review-server.log 2>&1 &"
  fi
  echo "The reviewer web app binds 127.0.0.1:4870 only -- see docs/REVIEW-SITE.md"
  echo "for exposing it (a Caddy vhost or a Cloudflare Tunnel ingress) and for the"
  echo "interim 'ssh -L 4870:127.0.0.1:4870' access path."
fi
