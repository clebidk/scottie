"""Cycle 110 (agent C): listicle parity with the pre-sell landing page
(~/asset-inbox/c110/BRIEF.md section 2 and 3.3 item 9; TEARDOWN.md D1, D2,
D5, D10).

The listicle gets the landing page's persuasion model without its look:

  - items are reasons: each serves one lever of the Value Equation
    (`lever`: outcome, likelihood, time, effort -- all four across the
    page), the heading leads with the outcome and is never a spec label,
    the body is at most 45 words (how it works + the spec that makes it
    true), and the proof line ADDS a verified proof element: `proof_ref`
    names a review quote, the store rating or the units line, which the
    renderer prints word for word (a writer never types a review);
  - the first screen pays off the ad's hook (persuasion.hook_match), the
    page never grades the ad (persuasion.AD_REFERENCE_RE, vocab
    banned_phrases), and the copy reads at grade 7.5 or lower
    (persuasion.grade_level, agent A's textutil.grade_level once merged);
  - the CTA phrase follows the ad's angle (persuasion.cta_texts_for_angle
    over the schema's map);
  - the renderer adds a proof row and a CTA under the first screen, a value
    stack from facts_pack.included (price, sale net price and deadline,
    the monthly figure with its disclosure, the store rating, the units
    line) and a review wall of up to 3 verbatim review quotes -- every
    piece from landing_sell's facts_pack contract readers, each with the
    fallback that module documents when agent A's field is absent;
  - the quality numbers of a rendered page go to state.json
    listicle.quality (record_quality) for the review site.

Nothing here composes a claim. Tenant-neutral: names and figures come from
facts_pack and tenant.yaml.
"""
import json
import re
from pathlib import Path

from . import landing_sell, persuasion
from .claims import _QUOTED_SPAN_RE
from .persuasion import MAX_GRADE, grade_level
from . import quote_fidelity
from .quote_fidelity import _content_stems
from .textutil import NON_PROSE_KEYS, product_name_slug, walk_page

PREFIX = "listicle"
LEVERS = landing_sell.LEVERS
ITEM_HEADING_MAX = 10
ITEM_BODY_MAX = 45
# At least this many items carry a proof_ref when the facts pack offers that
# many proof elements (fewer when it offers fewer).
MIN_PROOF_REFS = 2
# A heading is a spec label when this share of its content words is in the
# product's own verified claims (the myths gate's cut, listicle_quality).
SPEC_LABEL_SHARE = 0.8
SPEC_LABEL_MIN_WORDS = 3
QUOTE_WALL = 3
_READER_RE = re.compile(r"\b(?:you|your|yours|you're|you'll|you've)\b", re.IGNORECASE)
# A figure with a unit in a heading is a spec ("120V/20A outlet", "150°F").
_SPEC_FIGURE_RE = re.compile(
    r"\d[\d,.]*\s?(?:°\s?[FC]|V\b|A\b|W\b|nm\b|in\b|inch(?:es)?\b|ft\b|feet\b|lbs?\b|%|-?person\b)|\$\s?\d",
    re.IGNORECASE)
_COUNT_WORDS = frozenset("one two three four five six seven eight nine ten".split())
_SOURCE_TAIL_RE = re.compile(r"\s*\([^)]*\)\s*\.?\s*$")


def _problem(path, key, issue, **extra):
    return {"path": path, "key": key, "issue": issue, **extra}


def _items(page):
    reasons = (page or {}).get("reasons")
    return [(i, it) for i, it in enumerate(reasons) if isinstance(it, dict)] if isinstance(reasons, list) else []


def _proof_ref(item):
    proof = item.get("proof")
    return str(proof.get("proof_ref") or "").strip() if isinstance(proof, dict) else ""


# ---------------------------------------------------------------------------
# Proof elements: the verified pieces an item's proof line may point at
# ---------------------------------------------------------------------------

def _visible_banned():
    from . import vocab

    try:
        v = vocab.active_or_none()
    except Exception:  # noqa: BLE001 -- a bare test vocabulary
        v = None
    if v is None:
        return ()
    terms = tuple(getattr(v, "visible_text_forbidden_terms", ()) or ())
    return terms + tuple(getattr(v, "emf_terms", ()) or ()) + tuple(getattr(v, "banned_names", ()) or ())


def review_quotes(facts_pack, n=100):
    """landing_sell.review_quotes (verified, 4+ stars, the page's product
    first), minus a quote that names a banned term (EMF, a competitor): the
    rendered-text gate would stop the page. A quote's "!" and hype words are
    the customer's own and stay (it is never in page.json, so the writer
    gates never see it)."""
    banned = [t.lower() for t in _visible_banned() if t and t != "!"]
    out = []
    for q in landing_sell.review_quotes(facts_pack, n=n):
        low = q["text"].lower()
        if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low) for t in banned):
            continue
        out.append(q)
    return out


def _plain(text):
    """A claim's own sentence without its source note: "(fetched ...)",
    "(Shopify, Oct 2026)" -- Sources lists those."""
    text = _SOURCE_TAIL_RE.sub("", str(text or "")).rstrip()
    return text + ("" if text.endswith((".", "!", "?")) else ".")


def proof_elements(facts_pack):
    """{claim_id: {"kind": "quote"|"rating"|"units", "text", "author",
    "stars", "claim_ids"}} -- what a proof_ref may name: each review quote,
    the store rating (else the product rating), the units line. {} when the
    facts pack verifies none."""
    fp = facts_pack or {}
    out = {}
    for q in review_quotes(fp):
        out[q["claim_ids"][0]] = {"kind": "quote", "text": q["text"], "author": q["author"], "stars": q["stars"],
                                  "claim_ids": list(q["claim_ids"])}
    rating = landing_sell.store_rating(fp)
    if rating:
        claims = landing_sell.claims_by_id(fp)
        cid = rating["claim_ids"][0]
        text = _plain((claims.get(cid) or {}).get("text") or rating["text"])
        out[cid] = {"kind": "rating", "text": text, "author": "", "stars": None, "claim_ids": list(rating["claim_ids"])}
    unit = landing_sell.units(fp)
    if unit:
        out[unit["claim_ids"][0]] = {"kind": "units", "text": _plain(unit["text"]), "author": "", "stars": None,
                                     "claim_ids": list(unit["claim_ids"])}
    return out


_QUOTE_MARKS = "\"'\u201c\u201d\u2018\u2019 "


def is_verbatim_review_quote(text, facts_pack):
    """True when `text` (outer quotation marks and spaces aside) is exactly
    one of the facts pack's verified review quotes."""
    want = str(text or "").strip(_QUOTE_MARKS)
    return bool(want) and any(want == q["text"].strip(_QUOTE_MARKS) for q in review_quotes(facts_pack))


def drop_review_quote_style_hits(problems, facts_pack):
    """The claims gate bans "!" and hype words in writer prose. A field whose
    whole text is a verified customer review, word for word, is the
    customer's voice, not the brand's: its "!" and hype-word hits are
    dropped (BRIEF 3.2). Any other "!" still fails, and every other check on
    that field stands."""
    from . import vocab

    try:
        hype = {w.lower() for w in (vocab.HYPE_WORDS or ())}
    except Exception:  # noqa: BLE001 -- a stand-in vocabulary
        hype = set()
    out = []
    for p in problems:
        term = str(p.get("term") or "").lower()
        if (term == "!" or term in hype) and is_verbatim_review_quote(p.get("text"), facts_pack):
            continue
        out.append(p)
    return out


def proof_refs_for_writer(facts_pack):
    """The writer's menu (user message `proof_refs`): [{"id", "kind",
    "text"}]."""
    return [{"id": cid, "kind": el["kind"], "text": el["text"]} for cid, el in proof_elements(facts_pack).items()]


# ---------------------------------------------------------------------------
# Gates over the writer's page.json
# ---------------------------------------------------------------------------

def _name_stems(name_words):
    return set(_content_stems(" ".join(n for n in (name_words or ()) if n)))


def _product_claim_stems(facts_pack):
    """Content stems of every verified claim about the product: its spec,
    product-page and allow-list lines (the product's own spec vocabulary)."""
    fp = facts_pack or {}
    slug = product_name_slug((fp.get("product") or {}).get("name") or "")
    stems = []
    for c in fp.get("verified_claims") or []:
        cid = str(c.get("id") or "")
        if (slug and (cid.startswith((f"spec-{slug}-", f"pdp-{slug}-", f"gbrain-{slug}-")))) \
                or cid.startswith("gbrain-allowlist-"):
            stems.append(set(_content_stems(c.get("text") or "")))
    return stems


def spec_label(heading, facts_pack, name_words=(), figures_only=False):
    """Why `heading` is a spec label, or None: it states a figure with a
    unit, or (with no word to the reader) at least SPEC_LABEL_SHARE of its
    content words are the product's own verified spec words -- "Full-
    spectrum heat and red light, built into one cabin" (TEARDOWN 2.2).
    `figures_only`: a questions page asks the seller about specs by design
    ("Is red light therapy included or an add-on?"), so only a figure
    counts there."""
    heading = str(heading or "")
    m = _SPEC_FIGURE_RE.search(heading)
    if m:
        return f"it states a spec figure ({m.group(0).strip()!r})"
    if figures_only or _READER_RE.search(heading):
        return None
    words = set(_content_stems(heading)) - _name_stems(name_words) - _COUNT_WORDS
    if len(words) < SPEC_LABEL_MIN_WORDS:
        return None
    vocab_stems = set().union(set(), *_product_claim_stems(facts_pack))
    vocab_stems |= {w[:-2] for w in vocab_stems if len(w) > 4 and w.endswith("er")}   # "heater" -> "heat"
    share = len(words & vocab_stems) / len(words)
    if share >= SPEC_LABEL_SHARE:
        return f"{share:.0%} of its words are the product's spec words"
    return None


def find_item_reason_violations(page, facts_pack, name_words=(), prefix=PREFIX):
    """Items as reasons: heading <= 10 words, body <= 45 words, a lever per
    item covering the Value Equation (min(4, n) levers), proof_ref from the
    facts pack's proof elements (each at most once; at least
    min(MIN_PROOF_REFS, available) items carry one)."""
    items = _items(page)
    refs = proof_elements(facts_pack)
    problems, levers, used = [], [], []
    for i, item in items:
        path = f"$.reasons[{i}]"
        head = str(item.get("heading") or "").strip()
        body = str(item.get("text") or "").strip()
        if len(head.split()) > ITEM_HEADING_MAX:
            problems.append(_problem(f"{path}.heading", f"{prefix}:item_heading_words:{i}",
                                     f"item {i + 1}'s heading is {len(head.split())} words; at most "
                                     f"{ITEM_HEADING_MAX}, the outcome first", text=head))
        if len(body.split()) > ITEM_BODY_MAX:
            problems.append(_problem(f"{path}.text", f"{prefix}:item_body_words:{i}",
                                     f"item {i + 1}'s body is {len(body.split())} words; at most {ITEM_BODY_MAX}: "
                                     "one sentence on how it works, one with the spec that makes it true",
                                     text=body))
        lever = item.get("lever")
        if lever not in LEVERS:
            problems.append(_problem(f"{path}.lever", f"{prefix}:item_lever:{i}",
                                     f"item {i + 1} needs \"lever\": one of {list(LEVERS)} -- which part of the "
                                     "reader's decision it answers"))
        else:
            levers.append(lever)
        ref = _proof_ref(item)
        if ref:
            if ref not in refs:
                problems.append(_problem(f"{path}.proof.proof_ref", f"{prefix}:item_proof_ref:{i}",
                                         f"proof_ref {ref!r} is not a proof element of this page -- use one id from "
                                         f"proof_refs ({sorted(refs) or 'none here: write a claim-backed proof line'})"))
            elif ref in used:
                problems.append(_problem(f"{path}.proof.proof_ref", f"{prefix}:item_proof_ref_repeat:{i}",
                                         f"proof_ref {ref!r} is already item {used.index(ref) + 1}'s proof; each "
                                         "proof element at most once"))
            used.append(ref)
    need_levers = min(len(LEVERS), len(items))
    if items and len(set(levers)) < need_levers:
        problems.append(_problem("$.reasons", f"{prefix}:item_levers",
                                 f"the items cover {sorted(set(levers))}; cover {need_levers} different levers of "
                                 f"{list(LEVERS)} -- the ad's promise (outcome), proof it works for this reader "
                                 "(likelihood), how fast (time), how little effort (effort)"))
    need_refs = min(MIN_PROOF_REFS, len(refs), len(items))
    have_refs = len({r for r in used if r in refs})
    if have_refs < need_refs:
        problems.append(_problem("$.reasons", f"{prefix}:item_proof_refs",
                                 f"{have_refs} item proof line(s) point at a verified proof element; at least "
                                 f"{need_refs} must -- set \"proof\": {{\"proof_ref\": \"<id>\"}} with an id from "
                                 f"proof_refs ({sorted(refs)}): the renderer prints that review, rating or units "
                                 "line word for word, which adds proof the body does not state"))
    return problems


def find_item_spec_label_violations(page, facts_pack, name_words=(), prefix=PREFIX):
    """listicle:hook_match:item:<i> -- an item heading that is a spec label
    sells nothing the ad promised: lead with the outcome the spec buys."""
    problems = []
    questions = (page or {}).get("style") == "questions"
    for i, item in _items(page):
        why = spec_label(item.get("heading"), facts_pack, name_words, figures_only=questions)
        if why:
            problems.append(_problem(f"$.reasons[{i}].heading", f"{prefix}:hook_match:item:{i}",
                                     f"item {i + 1}'s heading is a spec label ({why}) -- lead with the outcome the "
                                     "reader gets (\"Heat that reaches your whole body\"); the body carries the "
                                     "spec", text=str(item.get("heading") or "")))
    return problems


def first_screen_text(page, ad_brief=None, facts_pack=None):
    """(text, quotes): the first screen's own words -- eyebrow, headline,
    display headline, dek, lede -- plus the hero quote when the page shows
    one (face and story first screens)."""
    from . import first_screen

    page = page or {}
    parts = [first_screen.eyebrow(page, ad_brief), page.get("headline"), page.get("display_headline"),
             page.get("dek"), page.get("lede")]
    quotes = []
    if page.get("hero_style") != "display":
        q = first_screen.hero_quote(page, ad_brief, facts_pack)
        if q and q.get("text"):
            parts.append(q["text"])
            quotes.append(q["text"])
    return " ".join(str(p) for p in parts if p), quotes


def _exclude_words(name_words):
    out = set()
    for n in name_words or ():
        out |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", str(n or ""))}
    return out


def hook_match(page, ad_brief, facts_pack=None, name_words=()):
    text, quotes = first_screen_text(page, ad_brief, facts_pack)
    return persuasion.hook_match(text, ad_brief, exclude=_exclude_words(name_words), quotes=quotes), text


def find_hook_match_violations(page, ad_brief, facts_pack=None, name_words=(), prefix=PREFIX):
    """listicle:hook_match -- the first screen restates the ad's hook (B's
    persuasion.hook_match: two content words of hook/angle/promise, or the
    speaker's own sentence quoted); plus no item heading is a spec label."""
    problems = []
    brief = ad_brief or {}
    if any(str(brief.get(k) or "").strip() for k in ("hook", "angle", "promise")):
        result, text = hook_match(page, brief, facts_pack, name_words)
        if not result["ok"]:
            problems.append(_problem("$.headline", f"{prefix}:hook_match",
                                     "the first screen must pay off the ad's hook: restate it as closely as the "
                                     "verified facts allow -- same words, same number, same person. The hook: "
                                     f"{brief.get('hook')!r}. Shared content words now: "
                                     f"{result['overlap'] or 'none'}", text=text))
    return problems + find_item_spec_label_violations(page, facts_pack, name_words, prefix)


def _unquoted(node, drop=None):
    """A copy of page.json with every quoted span blanked: the ad speaker's
    verbatim words are hers, never the writer's tic. `drop`: a regex whose
    matches are blanked too."""
    if isinstance(node, dict):
        return {k: (v if k in NON_PROSE_KEYS else _unquoted(v, drop)) for k, v in node.items()}
    if isinstance(node, list):
        return [_unquoted(v, drop) for v in node]
    if isinstance(node, str):
        text = _QUOTED_SPAN_RE.sub('""', node)
        return drop.sub(" ", text) if drop is not None else text
    return node


def find_ad_reference_violations(page, prefix=PREFIX, extra_phrases=None):
    """persuasion.find_ad_reference_violations over the writer's own words
    (quoted speaker words left out): no "actually", "plainly", "real price",
    "not lifetime", "what holds up", vocab banned_phrases. Narration about
    the source ("the ad", "the video", "the ad speaker") is
    listicle_quality's meta_reference gate already, so it is not reported a
    second time here."""
    from .listicle_quality import _ALLOWED_FRAME_RE, _META_RE

    scan = _unquoted(_unquoted(page, _ALLOWED_FRAME_RE), _META_RE)
    return persuasion.find_ad_reference_violations(scan, prefix=prefix, extra_phrases=extra_phrases)


_RENDER_SKIP_RE = re.compile(r"<(script|style|blockquote|cite|figcaption)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def find_rendered_ad_reference_violations(html):
    """The ad-reference ban over the page as a reader sees it (the body from
    <main> to the footer, tags stripped): fixed template copy and a page
    written before the gate cannot slip past it. Customer and speaker words
    are exempt: blockquotes, cites, captions and quoted spans are dropped, and
    the attribution frame ("In the ad, she says,") is allowed.
    [{"term", "issue"}]."""
    from .claims import strip_html_to_visible_text
    from .listicle_quality import _ALLOWED_FRAME_RE

    text = html or ""
    if "<main" in text:
        text = text[text.index("<main"):]
    if "<footer" in text:
        text = text[:text.index("<footer")]
    text = strip_html_to_visible_text(_RENDER_SKIP_RE.sub(" ", text))
    text = _QUOTED_SPAN_RE.sub(" ", text)
    text = persuasion.ATTRIBUTION_FRAME_RE.sub(" ", _ALLOWED_FRAME_RE.sub(" ", text))
    hits = []
    for m in persuasion.AD_REFERENCE_RE.finditer(text):
        around = " ".join(text[max(0, m.start() - 50): m.end() + 50].split())
        hits.append({"term": m.group(0), "issue": f"{m.group(0)!r} in the rendered page: {around!r}"})
    return hits


READING_FIELDS_RE = re.compile(
    r"^\$\.(?:headline|dek|lede|reasons\[\d+\]\.(?:heading|text|proof\.text)"
    r"|audience_fit\.(?:for_you|not_for_you)\[\d+\]\.text|faq\.questions\[\d+\]\.(?:question|answer)"
    r"|closing\.(?:headline|recap\[\d+\]\.text))$")


def reading_fields(page, name_words=()):
    """[(path, text)] of the writer's selling copy, quoted speaker words
    dropped and product/brand names read as "it"."""
    out = []
    for path, node in walk_page(page or {}, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str) or not READING_FIELDS_RE.match(path):
            continue
        text = _QUOTED_SPAN_RE.sub(" ", node)
        for name in sorted((n for n in name_words if n), key=len, reverse=True):
            text = re.sub(r"(?i)\b" + re.escape(name) + r"\b", "it", text)
        text = re.sub(r"\s+", " ", text).strip(" ,;:")
        if re.search(r"[A-Za-z]", text):
            out.append((path, text.rstrip(" .!?") + "."))
    return out


def page_grade(page, name_words=()):
    fields = reading_fields(page, name_words)
    text = " ".join(t for _p, t in fields)
    return (grade_level(text) if len(text.split()) >= 40 else None), fields


def find_reading_level_violations(page, name_words=(), prefix=PREFIX, max_grade=MAX_GRADE):
    """Flesch-Kincaid grade <= 7.5 over the writer's copy. The problem points
    at the hardest field (12+ words) so the patch repair edits that one."""
    grade, fields = page_grade(page, name_words)
    if grade is None or grade <= max_grade:
        return []
    scored = [(grade_level(t), p, t) for p, t in fields if len(t.split()) >= 12]
    worst = max(scored) if scored else (grade, "$", "")
    return [_problem(worst[1], f"{prefix}:reading_level",
                     f"the copy reads at grade {grade}; write for grade {max_grade} or lower -- shorter sentences "
                     "(under 14 words), everyday words of one or two syllables, one idea per sentence. Hardest "
                     f"line now (grade {worst[0]}): {worst[2]!r}", grade=grade, text=worst[2])]


def find_persuasion_violations(page, facts_pack, ad_brief=None, name_words=(), prefix=PREFIX):
    """Every cycle 110 listicle check, combined (repair.check_page_gates
    runs it through listicle_quality.find_quality_violations)."""
    if not isinstance(page, dict):
        return []
    problems = find_item_reason_violations(page, facts_pack, name_words, prefix)
    problems += find_hook_match_violations(page, ad_brief, facts_pack, name_words, prefix)
    problems += find_ad_reference_violations(page, prefix)
    problems += find_reading_level_violations(page, name_words, prefix)
    problems += persuasion.find_copy_hygiene_violations(_unquoted(page), facts_pack, prefix=prefix)
    if ad_brief:
        problems += quote_fidelity.find_unquoted_first_screen(
            [(f"$.{k}", page.get(k)) for k in ("eyebrow", "headline", "display_headline", "dek", "lede")],
            ad_brief, facts_pack, prefix)
    return problems


def writer_lines():
    return [
        "Items are reasons to act on the ad's promise. Give every item a \"lever\": outcome (the ad's promise, "
        "in the reader's words), likelihood (proof it works for this reader), time (how fast: ships in days, "
        "heats quickly -- only what a verified claim states), effort (how little work: plugs in, clasps "
        "together, app control). Cover all four levers across the items.",
        f"Item heading: at most {ITEM_HEADING_MAX} words, the outcome first (\"Heat that reaches your whole "
        "body\", \"Fits a corner of a small apartment\") -- never a spec label (\"Full-spectrum heat and red "
        "light, built into one cabin\", \"120V outlet\"). In a mistakes, questions or myths page the heading "
        f"keeps its style. Item body: at most {ITEM_BODY_MAX} words -- one sentence on how it works, one with "
        "the spec that makes it true (cite it).",
        "Item proof line: prefer {\"proof\": {\"proof_ref\": \"<id>\"}} with one id from proof_refs (user "
        "message) -- the renderer prints that customer review word for word with its name, or the store rating, "
        f"or the units line. At least {MIN_PROOF_REFS} items use a proof_ref when proof_refs has that many; "
        "each id at most once. Otherwise the proof line states a verified fact the body does not state, with "
        "its claim_ids. Never type a review yourself.",
        "Never mention, quote, grade or correct the ad in your own words: no \"the ad\", \"actually\", \"real "
        "price\", \"plainly\", \"not verified\", \"not lifetime\", \"no separate\", \"nothing to submit\", "
        "\"no form required\", \"what holds up\". An ad claim the facts do not support is simply left out. The "
        "one place the ad is named is the quote frame (\"In the ad, she says, ...\").",
        f"Reading level: grade {MAX_GRADE} or lower. Short sentences (under 14 words), everyday words of one or "
        "two syllables, one idea per sentence.",
        "The first screen (eyebrow, headline, dek, lede) pays off the ad's hook: reuse at least two of its own "
        "words (hook, angle, promise) or show the speaker's own sentence as hero_quote_id.",
    ]


# ---------------------------------------------------------------------------
# The CTA phrase by the ad's angle
# ---------------------------------------------------------------------------

def allowed_cta_texts(schema, facts_pack, ad_brief=None, tenant=None):
    """schema.json `allowed_cta_texts` as a map by persuasion.ad_angle:
    sale -> "Get {offer_amount} off the {short_name}" / "Shop the sale",
    financing -> "See monthly payments", story -> "See the {short_name}
    {pronoun} uses" (she/he only), default -> "Shop the {short_name}" ...
    A tenant in consult mode keeps its own cta_variants.consult; a plain
    list resolves as before (write.resolve_allowed_cta_texts)."""
    from . import first_screen
    from . import offer as offer_mod
    from . import tenant as tenant_mod
    from .write import resolve_allowed_cta_texts, resolve_cta_mode

    tenant = tenant or tenant_mod.active()
    fp = facts_pack or {}
    name = (fp.get("product") or {}).get("name") or ""
    templates = schema.get("allowed_cta_texts") or []
    ad_angle_text = (ad_brief or {}).get("angle")
    if not isinstance(templates, dict):
        return resolve_allowed_cta_texts(schema, name, model_name=name, tenant=tenant, ad_angle=ad_angle_text)
    if resolve_cta_mode(tenant.get("cta_mode", "buy"), ad_angle_text) == "consult" and \
            tenant.get("cta_variants.consult"):
        return resolve_allowed_cta_texts({"allowed_cta_texts": []}, name, model_name=name, tenant=tenant,
                                         ad_angle=ad_angle_text)
    angle = persuasion.ad_angle(ad_brief, fp)
    view = offer_mod.offer_view(fp)
    pronoun = first_screen.speaker_pronoun(ad_brief=ad_brief or {}, tenant=tenant) if angle == "story" else None
    return persuasion.cta_texts_for_angle(templates, angle, short_name=name,
                                          offer_amount=(view or {}).get("amount"),
                                          pronoun=pronoun if pronoun in ("she", "he") else None)


# ---------------------------------------------------------------------------
# What the renderer adds (first_screen.render_context merges this)
# ---------------------------------------------------------------------------

def _price(facts_pack):
    from .first_screen import _PRICE_RE

    fp = facts_pack or {}
    slug = product_name_slug((fp.get("product") or {}).get("name") or "")
    claim = landing_sell.claims_by_id(fp).get(f"price-{slug}") if slug else None
    m = _PRICE_RE.search(claim["text"]) if claim else None
    return {"text": m.group(0), "claim_ids": [claim["id"]]} if m else None


def _stats(facts_pack, base):
    """The display first screen's strip, proof first: the rating (store,
    else product), the units line, then first_screen.stats() for the rest
    -- at most 3. With neither agent A field it is first_screen.stats()."""
    out = []
    rating = landing_sell.store_rating(facts_pack)
    if rating:
        out.append({"value": f"{rating['value']} ★", "label": f"{rating['count']} reviews" if rating["count"]
                    else "rating", "claim_ids": list(rating["claim_ids"])})
    unit = landing_sell.units(facts_pack)
    if unit:
        out.append({"value": unit["value"], "label": unit["label"], "claim_ids": list(unit["claim_ids"])})
    for s in base or []:
        if s["value"].endswith("★") and rating:
            continue
        out.append(s)
    return out[:3]


def sell_context(page, facts_pack, tenant=None, base_stats=None):
    """Everything the cycle 110 listicle prints that the writer never
    writes: {stats, proof_row, price_line, stack, wall, pull, item_proofs,
    claim_ids}. Every piece falls back (landing_sell) when agent A's
    facts_pack field is absent: no reviews_store -> the product rating; no
    units -> no units line; no included -> the model's verified feature
    lines; no financing.monthly -> the price alone; no review_quotes -> no
    wall; no offer net price -> the offer line as cycle 109 prints it."""
    from . import first_screen
    from . import tenant as tenant_mod

    fp = facts_pack or {}
    tenant = tenant or tenant_mod.active()
    ids = set()
    elements = proof_elements(fp)

    item_proofs = []
    for _i, item in _items(page):
        ref = _proof_ref(item)
        proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
        if ref in elements:
            item_proofs.append(elements[ref])
            ids |= set(elements[ref]["claim_ids"])
        elif str(proof.get("text") or "").strip():
            item_proofs.append({"kind": "text", "text": proof["text"], "author": "", "stars": None,
                                "claim_ids": list(proof.get("claim_ids") or [])})
        else:
            item_proofs.append(None)
    shown = {p["claim_ids"][0] for p in item_proofs if p and p["kind"] != "text" and p["claim_ids"]}

    rating = landing_sell.store_rating(fp)
    unit = landing_sell.units(fp)
    proof_row = []
    if rating:
        proof_row.append({"kind": "rating", "value": rating["value"], "text": rating["text"],
                          "claim_ids": list(rating["claim_ids"])})
    if unit:
        proof_row.append({"kind": "units", "text": f"{unit['value']} {unit['label']}",
                          "claim_ids": list(unit["claim_ids"])})
    for p in proof_row:
        ids |= set(p["claim_ids"])

    price = _price(fp)
    sale = landing_sell.sale_prices(fp, price) if price else None
    dl = landing_sell.deadline(fp)
    fm = landing_sell.financing_monthly(fp) if price else None
    for node in (price, sale, fm):
        if node:
            ids |= set(node["claim_ids"])
    if dl and sale:
        ids |= set(dl["claim_ids"])

    rows = landing_sell.included(fp, page, tenant)
    base = first_screen.value_stack(fp, page, tenant)
    stack = None
    if rows or price:
        product = fp.get("product") or {}
        try:
            full = tenant.product_names(product)["full_name"] or product.get("name")
        except Exception:  # noqa: BLE001 -- a bare test tenant
            full = product.get("name")
        stack_rating = rating if rating and rating["claim_ids"][0] not in shown else None
        stack = {"title": (base or {}).get("title") or f"What comes with the {full}", "items": rows,
                 "price": price, "sale": sale, "deadline": dl["short"] if dl and sale else None,
                 "monthly": fm, "rating": stack_rating, "units": unit,
                 "financing": (base or {}).get("financing") or ""}
        for r in rows:
            ids |= set(r["claim_ids"])

    quotes = [q for q in review_quotes(fp) if q["claim_ids"][0] not in shown]
    wall = quotes[:QUOTE_WALL]
    pull = quotes[QUOTE_WALL] if len(quotes) > QUOTE_WALL else None
    for q in wall + ([pull] if pull else []):
        ids |= set(q["claim_ids"])

    price_line = None
    if price:
        price_line = {"price": price["text"], "net": sale["net"] if sale else None,
                      "list": sale["list"] if sale else None, "code": sale["code"] if sale else None,
                      "monthly": fm["short"] if fm and stack else None}
    return {"stats": _stats(fp, base_stats), "proof_row": proof_row, "price_line": price_line, "stack": stack,
            "wall": wall, "pull": pull, "item_proofs": item_proofs, "claim_ids": ids,
            "warranty_in_stack": warranty_in_stack(stack, page)}


_WARRANTY_RE = re.compile(r"\bwarrant", re.IGNORECASE)


def warranty_in_stack(stack, page=None):
    """True when the value stack already states the warranty: an item cites
    the tenant's warranty claim (or the closing warranty line's own claim)
    or reads as a warranty line. The closing band then leaves its fixed
    warranty sentence out, so the warranty shows once."""
    if not stack:
        return False
    from .claims import warranty_claim_id

    try:
        wanted = {warranty_claim_id()}
    except Exception:  # noqa: BLE001 -- a bare test tenant
        wanted = set()
    line = (((page or {}).get("closing") or {}).get("warranty_line") or {})
    wanted |= set(line.get("claim_ids") or []) if isinstance(line, dict) else set()
    wanted.discard(None)
    for row in stack.get("items") or []:
        if set(row.get("claim_ids") or []) & wanted or _WARRANTY_RE.search(str(row.get("text") or "")):
            return True
    return False


# ---------------------------------------------------------------------------
# Quality numbers -> state.json listicle.quality (the review site)
# ---------------------------------------------------------------------------

QUALITY_KEYS = ("hook_match", "grade_level", "cta_count", "proof_count", "lcp_ms", "cls", "first_screen_ok",
                "first_cta_top", "ad_reference_rendered")


def _name_words(facts_pack, tenant=None):
    product = (facts_pack or {}).get("product") or {}
    names = {str(product.get("name") or ""), str(getattr(tenant, "display_name", "") or "")}
    try:
        names.add(tenant.product_names(product)["full_name"] or "")
    except Exception:  # noqa: BLE001 -- a bare test tenant
        pass
    return tuple(sorted((n for n in names if n), key=len, reverse=True))


PHONE_SCREEN = (390, 844)


def first_cta_top(layout):
    """The first CTA's top edge (px) in a capture.probe() layout at 390 wide,
    or None when the layout has no CTA or there is no layout."""
    cta = (layout or {}).get("cta") if isinstance(layout, dict) else None
    top = cta.get("top") if isinstance(cta, dict) else None
    return int(top) if isinstance(top, (int, float)) else None


def quality(page, html, ad_brief, facts_pack, tenant=None, layout=None):
    """{hook_match, grade_level, cta_count, proof_count, lcp_ms, cls,
    first_screen_ok, ad_reference_rendered} for one rendered listicle.
    hook_match is the share of the hook's content words the first screen
    carries (1.0 when it quotes the speaker); cta_count the links to the
    page's cta_url; proof_count the verified proof elements shown (rating,
    units, review quotes). first_screen_ok is positional: the hook matches,
    the first screen carries proof, and the first CTA's top edge is inside
    the first 844 px at 390 wide in `layout` (capture.probe at 390 x 844);
    None when no layout was measured. ad_reference_rendered: the banned
    phrases in the page as a reader sees it (find_rendered_ad_reference_
    violations). lcp_ms / cls stay None until a browser measures them."""
    import html as html_mod

    page = page or {}
    words = _name_words(facts_pack, tenant)
    hm, _text = hook_match(page, ad_brief or {}, facts_pack, words)
    grade, _fields = page_grade(page, words)
    url = page.get("cta_url") or ""
    cta = 0
    for m in re.finditer(r"<a\b[^>]*\bhref=\"([^\"]*)\"", html or ""):
        if url and html_mod.unescape(m.group(1)) == url:
            cta += 1
    ctx = sell_context(page, facts_pack, tenant)
    shown = {p["claim_ids"][0] for p in ctx["proof_row"]}
    shown |= {p["claim_ids"][0] for p in ctx["item_proofs"] if p and p["kind"] != "text"}
    shown |= {q["claim_ids"][0] for q in ctx["wall"] + ([ctx["pull"]] if ctx["pull"] else [])}
    top = first_cta_top(layout)
    first_ok = None
    if layout is not None:
        first_ok = bool(hm["ok"] and ctx["proof_row"] and top is not None and top < PHONE_SCREEN[1])
    return {"hook_match": 1.0 if hm["quoted"] else hm["score"], "grade_level": grade, "cta_count": cta,
            "proof_count": len(shown), "lcp_ms": None, "cls": None, "first_screen_ok": first_ok,
            "first_cta_top": top, "ad_reference_rendered": [h["term"] for h in
                                                            find_rendered_ad_reference_violations(html)]}


def record_quality(run_dir, cartridge="listicle", tenant=None, layout=None, browser=None):
    """Computes quality() from the run's own files and writes it under
    state.json[<cartridge>]["quality"]; returns it (None when a file is
    missing). `layout`: a capture.probe() result at 390 x 844 the caller
    already has (the design gate's, a screenshot step's); `browser`: measure
    one here. With neither, first_screen_ok is None ("—" on the site), never
    a guess. Measured values another step wrote (lcp_ms, cls, a layout-based
    first_screen_ok) are kept."""
    from . import runstate
    from . import tenant as tenant_mod

    run_dir = Path(run_dir)
    try:
        page = json.loads((run_dir / cartridge / "page.json").read_text())
        html = (run_dir / cartridge / "index.html").read_text()
        facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
        brief_path = run_dir / "ad_brief.json"
        ad_brief = json.loads(brief_path.read_text()) if brief_path.exists() else {}
        data = runstate.load_state(run_dir)
    except (OSError, ValueError):
        return None
    if tenant is None:
        try:
            tenant = tenant_mod.active()
        except Exception:  # noqa: BLE001
            tenant = None
    if layout is None and browser:
        from . import capture

        layout = capture.probe(browser, run_dir / cartridge / "index.html", width=PHONE_SCREEN[0],
                               height=PHONE_SCREEN[1], tenant=tenant)
    q = quality(page, html, ad_brief, facts_pack, tenant, layout=layout)
    entry = dict(data.get(cartridge) or {})
    old = entry.get("quality") or {}
    keep = ("lcp_ms", "cls") + (("first_screen_ok", "first_cta_top") if layout is None else ())
    for key in keep:
        if q[key] is None and old.get(key) is not None:
            q[key] = old[key]
    entry["quality"] = q
    data[cartridge] = entry
    runstate.save_state(run_dir, data)
    return q
