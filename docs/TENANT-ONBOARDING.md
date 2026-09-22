# Tenant onboarding

Stand up a second company in under an hour. `peak-saunas` is used below as the
worked example of an already-filled-in tenant; everything here applies to any
company.

## 0. Create the tenant (2 min)

```
harness tenant init <slug>
```

This copies `tenants/_template/` to `tenants/<slug>/` (refuses to run if that
directory already exists) and creates empty `out/` and `runs/` dirs under it.
Until the claims store (step 2) is filled in, every command needing the
tenant configured exits 4 with `tenant not configured: ...` -- see
Troubleshooting.

## 1. Identity (10 min)

Edit `tenants/<slug>/tenant.yaml` and `authors.yaml`. Every `CHANGE ME` in
`tenant.yaml` must go -- `Tenant.missing_pieces()` specifically checks that
`name` is no longer the placeholder string.

Key by key, what it actually controls:

- `name`, `slug` -- display name and directory name.
- `site_host` -- compared against a claim's source URL in `render.py`'s
  `source_label`; a mismatch falls back to a generic label instead of
  "product page" / `source_path_labels`, so Sources list entries look wrong.
- `shopify.products_json` / `.product_url_template` --
  `harness/sources/shopify_products.py`'s `products_json_url()` supplies the feed
  URL that `harness/prices.py` fetches in `refresh_prices`. Wrong URL: no
  crash, just silently stale prices from the products.json you curated.
- `reviews.source` (`none` / `judgeme-live`), `.platform_name`, `.store_url` --
  when not `none`, the `ground` stage calls `sources/judgeme.py` for a live
  rating/count; `store_url`'s host also gives review citations their own
  Sources line instead of colliding with the product page.
- `theme.full_bleed_css` -- pasted at the top of `harness shopify-body` output
  (`shopify.py`). Needed only if pages get pasted into a storefront theme.
- `whisper_prompt` -- `ingest.py` passes this to whisper.cpp as a decoding
  bias. Without it, a transcript reliably mishears brand and model names.
- `pdp_facts` (`fact name -> literal phrases`) -- `pdp_claims.py` creates a
  fact only when one of its phrases is found verbatim on the product page;
  nothing is inferred. Skip it and the writer has no page-derived claims to
  cite, even true ones.
- `benefit_allowlist_ids` / `universal_claim_ids` -- claim ids every
  product's `facts_pack` always carries (`ground.py`), the first product-
  benefit claims regardless of model, the second policy/founder claims.
  Empty means nothing to cite there.
- `warranty_claim_id` -- verified claim id backing the fixed warranty
  sentence (`claims.warranty_claim_id()` falls back to `"warranty-terms"` if
  unset); the deterministic repair pass attaches it when rewriting a
  warranty sentence.
- `claim_id_prefixes` -- id-family prefixes (`spec-`, `price-`, ...) that
  `claims.known_claim_id_prefixes()` accepts even when the exact id isn't a
  live/seeded id sitting in `verified.json`. Miss the right prefix and a real
  claim looks invented.
- `default_cartridge_pool` -- which cartridges `prepare_run`
  (`pipeline.py`) draws 3 from at random when `--cartridges` isn't passed.
- `source_path_labels` (`path -> label`) -- used for a same-host URL that
  isn't a product page. Skip it and those Sources entries fall back to a
  title-cased URL path.

`authors.yaml`'s `author` (who signs the page) and `contributor` (who reviews
it) feed the byline templates; leave a `CHANGE ME` here and the byline prints
it literally.

## 2. Claims store -- this is what makes a run possible (15-20 min)

`tenant.require_configured()` (called by `harness run`, `harness workflow
run`) checks `tenant.yaml` (present, with a `name` that is no longer `CHANGE
ME`), `claims/verified.json` and `claims/products.json`; skip any of them and
the run exits 4. Since Cycle 22 it also validates `tenant.yaml` itself --
`schema_version`, the required keys, and their types -- and `harness doctor`
reports the softer problems it only warns about, such as a misspelled key.
`claims/config.json` is NOT required: a missing one falls back to
`DEFAULT_CLAIMS_CONFIG` in `harness/tenant.py`.

**`products.json`** -- `{"products": {<slug>: {...}}}`, one curated entry per
product you'll write about, exactly one with `"default": true`. Illustrative
shape (not real data), fields trimmed to the essentials:

```json
{"products": {"acme-2-person-sauna": {
  "slug": "acme-2-person-sauna", "name": "Ridgeline",
  "short_name": "Acme Ridgeline 2-Person Sauna",
  "url": "https://acmesaunas.example/products/acme-2-person-sauna",
  "price": "3950.00",
  "specs": [{"label": "Capacity", "value": "2-Person", "claim_id": "spec-aurora-capacity"}],
  "active": true, "default": true
}}}
```

**`verified.json`** -- a flat list of every claim a page may cite. Add one
with (`--category` is one of `spec | price | comparison | health | trust`):

```
harness claims add "The Acme Ridgeline is priced at $3,950." \
  --category price --source https://acmesaunas.example/products/acme-2-person-sauna \
  --tenant <slug>
```

which appends an entry shaped like:

```json
{"id": "the-acme-ridgeline-is-priced-at-3-950", "text": "The Acme Ridgeline is priced at $3,950.",
 "category": "price", "source": "https://acmesaunas.example/products/acme-2-person-sauna",
 "approved_by": "operator", "date": "2026-09-10"}
```

List what's there with `harness claims list --tenant <slug>`.

**`config.json`** -- `financing_lender` (keep `null` until a real lender is
approved), `show_compare_at_price`, `reviews_source`, `speaker_name`,
`ad_overclaim_policy` (`stop`, the safe default, or `warn`). This file's keys
always win over `tenant.yaml`'s overlapping ones (`Tenant.claims_config`), so
an operator can flip a policy here without touching `tenant.yaml`.

## 3. Voice and guardrails (10 min)

`vocab.yaml` is data the engine enforces, not documentation -- both the writer
prompt and the deterministic gate (`harness/claims.py`, `harness/vocab.py`)
read it live. Fill in:

- `banned_names` -- competitors, discontinued models. Absolute ban, no
  claim_id excuses it.
- `hype_words` / `hype_synonyms` -- words never allowed in prose; the
  deterministic repair pass substitutes the synonym before calling the model.
- `trigger_words` -- words that force a `claim_ids` citation wherever they
  appear (e.g. `medical`, `clinical`, `proven`, `rated`, `reviews`).
- `allowed_warranty_sentence` / `_spec_label` / `_spec_value` -- the one fixed
  sentence and fixed spec-table row a warranty claim may use, verbatim. Any
  other phrasing of a warranty claim is a gate failure.
- `allowed_financing_sentence_no_lender` -- fixed sentence used when
  `financing_lender` is null.
- `visible_text_forbidden_terms` -- checked against the rendered page's
  visible text (not just page.json), so a term smuggled in via the template
  or an href still gets caught.

`guardrails.md` is the human-readable record of *why* -- fill in the
"Absolute" and "Needs approval" `CHANGE ME` sections. An override anywhere
(see step 5) must never relax anything this file or `vocab.yaml` establishes.

## 4. Brand (5-10 min)

`brand/tokens.json`, `brand/base.css`, `brand/byline.html`. Fix cycle 23:
`brand/base.css` is an **override layer**, not a replacement -- the renderer
always inlines `harness/structure.css` (layout, components, responsive
rules, image sizing, the sticky CTA bar, the ad label, byline, disclosure,
sources) first, then `brand/base.css` on top of it, so it only needs to
carry this tenant's tokens, fonts and colors. It never has to (and should
not) redefine any `adv-*` structural class -- those already have a rule in
`harness/structure.css`, enforced by `tests/test_css_coverage.py`. Missing
or empty, the renderer logs a warning and the page still gets the full
`structure.css` layer on its own -- fine for a first test run, not for
anything published (no tenant tokens/fonts/brand colors without it).
`brand/byline.html` has no such fallback layering: missing, the renderer
uses a plain built-in byline instead. If `brand/logo.<ext>` exists (svg,
png, jpg, or webp) the renderer copies it into the run's self-contained
`assets/` folder and the product-page/longform templates show it in the
page header (`.adv-brand-logo` in `harness/structure.css`); no logo file,
no `<img>` -- nothing else changes.

**What a token is for (cycle 52).** Every cartridge resolves a colour, a
shape or a face in one direction: `--pk-*` (the look's own name for it) ->
`--ps-*` (this tenant's `brand/base.css`) -> `--adv-*` (the harness default
in `harness/structure.css`). A cartridge never writes a literal, and
`harness/structure.css`'s own `:root` block is the last word for a tenant
that defines nothing, so adding a `--ps-*` token changes this tenant and no
other. The set a brand can drive today: `--ps-accent` / `--ps-on-accent`
(the accent fill and the text ON it -- set both, a light accent needs dark
text), `--ps-link`, `--ps-text`, `--ps-text-strong`, `--ps-muted`, `--ps-bg`,
`--ps-bg-muted`, `--ps-border`, `--ps-ink-dark` / `--ps-on-dark` (a dark
band and its text, which is not simply the ink colour used as a background),
`--ps-editorial`, `--ps-band`, `--ps-red`, `--ps-star-on` / `--ps-star-off`,
`--ps-pill-bg` / `--ps-pill-text` / `--ps-pill-border`,
`--ps-eyebrow-size` / `--ps-eyebrow-spacing` / `--ps-eyebrow-color`,
`--ps-radius-card` / `--ps-radius-btn` / `--ps-radius-pill` /
`--ps-radius-round` (all four to `0px` for a square-cornered brand), and
`--ps-sans` / `--ps-serif`.

Three tenant.yaml keys sit beside them:

- `brand.headline_case: upper` renders every heading uppercase through CSS
  (`adv-case-upper` on the page wrapper). It never rewrites the writer's
  copy, so the gate and REVIEW.md still see what was written. Omit it, or
  set `none`, and nothing changes.
- `product_display_strip_prefix` removes a storefront title prefix (e.g.
  `"Acme Saunas "`) wherever a product name is displayed and wherever the
  writer is handed one. Slugs, URLs and claim ids keep it.
- `brand:` is also where `harness brand import` merges what it found.

**Self-hosted webfonts.** Put the files in `brand/fonts/` and write the
`@font-face` in `brand/base.css` with a repo-relative `brand/fonts/...`
URL. The export carries those rules into the storefront body, and the
Shopify publisher uploads each file once, caches the CDN URLs in
`brand/fonts/cdn-manifest.json`, and rewrites the URLs at publish time. A
font that cannot be uploaded is logged and dropped from the export, so the
page falls back to the rest of the font stack rather than waiting on a URL
that never resolves. Never write a literal `<body>`, `<head>` or `<script>`
tag in `base.css`, even inside a comment: the stylesheet is inlined into the
document head and the export locates the document body by those tags.

### Brand kit import (Cycle 27)

`harness brand import --tenant <slug> --drive-folder <url-or-id>
[--dry-run] [--force]` turns a Drive folder into the brand files above,
without OAuth:

1. **Share the folder "Anyone with the link"** (view access is enough) and
   copy its URL or id. The folder can hold a logo (svg/png/jpg with "logo",
   "mark", "wordmark", or "favicon" in the filename -- a subfolder like
   "Logo Files" is walked one level deep), a brand guide (a PDF, or any file
   with "guide"/"brand"/"style" in the name), a palette file (`.json`/`.txt`
   named "color"/"palette"/"tokens" -- hex codes are parsed out of it;
   `.ase` swatch files are not read, and are flagged in BRAND-IMPORT.md for
   a human to open by hand), font files (`.ttf`/`.otf`/`.woff`/`.woff2`),
   and photos (indexed for later, not wired into a page yet).
2. **Not link-public?** The command fails with the exact message: "Drive
   folder is not link-public; share it as Anyone with the link, or upload
   the files into `tenants/<t>/brand/incoming/` and rerun with `--local`" --
   `--local <dir>` runs the identical classify/extract pipeline over files
   already on disk instead of Drive.
3. **Run `--dry-run` first.** It reports what it found and what it would
   write, without touching anything under `tenants/<slug>/`.
4. **A brand guide PDF/image gets one real model call** (vision, capped at 6
   rendered pages) that returns strict JSON only -- colors, fonts, logo
   rules, voice, don'ts. Anything written inside the guide's pages is
   treated as reference material, never as an instruction to the model.
   `pdftoppm` renders PDF pages; without it (or for anything else that can't
   be rendered) the gap is named in BRAND-IMPORT.md instead of silently
   skipped.
5. **Nothing you already set is clobbered.** `tokens.json` and `tenant.yaml`'s
   `brand:` section are merged: an existing key's value always wins over a
   re-import unless `--force`; a new key is always added; every imported
   value is tagged `"source": "brand-import:<file>"`. `base.css` is only
   (re)generated when it's missing or still the empty template stub, or with
   `--force` -- a hand-tuned `base.css` (Peak's, for instance) is never
   touched otherwise.
6. **A color that would be unreadable is caught, not shipped.** WCAG
   contrast is checked text-on-background (warns below 4.5:1) and
   accent-on-background (refuses to set the accent below 3:1, unless
   `--force`) -- both land in `brand/BRAND-IMPORT.md`'s "what needs a human
   decision" section along with anything else the import couldn't resolve on
   its own (an unreadable `.ase` file, an unrendered guide, ambiguous logo
   usage rules from the guide).

Read `brand/BRAND-IMPORT.md` after every import: what was found, what was
chosen, what still needs you, and the raw model JSON from the brand guide.

## 5. Optional, but do before a real run

- **Exemplars** -- `tenants/<slug>/exemplars/<cartridge>/`: one or two
  approved reference pages per cartridge. `write.py`'s `load_exemplars` pulls
  in up to 2 per write call (first ~700 words each) as voice references. None
  means the writer works from the cartridge spec alone.
- **Cartridge overrides** -- `tenants/<slug>/cartridge-overrides/<cartridge>/cartridge.md`,
  appended to the shared `cartridges/<cartridge>/cartridge.md` after its
  rules, for a tenant-specific structural or voice delta only. Never use one
  to relax a guardrail -- the gate still enforces `vocab.yaml` and
  `claims/verified.json` regardless of what an override says.
- `fixtures/` -- a `.txt`/still/video ad to dry-run against, so day one
  doesn't need a real ad.
- `.env` -- copy `.env.example`, add the API key. Never commit it;
  `Tenant.load_env()` loads it into the process environment and nothing logs
  or prints its contents.
- **Reviewers, publisher, notifications** (cycle 20 -- see
  `docs/PUBLISHING.md`) -- `tenant.yaml`'s `reviewers` list (`name`, `email`,
  `role: primary|backup`); an `approve`/`reject` from an email not on this
  list is refused. `publisher: export` (the default -- no credentials
  needed) or `shopify` (needs `SHOPIFY_STORE`/`SHOPIFY_TOKEN` in `.env`,
  scopes `write_content`, `write_files`, `read_content`). `notifications:
  {slack: true|false, email: [...]}`, plus `SLACK_WEBHOOK_URL`/`SMTP_*` in
  `.env` for whichever channel is turned on -- both are optional and fail
  closed, so it's safe to leave every one of these unset until a real
  reviewer and a real storefront credential exist.

## 6. First dry run (5 min)

```
harness run tenants/<slug>/fixtures/<something>.txt --tenant <slug>
```

or, equivalently, `harness workflow run ad-to-pages --input
tenants/<slug>/fixtures/<something>.txt --tenant <slug>` -- both execute the
same stage functions from `harness/pipeline.py`, so they cannot drift apart.

**Read `tenants/<slug>/out/<run-id>/REVIEW.md` before
opening any page.** It lists: which ad claims matched (and which didn't, under
a `warn` policy), every claim_id actually used and its source, word counts,
first-person-attribution and banned-topic checks, the gate-repair history per
cartridge, and estimated cost. If `REVIEW.md` looks wrong, the page will too.

Other useful commands: `harness ingest <input> --tenant <slug>` (ad ->
ad_brief.json only, for debugging), `harness review <run_dir> --tenant <slug>`
(self-contained review.html, images inlined), `harness tenant list` (every
tenant and whether it's configured).

## 7. Troubleshooting

**`tenant not configured: missing ...`** -- exit 4, from
`Tenant.missing_pieces()`. Names exactly what's missing: `tenant.yaml`
(absent, or `name` still `CHANGE ME`), `claims/verified.json` (absent or
empty), or `claims/products.json` (absent or its `products` map is empty).
Fix the named file; nothing else needs to be complete yet.

**A claims-gate STOP** (exit 2, `unmatched_claims.json` written under the run
directory) -- almost always the claims store, not the ad. The ad made a claim
with no matching `claims/verified.json` entry, or a locked-topic
(warranty/reviews/financing/price) overclaim of what's actually approved.
Read the printed items, then add the missing claim (`harness claims add ...`)
or fix the ad script. `ad_overclaim_policy: warn` in `claims/config.json`
lets the run continue with the claim dropped -- use it only once you've
confirmed the ad needs a correction, not to skip verifying claims.

**Review fetch finds nothing** -- `reviews.source: judgeme-live` but the
product page's `aggregateRating` JSON-LD and widget attributes both come back
empty (bot wall, redesigned page, or wrong `store_url`). Not an error: no
review claim is produced and no review numbers appear anywhere. Check
`store_url`; if the page structure changed, `sources/judgeme.py` needs an
update, not `tenant.yaml`.

## Checklist

- [ ] `harness tenant init <slug>` run; `tenant.yaml` (no `CHANGE ME`,
      `site_host`/`shopify.*` correct) and `authors.yaml` filled in
- [ ] `claims/products.json` (>=1 product, one `default: true`) and
      `claims/verified.json` (every claim the ad needs, each sourced) filled in
- [ ] `claims/config.json`: `ad_overclaim_policy` set deliberately (default `stop`)
- [ ] `vocab.yaml` and `guardrails.md` filled in: banned names/words, fixed
      warranty/financing sentences, absolute rules written down
- [ ] `brand/tokens.json`, `base.css`, `byline.html` in place; `.env` created
      (not committed) if an ad will actually be ingested
- [ ] First dry run completed, `REVIEW.md` read before any page; `harness
      tenant list` shows the tenant as `ready`
- [ ] `reviewers` filled in in `tenant.yaml`; `publisher` set deliberately
      (default `export`); `notifications` set (default both off) -- see
      `docs/PUBLISHING.md`
