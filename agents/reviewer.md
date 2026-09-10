---
name: reviewer
purpose: Render a self-contained review file per cartridge for a human to approve.
inputs:
  - page.json
  - rendered index.html
  - cartridge assets
outputs:
  - <cartridge>-review.html
model: none (deterministic)
---

## What it does
For each cartridge, builds one self-contained HTML file with every image
inlined as a data URI, so it can be opened or forwarded without broken
links. No model call; this is a formatting/packaging step over content the
writer and render stage already produced.

## Rules
- Never changes page content, claims, or copy; it only packages what
  already exists for human eyes.
- All images must be inlined as data URIs; the file must not depend on any
  external or relative asset path once written.
- One review file per cartridge, named <cartridge>-review.html.
- Does not approve or reject anything itself; approval is a human action
  taken after reading the file.

## Done when
A <cartridge>-review.html file exists for every cartridge in the run, opens
standalone with no missing images, and matches the corresponding page.json
content exactly.
