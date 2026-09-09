# Advertorial Harness — Spec v0.2 (2026-09-08)

Status: DRAFT for Caleb's approval. Supersedes v0.1. Same scope and deadline; restructured as an agent harness. [NEEDS INPUT] marks items waiting on you.

## 1. Goal
One command turns one ad (video, still, or text) into three publishable landing pages, each in a different style, matched to the ad's angle, on brand, with every claim traceable to a verified source. The harness, not the model, owns quality: memory, tools, workers, loops, budgets, guardrails, and evals sit around the model weights.

## 2. Harness ladder and phases
| Level | What it is | Peak phase |
|---|---|---|
| V0 | System prompt only (a Claude Project) | Rejected. Not enough control. |
| V1 static | Fixed orchestration: workers, tools, gates, renderer | **Friday 2026-09-11.** |
| V1.5 loop | Generate → grade → revise inside a budget | Week 2 |
| V2 self-improving | Cartridges versioned and scored; a cartridge-smith proposes edits from review and performance data | Week 3+, only after ≥30 scored pages exist |

Rule: no self-improvement layer turns on before a scored corpus exists. Self-modification on zero data is noise.

## 3. Harness components
**Memory, four tiers**
1. Long-term: g Brain (71k pages) and the site. Read through a retrieval allowlist (see Guardrails).
2. Brand: `brand/tokens.json`, `brand/base.css`, asset library index. Static, versioned.
3. Run: `ad_brief.json`, `facts_pack.json`, `page.json`, run log. One folder per run.
4. Review: `evals/scores.jsonl` — every page's human scores, later North Brew metrics. Feeds V1.5 and V2.

**Tools**: ffmpeg, whisper.cpp small.en, Claude vision (still text), Drive download by id, g Brain API, site product JSON, Jinja renderer, Shopify Admin API (week 2).

**Workers** (each a single Claude call or a plain function, with a typed input and output):
- `ingest` → ad_brief
- `grounder` → facts_pack (retrieval + compaction of g Brain hits into ≤4k tokens)
- `claims_gate` → pass, or STOP with the unmatched list (deterministic, no model)
- `writer[cartridge]` ×3 → page.json
- `renderer` → index.html (injects byline, dates, ad label, disclosure; the model never writes these)
- `grader[cartridge]` → rubric score + fix list (week 2)
- `cartridge_smith` → proposed cartridge diffs for human approval (week 3+)

**Loops**: V1 has none. V1.5: writer → grader → writer, max 3 rounds, stop at rubric ≥ 8/10 or budget hit, then hand to human with the grader's notes. No early quit: the loop cannot return a page below threshold without flagging it.

**Budgets** (the "grind" idea): per run, hard caps on wall clock (5 min), tokens (150k), and Claude calls (12). Logged per run. Exceeding a cap fails the run loudly; it never returns a partial page as done.

**Guardrails**
- Retrieval allowlist: grounder may read only g Brain page types `product`, `concept`, `campaign`, `spec`, `policy`, `kb`, `reference`, `book-analysis`. Never `customer`, `order`, `email`, `support_ticket`, `conversation`, `slack_log`, `person`. This is the privileged-context leak the QM talk names; for Peak it is customer data on a public page.
- Claims gate before render, always.
- Secrets only in `.env`, never in logs or prompts.
- Assets only from the asset library index; no external images.

**Evals**: Caleb and Michael score each page 1–5 on angle match, brand fit, claim safety, and "would publish". Stored in the review tier. North Brew and Meta metrics join per page URL in week 2. This is the benchmark; harness changes are judged against it, not by feel.

**Archive**: every cartridge version kept with the scores of pages it produced. V2 picks and edits from this archive.

## 4. Pipeline (V1)
```
input ─► ingest ─► ad_brief ─► grounder ─► facts_pack ─► claims_gate ─► writer×3 ─► page.json×3 ─► renderer ─► out/<run>/<cartridge>/index.html + REVIEW.md
                                                          │ STOP
                                                          └─► unmatched_claims.json → Caleb
```

## 5. Cartridge contract
```
cartridges/<name>/
  cartridge.md      # purpose, audience temperature, structure, voice, length, CTA count
  schema.json       # page.json shape
  template.html     # Jinja; brand tokens only
  rubric.md         # 10-point check; used by grader in V1.5
  exemplars/        # 2–3 approved pages
  VERSION           # semver; bumped on any edit; scores keyed to it
```
Engine loads 3 of 5 per run, random with a logged seed.

Cartridges [NEEDS INPUT — your five descriptions; which three ship Friday]:
1. Simplified product page — live product page adapted to the ad's highlights.
2. Article — editorial read, byline block, evidence first, soft CTA late.
3. Five-point breakdown — the ad's points as sections, image-led.
4. [NEEDS INPUT]
5. [NEEDS INPUT]

## 6. Claims policy
- Source of truth `claims/verified.json`: claim, category (spec, price, comparison, health), source URL, approved_by, date.
- Seed from g Brain verified studies and site spec pages. Caleb approves additions via `adv claims add`.
- Health: cite the study, state population and effect, never promise an outcome for Peak hardware.
- Competitor comparisons: source required, else rewritten as the speaker's own experience.
- Financing: always "/mo" plus lender. "Less than $300" headlines become "from est. $257/mo".
- Implied claims count: no lab coats, before/after, or clinical settings in assets.

## 7. Byline and disclosure
- Author Austin Laudenslager, Founder & CEO. Verifier Caleb Niednagel, Technology Lead. Format copied from the site's buyer's-guide byline, plus Published / Updated dates.
- Optional credentialed reviewer [NEEDS INPUT — name and credential, or skip].
- "Advertisement" label in the header block near the headline. Disclosure paragraph at the bottom.

## 8. Runtime
- Host prod, `/home/deploy/advertorial`. Verified 2026-09-08: Python 3.14 venv, anthropic 1.4.0, whisper.cpp + small.en (8 s per 30 s clip), ffmpeg. See ENVIRONMENT.md.
- CLI: `adv run <input>`, `adv claims add`, `adv score <run>`, `adv publish <run>` (week 2).
- Inputs: Drive link (public download by id, tested) or local path. Watched inbox in week 2 [NEEDS INPUT — drop location].
- Model: claude-sonnet-5 for writers and grader. Cost per ad ≈ $0.20–0.40 (V1), ≈ $1 with the V1.5 loop.
- Logs: `runs/<run-id>.log` — model ids, tokens, calls, seed, budget use, gate result.

## 9. Verification plan
- Fixtures on prod: 3 videos, 10 stills.
- Unit: each ingest path; claims gate rejects a planted unverified claim; renderer injects byline and disclosure; budget cap trips on a forced overrun; retrieval allowlist blocks a `customer` page.
- Golden: human scores on every Friday page. Threshold to call V1 done: mean "would publish" ≥ 4/5 across the first set.

## 10. Open inputs
1. Five style descriptions; which three ship Friday.
2. Asset library Drive folder link.
3. One Sun Home page URL you like.
4. Credentialed reviewer: name + credential, or skip.
5. Video drop location for week 2.
6. Shopify: manual publish Friday confirmed?
