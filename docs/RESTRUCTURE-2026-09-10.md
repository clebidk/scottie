# Restructure, 2026-09-10: one harness, many tenants

Branch `restructure`, off `master` at `def540f` (Cycle 15). Not merged.

`master` has since gained `df48b71` (a four-line FIXLOG note); this branch does
not include it, so a merge will want that commit brought in first. Nothing else
on `master` moved.

This was a reorganization with config indirection, not a rewrite. Every module
kept its logic. What changed is where files live, what they are called, and how
a company-specific value is resolved. The claims gate, the repair loop, the
budgets, the renderer, and every guardrail behave exactly as they did before.

**Why.** The harness was written for one company and had that company's name,
authors, URLs, review platform, theme rules, and forbidden words compiled into
the engine -- roughly forty distinct strings across eleven modules, plus the
cartridge prompts. A second company could not be added without editing the
engine, and every such edit risked the gate logic that thirteen fix cycles had
tuned. Now the engine reads none of it: `harness/` is company-agnostic, and
`tenants/<name>/` holds one company's data. Peak Saunas is the first tenant.

## Before / after

```
BEFORE                              AFTER
adv/                                harness/
  cli.py  claims.py  ground.py        cli.py  claims.py  ground.py  write.py
  write.py  render.py  ingest.py      render.py  ingest.py  prices.py
  prices.py  reviews.py  vocab.py     vocab.py (now a loader)  shopify.py
  shopify.py  budget.py  log.py       budget.py  log.py  jsonutil.py
  pdp_claims.py  semantic_match.py    pdp_claims.py  semantic_match.py
  jsonutil.py  anthropic_client.py    anthropic_client.py  config.py
  config.py  templates/  fallback.css templates/  fallback.css
                                      tenant.py     <- NEW: who this run is for
                                      pipeline.py   <- NEW: the stages, as data
                                      workflows.py  <- NEW: YAML pipeline runner
                                      review.py     <- split out of cli.py
                                      sources/      <- NEW: input adapters
                                        judgeme.py (was reviews.py)
                                        drive.py  shopify_products.py  gbrain.py
cartridges/<name>/                  cartridges/<name>/   (tenant-neutral)
  cartridge.md  schema.json           cartridge.md  schema.json
  template.html  rubric.md            template.html  rubric.md
  exemplars/                          (exemplars moved to the tenant)
  README.md  (listicle only)          (moved to the tenant's docs)
claims/                             tenants/
brand/                                default.txt          <- "peak-saunas"
fixtures/                             _template/           <- NEW skeleton
reference/                            peak-saunas/
docs/  (mixed harness + Peak)            tenant.yaml authors.yaml vocab.yaml
                                         guardrails.md claims/ brand/ fixtures/
                                         exemplars/<cartridge>/
                                         cartridge-overrides/<cartridge>/
                                         reference/ docs/ inbox/ evals/
                                         out/ runs/  (gitignored)
                                    agents/     <- NEW: 8 role briefs
                                    workflows/  <- NEW: 4 named pipelines
                                    crons/      <- NEW: systemd user templates
                                    evals/      <- NEW: generic rubric
                                    docs/  (harness-level only)
```

## Every move

Taken from `git diff --diff-filter=R --name-status -M master..restructure`.
History survives every one of them -- `git log --follow <new path>` still reaches
the original commits.

| From | To |
|---|---|
| `docs/LISTICLE-GENERATOR.md` | `docs/GENERATOR.md` |
| `adv/__init__.py` | `harness/__init__.py` |
| `adv/anthropic_client.py` | `harness/anthropic_client.py` |
| `adv/budget.py` | `harness/budget.py` |
| `adv/claims.py` | `harness/claims.py` |
| `adv/cli.py` | `harness/cli.py` |
| `adv/fallback.css` | `harness/fallback.css` |
| `adv/ground.py` | `harness/ground.py` |
| `adv/ingest.py` | `harness/ingest.py` |
| `adv/jsonutil.py` | `harness/jsonutil.py` |
| `adv/log.py` | `harness/log.py` |
| `adv/pdp_claims.py` | `harness/pdp_claims.py` |
| `adv/prices.py` | `harness/prices.py` |
| `adv/render.py` | `harness/render.py` |
| `adv/semantic_match.py` | `harness/semantic_match.py` |
| `adv/shopify.py` | `harness/shopify.py` |
| `adv/reviews.py` | `harness/sources/judgeme.py` |
| `adv/templates/base.html` | `harness/templates/base.html` |
| `adv/write.py` | `harness/write.py` |
| `brand/NOTES.md` | `tenants/peak-saunas/brand/NOTES.md` |
| `brand/assets-listicle-pack.json` | `tenants/peak-saunas/brand/assets-listicle-pack.json` |
| `brand/assets.json` | `tenants/peak-saunas/brand/assets.json` |
| `brand/base.css` | `tenants/peak-saunas/brand/base.css` |
| `brand/brand-guide-summary.md` | `tenants/peak-saunas/brand/brand-guide-summary.md` |
| `brand/byline.html` | `tenants/peak-saunas/brand/byline.html` |
| `brand/tokens.json` | `tenants/peak-saunas/brand/tokens.json` |
| `claims/config.json` | `tenants/peak-saunas/claims/config.json` |
| `claims/pending.json` | `tenants/peak-saunas/claims/pending.json` |
| `claims/products.json` | `tenants/peak-saunas/claims/products.json` |
| `claims/seed-from-gbrain.json` | `tenants/peak-saunas/claims/seed-from-gbrain.json` |
| `claims/verified.json` | `tenants/peak-saunas/claims/verified.json` |
| `docs/DRIVE-AUDIT-LISTICLE.md` | `tenants/peak-saunas/docs/DRIVE-AUDIT-LISTICLE.md` |
| `cartridges/listicle/README.md` | `tenants/peak-saunas/docs/LISTICLE-SHOPIFY-TRAPS.md` |
| `docs/PACKET-DRAFT.md` | `tenants/peak-saunas/docs/PACKET-DRAFT.md` |
| `docs/RESEARCH-LISTICLE.md` | `tenants/peak-saunas/docs/RESEARCH-LISTICLE.md` |
| `docs/STYLES.md` | `tenants/peak-saunas/docs/STYLES.md` |
| `docs/SWEEP-2026-09-09.md` | `tenants/peak-saunas/docs/SWEEP-2026-09-09.md` |
| `docs/SWEEP-2026-09-10.md` | `tenants/peak-saunas/docs/SWEEP-2026-09-10.md` |
| `docs/SWEEP-2026-09-10b.md` | `tenants/peak-saunas/docs/SWEEP-2026-09-10b.md` |
| `docs/design-notes-batch50.md` | `tenants/peak-saunas/docs/design-notes-batch50.md` |
| `docs/existing-page-generator.md` | `tenants/peak-saunas/docs/existing-page-generator.md` |
| `docs/knowledge-map.md` | `tenants/peak-saunas/docs/knowledge-map.md` |
| `cartridges/article/exemplars/best-sauna-brands-2026.md` | `tenants/peak-saunas/exemplars/article/best-sauna-brands-2026.md` |
| `cartridges/article/exemplars/br-5-reasons-sauna-owners-switch.md` | `tenants/peak-saunas/exemplars/article/br-5-reasons-sauna-owners-switch.md` |
| `cartridges/listicle/exemplars/5-reasons-mini-sauna-live.md` | `tenants/peak-saunas/exemplars/listicle/5-reasons-mini-sauna-live.md` |
| `cartridges/longform/exemplars/br-expert-sauna-comparison.md` | `tenants/peak-saunas/exemplars/longform/br-expert-sauna-comparison.md` |
| `cartridges/product-page/exemplars/fuji-2-person-product-page.md` | `tenants/peak-saunas/exemplars/product-page/fuji-2-person-product-page.md` |
| `fixtures/founder-warranty-demo.txt` | `tenants/peak-saunas/fixtures/founder-warranty-demo.txt` |
| `fixtures/hidden-costs-v2.transcript.txt` | `tenants/peak-saunas/fixtures/hidden-costs-v2.transcript.txt` |
| `fixtures/manifest.json` | `tenants/peak-saunas/fixtures/manifest.json` |
| `fixtures/price-comparison-v2.transcript.txt` | `tenants/peak-saunas/fixtures/price-comparison-v2.transcript.txt` |
| `fixtures/product-features-v2.transcript.txt` | `tenants/peak-saunas/fixtures/product-features-v2.transcript.txt` |
| `fixtures/transcript-small.txt` | `tenants/peak-saunas/fixtures/transcript-small.txt` |
| `docs/guardrails.md` | `tenants/peak-saunas/guardrails.md` |
| `reference/peak-listicle-lp/README.md` | `tenants/peak-saunas/reference/peak-listicle-lp/README.md` |
| `reference/peak-listicle-lp/assets/free_shipping.jpg` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/free_shipping.jpg` |
| `reference/peak-listicle-lp/assets/hero_mini_sauna.png` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/hero_mini_sauna.png` |
| `reference/peak-listicle-lp/assets/peaks_logo.png` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/peaks_logo.png` |
| `reference/peak-listicle-lp/assets/red_light_panel.png` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/red_light_panel.png` |
| `reference/peak-listicle-lp/assets/sauna_interior.png` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/sauna_interior.png` |
| `reference/peak-listicle-lp/assets/sauna_lifestyle.jpg` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/sauna_lifestyle.jpg` |
| `reference/peak-listicle-lp/assets/sauna_stepping_in.jpg` | `tenants/peak-saunas/reference/peak-listicle-lp/assets/sauna_stepping_in.jpg` |
| `reference/peak-listicle-lp/index.html` | `tenants/peak-saunas/reference/peak-listicle-lp/index.html` |
| `reference/peak-listicle-lp/shopify-body.html` | `tenants/peak-saunas/reference/peak-listicle-lp/shopify-body.html` |
| `tests/test_reviews.py` | `tests/test_judgeme.py` |

Deleted outright: nothing. Added: `harness/tenant.py`, `harness/pipeline.py`,
`harness/workflows.py`, `harness/review.py`, `harness/sources/*`,
`tenants/default.txt`, `tenants/_template/*`, `tenants/peak-saunas/tenant.yaml`,
`authors.yaml`, `vocab.yaml`, `agents/*`, `workflows/*`, `crons/*`,
`evals/rubric.md`, `docs/ARCHITECTURE.md`, `docs/TENANT-ONBOARDING.md`,
`tests/support.py`, `tests/test_tenant.py`, `tests/test_workflows.py`, this file.

## Config keys introduced

Everything below used to be a literal in the engine. Each row is the value that
moved, where it now lives, and which module reads it.

### `tenants/<t>/tenant.yaml`

| Key | Replaces | Read by |
|---|---|---|
| `name`, `slug` | the company name, hardcoded in prompts, JSON-LD, the page title, and the disclosure | `write.py`, `render.py`, `templates/base.html` |
| `site_url`, `site_host` | `peaksaunas.com`, matched in URL labels and link rewriting | `render.py`, `shopify.py` |
| `shopify.products_json` | `SHOPIFY_PRODUCTS_URL` | `prices.py` |
| `shopify.product_url_template` | the `https://.../products/{slug}` f-string | `prices.py` |
| `reviews.source`, `reviews.platform_name`, `reviews.store_url`, `reviews.claim_template` | `JUDGEME_STORE_URL` and the review claim's wording | `sources/judgeme.py`, `render.py` |
| `price_claim_template` | `"The Peak Saunas {name} is priced at ..."` | `prices.py` |
| `theme.name`, `theme.root_class`, `theme.full_bleed_css` | `AURORA_FULL_BLEED_RULES` | `shopify.py` |
| `default_cta_url` | the `/collections/all` fallback href | `shopify.py` |
| `disclosure_text` | the disclosure paragraph in `base.html` | `render.py` |
| `source_label_prefix`, `source_path_labels`, `product_page_label`, `review_source_label` | `_SOURCE_PATH_LABELS` and the hand-written label strings | `render.py` |
| `asset_alt_fallback` | the alt-text fallback company name | `render.py` |
| `whisper_prompt` | `WHISPER_INITIAL_PROMPT` (brand and model names) | `ingest.py` |
| `pdp_facts` | `PDP_FACTS` (which page facts to harvest, and their phrases) | `pdp_claims.py` |
| `benefit_allowlist_ids` | `BENEFIT_ALLOWLIST_IDS` | `ground.py` |
| `universal_claim_ids` | the `{"founder-ceo", "warranty-terms", ...}` set | `ground.py` |
| `listicle_pack_models` | `LISTICLE_PACK_MODELS` | `ground.py` |
| `warranty_claim_id` | the literal `"warranty-terms"` in three places | `claims.py`, `cli.py` |
| `excluded_benefit_ids` | `_EXCLUDED_BENEFIT_IDS` | `claims.py` |
| `claim_id_prefixes` | `KNOWN_CLAIM_ID_PREFIXES` | `claims.py` |
| `default_cartridge_pool` | `DEFAULT_CARTRIDGE_POOL` | `pipeline.py` |
| `allow_ai_renders`, `ad_overclaim_policy`, `financing_lender`, `speaker_name` | defaults for `claims/config.json`, which still wins | `tenant.py` |

### `tenants/<t>/authors.yaml`

| Key | Replaces | Read by |
|---|---|---|
| `author.name`, `author.title` | `AUTHOR_NAME`, the JSON-LD author, the writer prompt's "the page author (...)" | `render.py`, `write.py` |
| `contributor.name`, `contributor.title` | `CONTRIBUTOR_NAME` | `render.py` |
| `byline_author_template`, `byline_contributor_template` | the fallback byline markup's fixed title text | `render.py` |

### `tenants/<t>/vocab.yaml`

Every list that used to be a tuple in `adv/vocab.py`, plus two that lived in
`cli.py` and one in `claims.py`: `emf_terms`, `banned_names`, `hype_words`,
`ban_exclamation_mark`, `hype_synonyms` (was `_HYPE_SYNONYMS`),
`forbidden_lender_names`, `trigger_words`, `trigger_word_synonyms` (was
`_TRIGGER_WORD_SYNONYMS`), `implied_claim_forbidden_terms`,
`allowed_financing_sentence_no_lender`, `allowed_warranty_sentence`,
`allowed_warranty_spec_label`, `allowed_warranty_spec_value`,
`visible_text_forbidden_terms` (was `VISIBLE_TEXT_FORBIDDEN_TERMS`),
`competitor_aliases`.

The EMF ban stays absolute for `peak-saunas`: `emf_terms` feeds the absolute
forbidden list, the ingest drop, the post-render visible-text scan, and the
product-page claim harvester. The engine treats it as data; the tenant makes it
absolute.

### `tenants/<t>/claims/config.json`

Keys unchanged (`financing_lender`, `show_compare_at_price`, `reviews_source`,
`speaker_name`, `ad_overclaim_policy`, `allow_ai_renders`), now read through
`tenant.claims_config`, which layers engine defaults, then `tenant.yaml`, then
this file -- so a hand edit here still wins.

## How a company reaches the engine

```
--tenant flag  >  HARNESS_TENANT env  >  tenants/default.txt
        |
        v
  tenant.load_tenant(name, require=True)
        |  reads tenant.yaml, authors.yaml, vocab.yaml, claims/config.json
        |  refuses with TenantNotConfigured (exit 4) if the claims store is empty
        v
  tenant.activate(t) ---> vocab.activate(t)
        |
        v
  every stage reads tenant.<path> / vocab.<LIST>; nothing reads a literal
```

Cartridge prompts are rendered at load time: `{{ tenant.name }}`,
`{{ authors.author.name }}`, `{{ tenant.reviews.platform_name }}`. An unknown
placeholder is left in place rather than silently emptied, so a typo shows up in
the prompt instead of quietly producing a sentence with a hole in it.

Secrets: the API key lives in `tenants/<t>/.env`, loaded by `tenant.load_env()`.
`harness/anthropic_client.py` reads it from the environment only, and it is never
logged, printed, or put in a prompt. `tenants/*/.env` is gitignored.

## Verified on the server, 2026-09-10

Run in a throwaway worktree (`git worktree add ../advertorial-restructure
restructure`) so `master` stayed untouched, with its own venv, the `.env`
copied to `tenants/peak-saunas/.env`, and `models`/`vendor` symlinked from
`~/advertorial`. Media fixtures (`*.mov`, `*.png`) are untracked and were
symlinked in the same way.

| Check | Result |
|---|---|
| `pytest -q` | 390 passed |
| `harness run .../hidden-costs-v2.mov --tenant peak-saunas` | PASS, three pages, 1m52s, 3 attempts / 0 repairs, est. $0.3078 |
| `harness run ... --cartridges listicle` | PASS, one page, 47s, 1 attempt / 0 repairs, est. $0.1013 |
| `harness workflow run ad-to-pages --input .../hidden-costs-v2.transcript.txt` | PASS, three pages, est. $0.3103 |
| `harness tenant init demo-co` then a run against it | exit 4, one-line `tenant not configured: missing ...`, no traceback |
| `grep -rn "Peak\|Austin\|Judge.me\|Aurora" harness/ cartridges/` | no matches |
| `git status` | clean |
| Output location | `tenants/peak-saunas/out/<run-id>/` |

Two observations from the live runs, neither caused by this restructure:

- `founder-warranty-demo.txt` STOPs at the ad-claims gate on a warranty
  overclaim, through `harness run` and `harness workflow run` alike. The ad
  restates part of the warranty ("cabinetry and structure for 7 years") which
  is true but is not one of the fixed allowed forms, and the tenant's
  `ad_overclaim_policy` is `stop`. `evaluate_warranty_claim` is byte-identical
  to `master`'s apart from the tenant lookup, so this is the gate behaving as
  designed on a fixture built to exercise it.
- A repeat of the transcript run STOPped on an `attributed_to_customer` item
  with no visible attribution, after two repairs. Real model calls are not
  deterministic -- `--seed` pins cartridge selection and the product, not the
  model -- so a run-to-run difference here is ordinary. The claim that the
  workflow runner reproduces `harness run` is proved deterministically in
  `tests/test_workflows.py`, which byte-compares every `page.json` from both
  entry points against a fake client, and asserts the workflow's stage list
  equals `pipeline.DEFAULT_STAGES`.

## What a reviewer should check before merging to master

1. **The gate logic is untouched.** `git diff master..restructure -- harness/claims.py`
   should show only import changes, five lookups turned into function calls
   (`vocab.*`, `warranty_claim_id()`, `known_claim_id_prefixes()`,
   `_excluded_benefit_ids()`, `_allowed_warranty_forms()`), and reworded comments.
   No threshold, regex, or branch changed. Same for `write.py`'s repair loop and
   `budget.py` (which is byte-identical apart from its path).
2. **The voice block still says the same things.** `harness/write.py`'s
   `global_voice_block()` replaced a module-level f-string. Read it against
   `master`'s `GLOBAL_VOICE_BLOCK` and confirm every rule survived: the trigger
   words, the claim-id-never-in-prose rule, the attribution rule, the fixed
   financing and warranty sentences, the numerals-as-words rule.
3. **`tests/test_tenant.py::test_no_tenant_specific_words_in_the_engine_or_the_cartridges`
   passes.** This is the rule the restructure exists to keep. If it starts failing,
   a company value has crept back into the engine.
4. **`tests/test_workflows.py::test_ad_to_pages_runs_exactly_the_default_pipeline`
   passes.** The workflow runner and `harness run` must stay the same thing.
5. **A real run still produces three pages** on `hidden-costs-v2.mov`, and a
   `--cartridges listicle` run still produces one. Output must land under
   `tenants/peak-saunas/out/`, not the old top-level `out/`.
6. **`git status` is clean after a run.** Product data stays hand-curated; the
   live refresh writes only its cache under the tenant's `runs/`.
7. **No timer is enabled.** `crons/install.sh` renders the unit files and prints
   the `systemctl --user enable` commands without running them.
8. **`.env` was moved, not copied or printed.** On the server it now lives at
   `tenants/peak-saunas/.env`.

## Rollback

```
git checkout master
```

Nothing on `master` was touched. The branch is additive; deleting it loses only
the restructure.
