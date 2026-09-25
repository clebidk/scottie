#!/bin/bash
# Daily snapshot of every tenant's non-git data (run outputs, logs, brand kit,
# claims, evals, config, env, A/B/C tests, jobs, Meta inbox). Local retention
# 14 days. Restores are plain tar.
# SQLite databases (abtests/events.sqlite, jobs/jobs.sqlite) are copied with
# sqlite3 .backup, not read as live files, so a write in progress at backup
# time cannot leave a torn copy in the archive.
set -euo pipefail
REPO="${HARNESS_REPO:-$HOME/advertorial}"
DEST="${HARNESS_BACKUP_DIR:-$HOME/backups/advertorial}"
KEEP_DAYS="${HARNESS_BACKUP_KEEP_DAYS:-14}"
umask 077
mkdir -p "$DEST"
stamp=$(date -u +%Y%m%d-%H%M%S)
# work dir on disk next to the archives, not in /tmp (tmpfs = RAM on this box)
work=$(mktemp -d "$DEST/.work.XXXXXX"); trap 'rm -rf "$work"' EXIT
for t in "$REPO"/tenants/*/; do
  name=$(basename "$t"); [ "$name" = "_template" ] && continue
  tar -C "$REPO/tenants" -cf "$work/$name.tar" \
    --exclude="$name/runs/asset-cache" \
    --exclude="*.sqlite" --exclude="*.sqlite-wal" --exclude="*.sqlite-shm" \
    "$name" 2>/dev/null || { echo "backup failed: $name" >&2; exit 1; }
  while IFS= read -r db; do
    rel="${db#"$REPO/tenants/"}"
    mkdir -p "$work/snap/$(dirname "$rel")"
    sqlite3 "$db" ".backup '$work/snap/$rel'" || { echo "backup failed: $rel" >&2; exit 1; }
  done < <(find "$REPO/tenants/$name" -name "*.sqlite" -not -path "*/runs/asset-cache/*")
  if [ -d "$work/snap/$name" ]; then
    tar -C "$work/snap" -rf "$work/$name.tar" "$name"
  fi
  gzip -c "$work/$name.tar" > "$DEST/$name-$stamp.tar.gz"
done
find "$DEST" -name "*.tar.gz" -mtime +"$KEEP_DAYS" -delete
echo "backup ok $stamp: $(ls "$DEST" | wc -l) archives, $(du -sh "$DEST" | cut -f1)"
