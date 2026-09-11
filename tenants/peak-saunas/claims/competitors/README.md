# claims/competitors/ — sourced comparison subjects

One JSON file per comparison subject the comparison cartridge
(`cartridges/comparison/`, phase 6 draft) may put in its spec table, next to
the tenant's own active products (which are always available as targets and
need no file here).

**Nothing here loads until it is approved.** A file without `"approved_by"`
set is pending and is skipped by `harness/ground.py` — no run can name a
competitor until Caleb approves the competitor claims (docs/KIMI-LONG-RUN.md
phase 6). The tenant's vocab bans (competitor names, forbidden topics) apply
to comparison pages exactly as to every other cartridge.

File shape:

```json
{
  "id": "sun-category-note",
  "name": "the typical alternative in the category",
  "kind": "competitor | category",
  "approved_by": null,
  "rows": [
    {
      "id": "cmp-<subject>-<dimension>",
      "label": "Spec dimension",
      "text": "The stated fact, exactly as sourced.",
      "source": "https://... (a public source URL, required)"
    }
  ]
}
```

Rules:

- Every row carries its own `source`; a row without one does not belong here.
- Rows are facts, never judgments: "Heats to 150°F" with a source, never
  "weak heaters".
- `kind: "category"` is the generic-category framing (the wireframe's safe
  form, e.g. "a traditional sauna"); `kind: "competitor"` names a specific
  alternative and needs Caleb's explicit approval before `approved_by` is
  set.
- Pending files stay in the repo as proposals — the same discipline as
  `claims/pending.json`.
