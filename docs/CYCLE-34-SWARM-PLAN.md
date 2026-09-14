# Cycle 34 — Grok 4.6 swarm (≥100 agents)

Status: GO — operator requested Cycle 34 with Grok 4.6, minimum 100 subagents.
Precondition: master @ `715e590` (Cycle 32 merged). Cycle 33 warm-up and related PRs may still be open; Cycle 34 starts from current master tip and must not assume those merges.

## 1. Objective

Wide harness improvement after Cycle 33: map → review → research → verify → exclusive packages → synthesis. Same principles as Cycle 33; model fleet is **Grok 4.6** for the swarm body.

## 2. Principles

- Most agents read; few write. Exclusive file ownership for implementers.
- Two independent refuters must fail to refute a finding before it ships.
- Never merge from inside the swarm. Operator merges; real `harness run` only after merge.
- Fake-client only: `python -m evals.fake_run`. No real model client, no Shopify/Slack, no tenant lock files.
- Company-neutral engine (`harness/`, `cartridges/`). Branch prefix: `cycle34/<pkg>` plus `cycle34/synthesis`.

## 3. Sizing (this run)

| Phase | Target agents | Model |
|---|---|---|
| Map | ≥12 | Grok 4.6 low/fast |
| Review | ≥70 (area × lens) | Grok 4.6 low/fast |
| Research | ≥15 | Grok 4.6 medium |
| Verify / critic | ≥5 | Grok 4.6 high |
| Plan / implement / synthesis | core agent + packages | as needed |
| **Floor** | **≥100 live subagents** | |

## 4. Areas × lenses

Areas: cli · pipeline · repair · claims · locked topics · vocab · write/prompts · caching/pricing · budget · ingest · ground · pdp claims · assets/images · render/templates · structure.css/mobile · blocks · design skills · simplicity · warm-up · serve · revise · runstate/approve/packet · publishers · notify · sources · evals · tenant · doctor · workflows · tests · docs.

Lenses: correctness · security · cost/performance · maintainability · test gaps.

## 5. Guardrails (every agent)

- Read-only unless assigned an exclusive package branch.
- No `harness run` with real client; fake_run only.
- Never touch `tenants/*/claims/verified.json`, `vocab.yaml`, `authors.yaml`, `.env`.
- Never add company words to `harness/` or `cartridges/`; no model ids; no new network calls.
- Failing test = finding, not a reason to weaken the test.

## 6. Deliverables

- `cycle34/<pkg>` branches + PRs vs master
- `docs/SWARM-2026-09-14-cycle34.md` on `cycle34/synthesis`
- Honest agent count; ranked backlog of what was not taken
