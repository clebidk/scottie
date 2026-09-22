# Live PDP structure (measured 2026-09-22)

Source: `curl -A "Mozilla/5.0"` of the live Fuji PDP
(`/products/peak-saunas-fuji-2-person-...-red-light-therapy`, 1.06 MB HTML).
Measured for STRUCTURE only. The live theme's colours and fonts are legacy;
the `pdp` look takes its colours, type and shape from `brand/base.css` only.

## Section order (Shopify sections, top to bottom)
1. `pk-outcome-headline` -- promise band "Longer Life. Deeper Sleep. More
   Energy." plus three research statistics (not in verified claims).
2. `main` -- gallery (left) + buy box (right). 21 distinct product images in
   the gallery markup (`pk-gmedia`); main image + thumbnail strip.
3. Judge.me carousel (`jdgm-*`) -- "Let customers speak for us", review quotes.
4. `product-details` -- "Overview & What's Included" long copy.
5. Custom liquid -- Peak Wellness Club block.
6. Judge.me full widget -- star histogram + review list.
7. `custom_liquid` -- "The Peak Difference / Why buyers choose Peak over the
   legacy brands": 6 tiles + head-to-head table against "legacy brands".
8. `faq` -- accordion (order process, cord length, chromotherapy, ...).
9. Recently viewed.

## Buy box contents (in order)
- Badge "Bestseller", eyebrow line, H1 "Fuji 2-Person Infrared Sauna".
- Rating: 5 stars, "4.79", "4,158 reviews" (store-wide count; the product's
  own verified count is 47 -- `reviews-live`).
- One-line descriptor (capacity, spectrum, red light, app control).
- Stock/lead time: "In stock · Free delivery by <date> · order within <timer>".
- Pay tabs `pk-tabs-wrap`: Pay now / Finance / truemed HSA-FSA.
- Price "$8,250" + "$14,032 value" + "$5,782 total savings" (a value stack).
- CTA buttons: "Checkout — $8,250", "Add to cart"; lender modals with
  monthly figures and APR.
- Included-extras list `pk-gift` (red light panel, club, crate, gift box).
- Timeline `pk-tl` (order today -> ships -> arrives).
- Trust `pk-trust`: "7-Year Warranty", "U.S. support".
- Call block (phone number), doc buttons "Full specifications", "Owner's manual".
- Model switcher buttons (Everest, Rainier, Matterhorn, ...).

## Spec table (`pk-specs`, id `specifications`)
Intro "Every number published -- nothing hidden behind a sales call." Rows:
Capacity, Placement, Wood, Infrared (range nm), Max Temperature, Heating
Panels, Red Light Therapy (panel detail), Red Light Programs, Control, ...
Two columns (label / value), thin rules.

## What the `pdp` look takes, and what it does not
Takes: two-column gallery + sticky buy box above the fold; thumbnail strip;
rating line near the H1; price, pay line, CTA, included list, lead-time
line, warranty line in the buy box; a proof/tiles band; a spec table with
thin rules; a model comparison table; a reviews summary; an FAQ accordion; a
repeat buy band; a sticky mobile bar.

Does not take (claims rules, `claims/config.json`, cartridge rules):
- the value stack and "total savings" (`show_compare_at_price: false`);
- the delivery-date countdown and "LIMITED TIME" (no urgency);
- lender monthly figures and APR (fixed financing sentence only);
- "7-Year Warranty" (fixed warranty sentence only);
- the store-wide "4,158 reviews" (verified product rating only, 25-review floor);
- Judge.me review quotes (only when `facts_pack.review_quotes` exists);
- the research statistics band (no verified claim ids);
- the "legacy brands" head-to-head (never competitor claims) -- the look's
  compare table shows the tenant's own models only;
- phone number, recently viewed, club upsell, gift box (not in facts_pack).
