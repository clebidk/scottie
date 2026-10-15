"""Cycle 110: the copy rules of a pre-sell page that clicks (brief
~/asset-inbox/c110/BRIEF.md section 2, teardown TEARDOWN.md).

The cycle 82 landing page was built as a compliance audit ("The ad,
checked"): it graded the ad the reader had just believed, wrote a spec label
as its headline and answered the installer's questions. 40 live pages, 2.0%
CTA click-through. This module holds the deterministic copy checks that
replace that frame, shared by the landing cartridge and the listicle:

  - find_copy_hygiene_violations: no placeholder text, no returns /
    restocking / risk-free copy, no "applies automatically" next to an offer
    that needs a code (phase 0);
  - find_ad_reference_violations: the page never quotes, grades or corrects
    the ad ("the ad", "actually", "not lifetime", "priced plainly", ...);
  - find_hook_match_violations: the first screen restates the ad's hook;
  - find_reading_level_violations: grade 7.5 or lower (Flesch-Kincaid);
  - ad_angle / cta_texts_for_angle: the CTA phrase by the ad's angle.

Tenant-neutral: banned phrases a tenant adds live in vocab.yaml
`banned_phrases`; no company or product word is written here.
"""
import re

from .textutil import NON_PROSE_KEYS, walk_page

# ---------------------------------------------------------------------------
# Phase 0: hygiene
# ---------------------------------------------------------------------------

# Two live pages printed "Placeholder for offer, rendered separately by the
# page." / "placeholder" as a checked claim (two sale-ad arms, 2026-10-14).
PLACEHOLDER_RE = re.compile(r"(?i:\bplaceholder\b|\blorem\b|\bipsum\b|rendered\s+separately)|\bTODO\b|\bTBD\b")

# Returns are a deterrent for this kind of product (a restocking fee, the buyer pays return
# shipping), never a reversal: no copy about returns, refunds or a risk-free
# trial. aiir-300 A had "Can I return it if it doesn't fit my space?".
RETURNS_RE = re.compile(
    r"\brestock(?:ing)?\b|\brefund(?:s|ed|able)?\b|\breturn\s+(?:policy|policies|shipping|window|process|label)\b"
    r"|\breturns?\s+(?:are|is|require|requires|accepted|within|must|take)\b|\b(?:can|could)\s+(?:i|you|we)\s+return\b"
    r"|\breturn\s+it\b|\bsend\s+it\s+back\b|\bmoney[-\s]back\b|\brisk[-\s]free\b|\bfree\s+trial\b"
    r"|\b\d+[-\s]day\s+trial\b|\bchange\s+(?:my|your)\s+mind\b",
    re.IGNORECASE)

# "The discount applies automatically at checkout" (two live pages) while the
# offer needs a code: a buyer who skips the code pays the full price.
AUTO_WORD_RE = re.compile(r"^automatic(?:ally)?$", re.IGNORECASE)
OFFER_WORD_RE = re.compile(r"^(?:discounts?|offers?|codes?|sales?|savings?|promo(?:tion)?s?|coupons?|deals?)$",
                           re.IGNORECASE)
AUTO_WINDOW = 8


def _prose(page):
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if isinstance(node, str) and node.strip():
            yield path, node


def _problem(path, key, issue, **extra):
    return {"path": path, "key": key, "issue": issue, **extra}


def says_automatic_offer(text):
    """True when "automatic(ally)" sits within AUTO_WINDOW words of an offer
    word (discount, offer, code, sale, savings, promo, coupon, deal)."""
    words = [w.strip(".,;:!?()\"'\u201c\u201d\u2019") for w in str(text or "").split()]
    for i, w in enumerate(words):
        if AUTO_WORD_RE.match(w):
            near = words[max(0, i - AUTO_WINDOW): i + AUTO_WINDOW + 1]
            if any(OFFER_WORD_RE.match(x) for x in near):
                return True
    return False


def offer_has_code(facts_pack):
    """The page's offer needs a code: the verified offer's codes, or a code
    the ad itself states (an unverified offer)."""
    from . import offer as offer_mod

    fp = facts_pack or {}
    view = offer_mod.offer_view(fp)
    if view and view.get("codes"):
        return True
    unverified = fp.get("offer_unverified")
    return isinstance(unverified, dict) and bool(unverified.get("codes"))


def find_copy_hygiene_violations(page, facts_pack=None, prefix="landing"):
    """Placeholder text, returns copy, and "automatic" next to an offer word
    when the offer needs a code -- anywhere in the writer's prose."""
    problems = []
    auto = offer_has_code(facts_pack) or bool((facts_pack or {}).get("offer"))
    for path, text in _prose(page):
        if PLACEHOLDER_RE.search(text):
            problems.append(_problem(path, f"{prefix}:placeholder:{path}",
                                     "placeholder text on the page -- write the real line, or leave the item out "
                                     "(the renderer adds the offer itself)", text=text))
        if RETURNS_RE.search(text):
            problems.append(_problem(path, f"{prefix}:returns:{path}",
                                     "no copy about returns, refunds, restocking or a risk-free trial -- leave "
                                     "returns off the page entirely", text=text))
        if auto and says_automatic_offer(text):
            problems.append(_problem(path, f"{prefix}:auto_offer:{path}",
                                     "the offer needs its code at checkout -- never say it applies automatically",
                                     text=text))
    return problems


HERO_REQUIRED = ("eyebrow", "headline", "subhead")


def find_hero_required_violations(page, prefix="landing"):
    """One live page had a blank subhead: every first-screen line is written."""
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else {}
    return [_problem(f"$.hero.{f}", f"{prefix}:hero_required:{f}", f"hero.{f} is required and may not be empty")
            for f in HERO_REQUIRED if not str(hero.get(f) or "").strip()]


# ---------------------------------------------------------------------------
# Phase 2: never grade the ad
# ---------------------------------------------------------------------------

# Brief 3.3 item 3, plus the phrases the teardown counted on the live pages.
AD_REFERENCE_RE = re.compile(
    r"\bthe\s+ad\b|\bthe\s+ad's\b|\bactually\b|\breal\s+(?:price|prices|numbers?|specs?)\b"
    r"|\bnot\s+(?:verified|part\s+of|lifetime)\b|\bdoes\s+not\s+verify\b|\bdoesn't\s+verify\b|\bno\s+separate\b"
    r"|\bplainly\b|\bnothing\s+to\s+submit\b|\bno\s+form\s+required\b|\bwhat\s+holds\s+up\b"
    r"|\bwhat'?s\s+(?:actually\s+)?verified\b|\bthe\s+ad\s+(?:said|says|claimed|promised|implied|shows?)\b",
    re.IGNORECASE)
# The one way the page may name the ad: crediting the speaker's own words
# ("In the ad, she says ..."), the framing quote_fidelity requires.
ATTRIBUTION_FRAME_RE = re.compile(
    r"\bin\s+the\s+ad,?\s+(?:she|he|they|the\s+creator|the\s+speaker|[A-Z][a-z]+)\s+(?:says|said)\b", re.IGNORECASE)
ADD_ON_RE = re.compile(r"\bnot\s+an\s+add-?on\b", re.IGNORECASE)


def banned_phrases(tenant=None):
    from . import vocab

    v = vocab.active_or_none()
    return tuple(getattr(v, "banned_phrases", ()) or ()) if v is not None else ()


def find_ad_reference_violations(page, prefix="landing", extra_phrases=None):
    """No line quotes, paraphrases, grades or corrects the ad, and no
    defensive tic ("actually", "priced plainly"); vocab.yaml banned_phrases
    too. "not an add-on" at most once on the page."""
    phrases = tuple(extra_phrases if extra_phrases is not None else banned_phrases())
    problems = []
    add_on = 0
    for path, text in _prose(page):
        scan = ATTRIBUTION_FRAME_RE.sub(" ", text)
        m = AD_REFERENCE_RE.search(scan)
        hit = m.group(0) if m else next(
            (p for p in phrases if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", scan, re.IGNORECASE)), None)
        if hit:
            problems.append(_problem(path, f"{prefix}:ad_reference:{path}",
                                     f"{hit!r}: never quote, grade or correct the ad, and no defensive words -- "
                                     "write to the reader's outcome; an ad claim the facts do not support is simply "
                                     "left out", text=text))
        add_on += len(ADD_ON_RE.findall(text))
    if add_on > 1:
        problems.append(_problem("$", f"{prefix}:ad_reference:add_on", f"'not an add-on' appears {add_on} times; at "
                                 "most once on the page"))
    return problems


# ---------------------------------------------------------------------------
# Phase 2: hook match
# ---------------------------------------------------------------------------

_STOP = frozenset("""a an and are as at be been but by can do does for from has have how i if in into is it its
just me my no not of on or our so that the their them then there these they this to too up us was we were what
when where which who why will with you your yours it's i'm you're we're that's here more most very all any every
each one get got make makes made new now own same than out about over off""".split())


def _stem(word):
    for suf in ("'s", "’s"):
        if word.endswith(suf):
            word = word[: -len(suf)]
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def content_words(text, exclude=()):
    """Lower-case content words (stop words dropped, light stemming); a
    figure keeps its digits ("$300" -> "300", "8,250" -> "8250")."""
    out = set()
    for tok in re.findall(r"[A-Za-z0-9$][A-Za-z0-9$,'’.-]*", str(text or "")):
        tok = tok.lower().strip(".,'’-")
        if re.search(r"\d", tok):
            tok = re.sub(r"[^\d]", "", tok)
            if tok:
                out.add(tok)
            continue
        tok = tok.replace("$", "")
        if len(tok) < 3 or tok in _STOP:
            continue
        stem = _stem(tok)
        if stem not in exclude and tok not in exclude:
            out.add(stem)
    return out


def _norm_quote(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(text or "").lower())).strip()


def hook_match(hero_text, ad_brief, exclude=(), quotes=()):
    """{"overlap": [...], "score": overlap / hook words, "quoted": bool,
    "ok": bool} -- content-word overlap between the first screen and the
    ad's hook + angle + promise (>= 2 words), or a quoted ad sentence."""
    brief = ad_brief or {}
    ad_words = content_words(" ".join(str(brief.get(k) or "") for k in ("hook", "angle", "promise")), exclude)
    hook_words = content_words(brief.get("hook"), exclude) or ad_words
    hero_words = content_words(hero_text, exclude)
    overlap = sorted(hero_words & ad_words)
    norm_hero = _norm_quote(hero_text)
    quoted = any(len(_norm_quote(q).split()) >= 5 and _norm_quote(q) in norm_hero for q in quotes)
    return {"overlap": overlap, "score": round(len(set(overlap) & hook_words) / len(hook_words), 2) if hook_words
            else 0.0, "quoted": quoted, "ok": len(overlap) >= 2 or quoted}


def find_hook_match_violations(page, ad_brief, *, facts_pack=None, filled_hero=None, exclude=(), quotes=(),
                               prefix="landing"):
    """landing:hook_match -- the first screen (eyebrow + headline + subhead,
    figure tokens filled) restates the ad's hook: at least two content words
    of the hook/angle/promise, or one of the speaker's sentences quoted."""
    brief = ad_brief or {}
    if not any(str(brief.get(k) or "").strip() for k in ("hook", "angle", "promise")):
        return []
    hero = filled_hero if filled_hero is not None else (page.get("hero") or {})
    text = " ".join(str(hero.get(k) or "") for k in ("eyebrow", "headline", "subhead"))
    result = hook_match(text, brief, exclude=exclude, quotes=quotes)
    if result["ok"]:
        return []
    return [_problem("$.hero.headline", f"{prefix}:hook_match",
                     "the first screen must pay off the ad's hook: restate it as closely as the verified facts "
                     f"allow -- same words, same number, same person. The hook: {brief.get('hook')!r}. Shared "
                     f"content words now: {result['overlap'] or 'none'}", text=text)]


# ---------------------------------------------------------------------------
# Phase 2: reading level
# ---------------------------------------------------------------------------

MAX_GRADE = 7.5

try:  # agent A's helper when it is merged (same name and signature)
    from .textutil import grade_level  # noqa: F401
except ImportError:  # pragma: no cover - exercised until A's textutil lands
    def _syllables(word):
        w = re.sub(r"[^a-z]", "", word.lower())
        if not w:
            return 1
        groups = re.findall(r"[aeiouy]+", w)
        n = len(groups)
        if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
            n -= 1
        return max(1, n)

    def grade_level(text):
        """Flesch-Kincaid grade of `text` (0.0 for no words)."""
        text = str(text or "")
        sentences = [s for s in re.split(r"[.!?]+(?:\s|$)|\n+", text) if re.search(r"[A-Za-z0-9]", s)]
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text)
        if not words:
            return 0.0
        syll = sum(sum(_syllables(p) for p in w.split("-")) for w in words)
        return round(0.39 * len(words) / max(1, len(sentences)) + 11.8 * syll / len(words) - 15.59, 1)


READING_FIELDS_RE = re.compile(r"^\$\.(?:hero\.(?:headline|subhead)|reasons\[\d+\]\.(?:headline|body)|"
                               r"faq\.questions\[\d+\]\.(?:question|answer)|closing\.headline|items\[\d+\]\.\w+|"
                               r"reasons\[\d+\]\.\w+)$")


def reading_text(page, exclude=()):
    """The writer's selling copy as sentences (each field ends a sentence),
    product and brand names dropped (a model name is not a hard word)."""
    parts = []
    for path, text in _prose(page):
        if READING_FIELDS_RE.match(path):
            t = text
            for name in exclude:
                t = re.sub(r"(?i)\b" + re.escape(name) + r"\b", "it", t)
            parts.append(t.rstrip(" .!?") + ".")
    return " ".join(parts)


def find_reading_level_violations(page, exclude=(), prefix="landing", max_grade=MAX_GRADE):
    text = reading_text(page, exclude)
    if len(text.split()) < 40:
        return []
    grade = grade_level(text)
    if grade <= max_grade:
        return []
    return [_problem("$", f"{prefix}:reading_level",
                     f"the copy reads at grade {grade}; write for grade {max_grade} or lower -- shorter sentences "
                     "(under 14 words), everyday words of one or two syllables, one idea per sentence",
                     grade=grade)]


# ---------------------------------------------------------------------------
# Phase 2: the ad's angle and the CTA phrase
# ---------------------------------------------------------------------------

ANGLES = ("sale", "financing", "story", "default")
_FINANCING_RE = re.compile(r"payment\s+plans?|\bfinanc\w*|per\s+month|/\s?mo\b|\bmonthly\b|pay\s+over\s+time|"
                           r"\bafford\w*|\binstall?ments?\b|\b0%\s*apr\b", re.IGNORECASE)


def ad_angle(ad_brief, facts_pack=None):
    """sale (a verified offer), financing (the ad sells payments), story (a
    first-person speaker: a testimonial or creator ad), else default."""
    from . import offer as offer_mod

    brief = ad_brief or {}
    if offer_mod.offer_view(facts_pack or {}) is not None:
        return "sale"
    head = " ".join(str(brief.get(k) or "") for k in ("hook", "angle", "promise"))
    if _FINANCING_RE.search(head):
        return "financing"
    if str(brief.get("speaker_pov") or "") == "first_person":
        return "story"
    return "default"


def cta_texts_for_angle(templates, angle, *, short_name, offer_amount=None, pronoun=None):
    """The allowed CTA phrases for this angle from schema.json's map (a plain
    list keeps working). A phrase whose figure or pronoun this page lacks is
    dropped; the default phrases are the fallback."""
    if isinstance(templates, list):
        groups = [templates]
    else:
        groups = [templates.get(angle) or [], templates.get("default") or []] if angle != "default" else \
            [templates.get("default") or []]
    values = {"short_name": short_name, "model_name": short_name, "offer_amount": offer_amount, "pronoun": pronoun}
    out = []
    for group in groups:
        for t in group:
            names = re.findall(r"\{([a-z_]+)\}", t)
            if any(not values.get(n) for n in names):
                continue
            text = t.format(**{n: values[n] for n in names})
            if text not in out:
                out.append(text)
        if out:
            break
    return out


# ---------------------------------------------------------------------------
# Phase 2: the buyer's FAQ
# ---------------------------------------------------------------------------

LOGISTICS_Q_RE = re.compile(r"\b(?:deliver\w*|ship\w*|freight|assembl\w*|install\w*|set\s?up|outlet|electric\w*|"
                            r"wir(?:e|ing)|volt\w*|amps?|circuit|breaker|plug\w*|warrant\w*)\b", re.IGNORECASE)
HSA_Q_RE = re.compile(r"\bhsa\b|\bfsa\b|health\s+savings|flexible\s+spending", re.IGNORECASE)
MAX_LOGISTICS_QUESTIONS = 1


def find_faq_rule_violations(page, facts_pack=None, prefix="landing"):
    """The FAQ answers the buyer: at most one logistics question (delivery,
    assembly, power, warranty -- the renderer's "Delivery & setup" row holds
    the rest); an HSA/FSA question only with a verified HSA/FSA claim."""
    from . import landing_sell

    faq = page.get("faq") if isinstance(page.get("faq"), dict) else {}
    questions = faq.get("questions") if isinstance(faq.get("questions"), list) else []
    problems = []
    logistics = [i for i, q in enumerate(questions) if isinstance(q, dict)
                 and LOGISTICS_Q_RE.search(str(q.get("question") or ""))]
    if len(logistics) > MAX_LOGISTICS_QUESTIONS:
        problems.append(_problem(f"$.faq.questions[{logistics[1]}]", f"{prefix}:faq_logistics",
                                 f"{len(logistics)} FAQ questions are logistics (delivery, assembly, power, "
                                 "warranty); at most one -- the page already shows a Delivery & setup row. Ask what "
                                 "a buyer asks before spending this much: will I use it, how long is a session, how "
                                 "fast does it heat, will it fit, can I pay over time"))
    hsa = landing_sell.hsa_line(facts_pack)
    for i, q in enumerate(questions):
        if isinstance(q, dict) and HSA_Q_RE.search(str(q.get("question") or "")) and not hsa:
            problems.append(_problem(f"$.faq.questions[{i}]", f"{prefix}:faq_hsa",
                                     "no verified HSA/FSA claim on this page: no HSA/FSA question"))
    return problems
