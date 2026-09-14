# DESIGN.md reference pack

Independent third-party analyses of ten public marketing sites, pulled from the
getdesign.md catalog (VoltAgent/awesome-design-md, MIT) with `npx getdesign add <slug>`.
Provenance and license: SOURCE.json and LICENSE in this folder.

| Slug | Visual language it documents |
|------|------------------------------|
| shopify | commerce marketing site, product-led sections |
| tesla | product photography, radical subtraction |
| nike | athletic, big type, hero photography |
| airbnb | lifestyle photography, warm rounded UI |
| apple | premium whitespace, photography-first |
| stripe | elegant marketing weight |
| webflow | polished marketing site |
| framer | motion-first, design-forward |
| notion | warm editorial minimal |
| clay | art-directed agency feel |

## How the harness uses these

These files are evidence for the design-skills adapter, in the same take / adapt /
decline discipline as the vendored skill. A tenant may name one or more slugs as
`design_reference` in its tenant.yaml; the adapter takes structural and rhythm rules
(section order, whitespace, photo-first hero, type scale ratios) and declines the
rest. The tenant brand layer always owns fonts and colors. Nothing in these files is
copy or a claim, and no value in them is applied as a tenant token.

Each DESIGN.md is kept byte-identical to the catalog version. Do not edit them;
re-pull to update and record the new date in SOURCE.json.
