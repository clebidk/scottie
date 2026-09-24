# Listicle headline templates (cycle 70)

Owner request 2026-09-24: use the 17 "best-performing pre-sell listicle"
headline types as listicle titles. The claims rules of the brand apply to
headlines too.

- Data: `cartridges/listicle/headlines.yaml` (one entry per template).
- Code: `harness/headlines.py` (load and check the file, eligibility,
  selection, writer lines, gate).
- Tests: `tests/test_headlines_cycle70.py`.

## How a run uses it

1. The run resolves its style as before (`--style`, else the seed).
2. When the facts pack exists (`write_pages`), the run finds the templates
   that fit the style and whose evidence the facts pack can cite, then applies
   the tenant's include/exclude. It picks one from the run seed.
   `harness run --headline-template h04` names one; it must fit the style and
   its evidence must exist, or the run stops with a message.
3. The writer gets the template with the harness-filled parts already in
   place (brand, year, verified count, named authority, "Safe"/"Better"), the
   rules for each slot it fills, and the claim ids its items must cite.
4. The gate checks the headline against that template, not the style
   formula. A wrong N is fixed without a model call. A repair message names
   the template id and its pattern.
5. `page.json` and `state.json` (`listicle.headline_template_id`) record the
   id. An A/B/C variant records it too, and
   `harness abtest library --by headline` shows pooled views, CTA clicks and
   CTR per template.

The five style formulas are templates `s-reasons`, `s-mistakes`,
`s-questions`, `s-myths`, `s-tested`. They are always eligible for their own
style and keep the cycle 41 checks. `questions`, `myths` and `tested` have
only their own formula: no new template has those item types.

## Tenant setting

```yaml
cartridges:
  listicle:
    headline_templates:
      include: [s-reasons, h04, h16]   # pin: only these (when eligible)
      exclude: [h08]                   # never these
```

If the filter leaves no template for a style, the run uses the style's own
formula. `--headline-template` wins over include/exclude, never over the
style or the evidence.

## Rules for every template

- N is the item count, written as a numeral.
- No other number, except the run year, a verified count and a plain age
  (template h08). A number needs a verified claim.
- No fear words anywhere in a headline: EMF, radiation, toxins, chemicals,
  off-gassing, mold, germs and the rest of `word_lists.fear` (plus the
  tenant's `emf_terms`).
- The brand name is only in a `<brand>` slot. A `<product>` slot holds the
  category or a model's full name (cycle 64 rule, e.g. "Peak Fuji"); never
  the model alone, never the long catalog title. No other slot names the
  brand or a model.
- No competitor name in any slot (vocab.yaml `banned_names` and
  `competitor_aliases`).
- Problem-type slots (problem, solution, desire, usp, concern, features) are
  everyday and non-medical: no `word_lists.medical` word and no vocab
  trigger word (medical, clinical, study, proven, ...).
- An audience slot is never only "people", "buyers", "shoppers",
  "customers" or "everyone".
- "Game-Changer" (h16) and "Must-Have" (h04) are allowed in that template's
  headline only. The vocab.yaml hype ban stays for all other text.

## The templates

| id | pattern | styles | needs | extra gates |
| --- | --- | --- | --- | --- |
| h01 | N Reasons Why Most \<product_category\> Don't Work for \<problem\> (and How \<brand\> Is Different) | reasons | feature claim, cited by an item | category never a brand |
| h02 | N Reasons Why Everyone's Switching to \<brand\> for \<solution\> in \<year\> | reasons | growth claim, cited by an item | year = run year |
| h03 | N Reasons Why Every \<audience\> Needs This \<product\> for \<problem\> | reasons | - | - |
| h04 | N Reasons \<product\> Is a Must-Have for \<problem\> | reasons | - | "Must-Have" allowed |
| h05 | N Reasons This \<product\> Is Going Viral (and How It Works) | reasons | growth claim, cited by an item | - |
| h06 | N Reasons \<audience\> Started Switching to \<product\> | reasons | - | - |
| h07 | N Ways \<product\> Helps Solve \<problem\>, \<With/Without\> \<usp\> | reasons ("ways" items) | feature claim, cited by an item | no medical words in items |
| h08 | N Reasons People Over \<age\> Are Obsessed With \<brand\> | reasons | - | age = two digits; no medical words in items |
| h09 | N Reasons Why \<count\> \<count_unit\> Switched to This \<product\> | reasons | customer/order count claim, cited by an item | count = verified count rounded down |
| h10 | N Reasons \<audience\> Who Swore They Couldn't \<desire\> Are Choosing \<brand\> | reasons | - | - |
| h11 | N Concerning \<features\> in \<common_solution\>, and a \<Safe/Better\> Alternative | mistakes | "Safe" only with a safety claim, else "Better" | no fear or medical words in items |
| h12 | N Reasons This Is the Only \<product\> Built for \<niche\> | reasons | exclusivity claim, cited by an item | niche = the claim's own niche |
| h13 | N Ways \<product\> Removes Embarrassing \<problem\>, Without \<concern\> | reasons ("ways" items) | - | no medical words in items |
| h14 | N Reasons Why This Breakthrough \<product\> Crushes \<common_solution\> | reasons | feature claim, cited by an item | category never a brand |
| h15 | N Reasons Why \<authority\> Loves \<product\> for \<problem\> | reasons | named endorsement claim, cited by an item | authority = the claim's name |
| h16 | N Reasons Why \<product\> Is a Game-Changer for \<audience\> | reasons | - | "Game-Changer" allowed |
| h17 | N Reasons Why You're Still \<problem\>, Even After Trying \<common_solution\> | mistakes | - | no medical words in items |

h09 note: the owner's pattern has `[Avatar]` after the count. The harness
writes what the verified count counts ("12,000+ Customers"), because
"12,000+ Busy Parents" would state a number the claim does not support.

## Evidence

A verified claim is evidence of a kind when its own `evidence` field names
the kind, when its `category` is listed for the kind, or when its text
matches a pattern in `headlines.yaml`. Tagging the claim is the reliable way.
For example, to make h09 eligible, add a verified claim like:

```json
{"id": "customers-count", "text": "PEAK has delivered saunas to 12,480 customers.",
 "category": "trust", "evidence": "customer_count", "count": 12480, "unit": "customers",
 "source": "...", "approved_by": "...", "date": "..."}
```

The headline then says "12,000+ Customers" (two significant figures, rounded
down). An endorsement claim needs an `authority` field (or text that starts
"Dr. <Name> recommends/endorses ..."). A review count is not a customer
count.

## Eligible for PEAK today

From `tenants/peak-saunas/claims/verified.json` (140 claims, 2026-09-24):

| id | styles | eligible | why |
| --- | --- | --- | --- |
| s-reasons | reasons | yes | no extra evidence needed |
| s-mistakes | mistakes | yes | no extra evidence needed |
| s-questions | questions | yes | no extra evidence needed |
| s-myths | myths | yes | no extra evidence needed |
| s-tested | tested | yes | no extra evidence needed |
| h01 | reasons | yes | spec claims (e.g. spec-fuji-capacity) |
| h02 | reasons | no | no growth or popularity claim |
| h03 | reasons | yes | no extra evidence needed |
| h04 | reasons | yes | no extra evidence needed |
| h05 | reasons | no | no growth or popularity claim |
| h06 | reasons | yes | no extra evidence needed |
| h07 | reasons | yes | spec claims |
| h08 | reasons | yes | no extra evidence needed |
| h09 | reasons | no | no customer/order count (the Judge.me review count is not one) |
| h10 | reasons | yes | no extra evidence needed |
| h11 | mistakes | yes | says "a Better Alternative": no safety certification claim |
| h12 | reasons | no | no exclusivity claim |
| h13 | reasons | yes | no extra evidence needed |
| h14 | reasons | yes | spec claims |
| h15 | reasons | no | no named endorsement (founder-ceo is the company's own CEO; pwc-expert-protocols names no person) |
| h16 | reasons | yes | no extra evidence needed |
| h17 | mistakes | yes | no extra evidence needed |

A `reasons` run picks from 11 templates (s-reasons, h01, h03, h04, h06, h07,
h08, h10, h13, h14, h16); a `mistakes` run from 3 (s-mistakes, h11, h17).
