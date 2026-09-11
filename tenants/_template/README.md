# New tenant checklist

`harness tenant init <slug>` copies this directory to `tenants/<slug>/`. Work
down the list. Until steps 1-4 are done, `harness run --tenant <slug>` exits 4
with `tenant not configured: ...` rather than producing a page.

## 1. Identity
- [ ] `tenant.yaml`: replace every `CHANGE ME` -- name, slug, site_url,
      site_host, shopify.products_json, shopify.product_url_template.
- [ ] `tenant.yaml`: `disclosure_text` reads correctly with the real company
      name substituted.
- [ ] `authors.yaml`: the person who signs a page and the person who reviews it.

## 2. Claims store (this is what makes a run possible)
- [ ] `claims/products.json`: one curated entry per product you will write
      about -- slug, name, short_name, url, price, specs, `active`, and exactly
      one `default: true`.
- [ ] `claims/verified.json`: every claim a page may cite, each with a source
      URL, a category, and who approved it. Add with
      `harness claims add "..." --category spec --source https://... --tenant <slug>`.
- [ ] `claims/config.json`: `financing_lender` stays null until a real lender is
      approved; `reviews_source`; `ad_overclaim_policy` (`stop` is the safe
      default for an unreviewed ad).

## 3. Voice and guardrails
- [ ] `vocab.yaml`: banned words, competitor names, discontinued model names,
      the fixed warranty sentence, the fixed financing sentence.
- [ ] `guardrails.md`: write down why each absolute rule exists.

## 4. Brand
- [ ] `brand/tokens.json`, `brand/base.css`, `brand/byline.html`. Without
      base.css/byline.html the renderer falls back to the harness defaults and
      logs a warning -- fine for a first run, not for a published page.
- [ ] Fastest path to the above: `harness brand import --tenant <slug>
      --drive-folder <url-or-id>` (a Drive folder shared "Anyone with the
      link", holding some mix of a logo, a brand guide PDF/image, a palette
      file, and/or font files) or `harness brand import --tenant <slug>
      --local <dir>` (the same files already on disk). Writes `brand/logo.*`,
      merges into `brand/tokens.json` and this file's `brand:` section
      (never clobbers a value you already set, unless `--force`), regenerates
      `brand/base.css` only if it's still the empty template stub, and writes
      `brand/BRAND-IMPORT.md` listing what it found, what it chose, and what
      still needs a human decision. Run with `--dry-run` first to see what it
      would do.

## 5. Optional, but do it before the first real run
- [ ] `tenant.yaml`: `theme.full_bleed_css` if pages will be pasted into a
      storefront theme.
- [ ] `tenant.yaml`: `whisper_prompt` -- brand name, category words, model
      names. Without it a transcript mishears the product names.
- [ ] `tenant.yaml`: `pdp_facts` -- phrases to harvest from a product page.
- [ ] `exemplars/<cartridge>/`: one or two approved pages per cartridge, as
      voice references. Up to two are used per write call.
- [ ] `cartridge-overrides/<cartridge>/cartridge.md`: only if a cartridge needs
      a tenant-specific delta. Never use one to relax a guardrail.
- [ ] `fixtures/`: a `.txt` ad transcript to dry-run against.
- [ ] `.env`: copy `.env.example`, add the API key. Never commit it.

## 6. First run
```
harness run tenants/<slug>/fixtures/<something>.txt --tenant <slug>
```
Read `tenants/<slug>/out/<run-id>/REVIEW.md` before looking at the page itself:
it lists which ad claims matched, which were dropped, and every source used.
