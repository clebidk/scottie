"""Cycle 79: the listicle's first screen -- three approved hero styles.

Owner approval 2026-10-05 (round 3, ~/asset-inbox/design-approved-2026-10-05/):
every first screen opens a loop instead of answering it, starts the content
on the first phone screen, shows a real person where it can, has no boxes,
and ends on a scroll cue. Three styles, a page dimension next to the copy
style and the look:

  face     a still from the ad video (the creator, harness/ad_frames.py),
           4:5, with the speaker's verbatim quote as its caption ("In the ad
           she made for <tenant>"); headline above, one-line byline.
  story    text first: eyebrow, headline, byline, then a lede that starts on
           screen 1 -- the speaker's verbatim quote next to a small round crop
           of the ad still, an open-loop paragraph -- then item 01.
  display  a wide display face (the tenant's display_wide font, upper
           case; or display_acid), accent phrase in red, dek, the product
           cut-out, the stats strip, a scroll cue.

The style is LAYOUT, not copy: the writer writes one set of first-screen
fields (eyebrow, headline, accent_phrase, dek, lede, scroll_cue,
hero_quote_id, speaker_pronoun) and any style renders them, so `harness
rerender --hero-style` switches it with no model call, like `--look`.

Face needs an ad still; story and display need nothing. A run with no
still (an image ad that failed its check, a text ad) falls back: face ->
story (with no avatar) when the ad has a quotable speaker, else display.
The style a page actually rendered in is stamped on page.json
("hero_style") and recorded in state.json, and an A/B/C variant records it.

Everything the renderer adds here comes from facts_pack, the ad brief or the
tenant -- the stats strip and the value stack cite verified claims only, the
quote is one of claims.safe_quote_candidates word for word.
"""
import datetime
import html
import random
import re

from markupsafe import Markup

from . import tenant as tenant_mod
from .textutil import product_name_slug, walk_page

HERO_STYLES = ("face", "story", "display")
DISPLAY_FONTS = ("wide", "acid")
READ_WPM = 230
PRONOUNS = ("she", "he", "they")


# ---------------------------------------------------------------------------
# Which style
# ---------------------------------------------------------------------------

def tenant_hero_styles(tenant=None):
    """tenant.yaml cartridges.listicle.hero_styles filtered to real styles,
    else all three."""
    tenant = tenant or tenant_mod.active()
    pinned = tenant.get("cartridges.listicle.hero_styles") or ()
    chosen = tuple(s for s in pinned if s in HERO_STYLES)
    return chosen or HERO_STYLES


def seeded_hero_style(seed, tenant=None):
    allowed = tenant_hero_styles(tenant)
    return random.Random(f"listicle-hero-style:{seed}").choice(allowed)


def resolve_hero_style(requested=None, *, seed=0, tenant=None, frame=False, quote=False):
    """(style, note). `requested` (--hero-style, page.json's own value, an
    A/B/C arm) wins when its inputs exist; else a seeded pick from the
    tenant's allowed styles. Face with no ad still falls back to story when
    the ad has a quotable speaker, else to display; note says why."""
    if requested and requested not in HERO_STYLES:
        raise ValueError(f"unknown hero style {requested!r}; choose one of {list(HERO_STYLES)}")
    want = requested or seeded_hero_style(seed, tenant)
    if want == "face" and not frame:
        fallback = "story" if quote else "display"
        return fallback, f"face needs a still from the ad; none usable, so {fallback}"
    return want, ""


# ---------------------------------------------------------------------------
# The quote and the speaker
# ---------------------------------------------------------------------------

def quote_candidates(ad_brief, facts_pack):
    from .claims import safe_quote_candidates

    return safe_quote_candidates(ad_brief or {}, facts_pack or {}) if ad_brief else []


def hero_quote(page, ad_brief, facts_pack):
    """{"id", "text", "source"}: the writer's hero_quote_id when it names an
    ad_quotes entry; for a page written before cycle 79, the first entry
    (the ad's opening line -- usually its hook). None when the ad has no
    quotable speaker."""
    candidates = quote_candidates(ad_brief, facts_pack)
    if not candidates:
        return None
    by_id = {c["id"]: c for c in candidates}
    wanted = (page or {}).get("hero_quote_id")
    if wanted in by_id:
        return dict(by_id[wanted], source="writer")
    # Cycle 79 fix 5: an older page gets the first quote its body does not
    # already use (a quote appears once on a page).
    used = quotes_used_in_body(page, candidates)
    unused = [c for c in candidates if c["id"] not in used]
    return dict((unused or candidates)[0], source="fallback")


# Cycle 79 fix 1: the speaker's pronoun is never guessed. Evidence only:
# a tenant override for the ad (tenant.yaml first_screen.speaker_pronouns,
# keyed by the ad id or the source file stem), else the ad-frame vision
# check's answer when it was unambiguous (ad_frames records it), carried on
# ad_brief["speaker_pronoun"] by prepare_speaker(). No evidence -> None, and
# every frame is neutral: "In the ad, the creator says," -- never she/he.
NEUTRAL_FRAME = "In the ad, the creator says,"
_FRAME_PRONOUN_RE = re.compile(r"\bIn the ad,?\s+(she|he)\s+(?:says|said)\b|\b(she|he)\s+(?:says|said)\s+in\s+the\s+ad\b",
                               re.IGNORECASE)
_SPEAKER_PRONOUN_RE = re.compile(r"\b(?:she|he|her|his|him|hers|herself|himself)\b", re.IGNORECASE)
_PRONOUN_FAMILY = {"she": {"she", "her", "hers", "herself"}, "he": {"he", "him", "his", "himself"}}


def _ad_key(ad_brief):
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", str((ad_brief or {}).get("source_file") or ""))
    m = re.search(r"(\d{6,})", stem)
    return (m.group(1) if m else None), stem


def override_pronoun(tenant, ad_brief):
    table = (tenant.get("first_screen.speaker_pronouns") if tenant else None) or {}
    if not isinstance(table, dict):
        return None
    ad_id, stem = _ad_key(ad_brief)
    for key in (ad_id, stem):
        value = str(table.get(key) or table.get(str(key)) or "").strip().lower() if key else ""
        if value in ("she", "he"):
            return value
    return None


def evidence_pronoun(ad_brief=None, frame=None, tenant=None):
    """'she' | 'he' | None -- see the block comment above."""
    value = override_pronoun(tenant, ad_brief) if tenant is not None else None
    if value:
        return value
    value = str((ad_brief or {}).get("speaker_pronoun") or "").strip().lower()
    if value in ("she", "he"):
        return value
    value = str((frame or {}).get("speaker_pronoun") or "").strip().lower()
    return value if value in ("she", "he") else None


def speaker_pronoun(page=None, *, ad_brief=None, frame=None, tenant=None):
    """The pronoun the RENDERER may use for the speaker, from evidence only;
    the writer's own page.speaker_pronoun is not evidence."""
    return evidence_pronoun(ad_brief, frame, tenant)


def quote_frame(pronoun):
    return f"In the ad, {pronoun} says," if pronoun in ("she", "he") else NEUTRAL_FRAME


# ---------------------------------------------------------------------------
# Headline with its accent phrase
# ---------------------------------------------------------------------------

def accent_phrase(page):
    """The writer's accent_phrase when it is a substring of the headline;
    for an older page, its last sentence when the headline has two or more
    (the three-beats pattern), else none."""
    headline = str((page or {}).get("headline") or "")
    phrase = str((page or {}).get("accent_phrase") or "").strip()
    if phrase and phrase in headline:
        return phrase
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", headline.strip()) if s]
    return sentences[-1] if len(sentences) >= 2 else ""


DISPLAY_MAX_WORDS = 8
DISPLAY_MAX_CHARS = 48


def fits_display(text):
    text = str(text or "").strip()
    return bool(text) and len(text.split()) <= DISPLAY_MAX_WORDS and len(text) <= DISPLAY_MAX_CHARS


def display_line(page):
    """Cycle 79 fix 3: the line the display style sets in the wide face --
    the writer's display_headline when it fits (<= 8 words, <= 48
    characters), else the headline when that fits, else None (the display
    style then sets the headline in the heading face, sentence case)."""
    for value in ((page or {}).get("display_headline"), (page or {}).get("headline")):
        if fits_display(value):
            return str(value).strip()
    return None


def headline_markup(page, text=None):
    headline = str(text if text is not None else (page or {}).get("headline") or "")
    phrase = accent_phrase(page) if text is None else str((page or {}).get("accent_phrase") or "").strip()
    if text is not None and phrase not in headline:
        sentences = [x for x in re.split(r"(?<=[.?!])\s+", headline.strip()) if x]
        phrase = sentences[-1] if len(sentences) >= 2 else ""
    escaped = html.escape(headline, quote=False)
    if phrase:
        target = html.escape(phrase, quote=False)
        escaped = escaped.replace(target, f'<span class="op-accent">{target}</span>', 1)
    return Markup(escaped)


# ---------------------------------------------------------------------------
# Byline, read time
# ---------------------------------------------------------------------------

def _words(page):
    n = 0
    for path, node in walk_page(page or {}):
        if isinstance(node, str) and not path.endswith(("asset_id", "cta_url", "style", "look", "hero_style",
                                                        "headline_template_id", "hero_quote_id")):
            n += len(node.split())
    return n


def read_minutes(page):
    return max(1, round(_words(page) / READ_WPM))


def display_date(iso):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def byline(tenant, page, updated):
    author = (tenant.authors or {}).get("author") or {}
    return {
        "name": author.get("name") or tenant.display_name,
        "title": tenant.get("first_screen.byline_title") or author.get("title") or "",
        "url": author.get("page") or "",
        "verified": tenant.get("first_screen.byline_verified_tick") is not False,
        "photo": tenant.get("first_screen.author_photo") or None,
        "updated": display_date(updated),
        "minutes": read_minutes(page),
    }


# ---------------------------------------------------------------------------
# Stats strip and value stack -- verified claims only
# ---------------------------------------------------------------------------

def _claims(facts_pack):
    return {c["id"]: c for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}


def _slug(facts_pack):
    return product_name_slug(((facts_pack or {}).get("product") or {}).get("name") or "")


_RATING_RE = re.compile(r"Rated\s+([\d.]+)\s+out of\s+5\s+across\s+([\d,]+)\s+reviews", re.IGNORECASE)
_WIDE_RE = re.compile(r"([\d.]+)\s*in(?:ches)?\s+wide", re.IGNORECASE)
_CAPACITY_RE = re.compile(r"Capacity:\s*([0-9]+(?:-[0-9]+)?-Person)", re.IGNORECASE)


def stats(facts_pack):
    """Up to three {value, label, claim_ids} for the strip: the rating (only
    when listicle.rating_line shows it), a standard outlet, the width, the
    capacity, free shipping -- each from one verified claim's own text."""
    from . import listicle

    claims, slug, out = _claims(facts_pack), _slug(facts_pack), []
    rating = listicle.rating_line(facts_pack)
    if rating:
        m = _RATING_RE.search(rating["text"])
        if m:
            out.append({"value": f"{m.group(1)} ★", "label": f"{m.group(2)} reviews",
                        "claim_ids": rating["claim_ids"]})
    elec = claims.get(f"spec-{slug}-electrical")
    if elec and "120V" in elec["text"] and "standard outlet" in elec["text"].lower():
        out.append({"value": "120V", "label": "Standard outlet", "claim_ids": [elec["id"]]})
    dims = claims.get(f"spec-{slug}-dimensions")
    m = _WIDE_RE.search(dims["text"]) if dims else None
    if m:
        out.append({"value": f"{round(float(m.group(1)))} in", "label": "Wide", "claim_ids": [dims["id"]]})
    cap = claims.get(f"spec-{slug}-capacity")
    m = _CAPACITY_RE.search(cap["text"]) if cap else None
    if m:
        out.append({"value": m.group(1), "label": "Capacity", "claim_ids": [cap["id"]]})
    ship = listicle.free_shipping_claim(facts_pack)
    if ship:
        out.append({"value": "Free", "label": "Shipping", "claim_ids": [ship["id"]]})
    return out[:3]


_PRICE_RE = re.compile(r"\$\d[\d,]*(?:\.\d\d)?")
_STACK_SKIP_RE = re.compile(r"warrant|financ|\$|price|lifetime|return|refund", re.IGNORECASE)
STACK_MAX_ITEMS = 6
STACK_MAX_WORDS = 16
# Cycle 79 fix 8: a stack line is a whole statement. Short taglines ("Medical-
# grade red light, where it works.", "Clasp-together assembly.") read oddly out
# of their product-page context.
STACK_MIN_WORDS = 7


def value_stack(facts_pack, page, tenant=None):
    """"What comes with the <model>": the model's own short verified feature
    lines (pdp-<model>-*, each its claim's text verbatim, warranty, price and
    financing wording left out -- those are fixed sentences elsewhere), the
    verified price, the page's own financing sentence. None when the facts
    pack verifies no feature line for the model."""
    tenant = tenant or tenant_mod.active()
    claims, slug = _claims(facts_pack), _slug(facts_pack)
    if not slug:
        return None
    items = []
    for cid, claim in claims.items():
        if not cid.startswith(f"pdp-{slug}-") or cid.endswith("-capacity"):
            continue
        text = claim["text"].strip()
        if not STACK_MIN_WORDS <= len(text.split()) <= STACK_MAX_WORDS or _STACK_SKIP_RE.search(text):
            continue
        items.append({"text": text.rstrip("."), "claim_ids": [cid]})
        if len(items) == STACK_MAX_ITEMS:
            break
    if not items:
        return None
    price = None
    price_claim = claims.get(f"price-{slug}")
    m = _PRICE_RE.search(price_claim["text"]) if price_claim else None
    if m:
        price = {"text": m.group(0), "claim_ids": [price_claim["id"]]}
    product = (facts_pack or {}).get("product") or {}
    full = tenant.product_names(product)["full_name"] or product.get("name")
    financing = ((page or {}).get("closing") or {}).get("financing_line") or {}
    return {"title": f"What comes with the {full}", "items": items, "price": price,
            "financing": financing.get("text") or ""}


# ---------------------------------------------------------------------------
# Fallbacks for a page written before cycle 79
# ---------------------------------------------------------------------------

_CUE_BY_STYLE = {
    "reasons": "{n} reasons, in order",
    "mistakes": "{n} mistakes to check first",
    "questions": "Question 1 of {n}",
    "myths": "{n} myths, checked",
    "tested": "{n} claims, checked",
}


def scroll_cue(page):
    cue = str((page or {}).get("scroll_cue") or "").strip()
    if cue:
        return cue
    n = len((page or {}).get("reasons") or [])
    return _CUE_BY_STYLE.get((page or {}).get("style"), "Keep reading").format(n=n)


def eyebrow(page, ad_brief):
    value = str((page or {}).get("eyebrow") or "").strip()
    if value:
        return value
    audience = str((ad_brief or {}).get("audience") or "").strip()
    return f"For {audience}" if audience and len(audience.split()) <= 5 else ""


def lede(page):
    value = str((page or {}).get("lede") or "").strip()
    return value or str((page or {}).get("dek") or "").strip()


# ---------------------------------------------------------------------------
# The render context
# ---------------------------------------------------------------------------

def display_font(tenant):
    value = tenant.get("first_screen.display_font") if tenant else None
    return value if value in DISPLAY_FONTS else "wide"


def render_context(page, *, ad_brief, facts_pack, tenant, hero_style, frame=None, updated=None):
    """Everything the look's first screen and value stack render that the
    writer did not write as such. `frame` is ad_frames.load(run_dir)."""
    quote = hero_quote(page, ad_brief, facts_pack)
    strip = stats(facts_pack)
    stack = value_stack(facts_pack, page, tenant)
    claim_ids = set()
    for s in strip:
        claim_ids.update(s["claim_ids"])
    if stack:
        for item in stack["items"]:
            claim_ids.update(item["claim_ids"])
        if stack["price"]:
            claim_ids.update(stack["price"]["claim_ids"])
    pronoun = speaker_pronoun(ad_brief=ad_brief, frame=frame, tenant=tenant)
    line = display_line(page)
    return {
        "hero_style": hero_style,
        "display_font": display_font(tenant),
        # display style: the short line in the wide face, else the headline in
        # the heading face (display_fits False)
        "display_fits": line is not None,
        "display_html": headline_markup(page, line) if line else headline_markup(page),
        "eyebrow": eyebrow(page, ad_brief),
        "headline_html": headline_markup(page),
        "accent_phrase": accent_phrase(page),
        "lede": lede(page),
        "scroll_cue": scroll_cue(page),
        "quote": quote,
        "pronoun": pronoun,
        "quote_frame": quote_frame(pronoun),
        "made_for": (f"In the ad {pronoun} made for {tenant.display_name}" if pronoun
                     else f"From the creator's ad for {tenant.display_name}"),
        "frame": frame,
        "byline": byline(tenant, page, updated),
        "stats": strip,
        "value_stack": stack,
        "claim_ids": claim_ids,
    }


# ---------------------------------------------------------------------------
# Gates (wired through listicle_quality.find_quality_violations)
# ---------------------------------------------------------------------------

EYEBROW_MAX_WORDS = 6
LEDE_MAX_WORDS = 60
CUE_MAX_WORDS = 8

_QUOTED_RE = re.compile(r"[\"“]([^\"“”]{3,}?)[\"”]")
_MOST_RE = re.compile(
    r"\b(?:most|nearly all|almost all|the majority of)\s+(?:people|buyers|shoppers|owners|customers|users|"
    r"homeowners|households|americans|athletes|families|sauna\s+(?:buyers|owners|users)|apartment\s+dwellers|"
    r"renters|parents|men|women)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^.?!]+[.?!]?")


def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


def _norm(text):
    text = (text or "").replace("’", "'").replace("‘", "'").lower()
    return " ".join(re.findall(r"[a-z0-9']+", text))


def find_verbatim_quote_violations(page, ad_brief):
    """A quoted span ("...") in the headline, dek, lede or eyebrow must be
    the ad speaker's own words, word for word (a contiguous run of the
    transcript). This is what lets the quoted-hook headline exist."""
    transcript = _norm((ad_brief or {}).get("transcript_or_text"))
    problems = []
    for field in ("headline", "dek", "lede", "eyebrow"):
        value = (page or {}).get(field)
        if not isinstance(value, str):
            continue
        for span in _QUOTED_RE.findall(value):
            if not transcript or _norm(span) not in transcript:
                problems.append(_problem(
                    f"$.{field}", f"listicle:quote_verbatim:$.{field}",
                    f'the quoted words "{span}" in the {field} are not the ad speaker\'s own words word for '
                    "word -- quote a run of ad_quotes exactly, or drop the quotation marks and the quote",
                    text=value,
                ))
    return problems


def find_most_people_violations(page):
    """Cycle 79 (owner): an unverified generalisation about "most people",
    "most buyers" ... is never asserted. As a question it is fine ("Do most
    buyers check the outlet first?"); with a claim_id on the same node it is
    a verified statement."""
    problems = []
    for path, node in walk_page(page or {}):
        if not isinstance(node, dict):
            continue
        cited = bool(node.get("claim_ids"))
        for key, value in node.items():
            if not isinstance(value, str) or key in ("asset_id", "cta_url"):
                continue
            # the speaker's own quoted words are hers, not the page's claim
            unquoted = _QUOTED_RE.sub(" ", value)
            for sentence in _SENTENCE_RE.findall(unquoted):
                if _MOST_RE.search(sentence) and not sentence.strip().endswith("?") and not cited:
                    sub = f"{path}.{key}"
                    problems.append(_problem(
                        sub, f"listicle:most_people:{sub}",
                        f'"{sentence.strip()}" asserts what most people do or think, and no verified claim says '
                        "so -- ask it as a question, or drop it",
                        text=value,
                    ))
                    break
    return problems


def find_first_screen_violations(page, ad_brief=None, facts_pack=None):
    """The writer's first-screen fields (cycle 79 schema)."""
    if not isinstance(page, dict):
        return []
    problems = []
    headline = str(page.get("headline") or "")
    eyebrow_v = page.get("eyebrow")
    if not isinstance(eyebrow_v, str) or not eyebrow_v.strip():
        problems.append(_problem("$.eyebrow", "listicle:first_screen:eyebrow",
                                 "eyebrow is missing: a 2-6 word label above the headline (who or what this is for)"))
    elif len(eyebrow_v.split()) > EYEBROW_MAX_WORDS or re.search(r"\d", eyebrow_v):
        problems.append(_problem("$.eyebrow", "listicle:first_screen:eyebrow",
                                 f"eyebrow {eyebrow_v!r} must be 2-{EYEBROW_MAX_WORDS} words with no number"))
    phrase = page.get("accent_phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        problems.append(_problem("$.accent_phrase", "listicle:first_screen:accent",
                                 "accent_phrase is missing: 1-6 words copied exactly from the headline"))
    elif phrase not in headline:
        problems.append(_problem("$.accent_phrase", "listicle:first_screen:accent",
                                 f"accent_phrase {phrase!r} is not part of the headline {headline!r}; copy 1-6 "
                                 "words of the headline exactly, same case and punctuation"))
    elif len(phrase.split()) > 6 or phrase.strip() == headline.strip():
        problems.append(_problem("$.accent_phrase", "listicle:first_screen:accent",
                                 f"accent_phrase {phrase!r} must be 1-6 words, not the whole headline"))
    lede_v = page.get("lede")
    if not isinstance(lede_v, str) or not lede_v.strip():
        problems.append(_problem("$.lede", "listicle:first_screen:lede",
                                 "lede is missing: 1-3 sentences that open the loop the items close"))
    elif len(lede_v.split()) > LEDE_MAX_WORDS:
        problems.append(_problem("$.lede", "listicle:first_screen:lede",
                                 f"lede is {len(lede_v.split())} words; at most {LEDE_MAX_WORDS}"))
    else:
        from . import vocab

        unquoted = _QUOTED_RE.sub(" ", lede_v)
        hit = re.search(r"[\d$%]", unquoted) or vocab.TRIGGER_WORD_RE.search(unquoted.lower())
        if hit:
            problems.append(_problem("$.lede", "listicle:first_screen:lede",
                                     f"the lede states {hit.group(0)!r}: it opens the loop -- no number, spec or "
                                     "trigger word (the items state the facts, with their claim_ids)", text=lede_v))
    cue = page.get("scroll_cue")
    if not isinstance(cue, str) or not cue.strip():
        problems.append(_problem("$.scroll_cue", "listicle:first_screen:scroll_cue",
                                 "scroll_cue is missing: 2-8 words under the first screen that say what comes next"))
    elif len(cue.split()) > CUE_MAX_WORDS:
        problems.append(_problem("$.scroll_cue", "listicle:first_screen:scroll_cue",
                                 f"scroll_cue is {len(cue.split())} words; at most {CUE_MAX_WORDS}"))
    display = page.get("display_headline")
    if not isinstance(display, str) or not display.strip():
        problems.append(_problem("$.display_headline", "listicle:first_screen:display_headline",
                                 f"display_headline is missing: a punchy 3-{DISPLAY_MAX_WORDS} word line (at most "
                                 f"{DISPLAY_MAX_CHARS} characters) for the display first screen"))
    elif not fits_display(display) or re.search(r"\d", display):
        problems.append(_problem("$.display_headline", "listicle:first_screen:display_headline",
                                 f"display_headline {display!r} must be 3-{DISPLAY_MAX_WORDS} words, at most "
                                 f"{DISPLAY_MAX_CHARS} characters, no number", text=display))
    candidates = quote_candidates(ad_brief, facts_pack) if ad_brief else []
    ids = {c["id"] for c in candidates}
    qid = page.get("hero_quote_id")
    if candidates and qid not in ids:
        problems.append(_problem(
            "$.hero_quote_id", "listicle:first_screen:hero_quote",
            (f"hero_quote_id {qid!r} is not an ad_quotes id" if qid else "hero_quote_id is missing")
            + f" -- name the one ad_quotes entry ({', '.join(sorted(ids))}) the first screen quotes",
        ))
    elif not candidates and qid:
        problems.append(_problem("$.hero_quote_id", "listicle:first_screen:hero_quote",
                                 "this ad has no quotable speaker (ad_quotes is empty); leave hero_quote_id out"))
    problems += find_verbatim_quote_violations(page, ad_brief)
    problems += find_most_people_violations(page)
    problems += find_speaker_pronoun_violations(page, ad_brief)
    problems += find_speaker_narration_violations(page)
    problems += find_quote_repeat_violations(page, ad_brief, facts_pack)
    problems += find_source_parenthetical_violations(page)
    return problems


# ---------------------------------------------------------------------------
# Cycle 79 review fixes 1, 2, 4, 5
# ---------------------------------------------------------------------------

def _unframed(text):
    """`text` without its quoted spans and its quote frames."""
    from .listicle_quality import _ALLOWED_FRAME_RE

    return _ALLOWED_FRAME_RE.sub(" ", _QUOTED_RE.sub(" ", text or ""))


def find_speaker_pronoun_violations(page, ad_brief):
    """Fix 1: a quote frame (or the headline) may say she/he only when the
    evidence (ad_brief.speaker_pronoun, set by prepare_speaker from the ad
    still check or a tenant override) says so; with no evidence the frame is
    neutral ("In the ad, the creator says,"). Not checked for a brief from
    before this rule (no speaker_pronoun key) -- the renderer still never
    guesses."""
    if not isinstance(ad_brief, dict) or "speaker_pronoun" not in ad_brief:
        return []
    want = evidence_pronoun(ad_brief)
    problems = []
    for path, node in walk_page(page or {}):
        if not isinstance(node, str):
            continue
        used = {m.group(1) or m.group(2) for m in _FRAME_PRONOUN_RE.finditer(node)}
        if path == "$.headline":
            used |= {w for w in re.findall(r"\b(she|he|her|his)\b", _QUOTED_RE.sub(" ", node), re.IGNORECASE)}
        used = {u.lower() for u in used if u}
        family = _PRONOUN_FAMILY.get(want, set())
        wrong = sorted(u for u in used if u not in family)
        if wrong:
            how = (f'the ad\'s speaker is "{want}"' if want else
                   'nothing shows the ad speaker\'s pronoun, so never write she/he -- use "In the ad, the creator '
                   'says, \"...\""')
            problems.append(_problem(path, f"listicle:speaker_pronoun:{path}",
                                     f'"{", ".join(wrong)}" for the ad\'s speaker: {how}', text=node))
    return problems


# Fields where the page may NOT narrate the speaker (fix 2). The headline is
# held to its template (o1/o4 are owner-approved speaker patterns).
_NARRATION_SKIP = ("$.headline", "$.accent_phrase", "$.eyebrow", "$.scroll_cue", "$.display_headline",
                   "$.speaker_pronoun", "$.hero_quote_id", "$.hero_style", "$.style", "$.look",
                   "$.headline_template_id", "$.cta_text")
_PAST_RE = re.compile(
    r"\b(?:was|were|had|took|kept|got|went|felt|found|spent|wanted|needed|tried|ended|started|decided|bought|"
    r"ordered|looked|thought|knew|used|missed|slipped|struggled|worried|realized|learned|drove|paid)\b",
    re.IGNORECASE)
_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours|you're|you've|you'd|you'll)\b", re.IGNORECASE)


def find_speaker_narration_violations(page):
    """Fix 2: outside a verbatim attributed quote the page never tells the
    speaker's story -- no she/he/her/his in any prose field (other than the
    quote frame), and no lede sentence that narrates a past ("the routine
    kept slipping") unless it speaks to the reader ("you") or asks a
    question. The loop the lede opens is about the reader or the product."""
    from .textutil import NON_PROSE_KEYS

    problems = []
    for path, node in walk_page(page or {}, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str) or path in _NARRATION_SKIP or path.endswith((".question",)):
            continue
        rest = _unframed(node)
        m = _SPEAKER_PRONOUN_RE.search(rest)
        if m:
            problems.append(_problem(path, f"listicle:speaker_narration:{path}",
                                     f'"{m.group(0)}" narrates the ad\'s speaker outside her own quoted words -- tell '
                                     "the speaker's story only through a verbatim ad_quotes entry; your own "
                                     "sentences speak to the reader (you) or about the product", text=node))
            continue
        if path == "$.lede":
            for sentence in _SENTENCE_RE.findall(rest):
                sentence = sentence.strip()
                if (sentence and not sentence.endswith("?") and _PAST_RE.search(sentence)
                        and not _SECOND_PERSON_RE.search(sentence)):
                    problems.append(_problem(path, "listicle:speaker_narration:$.lede",
                                             f'"{sentence}" tells a backstory nobody said -- the lede opens a loop '
                                             "about the reader (you) or the product; the speaker appears only in "
                                             "her own quoted words", text=node))
                    break
    return problems


def quotes_used_in_body(page, candidates):
    """{quote id} for every ad_quotes entry a page text field quotes."""
    from .quote_fidelity import quoted_spans

    used = set()
    for path, node in walk_page(page or {}):
        if not isinstance(node, str) or path == "$.hero_quote_id":
            continue
        for span in quoted_spans(node):
            n = _norm(span)
            if len(n.split()) < 4:
                continue
            for c in candidates:
                cn = _norm(c["text"])
                if n in cn or cn in n:
                    used.add(c["id"])
    return used


def find_quote_repeat_violations(page, ad_brief, facts_pack):
    """Fix 5: one ad quote at most once on the page -- the first screen's
    hero quote counts as one use, and each text field that quotes it as
    another."""
    from .quote_fidelity import quoted_spans

    candidates = quote_candidates(ad_brief, facts_pack) if ad_brief else []
    if not candidates:
        return []
    uses = {c["id"]: [] for c in candidates}
    if (page or {}).get("hero_quote_id") in uses:
        uses[page["hero_quote_id"]].append("$.hero_quote_id (the first screen)")
    for path, node in walk_page(page or {}):
        if not isinstance(node, str) or path == "$.hero_quote_id":
            continue
        hit = set()
        for span in quoted_spans(node):
            n = _norm(span)
            if len(n.split()) < 4:
                continue
            for c in candidates:
                cn = _norm(c["text"])
                if n in cn or cn in n:
                    hit.add(c["id"])
        for qid in hit:
            uses[qid].append(path)
    problems = []
    for qid, paths in uses.items():
        if len(paths) > 1:
            problems.append(_problem(paths[-1], f"listicle:quote_repeat:{qid}",
                                     f"ad quote {qid} appears {len(paths)} times ({', '.join(paths)}) -- each ad "
                                     "quote at most once on the page, the first screen included; use another "
                                     "ad_quotes entry or none"))
    return problems


SOURCE_PAREN_RE = re.compile(
    r"\s*\((?=[^()]*(?:\bpage\b|\bpolicy\b|\bpolicies\b|\bsite\b|\bspec sheet\b|\bmanual\b|\bFAQ\b|"
    r"\bsource\b|\b(?:19|20)\d\d\b))[^()]{2,80}\)",
    re.IGNORECASE)
_SOURCE_PAREN_KEYS = ("text", "answer")


def find_source_parenthetical_violations(page):
    """Fix 4: no "(<brand> product page, 2026)" in the copy -- the Sources
    section names sources; the claim_ids carry them."""
    problems = []
    for path, node in walk_page(page or {}):
        if isinstance(node, str) and path.rsplit(".", 1)[-1] in _SOURCE_PAREN_KEYS:
            m = SOURCE_PAREN_RE.search(node)
            if m:
                problems.append(_problem(path, f"listicle:source_parenthetical:{path}",
                                         f'"{m.group(0).strip()}" names a source in the copy -- drop it; the '
                                         "Sources section lists sources from the claim_ids", text=node))
    return problems


def strip_source_parentheticals(page):
    """Render-time backstop for fix 4 (a page written before the gate):
    the parenthetical source notes removed from item, proof and FAQ text."""
    import copy as _copy

    out = _copy.deepcopy(page)
    for path, node in walk_page(out):
        if isinstance(node, dict):
            for key in _SOURCE_PAREN_KEYS:
                if isinstance(node.get(key), str):
                    node[key] = SOURCE_PAREN_RE.sub("", node[key])
    return out


def pronoun_allowed(page, ad_brief, facts_pack):
    """Cycle 79 gate change (owner): "she"/"he" in the headline is allowed
    when it refers to the attributed ad speaker -- the ad is first person,
    the page carries that speaker's verbatim quote (a valid hero_quote_id),
    and (review fix 1) the evidence gives that pronoun. The dek is held to
    the narration rule (find_speaker_narration_violations)."""
    if not isinstance(page, dict) or not ad_brief:
        return False
    if (ad_brief.get("speaker_pov") or "") in ("brand", "none"):
        return False
    if "speaker_pronoun" in ad_brief and not evidence_pronoun(ad_brief):
        return False
    ids = {c["id"] for c in quote_candidates(ad_brief, facts_pack)}
    return page.get("hero_quote_id") in ids


def writer_lines():
    """The hard-constraint lines for the first-screen fields."""
    return [
        "First screen (cycle 79 schema): the page opens a loop and never gives the answer away up front. "
        "Write these top-level fields:",
        '"eyebrow": 2-6 words above the headline naming who or what this is for ("Small-space saunas", '
        '"For people who train most days"); no number.',
        '"headline": from the ad\'s own hook, 6-13 words, and it must NOT answer the question the items answer '
        "(no spec, price or verdict in it). Follow this run's headline template.",
        '"accent_phrase": 1-6 words copied EXACTLY from the headline (same case and punctuation) -- the phrase '
        "the page sets in the brand colour, usually the turn or the payoff of the headline.",
        '"display_headline": a punchy 3-8 word line, at most 48 characters, the same promise as the headline '
        '("No electrician. No spare room.", "Train hard. Recover at home.") -- the display first screen sets it '
        "big; no number.",
        '"dek": one plain sentence under the headline that states the ad\'s problem in its own words, to the '
        "reader -- never a story about the speaker.",
        '"lede": 1-3 short sentences that open the loop the items close -- about the READER (you) or the product, '
        "never a backstory of the speaker: no she/he/her/his outside her quoted words, nothing she did or felt "
        "that she did not say. No answer, no number, no spec.",
        '"scroll_cue": 2-8 words under the first screen that say what comes next ("5 things apartment buyers '
        'check first", "Question 1 of 6").',
        '"hero_quote_id": the id of the ONE ad_quotes entry the first screen shows as the speaker\'s own words '
        "(the most vivid, usually the hook). Leave it out only when ad_quotes is empty.",
        "The speaker's pronoun is ad_brief.speaker_pronoun -- never guess it. When it is \"she\" or \"he\", a "
        "quote frame may say \"In the ad, she says, \"...\"\"; when it is \"unknown\" (or missing), every quote "
        "frame is \"In the ad, the creator says, \"...\"\" and no she/he appears anywhere on the page.",
        "The speaker appears ONLY in her own verbatim quotes. Outside quotation marks no sentence anywhere tells "
        "her story (no she/he/her/his, nothing she did or felt). Each ad_quotes entry appears at most once on the "
        "whole page -- the hero_quote_id counts as one use, so never quote that entry again in an item.",
        "Quoted words in the headline, dek or lede are the speaker's own, copied word for word from ad_quotes "
        "inside quotation marks.",
        "Never write a source note in the copy (\"(<brand> product page, 2026)\", \"(per the warranty page)\") -- "
        "claim_ids carry the source and the Sources section lists it.",
        "Never assert what \"most people\", \"most buyers\" (or most anyone) do or think -- no verified claim "
        "says so. Ask it as a question, or leave it out.",
    ]


def renderer_owned_keys():
    return ("value_stack", "stats", "byline")


# ---------------------------------------------------------------------------
# Pipeline hook: the ad still and the hero style, before the page renders
# ---------------------------------------------------------------------------

def ad_source(run_dir, args_input, ad_brief):
    """The ad's own media file: the input path, else the file ingest
    downloaded into the run dir (ad_brief.source_file)."""
    from pathlib import Path

    local = Path(str(args_input or ""))
    if args_input and local.is_file():
        return local
    name = (ad_brief or {}).get("source_file")
    if name and (Path(run_dir) / name).is_file():
        return Path(run_dir) / name
    return None


def _build_frame(state):
    from . import ad_frames

    run_dir = state.run_dir
    if (ad_frames.frame_dir(run_dir) / ad_frames.RECORD).exists():
        return
    src = ad_source(run_dir, getattr(state.args, "input", None), state.ad_brief)
    if src is None:
        return
    dry = type(state.client).__name__ == "FakeClient"
    ad_frames.build_for_input(
        src, run_dir, ffmpeg_bin=getattr(state.args, "ffmpeg_bin", None) or "ffmpeg",
        client=None if dry else state.client,
        model=state.tenant.get("models.vision") or ad_frames.DEFAULT_VISION_MODEL,
        budget=state.budget, log=state.log,
        retired_mark=state.tenant.get("first_screen.retired_mark"),
    )


def prepare_speaker(state):
    """Called by pipeline.write_pages BEFORE the writer for a listicle run
    (review fix 1): makes the ad still (one cheap vision call on a real run,
    the sharpness heuristic on a dry run) and puts the speaker's pronoun on
    the ad brief from evidence only -- a tenant override, else the still
    check when it was unambiguous, else "unknown". The writer reads it there
    (ad_brief is in its user message) and the gate holds the page to it."""
    import json as _json

    from . import ad_frames

    _build_frame(state)
    record = ad_frames.load(state.run_dir)
    if record is None:
        try:
            record = _json.loads((ad_frames.frame_dir(state.run_dir) / ad_frames.RECORD).read_text())
        except (OSError, ValueError):
            record = None
    pronoun = evidence_pronoun(state.ad_brief, record, state.tenant)
    state.ad_brief["speaker_pronoun"] = pronoun or "unknown"
    state.log.event("run", f"ad speaker pronoun: {state.ad_brief['speaker_pronoun']} (evidence only)")
    path = state.run_dir / "ad_brief.json"
    if path.exists():
        path.write_text(_json.dumps(state.ad_brief, indent=2))


def prepare(state):
    """Called by pipeline.render_pages for a listicle run: resolves the hero
    style (--hero-style / an A/B/C arm, else seeded; face falls back when
    there is no still), stamps it on page.json and records it in state.json.
    The still itself was made before the writer ran (prepare_speaker)."""
    from . import ad_frames, runstate

    page = (state.pages or {}).get("listicle")
    if page is None:
        return
    run_dir = state.run_dir
    _build_frame(state)   # a run that skipped write_pages (a workflow) still gets one
    quote = hero_quote(page, state.ad_brief, state.facts_pack)
    style, note = resolve_hero_style(
        getattr(state.args, "hero_style", None), seed=state.seed, tenant=state.tenant,
        frame=ad_frames.load(run_dir) is not None, quote=quote is not None,
    )
    if note:
        state.log.event("run", f"hero style: {note}")
    state.log.event("run", f"listicle hero style: {style}")
    page["hero_style"] = style
    runstate.record_listicle_choice(run_dir, hero_style=style)
