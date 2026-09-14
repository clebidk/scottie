# PROMPTING.md — running a long agent engagement on this harness

The runbook for a long-running model agent working on this repo (written for
the Kimi K3 long run, kept for the next one). It exists because the harness's
own rules — evidence gates, one commit per loop, never touch prod — only hold
if the agent's loop is as disciplined as the pipeline's.

## Master prompt

> You are working on the advertorial harness (engine `harness/`, cartridges
> `cartridges/`, tenant data `tenants/<name>/`). Read `docs/ARCHITECTURE.md`,
> `docs/KIMI-BASELINE.md` (or its successor baseline doc), and
> `docs/FIXLOG.md`'s last two cycles before touching anything.
>
> Objective: <one paragraph, naming the phase and its pre-registered done
> criteria — never a vaguer goal than that>.
>
> Repo map: the claims gate (`harness/claims.py`) is the asset — do not
> rewrite it; add checks in its established pattern. The repair loop is
> `harness/repair.py`; the pipeline is `harness/pipeline.py`; deterministic
> non-claim checks are `harness/pagechecks.py`; layout blocks are
> `harness/blocks/`; the design-skills pack is `harness/design_skills/`
> (adapter in `rules.json` wins over the raw SKILL.md); eval records are
> `harness/evals.py`.
>
> Artifact discipline: one commit per loop iteration; the full suite and
> ruff stay green at every commit; behavior-preserving work proves itself by
> byte-compare against `evals/baseline/` via `python -m evals.fake_run`.
>
> Evidence gates: a phase is done when its pre-registered criteria pass, not
> when the code looks right. Report counts (tests before/after, ruff status,
> findings by id), not adjectives.
>
> Stop conditions and budget guards: below.

## Per-phase loop prompts

Each phase runs the same loop; the phase prompt supplies the criteria.

1. **Read** the phase's inputs (the baseline doc's phase section, the named
   files, the finding ids). Restate the done criteria back before editing.
2. **Implement** the smallest change that could satisfy the criteria.
3. **Test**: write the tests first when the phase adds behavior; run the
   full suite (`pytest -q`) and `ruff check` — never the changed files only.
4. **Prove parity** when the phase is behavior-preserving:
   `python -m evals.fake_run <both fixtures> --tenant peak-saunas` and the
   suite's baseline byte-compare must pass.
5. **Commit** with the finding id or phase in the message; push; update the
   PR's checkpoint section.
6. **Checkpoint** (format below) and pick up the next phase.

## Checkpoint report format (every phase end)

- Commit hashes for the phase's commits.
- Test count before and after; ruff status.
- Findings applied by id (or the phase's deliverables, each named).
- What you could not do, and why (one line each — a deviation without a
  reason is a defect).
- Spend, if any real runs happened (must be $0 without a Caleb-issued key).
- The next phase's done criteria, restated.

## Stop conditions

Stop and ask the human (do not keep looping) when:

- A gate, a cartridge rule, or a tenant file needs changing and the change
  is not in the phase's registered scope. (Tenant files
  `claims/verified.json`, `vocab.yaml`, `authors.yaml` are never modified —
  additions go to `claims/pending.json` with a source.)
- The suite or ruff cannot go green without weakening a check.
- Baseline parity fails and the change was supposed to be
  behavior-preserving.
- Anything needs a real model key, a Shopify/Slack credential, or prod
  access. Those arrive only as a Caleb-issued capped key; the daily spend
  cap (`budget.daily_usd`, phase 3) bounds every real run.
- The done criteria are met. Done is a condition, not a vibe.

## Budget guards

- Per run: 300 s wall clock, 220,000 tokens, 14 model calls
  (`harness/budget.py`; exit 3 on overflow, never a partial page).
- Per tenant per day: `budget.daily_usd` in `claims/config.json` (wins) or
  `tenant.yaml`; every run appends its cost estimate to
  `runs/spend-ledger.jsonl`; a new run refuses to start once the cap is
  spent. Unset means uncapped — set it before any real-run engagement.
- The repair loop caps model repairs at `MAX_REPAIR_ATTEMPTS = 2`
  (`harness/repair.py`); the deterministic pre-repair pass spends nothing.
- An agent's own loop has no wall-clock guard — the stop conditions above
  are the guard.

## The repair loop's vocabulary (landscape borrowing #11)

Borrowed from guardrails-ai's `OnFailAction` so the design is legible to a
new contributor (the mechanisms predate the names):

- **fix** — `repair.apply_deterministic_fixes`: a no-model-call text
  substitution (hype synonym, incidental numeral, warranty/financing fixed
  sentence, leaked claim id) applied before any repair call.
- **reask** — the model repair attempt: the original prompt plus a REVISION
  REQUIRED block carrying every failure seen so far.
- **exception** — `claims.ClaimsGateFailure`: the run STOPs (exit 2), no
  partial page is written, `unmatched_claims.json` records what failed.

The phase-2 deterministic checks split the same way by who can fix the
failure: writer-owned fields (asset ids, CTA urls, block picks) fail inside
the repair loop (fix/reask apply); rendered-document structure (HTML
validity, JSON-LD, rendered link count) is a post-render backstop that only
ever raises — the writer cannot repair a template bug.
