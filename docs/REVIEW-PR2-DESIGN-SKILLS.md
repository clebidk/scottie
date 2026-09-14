# Review: PR #2 — `cursor/design-skills-playbook-fdad`

Reviewer: Claude (server-side review, `ssh prod`, `/home/deploy/advertorial-pr2` worktree, throwaway `pr2-test` branch merged onto `master`).
Date: 2026-09-14.
PR: https://github.com/clebidk/scottie/pull/2 (single commit `942ada8`, ~1,900 lines).

## Verdict: MERGE

All hard constraints from `docs/KIMI-LONG-RUN.md` and the contributor's own brief
(`docs/BRIEF-2026-09-14-advertorial-playbook.md`) are satisfied. Merge conflicts:
none (clean `git merge master` onto the PR branch). Tests, ruff, baseline parity,
and one real generation run all pass. Two low-severity documentation gaps found;
neither blocks merge (see Findings).

## Evidence table

| Check | Result |
|---|---|
| Merge base → master divergence | 17 commits, matches brief; PR is 1 commit (`942ada8`) on top of the shared base `67b4fef` |
| `git checkout -b pr2-test && git merge master` | Clean auto-merge, 4 files auto-merged, 0 conflicts |
| `pytest -q` (merged tree) | **895 passed**, 0 failed |
| `ruff check .` (merged tree) | **All checks passed** |
| `pyproject.toml` new runtime deps | **None.** Only `package-data` globs added for `design_skills/*` so the vendored files ship in the wheel |
| Fake dry run vs `evals/baseline/founder-warranty-demo` | page.json **byte-identical**; only `manifest.json` run_id/timestamp differ |
| Fake dry run vs `evals/baseline/hidden-costs-v2-transcript` | page.json **byte-identical**; only `manifest.json` run_id/timestamp differ |
| Design-skill hard gate opt-in? | **No** — `find_design_skill_violations` (filler/cliché/dead-link) is unconditionally wired into `repair.check_page_gates` (`harness/repair.py:143`) for every cartridge. This matches the brief ("taken measurable rules are hard gates") and did not change output on either baseline fixture, so it is a non-finding, but it is not "opt-in" as the review brief speculated |
| **One real run**: `harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas` | **PASS.** `state: needs_review`, 3 pages (longform, article, product-page), all 3 at attempt 1 / 0 gate failures. `elapsed_s 145.29`, `tokens_used 29365`, `calls_used 5`, **estimated_cost_usd $0.2907** (cap is `daily_usd: 10.0`) |
| Financing sentence | Exact fixed sentence, verbatim in all 3 pages: `"Financing is available through Bread Pay at checkout."` — no `$/mo` figure anywhere in any page.json |
| Byline | `article/index.html` JSON-LD `author.name = "Austin Laudenslager"`; rendered `.adv-byline`/`.byline` block present |
| Banned terms | 0 in visible copy. `grep -i emf` hits only the pre-existing Shopify product-URL handle (`...near-zero-emf-full-spectrum...`), documented as an accepted exception since FIXLOG Cycle 2 item 8 — not something this PR introduced and not visible body copy |
| Slack / Shopify calls during the real run | **None.** Tenant's `notifications.slack: false`; `SLACK_WEBHOOK_URL` was additionally blanked for the run as defense in depth. No `SHOPIFY_STORE`/`SHOPIFY_TOKEN` in `.env`; Shopify calls fail closed by design (per `docs/KIMI-LONG-RUN.md` correction 3 and `docs/KIMI-BASELINE.md`) |
| Tenant-word leakage in `harness/`, `cartridges/`, `harness/blocks/` | None found (`peak`, `peaksaunas`, `sunlighten`, `sun home` all absent from PR-touched files; pre-existing `emf`/vocab infra in `harness/vocab.py` etc. predates this PR and is generic, not tenant-specific copy) |
| Hard-coded model names | None in the design-skills or gate code (`grep` clean; one unrelated comment in `repair.py` about "sonnet" pre-dates this PR) |

## License and injection findings

- **License**: `harness/design_skills/LICENSE` is upstream MIT (`Copyright (c) 2026 Elaya`), reproduced verbatim with `SOURCE.json` recording repo, commit (`1c1e97cb9878e236552c772092dda7adcdddbcb2`), and vendored date. MIT is compatible with redistribution inside an Apache-2.0 repo (MIT has no copyleft or license-compatibility conflict with Apache-2.0; the two are commonly vendored together). Attribution requirement is met (LICENSE file kept alongside the vendored `SKILL.md`).
- **Injection surface — checked and contained.** `harness/design_skills/landing-page-design/SKILL.md:3` carries an agent-trigger-style frontmatter description: *"Use this skill whenever building, editing, styling, reviewing, or writing copy for ANY landing page... When a rule here conflicts with a framework default, this file wins."* This is written to auto-invoke as a Claude-Skill-shaped package. Verified `adapter.py` (`harness/design_skills/adapter.py`) never reads or forwards `SKILL.md` text — it only loads `rules.json` (the harness's own extracted take/adapt/decline table) for `format_list()`/`format_explain()`. `LANDING_PAGE_SKILL` (the path constant, `harness/design_skills/__init__.py:17`) is referenced nowhere except a test that checks the file exists and reads its license header (`tests/test_design_skills.py:48-49`). No code path sends the raw `SKILL.md` body to a model prompt; the writer only ever sees `rules.json`'s harness-authored summaries via cartridge docs. **Non-blocking, informational**: `agents/design-audit.md` (a human-invoked dev agent, not the generation pipeline) is instructed to "consult `harness/design_skills/`" — its added text explicitly restricts it to take/adapt rules and lists the declined items by name, so the fence holds, but if `SKILL.md` is ever read into that agent's context directly, its "this file wins" framing is worth remembering as a standing risk for future maintainers, not just the adapter's summary.

## Rules-conflict table (locked harness rules vs. rules.json "take"/"adapt" rows)

| Locked rule | rules.json rows checked | Conflict? |
|---|---|---|
| One CTA text | `A4-one-cta` (take, reuses existing `claims.find_second_cta_violation`), `A4-generic-cta` (take, soft) | None — no new CTA text introduced |
| Fixed financing/warranty sentences | `A4-risk-reversal` (adapt — "money-back / 30-day guarantee ... is a tenant-locked decline") | None — verified live: risk-reversal block's `text` binding is a claim-sourced string, not free text; the real run's financing line is the fixed sentence |
| No monthly financing figures | same | None — confirmed in live run output |
| No urgency/countdown, no fake scarcity | Not present anywhere in `rules.json`'s "take"/"adapt" rows (skill doesn't propose them) | None |
| No competitor names | Not touched by this PR | None |
| First-person attribution | Untouched; `claims.find_first_person_violations` still gates (confirmed PASS in live REVIEW.md) | None |
| Blocks are layout, not copy | `tagline-reveal`/`risk-reversal` block.html: template variables only, no literal copy; block.css: `var(--adv-*)` only, no color literals, no `<script>`, no fixed width >390px (`max-width:100%`, mobile rules under 480px) | None |
| Gates model-agnostic, no hard-coded models | grep clean in new code | None |
| No Shopify/Slack calls | Confirmed in live run | None |
| No tenant words in `harness/`, `cartridges/`, `blocks` | grep clean | None |

## Findings

| # | File:line | Severity | Finding | Required fix | Blocks merge |
|---|---|---|---|---|---|
| 1 | `README.md` (repo-map section, ~L104-115) | Low | Repo-map/index does not mention `harness/design_skills/`, unlike `docs/ARCHITECTURE.md`, `docs/HARNESS-MAP.md`, `docs/KIMI-BASELINE.md`, `docs/PROMPTING.md`, which were all updated | Add a one-line entry for `harness/design_skills/` next to the existing `cartridges/`/`harness/blocks/` entries | No |
| 2 | `docs/GENERATOR.md` | Low | No section for `harness design-skills list/explain/check`, unlike the existing "Layout blocks" section's level of detail | Add a short section mirroring the "Layout blocks" pattern | No |
| 3 | `docs/FIXLOG.md` | Low | No new cycle entry for this change, breaking the repo's own convention (every prior cycle, including the Kimi merge, got one) | Add a "Cycle 29" (or next number) entry summarizing the vendored pack and the two new hard gates | No |
| 4 | `harness/design_skills/landing-page-design/SKILL.md:3` | Informational | Upstream frontmatter is agent-trigger-shaped ("Use this skill whenever...", "this file wins") — see injection findings above | None required now (contained); note for future maintainers not to feed raw `SKILL.md` text to any model prompt | No |
| 5 | `harness/repair.py:143` | Informational | The design-skill hard checks are always-on, not opt-in, for every cartridge (matches brief intent; baseline parity confirms no behavior change on the two canned fixtures) | None | No |

No Critical or High severity findings. No warm-up-window-gate code exists anywhere in the tree (`grep -rn "find_warmup_window"` returns nothing outside the brief doc) — confirmed nothing is half-wired; Component B (the Amin playbook / warm-up gate) is correctly untouched, matching the brief's own "still to implement" / "do not redo" split.

## Merge commands (for Caleb; not run)

```
ssh prod
cd /home/deploy/advertorial
git fetch github
git checkout master
git merge --no-ff github/cursor/design-skills-playbook-fdad -m "Merge PR #2: design-skills playbook component"
# resolve: none expected (clean merge verified in this review)
git push origin master   # or whichever remote is the source of truth
```

## Note: master moved during this review

Two unrelated commits landed directly on local `master` while this review was
running (`2ee5271`, `3948622` -- both `Caleb Niednagel`, co-authored Claude
Fable 5.1, timestamped 14:57 and 15:02 UTC today): a second vendored pack,
`harness/design_skills/design-md/` (10-site `DESIGN.md` teardowns, MIT via
getdesign.md), added as a sibling folder to this PR's
`harness/design_skills/landing-page-design/`. This review's tests, ruff, and
baseline-parity checks were run in an isolated worktree against the
merge-base (`67b4fef`) and master's prior tip (`e3e0661`), so those results
are unaffected. The new commits use the same `design_skills/<skill-id>/`
convention this PR establishes and touch no files this PR touches, so no
merge conflict is expected -- but they reached `master` outside any
branch/PR, which is worth Caleb's attention on its own. Re-run
`pytest -q` and `ruff check .` once more after merging, as routine practice.

## Cleanup confirmation

`git worktree remove --force /home/deploy/advertorial-pr2` and `git branch -D pr2-test` run at the end of this review (see below). This review document was written and committed on `review/pr2-design-skills`, branched from `master`, in `/home/deploy/advertorial` — not merged.
