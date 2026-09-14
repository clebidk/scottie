# Review set — 2026-09-14

Source: `harness` at master `983a086` (cycles 30-34 merged: warm-up fix, images, simplicity gate, all swarm packages).
Tenant: peak-saunas. All runs foreground, one at a time, default budget cap.
Spend for this set: **$2.71** ($0.7629 -> $3.4717 on `harness spend --tenant peak-saunas`; above the ~$2.50 target, mostly from the five STOP attempts below, which still consume tokens before failing).

## Run table

| Ad | Cartridges | Run ID(s) | PASS/STOP | Attempts | Cost (est.) |
|---|---|---|---|---|---|
| hidden-costs-v2 | product-page, article, longform | `20260914-205949-hidden-costs-v2-hyhf` | PASS | 1 | $0.2342 |
| hidden-costs-v2 | listicle | `20260914-213100-hidden-costs-v2-wl7l` | PASS | 1 | see run's REVIEW.md |
| product-features-v2 | product-page, article, longform | `20260914-210443-product-features-v2-mkju` (STOP), `20260914-210859-product-features-v2-awiq` (STOP) | STOP x2 — no PASS, not retried a third time | 2 | n/a — no passing pages produced |
| product-features-v2 | listicle | `20260914-213234-product-features-v2-f55k` (STOP), `20260914-213446-product-features-v2-4qpj` (PASS) | PASS on retry | 2 | see run's REVIEW.md |
| price-comparison-v2 | product-page, article, longform | `20260914-211229-price-comparison-v2-qdvg` | PASS | 1 | $0.2351 |
| still-lessthan300-4x5 | product-page, article, longform | `20260914-211540-still-lessthan300-4x5-siai` | PASS | 1 | $0.1868 |
| still-levelup-4x5 | product-page, article, longform | `20260914-211749-still-levelup-4x5-ldxk` | PASS | 1 | $0.1919 |
| still-infraredglow-4x5 | product-page, article, longform | `20260914-212001-still-infraredglow-4x5-ivnk` (STOP), `20260914-212216-still-infraredglow-4x5-kaul` (PASS) | PASS on retry | 2 | $0.3152 |
| still-unforgettable-4x5 | product-page, article, longform | `20260914-212606-still-unforgettable-4x5-o24u` (STOP), `20260914-212840-still-unforgettable-4x5-t4zm` (PASS) | PASS on retry | 2 | $0.2282 |

**STOPped twice with no PASS:** product-features-v2, default 3-cartridge run. Both STOPs were claims-gate failures on the `article` page over uncited claims ("medical" in attempt 1, a specific electrical-circuit claim in attempt 2) — a different sentence each time, i.e. writer non-determinism, not a config error. Per the task rule (rerun once, record both, do not force a third attempt) this ad has no product-page/article/longform pages in this review set — only its listicle passed, on the second attempt.

## Per-page detail

### hidden-costs-v2 — default (hyhf)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 279 | n/a | WARN: headline 12 words (band 4-10) | 3 | none | Financing is available through Bread Pay at checkout. | 0 | 592,296 B |
| article | 1173 | word 1102 | PASS | 3 | n/a (article template has no hero_image field) | same | 0 | 670,101 B |
| longform | 860 | n/a | PASS | 8 | none | same | 0 | 1,025,481 B |

### hidden-costs-v2 — listicle (wl7l)
| Page | Words | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|
| listicle | 776 | PASS | 7 | none | same | 0 | 1,699,085 B |

### product-features-v2 — listicle (4qpj, after 1 STOP)
| Page | Words | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|
| listicle | 647 | PASS | 6 | none | same | 0 | 1,136,455 B |

### price-comparison-v2 (qdvg)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 310 | n/a | WARN: headline 11 words (band 4-10) | 3 | none | same | 0 | 559,637 B |
| article | 1233 | word 1158 | PASS | 3 | n/a | same | 0 | 488,961 B |
| longform | 956 | n/a | PASS | 9 | none | same | 0 | 553,729 B |

### still-lessthan300-4x5 (siai)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 298 | n/a | WARN: headline 12 words (band 4-10) | 3 | none | same | 0 | 673,570 B |
| article | 1202 | word 1107 | PASS | 3 | n/a | same | 0 | 521,650 B |
| longform | 967 | n/a | PASS | 8 | none | same | 0 | 1,019,478 B |

### still-levelup-4x5 (ldxk)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 278 | n/a | PASS | 3 | none | same | 0 | 660,477 B |
| article | 1171 | word 248 | PASS | 3 | n/a | same | 0 | 648,150 B |
| longform | 945 | n/a | PASS | 10 | none | same | 0 | 1,214,354 B |

### still-infraredglow-4x5 (kaul, after 1 STOP)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 329 | n/a | PASS | 3 | none | same | 0 | 781,313 B |
| article | 1465 | word 443 | PASS | 3 | n/a | same | 0 | 600,370 B |
| longform | 857 | n/a | PASS | 6 | none | same | 0 | 261,166 B |

### still-unforgettable-4x5 (t4zm, after 1 STOP)
| Page | Words | 1st brand mention | Simplicity | Images | Hero | Financing | Banned-term* | Review HTML |
|---|---|---|---|---|---|---|---|---|
| product-page | 331 | n/a | PASS | 3 | none | same | 0 | 660,864 B |
| article | 1106 | word 1026 | PASS | 3 | n/a | same | 0 | 541,145 B |
| longform | 964 | n/a | WARN: headline 13 words (band 6-12) | 8 | none | same | 0 | 1,019,732 B |

\* Banned-term (EMF) count is 0 in rendered body copy on every page. The Fuji product's real Shopify URL slug is "...-near-zero-emf-full-spectrum-..." (Shopify's own product handle) and that string appears in hrefs, image srcset filenames, and JSON-LD url fields on every page. That is unavoidable (it is the live product URL) and is not a violation of the no-EMF-mentions rule, which governs generated prose/claims, not third-party URL slugs. Verified by stripping href/src/srcset/JSON url attributes before counting, then checked the sole remaining hit (the product URL) by hand.

"Hero" column: no page in this set has a populated hero_image.asset_id (soft-check warning on every product-page/longform/listicle page); images are inline body images only, not a distinct hero slot. The article template carries no hero_image field at all.

## "Not repeated" lists

No REVIEW.md in any of the nine runs contains a "not repeated" section. Checked every run's REVIEW.md headers: Ad claims matched, Claims used, Sources, Assets used, Pages, Word counts, Warm-up window, Simplicity, Soft-check warnings, Review checklist, Banned-topic handling, Gate history, Budget use, Token totals — none of them is a not-repeated list. Flagging this rather than fabricating content: if "not repeated" refers to something else (e.g. a dedup/anti-repeat log elsewhere in the harness), point me at it and I will fold it in.

## Known caveat

`20260914-205949-hidden-costs-v2-hyhf` was created at 20:59:49 UTC, 11 seconds before the stated 21:00 UTC archive-agent cutoff. Verified still intact when this doc was written; its review HTML is also copied into `tenants/peak-saunas/out/REVIEW-SET-2026-09-14/`, which is itself excluded from archiving by name.
