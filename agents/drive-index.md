---
name: drive-index
purpose: Walk a Drive media folder, classify each file, and write an asset index for the tenant's brand dir.
inputs:
  - Drive folder id
outputs:
  - asset_index.json
model: claude-sonnet-5
---

## What it does
Walks every file in a given Drive folder, classifies each one into a type
(lifestyle, interior, render, installation, video, logo, ugc), and writes
one asset_index.json entry per file recording its Drive id, filename, and
classification.

## Rules
- Every file in the folder gets exactly one classification; none are
  skipped silently. A file that doesn't fit cleanly is marked
  unclassified rather than guessed into the wrong bucket.
- Never downloads, edits, or moves the source files; this role only reads
  metadata/content enough to classify and writes the index.
- asset_index.json is written to the tenant's brand directory, not mixed
  into any other tenant's index.
- Re-running on the same folder overwrites stale entries rather than
  appending duplicates.

## Done when
asset_index.json exists, contains one entry per file in the Drive folder,
and every entry has a classification and a Drive id that resolves back to
the source file.
