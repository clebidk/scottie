# Cycle 33 — swarm review and improvement of the harness

Status: PLAN ONLY. Nothing runs until Caleb picks a size and says go.
Precondition: cycles 31 (images) and 32 (simplicity gate) merged to master; suite green; mirror current.

## 1. Objective

Improve every part of the harness at once, with evidence, without breaking it: a wide read-only review and research fan-out, adversarial verification of every finding, a small number of implementers with exclusive file ownership, machine verification of every branch, and human-controlled merges. Output: merged improvements, a ranked backlog of what was not taken, and a report.

## 2. Principles

- Most agents read; few write. Readers cannot collide. Writers own disjoint files.
- No finding ships unverified. Two independent refuters must fail to refute it.
- Nothing merges from inside the swarm. The operator merges branches in the synthesized order, runs one real generation after each group, mirrors to GitHub.
- The swarm never spends the tenant's model budget: no `harness run` with the real client. Fake-client dry runs only. Real-run verification is the operator's step after merge.
- Company-neutral engine, locked tenant data, no Shopify or Slack calls, nothing left on the Mac. Same rules as every cycle.

## 3. Phases

| # | Phase | Agents | Model / effort | Input | Output |
|---|---|---|---|---|---|
| 0 | Prepare (operator, inline) | 0 | — | prod master | One shared read-only clone in the scratchpad; the module list; the run id and timestamp passed as args |
| 1 | Map | 12 | Sonnet, low | one module group each | Structured map: files, functions, responsibilities, test coverage, open items from FIXLOG and REVIEW docs |
| 2 | Review | 120 | Sonnet, low | one (area × lens) each, plus the phase-1 map for that area | Findings: file:line, severity, category, summary, failure scenario, proposed fix, effort |
| 3 | Research | 20 | Sonnet, medium | one domain each | 3–5 proposals with sources, effort, and the harness rule each touches |
| 4 | Dedup | 0 (code) | — | all findings | Deduped by file:line and normalized summary; sorted by severity |
| 5 | Verify | ≤100 | Sonnet, medium | one (finding × lens) each; lenses: "refute on correctness", "refute on repo conventions and locks" | Verdict: refuted true/false with reason. A finding survives only if both lenses fail to refute |
| 6 | Plan | 1 | Opus, high | surviving findings + proposals + map | ≤25 work packages; each: title, exclusive file list, findings covered, instructions, tests required, acceptance; conflict matrix |
| 7 | Implement | ≤25 | Sonnet, medium | one package each; own clone from prod; branch `cycle33/<pkg>` | Branch pushed to the server; commits; suite and ruff green in the clone; clone deleted |
| 8 | Package verify | ≤25 | Sonnet, low | one branch each; server worktree | Suite, ruff, fake-run baseline byte-compare, company-word scan, secret scan, no-network scan, diff size; verdict |
| 9 | Synthesis | 1 | Opus, high | all verdicts | Merge order, predicted conflicts, rollback notes, `docs/SWARM-2026-09-14.md` |
| 10 | Merge (operator) | 0 | — | branches in order | Sequential merges on prod master, one real generation per group, `harness spend`, mirror |

Total agents: full ≈ 300; half ≈ 150; quarter ≈ 75.

## 4. Areas × lenses (phase 2)

Areas (24): cli · pipeline · repair loop · claims gate · locked topics (warranty, financing, price, reviews) · vocab and forbidden terms · write and prompt assembly · prompt caching and pricing · budget and spend ledger · ingest (video, still, text) · ground and product picking · pdp claims · assets and image selection · render and templates · structure.css and mobile · blocks registry and gate · design skills adapter and gate · simplicity and warm-up gates · review site (serve) · revise loop · run state, approve, packet · publishers (shopify, export) · notify · sources (drive, shopify products, judgeme, gbrain stub) · evals, dataset export, eval report · tenant loader and validation · doctor · workflows runner and crons · tests and fixtures · docs and onboarding.

Lenses (5): correctness and edge cases · security (secrets, path safety, injection, untrusted ad text, HTML escaping) · cost and performance (tokens, prompt size, repeated IO, image work) · maintainability (duplication, naming, dead code, boundaries) · test gaps and flakiness (live non-determinism, real-data writes, global state).

Half size: drop the cost lens and the maintainability lens on the 8 lowest-risk areas. Quarter size: 8 highest-risk areas (repair, claims, locked topics, write, budget, render, serve, publishers) × 3 lenses.

## 5. Research domains (phase 3)

advertorial and listicle copy patterns · landing-page conversion evidence · image delivery and Core Web Vitals · Shopify Admin API pages and files (GraphQL) · Meta Marketing API ads read and creatives · Google Drive ingest (public link vs OAuth) · prompt caching and model tiering · eval methodology and human scoring · FTC native advertising and health claims · accessibility for landing pages · mobile performance · multi-tenant packaging and config · PyPI release and versioning · CI for a Python CLI · onboarding UX for connectors · block library design · comparison page structure · quiz cartridge design · reviewer notification UX · review site UX.

Each proposal must name the harness rule it touches and state take / adapt / decline against the locked rules (one CTA text, fixed financing and warranty sentences, no monthly figures, no urgency, no invented testimonials, no competitor names, verified claims only).

## 6. Schemas (structured outputs)

- Map: `{area, files[], functions[{name, file, purpose}], tests[], open_items[]}`
- Findings: `{findings[{id, file, line, severity: High|Med|Low, category, summary, failure_scenario, fix, effort: Low|Med|High}]}`
- Proposals: `{proposals[{id, title, rule_touched, action: take|adapt|decline, rationale, sources[{url, date, takeaway}], effort}]}`
- Verdict: `{refuted: boolean, reason}`
- Plan: `{packages[{id, title, files[], findings[], proposals[], instructions, tests, acceptance}], conflicts[[pkg, pkg, file]]}`
- Package result: `{branch, commits[], tests_passed, tests_total, ruff_clean, notes}`
- Package verdict: `{branch, verdict: MERGE|FIX|REJECT, suite, ruff, baseline_identical, tenant_words, secrets, network, diff_lines, notes}`

## 7. Guardrails inside every prompt

- Read only from the shared clone at the path given; never write there.
- Never run `harness run` with the real client; use `python -m evals.fake_run` only.
- Never touch `tenants/*/claims/verified.json`, `vocab.yaml`, `authors.yaml`, or any `.env`.
- Never add a company word to `harness/` or `cartridges/`; never add a model id; never add a network call.
- Implementers: one branch, one package, exclusive files; `git pull --rebase origin master` before push; delete the clone.
- Report honestly: a failed test is a finding, not a reason to weaken the test.

## 8. Cost and sizing

| Size | Agents | Rough tokens | Notes |
|---|---|---|---|
| Full | ~300 | 15–30M | one and a half to two and a half times this week's total subagent usage |
| Half | ~150 | 8–15M | one refuter per finding; three lenses on low-risk areas |
| Quarter | ~75 | 4–8M | eight areas × three lenses; ten domains; twelve packages |

Levers already in the design: readers on Sonnet at low effort; the map shared so reviewers do not re-read the repo; verification capped at the top findings by severity with the cap logged.

## 9. Acceptance for the cycle

- Every merged package: suite green, ruff clean, baseline byte-identical (or a deliberate, documented baseline update), zero company words in the engine, zero secrets, no new network paths.
- One real generation after each merge group: PASS, exact financing sentence, byline, zero banned terms, cost logged.
- `docs/SWARM-2026-09-14.md` with: findings counted by severity and by lens, verification survival rate, packages merged and not merged with reasons, the backlog, and total agent usage.
- FIXLOG "## Cycle 33" entry; mirror pushed.

## 10. Operator steps

1. Confirm cycles 31 and 32 merged; suite green; `harness spend` under cap.
2. Create the shared clone; pass `{ts, run_label, size}` as workflow args.
3. Launch the workflow; watch `/workflows`.
4. After synthesis: merge branches in order, real run per group, mirror, write the FIXLOG entry, delete the shared clone.
