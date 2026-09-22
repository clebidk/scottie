"""One Claude call per cartridge: (ad_brief, facts_pack) -> page.json.

System prompt = cartridge.md + schema.json + a global voice block.
User message = ad_brief + facts_pack + up to 2 exemplars if present.
"""
import json
import re
from pathlib import Path

from .anthropic_client import thinking_kwargs
from .design_skills import LANDING_CARTRIDGES
from .design_skills.design_md import design_reference_guidance_lines
from .errors import WriterFailed
from .jsonutil import extract_json
from . import listicle
from . import pdp
from . import tenant as tenant_mod
from . import vocab

TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def global_voice_block(tenant=None):
    """The voice/guardrail block appended to every writer system prompt.

    Built fresh per call from the active tenant's vocab.yaml and tenant.yaml
    (company name, author name, competitor aliases, fixed sentences), so the
    writer prompt and the deterministic gate can never drift apart -- add a
    word to vocab.yaml and both sides pick it up with no code change.
    """
    tenant = tenant or tenant_mod.active()
    v = vocab.active()
    # Fix cycle 21: claims/config.json's financing_lender (via tenant.yaml's
    # own fallback default) is tenant-scoped, stable config -- same category
    # as vocab.yaml's fixed sentences, already baked into this cached block --
    # not a per-run value, so reading it here (rather than threading it in as
    # a separate parameter) keeps cached_system_prefix's cache key exactly
    # what it already was: a function of the tenant, nothing per-run.
    financing_lender = tenant.claims_config.get("financing_lender")

    company = tenant.display_name
    author_name = (tenant.author("author") or {}).get("name") or "the page author"
    hype_words_list = ", ".join(f'"{w}"' for w in v.hype_words) or "(none configured)"
    trigger_words_list = ", ".join(f'"{w.upper() if len(w) <= 3 else w}"' for w in v.trigger_words)
    implied_terms_list = ", ".join(f'"{t}"' for t in v.implied_claim_forbidden_terms)
    lender_names_list = ", ".join(n.title() for n in v.forbidden_lender_names)

    # Absolute word bans, stated one group at a time so each reads as a real
    # sentence rather than a list dump. Every group is tenant data.
    ban_sentences = []
    for term in v.emf_terms:
        ban_sentences.append(
            f'Never write "{term.upper()}" in any form, anywhere, including as an abbreviation '
            "inside a claim -- this applies even to facts_pack's own internal-only data on the topic."
        )
        break
    if v.banned_names:
        names = ", ".join(n.title() for n in v.banned_names)
        ban_sentences.append(f"Never name any of these: {names}.")
    for wrong, right in (v.competitor_aliases or {}).items():
        ban_sentences.append(f'Refer to that competitor as "{right}", never "{wrong.title()}".')
    # Cycle 53: a former display name (tenant.yaml brand.retired_names, e.g.
    # after a rebrand) must never be written, even when a quoted claim
    # source still carries it (a policy title, an app name) -- the gate's
    # own check (harness/repair.py's find_retired_name_violations) is what
    # actually catches a slip here; this just heads it off up front.
    retired_names = tenant.get("brand.retired_names") or []
    if retired_names:
        retired_list = ", ".join(f'"{n}"' for n in retired_names)
        exceptions = tenant.get("brand.retired_name_exceptions") or []
        exception_clause = (
            " -- except the exact phrase" + ("s " if len(exceptions) != 1 else " ")
            + ", ".join(f'"{e}"' for e in exceptions) + ", which may stay"
            if exceptions else ""
        )
        ban_sentences.append(
            f'This brand is called "{company}", never {retired_list}, even when a quoted source '
            f"says otherwise{exception_clause}."
        )
    ban_sentences.append(
        "Never claim third-party or accredited-laboratory testing of any kind. Competitor "
        "statements are only ever the speaker's own experience, never a sourced fact about a "
        "competitor, unless a verified claim covers it."
    )
    guardrails = " ".join(ban_sentences)

    lender_clause = (
        f" Never name a financing lender ({lender_names_list}, or any other) unless "
        "financing.lender is non-null and IS that name."
        if lender_names_list
        else ""
    )
    implied_clause = (
        f" Never write {implied_terms_list} unless a verified claim's own text actually states it."
        if implied_terms_list
        else ""
    )

    # Fix cycle 21: the ONLY sentence a page may ever state a financing offer
    # in is vocab.allowed_financing_sentence(financing_lender) -- the
    # no-lender sentence when no lender is configured, or the with-lender
    # sentence (naming the actual configured lender) when one is. Before
    # this cycle, this paragraph always named the no-lender sentence
    # regardless of financing_lender -- the root cause of every rendered
    # page showing "Financing is available at checkout." even once a lender
    # (Bread Pay) was configured (docs/FIXLOG.md Cycle 18's own
    # verification flagged this, unfixed at the time). Financing is still a
    # single fixed sentence either way -- a lender being configured never
    # means the writer may state a monthly figure or an APR; there's no real
    # lender quote anywhere in this codebase to source one from (fix cycle 10).
    allowed_financing_sentence = v.allowed_financing_sentence(financing_lender)
    if financing_lender:
        financing_rule = (
            f"Financing: use facts_pack.product.financing. A lender ({financing_lender}) is configured for "
            "this tenant, but the ONLY sentence you may write anywhere on the page that actually STATES a "
            f'financing offer -- a monthly figure, a lender name, or that financing is available -- is exactly "{allowed_financing_sentence}", '
            "verbatim, nothing added before or after it in that field. Never invent a monthly figure or APR, "
            "and never name any other lender -- the configured lender's name only ever appears inside that one "
            "exact sentence."
        )
    else:
        financing_rule = (
            "Financing: use facts_pack.product.financing. If financing.lender is null, you may discuss "
            "financing as a general topic (e.g. contrasting it with the sticker price), but the ONLY sentence "
            "you may write anywhere on the page that actually STATES a financing offer -- a monthly figure, a "
            f'lender name, or that financing is available -- is exactly "{allowed_financing_sentence}", '
            "verbatim, nothing added before or after it in that field. Never invent a monthly figure or lender name."
        )

    return f"""## Voice and output rules

Voice: plain, specific, no hype words ({hype_words_list}). No exclamation marks. Prefer short declarative sentences. "Unlock" is the one writers reach for most often without noticing, in two different situations: (1) a feature that isn't gated behind an upgrade or extra payment -- say "included standard", "there's no extra step", or "it's included, not an add-on" instead; (2) information (like a price) that isn't gated behind a form or a sales call -- say "nothing to submit first", "no form required to see it", or "it's just on the page" instead of "nothing to unlock" / "unlock the price".

Never write the byline, publish/update dates, the "Advertisement" label, or the disclosure paragraph -- the renderer injects those automatically.

Every piece of text that states a number, a percentage, a dollar amount, or uses the words {trigger_words_list} MUST carry a non-empty "claim_ids" array referencing an id from facts_pack.verified_claims. Never invent a claim id. If you cannot support a statement with a verified claim, do not make the statement. Each row in facts_pack.specs already carries its own "claim_id" -- when you restate a spec fact in prose (not just in a specs table), copy that same claim_id into the prose sentence's claim_ids array; do not state a spec number in prose without it. The id goes in that JSON array field ONLY -- never typed out as part of the sentence itself, in parentheses or otherwise. WRONG: "the price, $8,250, is listed on the product page (price-model)" or "a heater system built around full-spectrum infrared (spec-model-wavelength-range)". RIGHT: drop the parenthetical entirely and put "price-model" / "spec-model-wavelength-range" in that sentence's own "claim_ids" array instead -- a claim id is never something a reader sees. This includes an illustrative or hypothetical number used to make a rhetorical point ("a $50 impulse buy versus an $8,000 purchase", "a standard 2-person cabin") -- there is no claim_id for a made-up example, so make the same point in words instead ("a small impulse buy" vs. "a major purchase"). It also includes the current year or any other calendar year stated in your own narration as color commentary (e.g. "a reasonable thing to want in 2026") -- there is no claim_id for a bare year either; drop it or put it inside a direct quote credited to the ad speaker instead.

Reference images only by an asset id from facts_pack.assets, in an "asset_id" field -- never by URL directly. Never write your own "alt" field for an image -- the renderer derives alt text on its own.

Write a dollar amount exactly as it appears in the source claim's text (e.g. "$8,250", no ".00" unless the claim's own figure has non-zero cents) -- never reformat it, and never add a ".00" that isn't in the claim text.

Never write a URL anywhere in body text (prose, headings, alt text, quotes). Cite a source inline as "(source name, year)" -- e.g. "({company} product page, 2026)" -- using a short human-readable name for the source, never the raw URL. The renderer builds the Sources list and its links on its own from claim_ids; the URL never needs to appear as text you write.

Claim ids never appear in any text field. Cite in prose only as (source name, year). Put ids only in claim_ids -- never in a headline, paragraph, label, or quote, even in parentheses next to the source name.

If ad_brief.speaker_pov is "first_person", never write the speaker's story in the page author's own first-person voice ("I ran into this...", "it made my mornings better"). Attribute it instead to "a customer" -- or to the name in facts_pack.speaker_name if that field is non-null -- e.g. "One customer told us she..." or a short quoted line clearly credited to that customer. The page author ({author_name}) never speaks in the ad speaker's first person.

Outside a sentence that carries a claim_id, write numbers as words, not numerals -- "seven in the morning", not "7 a.m."; "five-figure", not "5-figure"; "two hours", not "2 hours". This applies especially to an illustrative or incidental number with nothing to cite (a time of day, a small count, an age) -- it has no claim_id to give it, so numerals there read as an invented, uncited fact even when you didn't mean it as one. Never use a numeral for a time, a count, or an age unless that exact sentence's own claim_ids array cites a verified claim for it.

A number that comes only from the ad speaker's own statements (her own cost estimate, math, or hedge -- ad_brief.speaker_experience, e.g. "she put memberships at around $200 a month") is never something you can state as fact in the brand's own voice, and it never gets a claim_id (there isn't a verified claim for someone's personal estimate). It may ONLY appear inside a plain narrative paragraph, phrased explicitly as her own estimate and set "attributed_to_customer": true on that paragraph's own JSON node -- e.g. "One customer told us she put her studio memberships at around $200 a month, or about $2,400 a year." The sentence must itself read as attributed: say "customer", or "she"/"he"/"they" together with "told us"/"estimated"/"said" -- not just the attributed_to_customer flag with plain assertive prose. A number like this must NEVER appear in a heading, a proof/benefit bullet, a spec-table row, or an FAQ answer, marked attributed or not -- those are for verified facts only. A number NOT in the ad speaker's own words still needs an ordinary claim_id no matter where it appears, attributed_to_customer or not.

If the user message includes "exemplars", use them only as a voice and structure reference. A JSON exemplar shows the page.json shape; a {{"reference_article": "..."}} exemplar is a real published {company} article -- match its tone and rigor, but never copy its numbers, claims, or competitor comparisons into this page unless the same fact also appears in this page's own facts_pack.verified_claims.

## Guardrails
{guardrails}

{financing_rule}{lender_clause}

Compare-at / list price: only mention a "was $X" / compare-at / strikethrough price if facts_pack.product.compare_at_price is non-null. If it is null, state only the current price.

Warranty: the verified warranty claim covers each component differently (e.g. heating elements and cabinetry are covered longer than electronics like the control system or accent lighting) -- never write a sentence describing what's covered by component from memory. Anywhere any text mentions warranty, write EXACTLY "{v.allowed_warranty_sentence}" -- or, in a spec-table row, the label "{v.allowed_warranty_spec_label}" with value exactly "{v.allowed_warranty_spec_value}" -- or quote the verified warranty claim's own text verbatim. Never invent or paraphrase per-component warranty wording. Warranty may appear AT MOST ONCE on the whole page, either as one proof point/bullet or one specs-table row (never both, never a second time anywhere else on the page), and every time it appears it must use the fixed sentence verbatim -- do not shorten it to a label on its own, and do not paraphrase it even if the paraphrase is honest; use the exact sentence, word for word, or leave warranty out of that section entirely.

Implied claims: never infer a second, unverified fact from a verified claim -- a verified claim proves only what it literally says, nothing else. Being US-owned does not verify support is domestic; free shipping does not verify delivery speed; a star rating does not verify the product is "best".{implied_clause}

Reviews: use facts_pack.reviews_summary and its claim_ids exactly as given. If facts_pack.reviews_summary is null, do not state any review count or star rating anywhere on the page, and never use a remembered or placeholder figure for a review count or rating. The word "reviews" itself is not banned, but it always needs a claim_id -- this trips writers repeatedly in generic buyer-education prose that has no claim_id to give it, e.g. "look at ratings and reviews from other buyers", "a star average built on a handful of reviews". Say "customer feedback" or "what other buyers say" instead in that kind of sentence -- every time, not just the first draft -- unless you are citing facts_pack.reviews_summary's actual claim_id.

Same rule, same trap, for "study"/"studies", "clinical", "medical", "proven", and "rated": each always needs a claim_id, and each trips writers in the same generic buyer-education prose that has nothing in facts_pack.verified_claims to cite -- e.g. "a careful buyer treats research as useful background", "any brand citing a study should show its work". If you're not citing a specific verified claim_id when you make the point, rephrase without the trigger word: say "outside research" or "independent sources" instead of "a study"/"studies", describe a feature in plain terms instead of calling it "clinical" or "medical", and drop "proven"/"rated" rather than asserting them unsupported. When you ARE citing a real verified claim, use the word freely and put its claim_id in that sentence's claim_ids array as usual.

Refer to the product by facts_pack.product.short_name, not facts_pack.product.name alone and never by a raw marketing title -- on FIRST mention in each major section (hero, each FAQ answer, each step, etc.). A short_name often contains a digit (its capacity, e.g. "2-Person") -- any sentence that uses the full short_name needs a claim_id too; put the matching capacity spec row's claim_id from facts_pack.specs into that sentence's "claim_ids" array (the JSON field), every single time you write the short_name out, including in an FAQ answer or a step that isn't otherwise about specs -- the id itself is never printed as text next to the short_name or anywhere else. WRONG: "the full product short_name (spec-model-capacity), which is priced at $8,250" -- the id in parentheses is a bug, not a citation. RIGHT: the same sentence with no parenthetical at all and "claim_ids": ["spec-model-capacity", "price-model"] on that sentence's own JSON node. To avoid re-triggering this on every sentence (and to avoid sounding like a repeated ad slogan), after that first mention in a section just say "the sauna" or "this model" for the rest of that section -- you don't need the full short_name, or a claim_id, again until the next section.

Output ONLY a single JSON object matching the schema you were given. No markdown fences, no commentary before or after. Output compact JSON -- no pretty-printing, no indentation, no extra whitespace between tokens -- and never restate a verified claim's full text anywhere in your JSON; a claim is always referenced by its id in a claim_ids/claim_id field, never duplicated as text."""


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


def load_cartridge_prompt(cartridge_dir, tenant=None):
    """cartridge.md and schema.json, with the tenant's placeholders filled in.

    A cartridge is tenant-neutral on disk: it says {{ tenant.name }} where a
    company belongs. Both files are rendered here, at load time, so the writer
    never sees a placeholder and the cartridge never carries one company's
    words. A tenant's own cartridge-overrides/<name>/cartridge.md, when present,
    is appended after the shared rules."""
    tenant = tenant or tenant_mod.active()
    cartridge_md = tenant.render((cartridge_dir / "cartridge.md").read_text())
    override = tenant.cartridge_overrides(cartridge_dir.name)
    if override:
        cartridge_md += "\n\n## Tenant overrides\n" + tenant.render(override.read_text())
    schema = json.loads(tenant.render((cartridge_dir / "schema.json").read_text()))
    return cartridge_md, schema


# Fix cycle 17 item 2 (prompt caching): the writer's system prompt, minus the
# per-run "Hard constraints"/"DO NOT REPEAT" tail write_page appends -- this
# part is byte-identical across every repair attempt on the same cartridge
# (word_range/allowed_cta_texts/ad_not_repeated live outside it precisely so
# they never need to match byte-for-byte for the cache to hit; see
# write_page). Never put a date, run id, or product price in here -- schema
# is serialized with sort_keys=True so its JSON text never depends on dict
# insertion order, and cartridge_md/schema/tenant are all static, tenant-
# scoped data with nothing per-run in them.
def cached_system_prefix(cartridge_md, schema, tenant=None):
    tenant = tenant or tenant_mod.active()
    return (
        vocab.forbidden_words_block()
        + "\n\n"
        + cartridge_md
        + "\n\n## JSON schema for page.json\n"
        + json.dumps(schema, indent=2, sort_keys=True)
        + "\n\n"
        + global_voice_block(tenant)
    )


# Fix cycle 17 item 3 (output hygiene): max_tokens capped per cartridge from
# its own word range instead of one flat 6000 for every cartridge --
# product-page (250-500 words) never needed anywhere near as much headroom as
# longform (800-1,400).
#
# The spec's original formula (1.6 tokens/word + 800 flat overhead) was
# tried first and measured wrong: it caps longform (hi=1400) at 3040 output
# tokens, but the cycle-16 baseline's own real longform call used 3324
# output tokens uncapped, and the first real verification run under this cap
# reproduced exactly that -- `write.longform` hit 3040 output tokens twice in
# a row and both times returned invalid JSON ("Unterminated string"), i.e.
# the cap truncated mid-string. Longform's schema is far more
# structure-heavy per word than article/product-page (a 6-10 row specs
# table, 5-7 FAQ pairs, 3 steps, 2-3 proof_stats, several claim_ids arrays)
# so a flat per-word rate tuned for prose undercounts it specifically.
# 1.8 tokens/word + 1800 overhead keeps ~30% headroom over that measured
# 3324-token longform call (4320) while still capping well under the old
# flat 6000 for every cartridge (article 4680, product-page 2700 -- also
# safer than the original formula's 1600, which left only ~100 tokens of
# margin over product-page's own 1499-token baseline call).
def max_tokens_for_word_range(word_range, structural_overhead=1800):
    if not word_range:
        return 6000
    _, hi = word_range
    return int(hi * 1.8) + structural_overhead


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
# Fix cycle 7 item 3: schema.json can also template "{model_name}" (e.g.
# "Shop the {model_name}" -> "Shop the <model>") -- model_name defaults to None
# so an existing caller/template with no "{model_name}" placeholder is
# unaffected; str.format only substitutes a placeholder that's actually
# present in the template string.
#
# Fix cycle 16 item 9 (Thursday queue item 3, "consult-CTA variant per
# config"): a short, fixed list of angle keywords marking an ad as
# high-consideration -- worth a human conversation before a self-serve
# purchase -- for tenant.yaml's cta_mode "auto". Deliberately small, per the
# brief ("a short list of angle keywords"), not exhaustive.
HIGH_CONSIDERATION_ANGLE_KEYWORDS = (
    "custom", "commercial", "installation", "install", "financing",
    "consultation", "multi-person", "outdoor", "wholesale", "bulk",
)


def resolve_cta_mode(cta_mode, ad_angle=None):
    """"buy" or "consult" -- resolves tenant.yaml's cta_mode ("buy",
    "consult", or "auto") for this run. "auto" is "consult" when
    `ad_angle` (ad_brief.angle) contains one of
    HIGH_CONSIDERATION_ANGLE_KEYWORDS, else "buy". Any other/missing value
    is treated as "buy" -- the safer, unchanged-behavior default."""
    if cta_mode == "consult":
        return "consult"
    if cta_mode != "auto":
        return "buy"
    angle = (ad_angle or "").lower()
    return "consult" if any(kw in angle for kw in HIGH_CONSIDERATION_ANGLE_KEYWORDS) else "buy"


def resolve_allowed_cta_texts(schema, short_name, model_name=None, tenant=None, ad_angle=None):
    """Fix cycle 16 item 9: when this run's resolved cta_mode (tenant.yaml's
    cta_mode, via resolve_cta_mode) is "consult", the cartridge's own
    schema.json allowed_cta_texts is set aside in favor of the tenant's own
    cta_variants.consult list -- every cartridge becomes consult-CTA for
    that run. "buy" (the default for a tenant that
    never sets cta_mode) leaves schema's own list exactly as it always
    was -- no behavior change for the common case. Cartridges never hardcode
    a company's consult phrasing themselves; that list lives only in the
    tenant's own cta_variants (see tenant.yaml)."""
    tenant = tenant or tenant_mod.active()
    mode = resolve_cta_mode(tenant.get("cta_mode", "buy"), ad_angle)
    templates = None
    if mode == "consult":
        templates = tenant.get("cta_variants.consult") or None
    if templates is None:
        templates = schema.get("allowed_cta_texts") or []
    tenant_short_name = tenant.get("tenant_short_name") or tenant.display_name
    return [
        t.format(short_name=short_name, model_name=model_name, tenant_short_name=tenant_short_name)
        for t in templates
    ]


# Fix cycle 12 item 1: exemplars are the single largest piece of a writer
# call's input -- a real fixture (cartridges/article/exemplars/
# best-sauna-brands-2026.md) is 5,600+ words on its own, and sending it
# whole was the main driver of the ~36,800-token average article write.
# Trimmed to the first 700 words: still enough for the model to pick up
# voice/structure (the point of an exemplar per write_page's own guidance --
# "use them only as a voice and structure reference"), at a fraction of the
# token cost. Never applied to a .json exemplar (a page.json-shaped object,
# not prose) -- there are none in this repo today, and truncating structured
# JSON by word count would just produce invalid JSON; the 2-exemplar cap
# below is the control for those.
EXEMPLAR_MAX_WORDS = 700


def _truncate_words(text, limit):
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit])


def load_exemplars(exemplars_dir, limit=2):
    """Up to `limit` exemplars from tenants/<tenant>/exemplars/<cartridge>/.
    Exemplars are a tenant's own approved pages, not part of the cartridge
    definition, so they live with the tenant. A .json file
    is parsed as a page.json-shaped object; a .md/.txt file is a real
    reference article and is passed through as text (voice/structure
    reference, not something to copy verbatim), trimmed to the first
    EXEMPLAR_MAX_WORDS words (fix cycle 12 item 1)."""
    if not exemplars_dir:
        return []
    ex_dir = Path(exemplars_dir)
    if not ex_dir.exists():
        return []
    files = sorted(ex_dir.glob("*.json")) + sorted(ex_dir.glob("*.md")) + sorted(ex_dir.glob("*.txt"))
    exemplars = []
    for f in files[:limit]:
        if f.suffix == ".json":
            exemplars.append(json.loads(f.read_text()))
        else:
            exemplars.append({"reference_article": _truncate_words(f.read_text(), EXEMPLAR_MAX_WORDS)})
    return exemplars


def _build_hard_constraints(word_range, allowed_cta_texts):
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
    return hard_constraints


# Cycle 30: tenant.yaml's design_reference, landing-style cartridges only
# (article's own editorial warm-up declines the landing-page hero skeleton
# entirely -- see cartridges/article/cartridge.md). Guidance only, never a
# font/color/brand instruction -- design_reference_rules derives structure
# alone. Shared by both write_page's prompt build and
# build_initial_write_request's batch-mode prompt build below, so a
# `--batch` run and a normal run see the exact same guidance.
def _append_design_reference_guidance(hard_constraints, cartridge_name, tenant):
    if cartridge_name not in LANDING_CARTRIDGES:
        return
    design_guidance = design_reference_guidance_lines(tenant)
    if design_guidance:
        hard_constraints.append(
            "Design reference guidance (structural only -- never a font, color, or brand "
            "instruction): " + "; ".join(design_guidance) + "."
        )


# Cycle 41: the listicle cartridge writes in one of five styles, resolved
# once per run (harness/listicle.py's resolve_style). The style's headline
# formula and item pattern go into the writer's own hard constraints so the
# prompt states exactly what harness/listicle.find_listicle_violations will
# then measure. No-op for every other cartridge, and for a listicle run with
# no style resolved (the gate still rejects a page with no valid style).
def _append_listicle_style_guidance(hard_constraints, cartridge_name, style):
    if cartridge_name != "listicle":
        return
    hard_constraints.extend(listicle.writer_rules_lines())
    if style:
        hard_constraints.extend(listicle.writer_style_lines(style))


# Cycle 54: the product-page cartridge's own writer lines -- the same
# constants harness/pdp.py's gate measures. No-op for every other cartridge.
def _append_product_page_guidance(hard_constraints, cartridge_name):
    if cartridge_name != "product-page":
        return
    hard_constraints.extend(pdp.writer_rules_lines())


def _warmup_window_words(tenant):
    window = tenant.get("cartridges.article.warmup_window_words")
    if not isinstance(window, int) or window <= 0:
        return 600
    return window


def _warmup_brand_terms(tenant):
    terms = [tenant.display_name]
    short = tenant.get("tenant_short_name")
    if short and short not in terms:
        terms.append(short)
    return [t for t in terms if t]


def _reference_article_breaks_warmup(text, brand_terms, window):
    """True when a markdown exemplar names the brand inside the first
    `window` words -- feeding that as few-shot undercuts the warm-up rule.

    Matched on word boundaries, the same way claims.warmup_first_mentions
    matches the page itself. A bare substring test reads a short brand name
    inside ordinary words -- a four-letter name is a substring of several
    everyday ones -- and would silently drop exemplars that never name the
    brand at all."""
    head = " ".join(text.split()[:window]).lower()
    return any(re.search(r"\b" + re.escape(term.lower()) + r"\b", head) for term in brand_terms if term)


def filter_exemplars_for_warmup(exemplars, tenant, *, window=None):
    """Drop article reference_article exemplars that name the brand too early.

    JSON page.json exemplars are left alone (structured shape reference).
    Cycle 33 critic: a tenant's live .md exemplars name the brand in the
    headline/body and were canceling the warm-up instruction."""
    window = window if window is not None else _warmup_window_words(tenant)
    brand_terms = _warmup_brand_terms(tenant)
    kept = []
    for ex in exemplars:
        if not isinstance(ex, dict):
            kept.append(ex)
            continue
        ref = ex.get("reference_article")
        if isinstance(ref, str) and _reference_article_breaks_warmup(ref, brand_terms, window):
            continue
        kept.append(ex)
    return kept


# Cycle 33: article warm-up window. Real runs were naming the brand inside
# the first 327–583 words because cartridge + schema invited an early body/
# turn mention while the warm-up rule forbade it, and the global voice
# short_name-on-first-section-mention line pulled the same direction. The
# cartridge/schema now keep brand out until close (practical way to clear
# the numeric first-N-words gate); this hard constraint makes the override
# explicit in the per-run block the model weighs heavily.
def _append_warmup_hard_constraints(hard_constraints, cartridge_name, tenant):
    if cartridge_name != "article":
        return
    window = _warmup_window_words(tenant)
    company = tenant.display_name
    hard_constraints.append(
        f"Warm-up window (numeric gate: first {window} reading-order words): do not write "
        f"{company!r}, the tenant short name, facts_pack.product.short_name, the product name, "
        "any price/$ figure, or the CTA text anywhere in open, body_sections, "
        "alternatives_section, how_it_works_section, or turn_section (heading, intro, and "
        "criteria text included). Those sections stay brand-free and product-name-free so the "
        f"first brand mention lands after word {window}. Name the company and product only in "
        "close (short_name + claim_id there). Ignore the global voice short_name-on-first-"
        "section-mention rule until close for this cartridge. If a reference_article exemplar "
        "is present, use it for voice and structure only -- never copy its brand-placement "
        "timing."
    )


# Fix cycle 17 item 2 (prompt caching): system is now a list of content
# blocks instead of one string -- block 1 is cached_system_prefix (the
# stable prefix, cache_control on it), block 2 (only present when there's
# something to put in it) is the per-run "Hard constraints"/"DO NOT REPEAT"
# tail, uncached. Both hard_constraints and ad_not_repeated are the same on
# every attempt for a given cartridge in a given run (word_range/
# allowed_cta_texts/ad_not_repeated never change across the repair loop), but
# they stay outside the cached block deliberately, matching the cache plan
# exactly (cached block = forbidden words + cartridge.md + schema.json +
# global voice block, nothing else) rather than assuming a second breakpoint
# here would also pay off.
def _build_system(cartridge_md, schema, tenant, hard_constraints, ad_not_repeated):
    system = [{
        "type": "text",
        "text": cached_system_prefix(cartridge_md, schema, tenant),
        "cache_control": {"type": "ephemeral"},
    }]

    volatile = ""
    if hard_constraints:
        volatile += "\n\n## Hard constraints for this run\n" + "\n".join(f"- {c}" for c in hard_constraints)
    if ad_not_repeated:
        lines = ["## DO NOT REPEAT these ad statements; use the verified fact instead"]
        for item in ad_not_repeated:
            fact = item.get("verified_fact")
            if fact:
                lines.append(f'- Ad said: "{item["claim"]}" -- verified fact: "{fact}"')
            else:
                lines.append(f'- Ad said: "{item["claim"]}" -- not verified; do not state this on the page at all')
        volatile += "\n\n" + "\n".join(lines)
    if volatile:
        system.append({"type": "text", "text": volatile})
    return system


# Fix cycle 17 item 2: the user turn's first content block is facts_pack
# alone, with its own cache_control breakpoint -- identical across the three
# cartridge calls in one run (and across every repair attempt on the same
# cartridge), so it's worth its own breakpoint independent of the system
# block. ad_brief + exemplars + revision_note go in a second, uncached block
# AFTER that breakpoint -- ad_brief never carries a date/run id, but it's
# per-ad, not stable across runs, and exemplars/revision_note are per-call by
# design (fix cycle 12 item 1's exemplar-skip-on-repair, fix cycle 4's
# per-attempt revision note).
def _build_initial_user_message(ad_brief, facts_pack, exemplars, revision_note=None):
    volatile_payload = {"ad_brief": ad_brief}
    if exemplars:
        volatile_payload["exemplars"] = exemplars
    volatile_text = json.dumps(volatile_payload)
    if revision_note:
        volatile_text += "\n\n" + revision_note
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": json.dumps({"facts_pack": facts_pack}),
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": volatile_text},
        ],
    }


def _content_len(content):
    """chars in a system/message "content" value -- a plain string, or a
    list of {"type": "text", "text": ...} blocks (fix cycle 17's
    cache_control breakpoints)."""
    if isinstance(content, str):
        return len(content)
    return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))


# Fix cycle 17 item 5 (batch mode): the exact request write_page's own
# attempt 1 would send, as plain kwargs for client.messages.create /
# batches.create -- shared so a batched initial write is byte-for-byte the
# same request a synchronous one would have made. Never carries a
# revision_note -- attempt 1 never has one either way.
def build_initial_write_request(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, model,
                                 word_range=None, allowed_cta_texts=None, ad_not_repeated=None,
                                 tenant=None, listicle_style=None):
    """(schema, kwargs) -- schema so the caller can validate_schema() the
    parsed response the same way write_page does; kwargs is ready to pass to
    client.messages.create(**kwargs) or wrap in a batch Request's params."""
    tenant = tenant or tenant_mod.active()
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md, schema = load_cartridge_prompt(cartridge_dir, tenant)
    exemplars = load_exemplars(tenant.exemplars_dir(cartridge_name))
    if cartridge_name == "article":
        exemplars = filter_exemplars_for_warmup(exemplars, tenant)
    hard_constraints = _build_hard_constraints(word_range, allowed_cta_texts)
    _append_design_reference_guidance(hard_constraints, cartridge_name, tenant)
    _append_warmup_hard_constraints(hard_constraints, cartridge_name, tenant)
    _append_listicle_style_guidance(hard_constraints, cartridge_name, listicle_style)
    _append_product_page_guidance(hard_constraints, cartridge_name)
    system = _build_system(cartridge_md, schema, tenant, hard_constraints, ad_not_repeated)
    messages = [_build_initial_user_message(ad_brief, facts_pack, exemplars)]
    kwargs = {
        "model": model,
        "max_tokens": max_tokens_for_word_range(word_range),
        "system": system,
        "messages": messages,
        **thinking_kwargs(model),
    }
    return schema, kwargs


def write_page(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, client, model, budget, log,
               word_range=None, allowed_cta_texts=None, revision_note=None, ad_not_repeated=None,
               tenant=None, listicle_style=None):
    """word_range (min, max), allowed_cta_texts (resolved, concrete strings),
    and revision_note (fix cycle 4 item 1: a "REVISION REQUIRED" block from a
    prior failed gate check on this same cartridge, appended to the user
    message so the writer sees exactly what to fix) are all optional -- a
    caller that doesn't pass them gets the pre-cycle-4 behavior.

    ad_not_repeated (fix cycle 10 item 4, broadened fix cycle 12 item 3): only
    ever set when claims/config.json's ad_overclaim_policy is "warn" and the
    ad-claims gate found a claim (plain-unmatched OR a locked-topic claim
    that didn't match its locked fact) that didn't stop the run -- each
    item's own claim text and verified_fact (claims.gate_ad_brief_claims's
    return shape; verified_fact is None for a plain-unmatched claim -- there
    is no single fact to point to) are told to the writer as statements to
    never repeat."""
    tenant = tenant or tenant_mod.active()
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md, schema = load_cartridge_prompt(cartridge_dir, tenant)
    # A repair attempt (revision_note set) already saw the exemplars on the
    # first attempt -- it needs to fix specific flagged issues, not re-learn
    # voice/structure, and exemplars are the single largest piece of a call's
    # input tokens (up to ~45KB of reference text). Skipping them on repairs
    # buys real budget headroom for the repair loop without changing what
    # the writer is told to fix.
    exemplars = load_exemplars(tenant.exemplars_dir(cartridge_name)) if not revision_note else []
    if cartridge_name == "article" and exemplars:
        exemplars = filter_exemplars_for_warmup(exemplars, tenant)

    hard_constraints = _build_hard_constraints(word_range, allowed_cta_texts)
    _append_design_reference_guidance(hard_constraints, cartridge_name, tenant)
    _append_warmup_hard_constraints(hard_constraints, cartridge_name, tenant)
    _append_listicle_style_guidance(hard_constraints, cartridge_name, listicle_style)
    _append_product_page_guidance(hard_constraints, cartridge_name)
    # Fix cycle 6 item 3: the forbidden-word list, verbatim, goes at the very
    # top of the system prompt (and again inside every REVISION REQUIRED
    # block below) -- observed cycling on the hidden-costs-v2 verification
    # run where a repair attempt fixed one forbidden word but reintroduced
    # another two attempts later.
    system = _build_system(cartridge_md, schema, tenant, hard_constraints, ad_not_repeated)
    max_tokens = max_tokens_for_word_range(word_range)

    stage = f"write.{cartridge_name}"
    last_error = None
    messages = [_build_initial_user_message(ad_brief, facts_pack, exemplars, revision_note)]
    for attempt in range(2):
        budget.check()
        # Fix cycle 12 item 1: log an approximate prompt size (chars / 4, the
        # usual rough chars-per-token estimate) BEFORE the call -- so a
        # prompt-bloat regression (e.g. exemplar trimming silently stops
        # working) shows up in the log even without waiting for the real
        # usage.input_tokens number the API returns after the call.
        approx_prompt_chars = _content_len(system) + sum(_content_len(m["content"]) for m in messages)
        log.event(
            stage,
            f"prompt size: ~{approx_prompt_chars // 4} tokens (estimate, {approx_prompt_chars} chars)",
        )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            # This is bounded JSON extraction, not a reasoning task -- disable
            # thinking (when the model accepts the param -- Haiku 4.5 doesn't,
            # see anthropic_client.thinking_kwargs) so the full max_tokens
            # budget goes to visible output. Sonnet 5 runs adaptive thinking
            # by default when unset, and thinking tokens count against
            # max_tokens; without this the model can exhaust the budget on
            # hidden reasoning and return an empty/truncated response
            # (observed in practice on longform).
            **thinking_kwargs(model),
        )
        usage = response.usage
        budget.record_call(usage.input_tokens, usage.output_tokens)
        log.call(
            stage, model, usage.input_tokens, usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
        )

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
            # Fix cycle 6 verification: this retry used to resend the exact
            # same messages, blindly re-rolling with no reason to behave
            # differently -- caught live when a response came back empty
            # ("Expecting value: line 1 column 1") and, on the very next
            # attempt with no feedback, came back missing a required key
            # instead. Feed the bad response and the specific error back as
            # a real multi-turn correction instead.
            messages = messages + [
                {"role": "assistant", "content": text or "(empty response)"},
                {
                    "role": "user",
                    "content": (
                        f"That response was not valid JSON matching the schema: {e}. Return ONLY a "
                        "complete, corrected JSON object matching the schema -- no markdown fences, "
                        "no commentary before or after."
                    ),
                },
            ]
            continue

    raise WriterFailed(f"{stage} failed after retry: {last_error}")
