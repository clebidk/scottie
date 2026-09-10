#!/usr/bin/env bash
set -euo pipefail

# Install harness systemd --user units for one tenant.
# Usage: install.sh <tenant> [repo-dir]
#
# Renders each .service/.timer template in this directory, substituting
# @@TENANT@@ and @@REPO@@, and writes the result into the user unit
# directory (systemd calls this %h/.config/systemd/user, i.e. ~/.config/
# systemd/user). Never uses sudo and never installs a system unit.
#
# This script only renders units and reloads the daemon. It never enables
# or starts a timer itself — it prints the enable command for you to run.

usage() {
  echo "Usage: $0 <tenant> [repo-dir]" >&2
  exit 1
}

[ "$#" -ge 1 ] || usage
TENANT="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${2:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

installed_timers=()

for template in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer; do
  [ -f "$template" ] || continue
  base="$(basename "$template")"
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

echo
echo "NOTE: no timer was enabled or started by this script. Nothing runs on"
echo "a schedule until you run one of the commands above yourself."
