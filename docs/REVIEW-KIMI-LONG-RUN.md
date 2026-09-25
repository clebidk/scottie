# Review — `kimi/long-run` (12 commits, 130 files, +17,665/-1,214)

Reviewed at `67b4fef` against `master` at `3eb4a2b`, on the prod server in a
throwaway worktree (`../advertorial-kimi`, its own venv, `models`/`vendor`
symlinked, tenant `.env` copied). Criteria are the rules in
`docs/KIMI-LONG-RUN.md`. Nothing was merged; nothing was pushed.

## Verdict

**MERGE WITH FIXES.** Two fixes are required before the merge commit; both are
small and neither touches the gate's logic. The engineering is unusually
disciplined: the `cli.py` split is a genuine move, not a rewrite, and generated
pages are byte-identical to `master`.

Required before merge:

- **K1** — remove the tenant product names from `cartridges/comparison/schema.json:30`.
- **K2** — make the daily spend cap unbypassable by concurrent runs, or document it as advisory in `docs/SPEC.md` and `harness --help`.

## Evidence

| Check | Command | Result |
|---|---|---|
| Tests (branch) | `.venv/bin/python -m pytest -q` | **817 passed** in 13.6 s |
| Tests (master) | same, in `~/advertorial` | 734 passed in 12.7 s (**+83, none removed**) |
| Lint | `.venv/bin/ruff check .` | **All checks passed** |
| Dry run, founder fixture | `python -m evals.fake_run .../founder-warranty-demo.txt` | 3 pages; `article`/`product-page`/`longform` **byte-identical** to `evals/baseline/` |
| Dry run, hidden-costs fixture | `python -m evals.fake_run .../hidden-costs-v2.transcript.txt` | 3 pages; all three **byte-identical** to `evals/baseline/` |
| Master-vs-branch parity | branch's `fake_run.py` copied into `~/advertorial`, run on master code, `page.json` diffed with the asset-id fixture rename normalised | **identical on all three cartridges** — no generated-page change |
| Real run | `harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas` | **PASS**, run `20260911-204652-hidden-costs-v2-ssd5`, state `needs_review`, 3 pages |
| — financing sentence | `grep -ohE "Financing is available[^<"]*"` | exactly one form: `Financing is available through Bread Pay at checkout.` |
| — byline | grep for the three roles | Austin Laudenslager ×5, Peak Saunas Editorial Team ×3, Caleb Niednagel ×4 — the Cycle 19 three-role byline |
| — banned terms | harness gate | 0 (gate passed; see F5 for the URL-path caveat, which pre-dates the branch) |
| — cost | `REVIEW.md` / run log | `estimated_cost_usd (estimate): $0.3125`; budget 157.2 s / 300 s, 31,326 / 220,000 tokens, 6 / 14 calls |
| — repairs | `REVIEW.md` gate table | longform 1 attempt, product-page 2 (1 failure then clean), article 1 (1 deterministic fix) |
| Spend ledger | `tenants/peak-saunas/runs/spend-ledger.jsonl` | written by the real run |

Only one real model run was spent, foreground, inside the 600 s timeout.

## Rules compliance

| Rule | Result | Evidence |
|---|---|---|
| Branch only, master/main untouched | **PASS** | reviewed from a detached worktree; no push |
| Never edit `claims/verified.json`, `vocab.yaml`, `authors.yaml` | **PASS** | `git diff --name-status master...` lists exactly three tenant paths, none of them these |
| Tenant files touched | **PASS with note** | `tenants/peak-saunas/brand/byline.html` (comment-only, see F1); two new `claims/competitors/README.md` (peak + `_template`) |
| Blocks are layout, not copy | **PASS** | `harness/blocks/gate.py:142` rejects literal copy words; `:145` rejects tenant words; `:134` no `<script>`; `:136` no inline handlers; `:139` every `<img>` sized; `:153` colours must be `var(--…)`; `:162` no fixed width over 390 px |
| No monthly financing figures | **PASS** | `grep -niE '\$[0-9,]+ ?/ ?mo\|per month\|monthly payment\|as low as' harness/ cartridges/` → only the existing `claims.py` detector and the product-page rubric forbidding it |
| No competitor names in output | **PASS** | grep for Sunlighten/Sun Home/Clearlight/Jacuzzi/Therasage/HigherDOSE/Plunge across `harness/ cartridges/ evals/` → zero hits. `claims/competitors/*` entries without `approved_by` never load (`ground.py`), comparison is opt-in only |
| Model-agnostic gates, no hard-coded model | **PASS** | no model id in any new module; `config.py`'s `DEFAULT_MODELS` is unchanged from master (the only `config.py` change is R24, import-time → call-time paths). No Kimi/Moonshot provider adapter was added at all — plan item unfulfilled, not a violation |
| No Shopify or Slack calls | **PASS** | `publishers/`, `shopify.py`, `notify.py` are not in the diff; the only URL literal in the new modules is `https://schema.org` as a JSON-LD `@context` string comparison (`pagechecks.py:146`) |
| Tests green, ruff clean | **PASS** | 817 / 0 |
| No tenant words in `harness/` | **PASS** | `grep -rliE 'peak\|sauna\|austin\|caleb\|bread pay\|fuji' harness/blocks/` → no hits; the R40 whole-word scan still passes |
| No tenant words in `cartridges/` | **FAIL** | `cartridges/comparison/schema.json:30` — see **K1** |
| No secrets logged | **PASS** | no `environ`/`getenv`/`api_key`/`.env` reference in `evals.py`, `repair.py`, `pagechecks.py`, `blocks/gate.py`, `review_md.py`; `dataset export` emits ad_brief, facts_pack summary, page.json, gate history and check results only |
| No test weakened or deleted | **PASS** | `+83` `def test_`, `-0`; only two assert lines removed, both in `tests/test_render.py`, replaced one-for-one by the same assertions against the real manifest asset id |

## Findings

### K1 — tenant product names in an engine-level cartridge schema — **BLOCKS MERGE**

`cartridges/comparison/schema.json:30`

> `"… e.g. 'the Fuji vs. the Mini' for {{ tenant.name }}'s own lineup."`

`Fuji` and `Mini` are Peak's product names, compiled into a tenant-neutral
cartridge and sent to the model as prompt text. This is the same class as R40,
and the new leak arrived with this branch. The R40 neutrality test scans
`harness/` only, which is why 817 tests pass over it.

**Required fix:** replace with a placeholder example (`'the <model> vs. the
<other model>'`), and extend the R40 scan in `tests/test_tenant.py` to cover
`cartridges/` — the rest of `cartridges/` already fails that scan (pre-existing
R40 debt), so land the scan extension as a separate commit with the known
offenders listed, not as a blocker on this merge.

### K2 — the daily spend cap is advisory, not enforced — **BLOCKS MERGE (fix or document)**

`harness/budget.py:89-143`, called at `harness/pipeline.py:182`, recorded at
`pipeline.py:498/522/536` and `cli.py:118`.

- **Where stored:** `tenants/<t>/runs/spend-ledger.jsonl`, one JSON line per run.
- **Per tenant:** yes — the path hangs off `tenant.runs_dir`.
- **Fails closed:** **no, three ways.**
  1. The cap is checked *once, before* the run (`pipeline.py:182`) and spend is
     recorded *after* it. Two runs started inside the same window both read
     `$0.00` and both proceed. There is no lock, no in-flight reservation and no
     re-check. A concurrent run bypasses the cap completely.
  2. `budget.py:111` swallows every ledger write failure (`OSError`,
     `TypeError`, `ValueError`) and only logs. A read-only or full `runs/` dir
     silently makes the cap infinite. The comment argues a finished run should
     not crash over bookkeeping — correct — but the failure must then be loud
     enough to stop the *next* run, not logged and forgotten.
  3. A single run can overshoot the cap without bound; only the per-run `Budget`
     limits it. This one is documented and acceptable.
- Unset means uncapped, which matches the stated design.

**Required fix (either):** write the ledger line at run *start* with the
per-run token-budget ceiling as a reservation and reconcile it to actuals at the
end (kills 1 and 2 together); **or** state plainly in `docs/SPEC.md`, the
`budget.py` docstring and `harness --help` that the cap is a best-effort serial
guard that a concurrent run defeats — so nobody treats it as the control that
protects a metered key.

### F1 — a new render-time structural gate hard-fails on tenant HTML, after the model spend — Medium, does not block

`harness/render.py:588-592` raises `ClaimsGateFailure` when
`find_html_validity_violations` / `find_rendered_json_ld_violations` /
`find_rendered_internal_link_violations` hit. That is the right place for a
backstop, but the inputs include tenant-authored HTML: it is what forced
`tenants/peak-saunas/brand/byline.html:16` to be reworded (`--` is invalid
inside an HTML comment). Consequence: any tenant whose `byline.html` or brand
partials carry a `--` now fails **every** run, at render, after all three
cartridges have been written and paid for.

The R23 config-disagreement warning was added to `harness doctor`
(`doctor.py`), but this check was not.

**Required fix:** run the three structural checks over the tenant's brand
partials in `harness doctor` so the failure is caught for free, before spend.
Non-blocking because the one affected tenant file is already fixed on the
branch.

### F2 — `README.md` and `docs/GENERATOR.md` are stale for this branch — Medium, does not block

The branch adds `harness dataset export` and `harness eval report`
(`cli.py`) and the `harness/blocks/` registry. `README.md` is **not in the
diff** at all, and `docs/GENERATOR.md` has zero mentions of blocks or the new
commands. `docs/ARCHITECTURE.md` (+15), `HARNESS-MAP.md` (+6) and `SPEC.md`
(+4) were updated; `KIMI-BASELINE.md` (311 lines) and `PROMPTING.md` (106) are
new and good.

**Required fix:** add the two commands to `README.md`'s command list and a
blocks section to `GENERATOR.md`. Doc-only; land it in the same PR or
immediately after.

### F3 — no FIXLOG entry — Low, does not block

`docs/FIXLOG.md` is untouched. The repo's convention is one cycle entry per
work batch with the failure symptom and the fix; twelve commits of engine work
leave no trace in it. Add a Cycle 27 entry at merge.

### F4 — new hard gate checks widen the STOP surface — Low, does not block

`repair.py:135-139` appends `find_image_allowlist_violations`,
`find_internal_link_violations` and `find_block_violations` to `problems`, so
they are hard failures feeding the repair loop, not warnings. This is what the
plan asked for and the repair bounds are unchanged (`MAX_REPAIR_ATTEMPTS = 2`,
`repair.py:34`, same as master). Evidence they do not misfire: the 200-run soak
(`evals/soak-report.json`) reports 200/200 exit 0, 520 pages, **0 repairs, 0
check failures**, and the real run passed. Watch the first week of real runs for
an image-allowlist STOP on a legitimate asset.

### F5 — the banned term appears inside URL paths — Low, **pre-existing, and improved by this branch**

A raw grep of the real run's rendered HTML finds the banned term 6/9/10 times
per page — every hit inside the Shopify product handle, in `href` and in
`assets/<asset-id>.png` image paths. Master's own last run shows the same
(3/5/3). The harness gate correctly reports 0, since these are not visible text.

The branch *found* this and handled it the right way:
`pagechecks.find_forbidden_term_urls` (`pagechecks.py:200`) collects every such
URL per run, logs it (`pipeline.py:294`) and lists it in `REVIEW.md`
(`review_md.py:126`) — visibility without breaking a storefront handle the
harness does not control. Credit where due. The remaining decision (rename the
handle) is Caleb's, not the harness's.

### F6 — `tests/test_render.py` fixture asset id is now the real product slug — Informational

The image-allowlist gate compares page asset ids against the run's actual
manifest, so the fixture had to use the real `asset-<slug>-1`. Correct change,
but it is why the banned term now appears in the test file. If the handle is
ever renamed, that constant moves with it.

## Code review of the large changes

- **`cli.py` split (R1/R2/R5).** `cli.py` 1,646 → 733 lines; `repair.py` 785,
  `review_md.py` 160. Verified as a **pure move**: of 585 distinct normalised
  lines in `repair.py`, only 29 are absent from master's `cli.py`, and every one
  of those is the new module docstring, the `block_slots=` parameter, the
  `walk_page` call from R11, or the three new `pagechecks` lines. Locked topics,
  claim-id coverage, first-person, forbidden terms, the fixed warranty and
  financing sentences, the deterministic pre-repair pass and the repair bounds
  are byte-for-byte the code that was reviewed on master. The import cycle is
  gone — `pipeline.py` imports `repair` at top level and nothing imports `cli`.
- **`pagechecks.py` (218 lines).** Clean split of page-level checks (gate, hard)
  from rendered-document checks (render, backstop). No network. JSON-LD types
  are a per-cartridge allowlist.
- **Blocks registry.** 20 blocks, `registry.json` in the shadcn registry-item
  shape, gate as listed above. `harness/blocks/` contains no tenant word.
- **Comparison cartridge.** Genuinely well guarded, and better than the plan
  asked for: opt-in only (it did not appear in the real run's three cartridges);
  every table cell requires a `claim_ids` entry or the gate rejects it
  (`schema.json:54`, `:69`); the "honest alternative strengths" section is
  **required**, with claim ids, so the page cannot be an attack ad
  (`cartridge.md:11`); vocab bans apply as copy *and* as a table row or column
  (`cartridge.md:19`); the warranty and financing sentences stay verbatim or
  absent (`cartridge.md:21`); competitor entries without `approved_by` never
  load. Only K1 mars it.
- **`evals.py` (350 lines).** `dataset export` re-runs the deterministic checks
  over stored artifacts so old runs export the same shape as new ones — a good
  call. No network, no secrets.
- **Config precedence (R23).** The contributor did **not** change precedence.
  `claims/config.json` still wins; `Tenant.config_disagreements()`
  (`tenant.py:262`) surfaces the conflict in the run log and `harness doctor`.
  That is the conservative, correct reading of "a precedence change is a
  behaviour change" — better than what the review proposed.
- **R24.** `REPO_DIR` constant → `repo_dir()` / `whisper_bin()` /
  `whisper_model()` call-time functions. Behaviour-preserving, testable.

## Merge order

One merge is fine — the branch is internally consistent, the suite is green at
its tip, and the phases build on each other (blocks → comparison; the `cli.py`
split → everything after). Splitting would mean re-testing five intermediate
trees for no gain. Apply K1 and K2 as two fix commits **on the contributor's
branch** (ask the contributor; do not push to it yourself), then merge once.

If a split is forced, the only clean seam is:

1. `f83805c..b9af67d` — bootstrap, baseline, checks, blocks, and the R1/R2/R11 refactors.
2. `9535a08..67b4fef` — R23/R20/R24, eval export, comparison cartridge, runbook, soak.

## Commands to merge (after K1 and K2 land)

```sh
ssh prod
cd ~/advertorial
git fetch github
git log --oneline master..github/kimi/long-run          # confirm the fix commits
git merge --no-ff github/kimi/long-run -m "Merge kimi/long-run: blocks registry, deterministic checks, cli.py split, eval export, comparison cartridge"
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
harness doctor --tenant peak-saunas
make mirror                                              # pushes master and main to github
```

Do not merge until `pytest -q` and `ruff check .` are green **on the merge
commit**, not only on the branch tip.

## Status (2026-09-25)
Both blockers are resolved on master: K1 -- no tenant product names in
cartridges/ (tests/test_tenant.py::test_no_tenant_specific_words_in_the_engine_or_the_cartridges
passes); K2 -- harness/budget.py reserves spend under an exclusive ledger lock
at run start and reconciles at the end, so concurrent runs cannot bypass the cap.
