# Peak Saunas rebrand toolbox — brand import dry run (2026-09-14)

Cycle 35c. `harness brand import --tenant peak-saunas --drive-folder 1D8cG1cAY9sH0_fzMsUhseK_5sSWa_KCD --dry-run`, run on the server in a worktree of `cycle35c/brand-import-nesting`. **Dry run only** — nothing under `tenants/peak-saunas/brand/` or `tenant.yaml` was written. This is a survey of what the agency's "Peak Toolbox" folder contains and what applying it would change; whether to apply it is Caleb's call.

The folder is much larger than its top-level listing suggests: 475 files once every subfolder is walked, not a handful. See "How the run actually went" below for two real gaps this surfaced.

## The tree found

| Top-level folder | Files | Breakdown |
|---|---:|---|
| 1 - Peak Logotype | 78 | logo sources (CMYK/RGB × 8 colorways × 5 formats: .ai/.jpg/.pdf/.png/.svg) |
| 2 - Peak Icon | 78 | same shape, icon mark |
| 3 - Peak with Icon | 156 | "With big icon"/"With small icon" × CMYK/RGB × 8 colorways × 5 formats |
| 4 - Fonts | 3 | 2 `.otf` (both TRIAL builds, see below) + 1 `.pages` instructions file |
| 5 - Colors | 2 | `Peak_Sauna_Colors.pdf` (palette doc) + `Peak_Sauna_Colors.ai` (source, not read) |
| 6 - Peak Info Sheet | 1 | `Peak_Infos.pdf` — **263.8 MB**, not a small info sheet (see below) |
| 7 - Branding | 1 | `Peak Saunas _ EON Creation Presentation VF.pdf` (12.8 MB) |
| 8 - Logo layout for Merch | 156 | "With website"/"Without website" × CMYK/RGB × 8 colorways: 92 preview images, 32 `.ai` sources, 32 `.pdf` (misclassified as "guide" — see below) |
| **Total** | **475** | |

Colorways found across every logo/icon/merch folder: Basalt, Black, Cedar, Dust, Red, Solar Flare, Stone, White. `.DS_Store` files were present and skipped at multiple levels, as expected.

## Logo candidates (per variant, with Drive ids)

`choose_logo` prefers .svg > .png > .pdf > .jpg, largest-by-bytes within the winning format. All three variants have an .svg available, so all three resolved to one:

| Variant | File | Drive id |
|---|---|---|
| Logotype | `CMYK-Peak-Logotype-Basalt.svg` | `1IYHkTVGQgl6frpGS2pOchIEzDK723cSf` |
| Icon | `CMYK-Peak-Icon-Basalt.svg` | `1sc13dcpwYTKUwu4tXpolttveIPSaQVOl` |
| Combined (with icon) | `CMYK-Peak-with Icon-Basalt.svg` | `1AuJ4ygh_tcZuS02Y3H25OrYUn_ChIE4q` |

**Caution:** all three landed on the **Basalt** colorway purely because it happened to produce the largest `.svg` file in each folder — file size, not any "this is the default" marker. Nothing in the folder structure or the vision reads says which of the 8 colorways is the primary brand mark. **Confirm the intended default colorway with the agency/Caleb before using any of these.**

Every `.ai`/`.eps` source file next to a chosen logo (62 of them, one per colorway/format/folder combination) was correctly identified as a design source, not copied, and listed for hand review.

## Palette — exactly as extracted

Two independent vision reads found the palette. Both are shown in full below; only 3 of the 6 `Colors` values survive into `tokens.json`-shaped output today because `extract_brand_guide_json`'s color merge keys on the model's own free-text `role` string and the second color with a repeated role is silently dropped (`Stone`, `Solar Flare`, and `Cedar` all lost this way here — a real gap, not something Cycle 35c's scope covers; flagged in "Follow-ups" below).

**From `Peak_Sauna_Colors.pdf` (the Colors-folder palette document — this cycle's new `palette_pdf` path):**

| Name | Hex | Role |
|---|---|---|
| Fossil Dust | `#C0C8C3` | neutral |
| Basalt | `#181918` | dark |
| Stone | `#EFE3D2` | neutral |
| Red | `#702B33` | accent |
| Solar Flare | `#F27046` | accent |
| Cedar | `#483215` | neutral |

**From `Peak Saunas _ EON Creation Presentation VF.pdf` (the Branding-folder deck):**

| Name | Hex | Role |
|---|---|---|
| Black | `#000000` | background, primary text |
| White | `#FFFFFF` | text, contrast |
| Warm Brown | `#8B6F47` | accent, sauna imagery |
| Deep Red | `#8B3A3A` | accent, heat/energy |

Note the guide reads as a **dark/black-background** design (`background` role assigned to `#000000`, not white) — the opposite of the current live site's white background. That is a real, structural shift, not a footnote — see "What applying this would change."

## Fonts

- **`Acid Grotesk TRIAL Regular-9687.otf`** and **`Epika_Trial-Regular.otf`** (`4 - Fonts`): both filenames contain "TRIAL" — flagged as **not licensed for production** and **not copied into `brand/fonts`**, per this cycle's fix, `--force` or not. A licensed build of each needs to come from the foundry/agency before either typeface is usable.
- `Instructions_for_Fonts.pages`: noted, not read (`.pages` isn't parsed).
- No real font *name* was extracted from either vision read — the brand guide described fonts ("Sans-serif (geometric, modern)" for headlines, "Serif (elegant, classical)" for body) rather than naming them, so neither produced a Google Fonts import; a human needs to pick real typefaces for both roles.

## Brand guide's stated rules

From `Peak Saunas _ EON Creation Presentation VF.pdf`:

- **Logo usage:** "SAINT·URBAIN wordmark uses consistent spacing with centered dot separator"; logo appears in white on black and black on white; maintains prominent placement on all materials.
- **Voice:** sophisticated and timeless; focused on longevity and craftsmanship; emphasizes wellness as a lifelong journey; professional yet aspirational; contemplative and purposeful.
- **Don't:** none stated (the read returned an empty list for this section).
- No explicit clear-space or minimum-size rule was stated on the pages read (only the first 6 pages of the deck are ever rendered — `MAX_GUIDE_PAGES`).

**Flag this directly rather than passing it through quietly: the wordmark named in "Logo usage" is "SAINT·URBAIN," not "Peak Saunas."** The voice/color guidance reads as sauna-brand-appropriate (colors are literally roled "sauna imagery" and "heat/energy"), so this isn't obviously a wrong file — but a deck that names a different brand's wordmark in its own logo-usage rules is either (a) a copy-pasted template section the agency didn't finish customizing, or (b) evidence the "rebrand" concept actually proposes a new name and this is the first place that surfaces. Confirm which with the agency before anything here is treated as final — this is exactly the kind of detail worth catching before it quietly ships.

## Current live theme vs. the rebrand — side by side

Current values from `tenants/peak-saunas/brand/tokens.json` (the live-theme reference doc), not from this import.

| | Current live | Rebrand toolbox |
|---|---|---|
| Body font | DM Sans | Not named — "Serif (elegant, classical)" (guide description only) |
| Heading font | Poppins | Not named — "Sans-serif (geometric, modern)" (guide description only) |
| Accent | `#16C47F` (green) | `#702B33` (Red) or `#8B3A3A` (Deep Red) — two different vision reads, two different accent candidates |
| Page background | `#ffffff` (white) | `#000000` (black) per the guide's own role labels |
| Primary text | `#000000` (black, on white) | `#FFFFFF` (white, on black) |
| Palette style | Single green accent + a few muted neutrals | 8-colorway system (Basalt/Black/Cedar/Dust/Red/Solar Flare/Stone/White) with 6 named palette colors |
| Logo | `peak-saunas-logo.png` (site CDN) | `.svg` available per variant, 8 colorways each |
| `tenant.yaml` `brand:` section | Does not exist yet (never run before) | Would be created for the first time |

## Contrast results (WCAG)

Computed with `harness.brand_import.contrast_ratio` (the same function the importer's own accent gate uses; `ACCENT_CONTRAST_MIN = 3.0`, `TEXT_CONTRAST_MIN = 4.5`):

| Pair | Ratio | Passes accent min (3:1)? |
|---|---:|:---:|
| Current live accent `#16C47F` on white | 2.27:1 | **No** — the current live accent itself fails the importer's own gate |
| Colors-doc accent Red `#702B33` on white | 10.08:1 | Yes |
| Colors-doc accent Solar Flare `#F27046` on white | 2.92:1 | **No** (just under) |
| EON-deck accent Warm Brown `#8B6F47` on white | 4.71:1 | Yes |
| EON-deck accent Deep Red `#8B3A3A` on white | 7.60:1 | Yes |
| White text `#FFFFFF` on EON-deck black bg `#000000` | 21.00:1 | Yes (max possible) |
| White text on Colors-doc dark `#181918` (Basalt) | 17.63:1 | Yes |
| Colors-doc neutral Fossil Dust `#C0C8C3` on white | 1.71:1 | No (not proposed as a text/accent color, just a light neutral) |
| Colors-doc neutral Stone `#EFE3D2` on white | 1.27:1 | No (same — light neutral, not accent) |
| Colors-doc neutral Cedar `#483215` on white | 12.05:1 | Yes |

Worth surfacing on its own: **the site's current live accent green fails the 3:1 accent-contrast floor this same importer enforces on anything it writes.** If this rebrand's accent is applied later, several of the candidates above clear that bar more comfortably than what's live today — a real improvement, independent of whether the rest of the rebrand is adopted.

## How the run actually went (two real, out-of-scope gaps this surfaced)

1. **`Peak_Infos.pdf` is 263.8 MB**, not a small info sheet — almost certainly mislabeled or a huge flattened export. Its single rendered page (100 DPI) is a 9.2 MB PNG, and base64-encoding it for the API inflates that past Anthropic's 10 MB image limit, so the first dry-run attempt aborted with a 400 before any vision call ran. `harness/brand_import.py`'s guide-rendering path has no size guard for this. Fixing that is out of Cycle 35c's approved scope (folder nesting, not guide-image sizing) — this run excluded that one file from automatic guide selection to complete the read against the legitimate 12.8 MB Branding-deck PDF instead, and flags it here as a follow-up. **A human should open `Peak_Infos.pdf` and confirm it's meant to be this large before it's used anywhere.**
2. **32 files under "8 - Logo layout for Merch" misclassify as `guide`.** `classify_file`'s pre-existing fallback ("any `.pdf` not caught by an earlier rule is a guide") isn't folder-scoped, and this cycle's depth-6 walk is what first reaches that folder at all (the old depth-2 cap never got this far). It doesn't change which file gets chosen as the guide (`Peak_Infos.pdf`/the EON deck are both larger), so it's cosmetic here, but it's real: `choose_guide`'s "multiple guide-like files found" note above lists 32 merch-layout PDFs that are not brand guides. Worth a small folder-scoped fix in a future cycle.

Both are genuine findings from testing against real, larger-than-expected data — not something this cycle's nested-folder-walk fix itself got wrong. The fix's own job (folder detection via the real aria-label marker, depth 6, never sending a folder id to the file downloader, `.DS_Store`/zero-byte skip, trial-font refusal) held up across all 475 files with no misfires.

## Decision: what applying this would change, and how to apply it later

Applying this rebrand (`--force`, once a human has picked real fonts, resolved the accent-candidate and colorway questions above, and confirmed the "SAINT·URBAIN" line) would:

- Replace the live green accent (`#16C47F`) with a red-family accent (Red `#702B33` or Deep Red `#8B3A3A` are the two real candidates — pick one, they come from different documents).
- Flip the page theme from light (white bg / black text) to dark (black bg / white text), per the guide's own color roles — a full-site visual shift, not a token tweak.
- Replace `logo.png` with a chosen-colorway `.svg` (currently defaults to Basalt, unconfirmed as the intended primary colorway).
- Add a `brand:` section to `tenant.yaml` for the first time.
- Leave both real font roles unset (no licensed, named typeface was found for either heading or body) until a human supplies one — DM Sans/Poppins would otherwise keep rendering even after `--force`, since there's nothing to replace them with.

**One-command path to apply later**, once the open questions above are resolved:

```
harness brand import --tenant peak-saunas --drive-folder 1D8cG1cAY9sH0_fzMsUhseK_5sSWa_KCD --force
```

`--force` is required because the current `tokens.json`/`base.css` already exist; without it the existing (current) values win and nothing changes. This dry run did not write anything — no code path in `import_brand_kit` touches `tenants/peak-saunas/brand/` or `tenant.yaml` when `dry_run=True`.

---
*Generated from a dry run of `harness brand import` on branch `cycle35c/brand-import-nesting`, executed in a server-side worktree. Real model calls made: 2 (one brand-guide read, one Colors-document palette read) — within this cycle's 4-call cap. No Shopify or Slack calls were made.*
