# Existing Shopify landing-page generator (per `ad-generator/landing-pages/readme`)

**What it is:** a Python CLI (`create_page.py` / `bulk_create.py`) that generates Shopify Pages for winning ad angles, living at `~/.openclaw/workspace/projects/peak-saunas/ad-generator/landing-pages` (run inside its own `.venv`). Trust-badge values in its template are placeholders, not verified — do not copy its "10,000+ customers, 4.9★" default into anything without pulling live.

**Inputs:** CLI flags or a CSV. Required: `headline`. Optional: `subheadline`, `handle` (URL slug, auto-generated if omitted), `cta_text` (default "Shop Now"), `cta_url` (default `/collections/saunas`), `title`, three `benefit_N_title`/`benefit_N_text` pairs, `testimonial_text`/`testimonial_author`, `product_image_url`. `--dry-run` previews without creating; `--unpublished` creates a draft. `bulk_create.py` batches a CSV with a configurable per-call delay and can dump results to JSON.

**Template:** `page_template.html` — a fixed, generic structure: Hero (headline/subheadline/CTA/product image) → Trust Badges → 3-card Benefits Grid → single testimonial Social Proof block → dark-section Final CTA with guarantee badges. Colors/layout/badges are edited directly in that file (brand color `#c9553d`).

**How it publishes:** directly via the Shopify Admin API — `SHOPIFY_STORE` and `SHOPIFY_TOKEN` env vars authenticate, and the script creates a Shopify `Page` resource (not a theme section, not a separate app). No mention of a review/approval step before publish other than `--unpublished`/`--dry-run`.

**Reusable for our HTML?** Yes, structurally — the publish mechanism (Admin API, Page resource, store/token env vars) is generic and could take any HTML body, including ours, rather than being locked to `page_template.html`. What's NOT reusable as-is is the template itself: it's a single fixed 5-section layout, no support for the article/longform/product-page cartridge variants this project needs. Reuse the API/publish plumbing; replace the template layer.
