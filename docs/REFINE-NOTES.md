# Refine notes (cycles 12-15)

## Scope

This covers the work since the listicle-type-and-Shopify-body-builder brief landed on
2026-09-10: fix cycles 12-15 and the listicle cartridge build (cycle 14). It does not
re-litigate cycles 1-11, which shipped the original three-cartridge harness the day
before. Every one of these four cycles refined an existing bone -- tightened a gate, fixed
a false positive, added a pre-repair path -- rather than replacing one. The one new engine
surface, the listicle cartridge and Shopify body builder, slots into the existing generic
contract rather than forking it.

## Refined, not rebuilt

**Cycle 12, problem 1 -- prompt size and budget.** The average article writer call ran
~36,800 tokens because `write.load_exemplars` sent each cartridge exemplar whole (one
alone was 5,600+ words), leaving the 150,000-token/12-call budget no room for a
numeric-heavy fixture's second repair. Fix: exemplars trimmed to 700 words, budget raised
to 220,000 tokens/14 calls (measured against a real run, not raised blind), and a
prompt-size estimate logged before every call.

**Cycle 12, problem 2 -- incidental numerals.** The writer wrote "driving to a studio at
7 a.m." in an uncited narrative sentence and tripped the plain-digit/claim_id rule -- true,
but there's no verified claim for an illustrative time of day, so every such sentence
forced an unnecessary repair call. Fix: a deterministic pre-repair pass converts a bare
numeral 1-12 next to a.m./p.m./o'clock, or standing alone, to its word form before any
REVISION REQUIRED prompt is built -- zero model calls for a failure that never needed one.

**Cycle 12, problem 3 -- the "warn" policy and alternative-claim classification.** The
"warn" policy (cycle 10) only exempted a *locked-topic* overclaim from stopping a run; a
plain unmatched claim on any other topic still stopped it, so "warn" rarely helped in
practice. Broadened so every unmatched-or-overclaimed claim is dropped from what the
writer may use instead of stopping the run, logged to `REVIEW.md` under **AD CLAIMS NOT
REPEATED ON PAGE**. Separately, a live sweep caught a regression in the new
alternative-claim classifier: "Competing saunas lack red light therapy" wasn't recognized
as a competitor statement (the noun list had "product"/"model," not "sauna") and instead
false-MATCHED Peak's own warranty claim -- the exact failure mode cycle 10 closed, for a
noun no one anticipated. Fixed by generalizing from an enumerated phrase list to a
(subject) x (noun) grid; needed a second correction one fixture later. Flagged to Caleb:
still a hand-enumerated pattern, and the next unanticipated phrasing will fail the same
way -- worth folding into the semantic-match call instead of patching the noun list per
sweep.

**Cycle 12, problem 4 -- semantic claim matching.** Token overlap alone missed a claim
that's true but phrased nothing like the verified claim's own words -- "4-in-1: near,
mid, far infrared + red light" shares almost no tokens with the full-spectrum or
red-light allowlist claims. Fix: one real Claude call per run proposes a meaning-based
mapping; the existing numeric-token guard in code still has final say (a mapping is only
accepted if it would also pass the same guard word-overlap matching enforces), so the
model's judgment widens what matches but never overrides the safety check.

**Cycle 13 -- warranty-wording deterministic pre-repair.** A live sweep showed two runs
STOPping on the pre-existing warranty gate because the writer's repair attempt reproduced
the same non-compliant shape it was asked to fix -- a short label or an honest paraphrase,
never one of the three allowed forms -- spending a full model call each time rewriting
into the same wrong shape. Fix: a deterministic pre-repair branch replaces a flagged field
with the fixed sentence (or spec-table label/value pair) directly, the same shape as the
existing hype-word and leaked-claim-id fixes, for a violation whose fix is always the same
known string.

**Cycle 14 -- the listicle cartridge and Shopify body builder.** The one addition, not a
refinement: a fourth cartridge (`cartridges/listicle/`) matching the "N reasons" format
already shipped live at `/pages/5-reasons-to-love-peak-saunas`, and a new
`harness/shopify.py` that turns any cartridge's rendered HTML into a paste-ready Shopify body
snippet. Built to prove out, not fork, the existing contract: `harness/claims.py` and
`harness/write.py` were not touched at all -- every claim gate already walks `page.json`
generically by cartridge name, so listicle passed through unmodified, verified by tests
that reuse the same `gate_page_json` call every other cartridge uses. The only real wiring
was a `DEFAULT_CARTRIDGE_POOL` constant so listicle stays opt-in until Caleb approves it
for the random-3 default.

**Cycle 15 -- image downscaling and the listicle pack's AI-render gate.** Two small,
targeted fixes. The Mini sample review file was 46 MB because a Drive original was
inlined into `harness review`'s HTML at full size -- the Drive audit had already flagged 42 of
105 pack files over 6 MB. Fix: every downloaded asset is now downscaled to a 1600px long
edge and re-encoded (JPEG quality 82; a PNG only converts to JPEG if still over 1.5 MB
after resize) before it reaches disk, with original/final byte counts logged. Separately,
the audit's own "AI renders: policy decision needed" flag -- 34 of 105 pack files are AI
composites, not photographs -- had no code enforcement; `ground.py` now reads the pack for
Mini/Matterhorn, prefers real photos, and never selects an `ai_generated: true` row unless
`tenants/peak-saunas/claims/config.json`'s new `allow_ai_renders` key is explicitly true (default false), with
the renderer prefixing "Rendering:" to any AI-composite alt text used.

## Deliberately not rewritten

- **`harness/claims.py`'s gate logic.** Iterated across 13+ cycles (locked-topic evaluators,
  semantic matcher, alternative-claim classifier, warranty pre-repair). Touching it needs
  a new, reproducible false-MATCH or false-STOP on a real fixture -- not a desire to
  simplify a file that has earned its own complexity the hard way.
- **`harness/write.py`'s generic cartridge-loading path.** Already cartridge-agnostic; a
  rewrite would only be justified by a type that genuinely can't express its rules through
  `cartridge.md`/`schema.json`. The listicle didn't need that.
- **The legacy Mac artifact and `tenants/peak-saunas/docs/existing-page-generator.md`'s CLI.** Both stayed
  read-only reference -- the listicle's markup/CSS DNA and the Shopify publish plumbing
  shape came from them, but neither was extended in place, to avoid forking effort away
  from the one system of record on prod.

## Open limitations

- **Semantic matcher variance.** The same claim matched in one real run and not another
  against the same candidate set -- the Thursday queue calls for a deterministic alias
  list checked before the model call; not yet built.
- **Warranty heuristic bluntness.** Went through four rounds of false-positive narrowing
  and still pattern-matches on a fixed sentence shape; a genuinely new honest phrasing can
  still misfire until observed and patched, the same reactive cycle the alternative-claim
  classifier is in.
- **No Shopify publish.** `harness shopify-body` only writes files under `out/`; no Admin API
  call, no `harness publish`, nothing in this harness pushes to peaksaunas.com.
- **Quiz and comparison cartridges absent.** `cartridges/` holds article, longform,
  product-page, and listicle only -- the remaining two are `tenants/peak-saunas/brand/NOTES.md`'s own
  "(Week 2)" scope, not started.
- **No self-grading loop.** `docs/SPEC.md`'s V1.5 writer-to-grader-to-writer loop has no
  code; `rubric.md` exists per cartridge but is documentation only, never executed.
