#!/bin/bash
# Daily snapshot of every tenant's non-git data (run outputs, logs, brand kit,
# claims, evals, config, env). Local retention 14 days. Restores are plain tar.
set -euo pipefail
REPO="${HARNESS_REPO:-$HOME/advertorial}"
DEST="${HARNESS_BACKUP_DIR:-$HOME/backups/advertorial}"
KEEP_DAYS="${HARNESS_BACKUP_KEEP_DAYS:-14}"
umask 077
mkdir -p "$DEST"
stamp=$(date -u +%Y%m%d-%H%M%S)
for t in "$REPO"/tenants/*/; do
  name=$(basename "$t"); [ "$name" = "_template" ] && continue
  tar -C "$REPO/tenants" -czf "$DEST/$name-$stamp.tar.gz" \
    --exclude="$name/runs/asset-cache" "$name" 2>/dev/null || { echo "backup failed: $name" >&2; exit 1; }
done
find "$DEST" -name "*.tar.gz" -mtime +"$KEEP_DAYS" -delete
echo "backup ok $stamp: $(ls "$DEST" | wc -l) archives, $(du -sh "$DEST" | cut -f1)"
