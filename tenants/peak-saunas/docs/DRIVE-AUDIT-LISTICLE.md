# Drive Audit: Listicle Image Pack

Source folder: `1TJIKdHrD4AheKYq-0D4dLnbGfZrFwIVA` (flat, no subfolders). Indexed read-only via Drive search API, classified from filenames only (no pixel content read). Full index: `brand/assets-listicle-pack.json`.

## Counts

**Total: 105 files.**

By kind:
- `photo_product`: 61 (studio shots, `MINI__MG_*` / `Matterhorn__MG_*`)
- `ai_render`: 34 (Firefly/Gemini/gpt-image composites, filenames start `AIrender`)
- `still_video`: 7 (`Stills_*` — office views, sauna stills, spring water)
- `photo_install`: 3 (filenames contain "install")

By model (derived from filename tokens):
- `matterhorn`: 34
- `mini`: 30
- `other` (named model, not mini/matterhorn — Denali, Patagonia): 2
- unresolved (generic/uuid AI-render filenames, no model token): 39

42 of 105 files exceed 6 MB and need downscaling before use in the generator.

Public download check: `Stills_Office Views Still 004.jpg` (1.55 MB) downloaded directly via `https://drive.google.com/uc?export=download&id={id}` — confirmed real JPEG (3840x2160, matched byte size), not an HTML interstitial. `public_download: true`.

## Role map for 5-item listicle

Preference given to real photos over AI renders. IDs from `assets-listicle-pack.json`.

- **hero**: `103mRQ0ggSpI2DSP9k0Iq-4QQKw5u8kc5` (Matterhorn__MG_3224), `17UghmkrmU613OIfIQmb3zi01bRb_S_9z` (MINI__MG_1225)
- **item 1**: `1YvQiz1LX2JWV0HZqSrp2L4K2uqKtMV2z` (Matterhorn__MG_3225), `1j_g3K3n92E0vwOtIlMmiDFT6md7G9-rb` (MINI__MG_1232)
- **item 2**: `1pkPaqS112ICwntLWfcAgYPirXaO_IF7C` (Matterhorn__MG_3226), `13WAP9ZyCybiItJlTRBKIImsnHmVl7Xu7` (MINI__MG_1236)
- **item 3**: `1rd4yQqf7n364QHq_tdl8MZfUiMHz1TRX` (Matterhorn__MG_3250), `1xMh3Eg_iDOyCh1u-RSxOe1IOF5uuFWD_` (MINI__MG_1250)
- **item 4**: `1HAFDUAO6w3tQScjQHW_GoROzzya9NrYx` (Matterhorn__MG_3254), `1-Ztvy-rZJuGV9-SgLz_dezzkRtzngbhT` (MINI__MG_1251)
- **item 5**: `1gq9T5rUY5H_bG3wzQ_WL0gK5MmJv3YD8` (Matterhorn__MG_3260), `1V97OlBSR4e6XGbBXAk2HlcMlU2v0NDQH` (MINI__MG_1253)
- **proof** (real installation): `13PAv9Jg94HrZkbhAxZ0wb3RGl0xOVZ8O` (MINI-install_10), `1MmsnMm5DXyLLohAJvoo8bYVodFWndytM` (Matterhorn-install_09), `11xw6CaHNI6TNb9ezBCUBoXTS6d5jG47n` (MINI-install_09-2)
- **lifestyle**: `14hQUSLvpiyd0C7bdNd9zanxW9kLn6xBH` (Stills_Sauna Still 01), `1QfwGYz7-quEi--33ALSizCoX4OJxlxiD` (Stills_Sauna Still 02), `1lqFnG3kksIql5fsQCyqubUOMKgt5Akd8` (Stills_Office Views 001)
- **closing**: `1r21HEkQa-ujinCifPPUglF01_22mOSbg` (Stills_Spring Water Still 007), `190IbxNZfKdZIXJX_c8_hij-QTuddVE5X` (Stills_Office Views 003)

## AI renders: policy decision needed

34 files (32%) of this pack are AI composites (Firefly/Gemini Flash/gpt-image), not photographs. They show a real sauna dropped into a rendered home, gym, bathroom, or poolhouse — the room is generated, not photographed. Every `ai_render` record is flagged `ai_generated: true` in the JSON.

Recommendation: do not use these as evidence of a real installation. If used at all (e.g., to illustrate a placement idea), the alt text must say "rendering" or "concept render" explicitly, never "customer install" or "in a customer's home." This needs a sign-off decision before the generator is allowed to pull from `kind: ai_render`.

## Gaps

- No Fuji or Everest units in this pack — only Mini, Matterhorn, and two singleton AI renders of Denali/Patagonia. Item slots referencing those models have no photo source here.
- Only 3 real install photos total, all Mini/Matterhorn. No verified people-in-sauna photos — filenames don't indicate people, and pixel content wasn't inspected (out of scope), so this is unconfirmed rather than ruled out.
- 39 AI renders have no resolvable model from the filename (generic "Firefly_Gemini Flash" or UUID names) — unusable for model-specific slots without manual review.

## Comparison with legacy pack (`reference/peak-listicle-lp/assets`, 7 files)

- `hero_mini_sauna.png` — **replaceable**: pack has 28 real Mini studio shots vs. one legacy hero.
- `sauna_interior.png`, `sauna_lifestyle.jpg` — **candidate replacement, unverified**: pack has product and lifestyle stills that may match, but content wasn't pixel-checked.
- `sauna_stepping_in.jpg` (implies a person) — **not replaceable**: no confirmed people-in-sauna photo in this pack.
- `red_light_panel.png` — **not replaceable**: no accessory-specific asset in this pack.
- `free_shipping.jpg`, `peaks_logo.png` — **out of scope**: promo graphic and logo, not product photography; this pack doesn't cover them.
