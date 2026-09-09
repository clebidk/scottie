"""One Claude call per cartridge: (ad_brief, facts_pack) -> page.json.

System prompt = cartridge.md + schema.json + a global voice block.
User message = ad_brief + facts_pack + up to 2 exemplars if present.
"""
import json
import re
from pathlib import Path

from .jsonutil import extract_json
from .vocab import (
    ALLOWED_FINANCING_SENTENCE_NO_LENDER,
    BANNED_NAMES,
    EMF_TERMS,
    FORBIDDEN_LENDER_NAMES,
    HYPE_WORDS,
    TRIGGER_WORDS,
    forbidden_words_block,
)

TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}

# Fix cycle 4 item 4: these sentences are generated from adv/vocab.py's word
# lists so the writer prompt and claims.gate_page_json's forbidden-term check
# can never drift apart -- add a word in one place (vocab.py) and both sides
# pick it up.
_HYPE_WORDS_LIST = ", ".join(f'"{w}"' for w in HYPE_WORDS)
_EMF_UPPER = EMF_TERMS[0].upper()
_FIRST_BANNED_NAME = BANNED_NAMES[0].title()
_OTHER_BANNED_NAMES_LIST = ", ".join(n.title() for n in BANNED_NAMES[1:])
_LENDER_NAMES_LIST = ", ".join(n.title() for n in FORBIDDEN_LENDER_NAMES)
_TRIGGER_WORDS_LIST = ", ".join(f'"{w.upper() if w == "emf" else w}"' for w in TRIGGER_WORDS)

GLOBAL_VOICE_BLOCK = f"""## Voice and output rules

Voice: plain, specific, no hype words ({_HYPE_WORDS_LIST}). No exclamation marks. Prefer short declarative sentences. "Unlock" is the one writers reach for most often without noticing, in two different situations: (1) a feature that isn't gated behind an upgrade or extra payment -- say "included standard", "there's no extra step", or "it's included, not an add-on" instead; (2) information (like a price) that isn't gated behind a form or a sales call -- say "nothing to submit first", "no form required to see it", or "it's just on the page" instead of "nothing to unlock" / "unlock the price".

Never write the byline, publish/update dates, the "Advertisement" label, or the disclosure paragraph -- the renderer injects those automatically.

Every piece of text that states a number, a percentage, a dollar amount, or uses the words {_TRIGGER_WORDS_LIST} MUST carry a non-empty "claim_ids" array referencing an id from facts_pack.verified_claims. Never invent a claim id. If you cannot support a statement with a verified claim, do not make the statement. Each row in facts_pack.specs already carries its own "claim_id" -- when you restate a spec fact in prose (not just in a specs table), copy that same claim_id into the prose sentence's claim_ids array; do not state a spec number in prose without it. The id goes in that JSON array field ONLY -- never typed out as part of the sentence itself, in parentheses or otherwise. WRONG: "the price, $8,250, is listed on the product page (price-fuji)" or "a heater system built around full-spectrum infrared (spec-fuji-infrared-wavelength-range)". RIGHT: drop the parenthetical entirely and put "price-fuji" / "spec-fuji-infrared-wavelength-range" in that sentence's own "claim_ids" array instead -- a claim id is never something a reader sees. This includes an illustrative or hypothetical number used to make a rhetorical point ("a $50 impulse buy versus an $8,000 purchase", "a standard 2-person cabin") -- there is no claim_id for a made-up example, so make the same point in words instead ("a small impulse buy" vs. "a major purchase"). It also includes the current year or any other calendar year stated in your own narration as color commentary (e.g. "a reasonable thing to want in 2026") -- there is no claim_id for a bare year either; drop it or put it inside a direct quote credited to the ad speaker instead.

Reference images only by an asset id from facts_pack.assets, in an "asset_id" field -- never by URL directly. Never write your own "alt" field for an image -- the renderer derives alt text on its own.

Write a dollar amount exactly as it appears in the source claim's text (e.g. "$8,250", no ".00" unless the claim's own figure has non-zero cents) -- never reformat it, and never add a ".00" that isn't in the claim text.

Never write a URL anywhere in body text (prose, headings, alt text, quotes). Cite a source inline as "(source name, year)" -- e.g. "(Peak Saunas product page, 2026)" -- using a short human-readable name for the source, never the raw URL. The renderer builds the Sources list and its links on its own from claim_ids; the URL never needs to appear as text you write.

Claim ids never appear in any text field. Cite in prose only as (source name, year). Put ids only in claim_ids -- never in a headline, paragraph, label, or quote, even in parentheses next to the source name.

If ad_brief.speaker_pov is "first_person", never write the speaker's story in the page author's own first-person voice ("I ran into this...", "it made my mornings better"). Attribute it instead to "a customer" -- or to the name in facts_pack.speaker_name if that field is non-null -- e.g. "One customer told us she..." or a short quoted line clearly credited to that customer. The page author (Austin) never speaks in the ad speaker's first person.

If the user message includes "exemplars", use them only as a voice and structure reference. A JSON exemplar shows the page.json shape; a {{"reference_article": "..."}} exemplar is a real published Peak Saunas article -- match its tone and rigor, but never copy its numbers, claims, or competitor comparisons into this page unless the same fact also appears in this page's own facts_pack.verified_claims.

## Guardrails
Never write "{_EMF_UPPER}" in any form, anywhere, including as an abbreviation inside a claim -- this applies even to facts_pack's own internal-only EMF testing data. Never write "{_FIRST_BANNED_NAME}". Refer to the competitor as "Sun", never "Sun Home". Never name {_OTHER_BANNED_NAMES_LIST} (discontinued Peak models). Never claim third-party or accredited-laboratory testing of any kind. Competitor statements are only ever the speaker's own experience, never a sourced fact about a competitor, unless a verified claim covers it.

Financing: use facts_pack.product.financing. If financing.lender is null, the ONLY sentence you may write anywhere on the page to mention financing is exactly "{ALLOWED_FINANCING_SENTENCE_NO_LENDER}" -- verbatim, nothing added before or after it in that field, no lender name, no monthly figure, no other financing phrasing anywhere else on the page. Never name a financing lender ({_LENDER_NAMES_LIST}, or any other) unless financing.lender is non-null and IS that name.

Compare-at / list price: only mention a "was $X" / compare-at / strikethrough price if facts_pack.product.compare_at_price is non-null. If it is null, state only the current price.

Warranty: always say "limited lifetime warranty" -- never bare "lifetime warranty".

Reviews: use facts_pack.reviews_summary and its claim_ids exactly as given. If facts_pack.reviews_summary is null, do not state any review count or star rating anywhere on the page -- never use the placeholder figures "9,000", "10,000", or "4.9" for a review count or rating. The word "reviews" itself is not banned, but it always needs a claim_id -- this trips writers repeatedly in generic buyer-education prose that has no claim_id to give it, e.g. "look at ratings and reviews from other buyers", "a star average built on a handful of reviews". Say "customer feedback" or "what other buyers say" instead in that kind of sentence -- every time, not just the first draft -- unless you are citing facts_pack.reviews_summary's actual claim_id.

Refer to the product by facts_pack.product.short_name, not facts_pack.product.name alone and never by a raw marketing title -- e.g. "Peak Fuji 2-Person Infrared Sauna", not "Fuji" or a Shopify product title, on FIRST mention in each major section (hero, each FAQ answer, each step, etc.). The short_name itself contains a digit (its capacity, e.g. "2-Person") -- any sentence that uses the full short_name needs a claim_id too; put the matching capacity spec row's claim_id from facts_pack.specs into that sentence's "claim_ids" array (the JSON field), every single time you write the short_name out, including in an FAQ answer or a step that isn't otherwise about specs -- the id itself is never printed as text next to the short_name or anywhere else. WRONG: "the Peak Fuji 2-Person Infrared Sauna (spec-fuji-capacity), which is priced at $8,250" -- the id in parentheses is a bug, not a citation. RIGHT: "the Peak Fuji 2-Person Infrared Sauna, which is priced at $8,250" with "claim_ids": ["spec-fuji-capacity", "price-fuji"] on that sentence's own JSON node; no parenthetical at all unless it's a plain-English "(source name, year)" citation. To avoid re-triggering this on every sentence (and to avoid sounding like a repeated ad slogan), after that first mention in a section just say "the sauna" or "this model" for the rest of that section -- you don't need the full short_name, or a claim_id, again until the next section.

Output ONLY a single JSON object matching the schema you were given. No markdown fences, no commentary before or after."""


def validate_schema(data, schema):
    if not isinstance(data, dict):
        return ["page.json is not a JSON object"]
    errors = []
    for key in schema.get("required", []):
        if key not in data:
            errors.append(f"missing required key: {key}")
    for key, prop in schema.get("properties", {}).items():
        if key in data and isinstance(prop, dict):
            typ_name = prop.get("type")
            expected = TYPE_MAP.get(typ_name)
            if expected and not isinstance(data[key], expected):
                errors.append(f"key {key!r} expected type {typ_name}, got {type(data[key]).__name__}")
    return errors


def load_cartridge_prompt(cartridge_dir):
    cartridge_md = (cartridge_dir / "cartridge.md").read_text()
    schema = json.loads((cartridge_dir / "schema.json").read_text())
    return cartridge_md, schema


# Fix cycle 4 item 2: parsed once from cartridge.md's own "N-M words" Rules
# line (article/longform/product-page all state it the same way) rather than
# hardcoding the range a second time anywhere else. Matches an en dash or a
# hyphen between the two numbers.
_WORD_RANGE_RE = re.compile(r"([\d,]+)\s*[–-]\s*([\d,]+)\s*words")


def parse_word_range(cartridge_md):
    """(min_words, max_words) parsed from cartridge_md's "N-M words" rule, or
    None if the pattern isn't found."""
    m = _WORD_RANGE_RE.search(cartridge_md)
    if not m:
        return None
    return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))


def word_range_target(word_range):
    """A word count comfortably clear of the minimum without demanding a big
    expansion -- a quarter of the way into the range, not the midpoint. Used
    by both the writer prompt (aim here) and the gate's failure message
    (expand toward here) so a repair asks for a modest, achievable amount of
    new content rather than tempting a rewrite big enough to introduce a
    fresh violation elsewhere."""
    lo, hi = word_range
    return lo + (hi - lo) // 4


# Fix cycle 4 item 3: schema.json's "allowed_cta_texts" is a list of CTA
# templates, e.g. "Shop the {short_name}". The writer has to submit the
# already-substituted, concrete text in page.json (there's nowhere else for
# the placeholder to be filled in before then), so this substitutes the
# product's actual short_name once, here -- both the prompt (the writer's
# menu of choices) and the gate (what it compares cta_text against) call this
# same function, so they can't end up comparing against different strings.
def resolve_allowed_cta_texts(schema, short_name):
    templates = schema.get("allowed_cta_texts") or []
    return [t.format(short_name=short_name) for t in templates]


def load_exemplars(cartridge_dir, limit=2):
    """Up to `limit` exemplars from cartridges/<name>/exemplars/. A .json file
    is parsed as a page.json-shaped object; a .md/.txt file is a real
    reference article and is passed through as text (voice/structure
    reference, not something to copy verbatim)."""
    ex_dir = Path(cartridge_dir) / "exemplars"
    if not ex_dir.exists():
        return []
    files = sorted(ex_dir.glob("*.json")) + sorted(ex_dir.glob("*.md")) + sorted(ex_dir.glob("*.txt"))
    exemplars = []
    for f in files[:limit]:
        if f.suffix == ".json":
            exemplars.append(json.loads(f.read_text()))
        else:
            exemplars.append({"reference_article": f.read_text()})
    return exemplars


def write_page(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, client, model, budget, log,
               word_range=None, allowed_cta_texts=None, revision_note=None):
    """word_range (min, max), allowed_cta_texts (resolved, concrete strings),
    and revision_note (fix cycle 4 item 1: a "REVISION REQUIRED" block from a
    prior failed gate check on this same cartridge, appended to the user
    message so the writer sees exactly what to fix) are all optional -- a
    caller that doesn't pass them gets the pre-cycle-4 behavior."""
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md, schema = load_cartridge_prompt(cartridge_dir)
    # A repair attempt (revision_note set) already saw the exemplars on the
    # first attempt -- it needs to fix specific flagged issues, not re-learn
    # voice/structure, and exemplars are the single largest piece of a call's
    # input tokens (up to ~45KB of reference text). Skipping them on repairs
    # buys real budget headroom for the repair loop without changing what
    # the writer is told to fix.
    exemplars = load_exemplars(cartridge_dir) if not revision_note else []

    hard_constraints = []
    if word_range:
        lo, hi = word_range
        target = word_range_target(word_range)
        hard_constraints.append(
            f"Body word count must be between {lo} and {hi} -- aim for roughly {target} words, "
            f"not the bare minimum of {lo}. Undershooting {lo} fails review and sends this "
            "back for a full rewrite, which costs more than writing enough the first time, so "
            "give each section real substance (concrete detail, not padding) rather than "
            "stopping as soon as the structure is technically complete. Count words in section "
            "bodies only -- headings, urls, asset ids, claim ids, and the cta_url are not part "
            "of the count."
        )
    if allowed_cta_texts:
        options = "; ".join(f'"{t}"' for t in allowed_cta_texts)
        hard_constraints.append(
            f"The CTA text must be exactly one of: {options}. Do not invent any other CTA wording."
        )

    # Fix cycle 6 item 3: the forbidden-word list, verbatim, goes at the very
    # top of the system prompt (and again inside every REVISION REQUIRED
    # block below) -- observed cycling on the hidden-costs-v2 verification
    # run where a repair attempt fixed one forbidden word but reintroduced
    # another two attempts later.
    system = (
        forbidden_words_block()
        + "\n\n"
        + cartridge_md
        + "\n\n## JSON schema for page.json\n"
        + json.dumps(schema, indent=2)
        + "\n\n"
        + GLOBAL_VOICE_BLOCK
    )
    if hard_constraints:
        system += "\n\n## Hard constraints for this run\n" + "\n".join(f"- {c}" for c in hard_constraints)

    user_payload = {"ad_brief": ad_brief, "facts_pack": facts_pack}
    if exemplars:
        user_payload["exemplars"] = exemplars

    user_content = json.dumps(user_payload)
    if revision_note:
        user_content += "\n\n" + revision_note

    stage = f"write.{cartridge_name}"
    last_error = None
    for attempt in range(2):
        budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=6000,
            # This is bounded JSON extraction, not a reasoning task -- disable
            # thinking so the full max_tokens budget goes to visible output.
            # Sonnet 5 runs adaptive thinking by default when unset, and
            # thinking tokens count against max_tokens; without this the
            # model can exhaust the budget on hidden reasoning and return an
            # empty/truncated response (observed in practice on longform).
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        usage = response.usage
        budget.record_call(usage.input_tokens, usage.output_tokens)
        log.call(stage, model, usage.input_tokens, usage.output_tokens)

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            page = extract_json(text)
            errors = validate_schema(page, schema)
            if errors:
                raise ValueError("; ".join(errors))
            return page
        except Exception as e:
            last_error = e
            log.event(stage, f"invalid page.json on attempt {attempt + 1}: {e}")
            continue

    raise ValueError(f"{stage} failed after retry: {last_error}")
