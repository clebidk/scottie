# DESIGN.md pack for Peak listicle / LP harness

Source tweet: https://x.com/Manixh02/status/2099337573160423843 (2026-09-14)

## Catalog sites from the post
| Site | Role |
|------|------|
| https://getdesign.md | Primary free catalog + `npx getdesign` (VoltAgent/awesome-design-md) |
| https://designmd.me | Generate DESIGN.md from any URL (credits) |
| https://designmd.supply | DESIGN.md marketplace (Vercel checkpoint from box) |
| https://typeui.sh | Type/UI systems (Vercel checkpoint from box) |
| https://styles.refero.design | Refero style refs |
| https://collectui.com | UI inspiration gallery |
| https://designmd-store.com | Paid DESIGN.md store |
| https://niblet.com | Related tooling |

Pulled via `npx getdesign add <slug>` into this folder. Independent analyses, not official brand docs.

## Pack (Peak LP / listicle relevant)
| Slug | Why for Peak |
|------|----------------|
| `shopify/` | E-com DNA (Peak sells on Shopify) |
| `tesla/` | Product photo, radical subtraction |
| `nike/` | Athletic wellness, big type, hero photo |
| `airbnb/` | Lifestyle photography, warm rounded UI |
| `apple/` | Premium whitespace |
| `stripe/` | Elegant marketing weight |
| `webflow/` | Polished marketing site |
| `framer/` | Motion-first design-forward LP |
| `notion/` | Warm editorial minimal |
| `clay/` | Art-directed agency feel |

Each folder has `DESIGN.md`. Point the harness at one (or blend) before generating HTML.

## Harness usage
```
Read design-md/<slug>/DESIGN.md before any UI.
Match Peak Saunas DNA from peaksaunas.com + Drive assets; DESIGN.md is visual language only — not copy or claims.
```

## Not pulled
- Private Peak DESIGN.md from designmd.me (needs credits + peaksaunas.com URL) — do next if you want a Peak-native token file.
