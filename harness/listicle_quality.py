"""Cycle 74: listicle copy-quality gates and the writer lines that go with them.

An external judge rated the 2026-10-05 listicle pages weakest on voice, flow
and overall, with proof and message-match down after cycle 73's skeletons.
Reading the passing pages showed concrete, checkable faults:

  - an item whose heading, body and proof line are the same sentence (run
    20261005-151302-hidden-costs-v2-2sxl item 6: the warranty sentence three
    times);
  - a proof line that restates its own item body ("runs on a dedicated
    120V/20A outlet" / "electrical requirement is a 120V / 20A outlet");
  - the same number or sentence again and again across items, audience fit
    and FAQ ("120V/20A" in six fields of 2sxl, "$8,250" three times);
  - the brand written two ways on one page (all capitals and title case);
  - narration about the source ("As the ad speaker found");
  - a myths item that is not a myth (a spec sentence as the heading);
  - a headline and dek that never mention the ad's own hook.

Each is a deterministic check here, wired into repair.check_page_gates for
the listicle only, so a failure goes to the patch repair (harness/pagepatch.py)
with the exact field and the fix. Nothing in this module is a claims gate and
nothing here relaxes one. Tenant-neutral: brand and product words come from
the caller (tenant.yaml / the catalog).
"""
import re

from . import vocab
from .claims import _NUMBER_TOKEN_RE, _QUOTED_SPAN_RE, _extract_numbers, _strip_digit_exempt_tokens
from .quote_fidelity import _content_stems
from .textutil import NON_PROSE_KEYS, walk_page

# A proof line whose content words are at least this share inside its own
# item body adds nothing the body did not say. Calibrated on the 65 listicle
# pages of the first tenant's run output (docs/FIXLOG.md cycle 74).
RESTATE_THRESHOLD = 0.8
# A proof line this short (in content words) is not scored -- "Priced at
# $5,450." has too few words for a share to mean anything.
RESTATE_MIN_WORDS = 4
# One number may appear in at most this many fields across the items, the
# audience-fit lines and the FAQ answers (the closing recap is a recap by
# design and is not counted).
MAX_NUMBER_FIELDS = 3
# A sentence this long (in words) may appear only once across the same fields.
REPEAT_SENTENCE_MIN_WORDS = 5

# Category words every sauna page carries -- they never prove the headline
# carries the ad's own hook. Kept generic (no brand or model words).
_GENERIC_MATCH_WORDS = frozenset(
    "sauna saunas home house infrared buy buying bought purchase product products brand brands "
    "people shopper shoppers buyer buyers new best get one thing things way ways reason reasons "
    "myth myths mistake mistakes question questions claim claims check checked evidence".split()
)
_GENERIC_MATCH_STEMS = frozenset(_content_stems(" ".join(_GENERIC_MATCH_WORDS)))

_NORMALIZE_RE = re.compile(r"[^a-z0-9$%]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _norm(text):
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


def _items(page):
    reasons = page.get("reasons")
    return [(i, it) for i, it in enumerate(reasons) if isinstance(it, dict)] if isinstance(reasons, list) else []


def _proof_text(item):
    proof = item.get("proof")
    return proof.get("text") if isinstance(proof, dict) and isinstance(proof.get("text"), str) else ""


def _exempt_stems(names):
    """Content stems of brand/product names -- naming the product twice is
    not restating a fact."""
    return set(_content_stems(" ".join(n for n in (names or []) if n)))


# ---------------------------------------------------------------------------
# Item shape: heading, body and proof are three different sentences.
# ---------------------------------------------------------------------------

def find_item_repeat_violations(page):
    problems = []
    for i, item in _items(page):
        fields = {"heading": _norm(item.get("heading")), "body": _norm(item.get("text")),
                  "proof": _norm(_proof_text(item))}
        same = [(a, b) for a, b in (("heading", "body"), ("body", "proof"), ("heading", "proof"))
                if fields[a] and fields[a] == fields[b]]
        if same:
            pairs = ", ".join(f"{a} = {b}" for a, b in same)
            problems.append(_problem(
                f"$.reasons[{i}]", f"listicle:item_repeats:{i}",
                f"item {i + 1} repeats one sentence ({pairs}) -- the heading names the point in a few "
                "words, the body says what it means for the reader, the proof line states the verified "
                "fact: three different lines",
                text=item.get("text") or "",
            ))
    return problems


def restate_score(proof, body, exempt=frozenset()):
    """Share (0..1) of the proof line's content words that are already in the
    body. None when the proof has fewer than RESTATE_MIN_WORDS content words."""
    wanted = set(_content_stems(proof)) - exempt
    if len(wanted) < RESTATE_MIN_WORDS:
        return None
    have = set(_content_stems(body))
    return len(wanted & have) / len(wanted)


def find_proof_restates_body_violations(page, exempt=frozenset()):
    problems = []
    for i, item in _items(page):
        proof = item.get("proof")
        if not isinstance(proof, dict) or proof.get("attributed_to_customer") is True:
            continue
        score = restate_score(_proof_text(item), item.get("text") or "", exempt)
        if score is not None and score >= RESTATE_THRESHOLD:
            problems.append(_problem(
                f"$.reasons[{i}].proof", f"listicle:proof_restates_body:{i}",
                f"item {i + 1}'s proof line repeats its own body ({score:.0%} of its words are already "
                "there) -- the proof line must add the verified fact the body does not state (the "
                "number, the spec, the policy), and the body must say what it means for the reader "
                "without restating that number",
                text=_proof_text(item),
            ))
    return problems


# ---------------------------------------------------------------------------
# Repetition across the page: items, audience fit, FAQ answers.
# ---------------------------------------------------------------------------

def _repetition_fields(page):
    """(path, text) in page order: item bodies and proof lines, audience-fit
    lines, FAQ answers. Headings carry no numbers; the closing recap is a
    recap by design."""
    out = []
    for i, item in _items(page):
        out.append((f"$.reasons[{i}].text", item.get("text") or ""))
        out.append((f"$.reasons[{i}].proof.text", _proof_text(item)))
    fit = page.get("audience_fit") if isinstance(page.get("audience_fit"), dict) else {}
    for field in ("for_you", "not_for_you"):
        for j, entry in enumerate(fit.get(field) or []):
            text = entry.get("text") if isinstance(entry, dict) else entry
            if isinstance(text, str):
                out.append((f"$.audience_fit.{field}[{j}].text", text))
    faq = page.get("faq")
    questions = faq.get("questions") if isinstance(faq, dict) else faq
    for k, entry in enumerate(questions if isinstance(questions, list) else []):
        if isinstance(entry, dict) and isinstance(entry.get("answer"), str):
            out.append((f"$.faq.questions[{k}].answer", entry["answer"]))
    return out


def _fixed_sentences(financing_lender=None):
    fixed = []
    for getter in (lambda: vocab.ALLOWED_WARRANTY_SENTENCE, lambda: vocab.allowed_financing_sentence(financing_lender)):
        try:
            sentence = getter()
        except Exception:  # a stand-in vocabulary without the field
            sentence = None
        if sentence:
            fixed.append(_norm(sentence))
    return fixed


def find_repetition_violations(page, digit_exempt_terms=None, financing_lender=None):
    fields = _repetition_fields(page)
    fixed = set(_fixed_sentences(financing_lender))
    problems = []

    seen_numbers = {}
    for path, text in fields:
        unquoted = _QUOTED_SPAN_RE.sub(" ", text)
        for number in sorted(_extract_numbers(_strip_digit_exempt_tokens(unquoted, digit_exempt_terms))):
            seen_numbers.setdefault(number, []).append((path, text))
    for number, hits in seen_numbers.items():
        for n, (path, text) in enumerate(hits[MAX_NUMBER_FIELDS:], start=MAX_NUMBER_FIELDS + 1):
            problems.append(_problem(
                path, f"listicle:repeat_number:{number}:{n}",
                f"{number} is stated in {len(hits)} places across the items, audience fit and FAQ "
                f"(at most {MAX_NUMBER_FIELDS}) -- state it where it proves something, and make this "
                "line add something new instead of repeating it",
                text=text,
            ))

    seen_sentences = {}
    for path, text in fields:
        for sentence in _SENTENCE_RE.split(text.strip()):
            key = _norm(sentence)
            if len(key.split()) < REPEAT_SENTENCE_MIN_WORDS or key in fixed:
                continue
            if key in seen_sentences and seen_sentences[key] != path:
                problems.append(_problem(
                    path, f"listicle:repeat_sentence:{path}",
                    f"this sentence already appears at {seen_sentences[key]} -- every line on the page "
                    "says something new; the FAQ answers questions the items did not already answer",
                    text=text,
                ))
            else:
                seen_sentences.setdefault(key, path)
    return problems


# ---------------------------------------------------------------------------
# One brand spelling per page.
# ---------------------------------------------------------------------------

def brand_spelling_regex(brand_word, display_name, model_names=(), protected_suffixes=()):
    """A case-sensitive regex for `brand_word` used on its own (not as part
    of a product's full name or a protected phrase), or None when the tenant
    has nothing to enforce: the product-name brand word and the display name
    are the same string, or differ in more than case."""
    if not brand_word or not display_name or brand_word == display_name:
        return None
    if brand_word.lower() != display_name.lower():
        return None
    tails = [re.escape(m) for m in sorted({m for m in model_names if m}, key=len, reverse=True)]
    tails += [r"\s+".join(re.escape(w) for w in s.split()) for s in protected_suffixes if s and s.strip()]
    lookahead = r"(?!\s+(?:" + "|".join(tails) + r")\b)" if tails else ""
    return re.compile(r"\b" + re.escape(brand_word) + r"\b" + lookahead)


def find_brand_spelling_violations(page, regex, display_name, verified_texts=frozenset()):
    if regex is None:
        return []
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str) or node.strip() in verified_texts:
            continue
        if regex.search(node):
            problems.append(_problem(
                path, f"brand_spelling:{path}",
                f"the brand is spelled another way here; on its own the brand is always "
                f"{display_name!r} (a product keeps its full name)",
                text=node,
            ))
    return problems


def fix_brand_spelling(text, regex, display_name):
    return regex.sub(display_name, text) if regex is not None else text


# ---------------------------------------------------------------------------
# No narration about the source.
# ---------------------------------------------------------------------------

# The owner's quote frames name the ad -- "In the ad, she says ..." -- and are
# the only place it may be named.
_FRAME_VERBS = r"(?:says|said|puts\s+it|put\s+it|mentions|mentioned|describes|described|explains|explained)"
_ALLOWED_FRAME_RE = re.compile(
    r"\bIn the ad,?\s+(?:she|he|they|[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})\s+" + _FRAME_VERBS + r"\b"
    r"|\b" + _FRAME_VERBS + r"\s+in\s+the\s+ad\b",
    re.IGNORECASE,
)
_META_RE = re.compile(
    r"\b(?:the\s+)?ad\s+speaker\b|\bthe\s+speaker\b|\bspeaker\s+in\s+the\b"
    r"|\b(?:the|this|that|our)\s+(?:ad|advert|advertisement|video|clip|commercial)\b",
    re.IGNORECASE,
)


# The headline and dek speak to the reader, who has not met the ad's speaker:
# "She ordered the <model> because ..." (smoke run 20261005-162336-product-
# features-v2-xkkd) narrates a stranger.
_TOP_PRONOUN_RE = re.compile(r"\b(?:she|he|her|his|him)\b", re.IGNORECASE)
_TOP_PATHS = ("$.headline", "$.dek")


def find_meta_reference_violations(page, allow_speaker_pronoun=False):
    """`allow_speaker_pronoun` (cycle 79 gate change, owner): "she"/"he" in
    the headline or dek refers to the attributed ad speaker when the page
    carries that speaker's verbatim quote (first_screen.pronoun_allowed)."""
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str):
            continue
        rest = _ALLOWED_FRAME_RE.sub(" ", _QUOTED_SPAN_RE.sub(" ", node))
        if path in _TOP_PATHS and not allow_speaker_pronoun:
            m = _TOP_PRONOUN_RE.search(_QUOTED_SPAN_RE.sub(" ", node))
            if m:
                problems.append(_problem(
                    path, f"listicle:meta_reference:{path}",
                    f'"{m.group(0)}" in the {path[2:]} narrates the ad\'s speaker, whom the reader has not '
                    "met -- the headline and dek speak to the reader: state the ad's problem directly",
                    text=node,
                ))
                continue
        m = _META_RE.search(rest)
        if m:
            problems.append(_problem(
                path, f"listicle:meta_reference:{path}",
                f'"{m.group(0)}" talks about the source instead of to the reader -- never mention the '
                'ad, the video or "the ad speaker" in your own narration; the only place the ad is named '
                'is a quote frame ("In the ad, she says, \\"...\\"")',
                text=node,
            ))
    return problems


# ---------------------------------------------------------------------------
# Myths items state a myth.
# ---------------------------------------------------------------------------

def find_myth_item_violations(page, style, verified_texts=(), name_words=()):
    if style != "myths":
        return []
    fact_stems = [set(_content_stems(t)) for t in verified_texts if t]
    try:
        fact_stems.append(set(_content_stems(vocab.ALLOWED_WARRANTY_SENTENCE)))
    except Exception:
        pass
    names = [n for n in name_words if n]
    problems = []
    for i, item in _items(page):
        heading = item.get("heading") or ""
        named = next((n for n in names if re.search(r"\b" + re.escape(n) + r"\b", heading)), None)
        stems = set(_content_stems(heading))
        is_fact = len(stems) >= 3 and any(len(stems & f) / len(stems) >= RESTATE_THRESHOLD for f in fact_stems)
        if named or is_fact:
            why = f"it names {named!r}" if named else "it is a verified fact, not a belief"
            problems.append(_problem(
                f"$.reasons[{i}].heading", f"listicle:myth_item:{i}",
                f"item {i + 1}'s heading is not a myth ({why}) -- on a myths page every heading is a "
                "belief shoppers hold about the category, in their words (\"A home sauna needs special "
                "electrical work\"), and the body answers it from the verified facts",
                text=heading,
            ))
    return problems


# ---------------------------------------------------------------------------
# Message match: the ad's own hook reaches the top of the page.
# ---------------------------------------------------------------------------

def ad_hook_stems(ad_brief, exempt=frozenset()):
    brief = ad_brief or {}
    parts = [brief.get(k) for k in ("hook", "angle", "promise")]
    parts += list(brief.get("objections_raised") or [])
    text = " ".join(p for p in parts if isinstance(p, str))
    return set(_content_stems(text)) - _GENERIC_MATCH_STEMS - exempt


def find_message_match_violations(page, ad_brief, exempt=frozenset()):
    wanted = ad_hook_stems(ad_brief, exempt)
    if not wanted:
        return []
    top = " ".join(s for s in (page.get("headline"), page.get("dek")) if isinstance(s, str))
    if set(_content_stems(top)) & wanted:
        return []
    hook = (ad_brief or {}).get("hook") or (ad_brief or {}).get("angle") or ""
    return [_problem(
        "$.dek", "listicle:message_match",
        f"neither the headline nor the dek carries the ad's own hook ({hook!r}) -- the reader "
        "clicked that ad; say its problem in its own words in the dek (and in the headline's free "
        "slots where the template allows)",
        text=page.get("dek") or "",
    )]


# ---------------------------------------------------------------------------
# All of the above, and the writer lines that state them up front.
# ---------------------------------------------------------------------------

def find_quality_violations(page, *, style=None, ad_brief=None, facts_pack=None, brand_regex=None,
                            display_name=None, name_words=(), financing_lender=None):
    if not isinstance(page, dict):
        return []
    facts_pack = facts_pack or {}
    verified_texts = [c.get("text") or "" for c in facts_pack.get("verified_claims", [])]
    exempt = _exempt_stems([display_name, *name_words])
    problems = []
    problems += find_item_repeat_violations(page)
    problems += find_proof_restates_body_violations(page, exempt)
    problems += find_repetition_violations(page, facts_pack.get("digit_exempt_terms"), financing_lender)
    problems += find_brand_spelling_violations(
        page, brand_regex, display_name, frozenset(t.strip() for t in verified_texts if t)
    )
    from . import first_screen

    problems += find_meta_reference_violations(
        page, allow_speaker_pronoun=first_screen.pronoun_allowed(page, ad_brief, facts_pack),
    )
    # Cycle 79: the first-screen fields (eyebrow, accent phrase, lede, scroll
    # cue, hero quote), verbatim quoted words at the top, no "most people".
    problems += first_screen.find_first_screen_violations(page, ad_brief, facts_pack)
    problems += find_myth_item_violations(page, style or page.get("style"), verified_texts,
                                          [display_name, *name_words])
    if ad_brief:
        problems += find_message_match_violations(page, ad_brief, exempt)
    return problems


def writer_lines(display_name=None):
    lines = [
        "Heading, body and proof line are three different lines: the heading names the point in a "
        "few words, the body says what it means for the reader, the proof line states the verified "
        "fact (the number, the spec, the policy). Put a number in the proof line and let the body "
        "say what it means -- never the same sentence or the same number in both.",
        f"State any one number in at most {MAX_NUMBER_FIELDS} places across the items, audience_fit "
        "and FAQ, and never repeat a sentence. The FAQ answers objections the items did not already "
        "answer -- it is not a second copy of the items.",
        "Numbers: state only a number listed in allowed_numbers (in the user message), with one of "
        "its claim_ids on that line. The ad speaker's own figures appear only inside her quoted words.",
        "Quotes: an attributed line (attributed_to_customer: true) is one allowed frame plus one "
        "ad_quotes entry copied word for word in quotation marks -- e.g. In the ad, she says, "
        "\"<ad_quotes text>\" -- and nothing else in that sentence. If no ad_quotes entry fits the "
        "item, write no attributed line there.",
        "Never talk about the source in your own narration: no \"the ad\", \"the video\", \"the ad "
        "speaker\", \"as she found\". The ad is named only in the quote frame above. The headline and "
        "dek speak to the reader; \"she\"/\"he\" there is allowed only for the ad's speaker, when "
        "hero_quote_id is set (see First screen).",
        "The headline and dek carry the ad's own hook: the dek says the ad's problem in the ad's "
        "words (ad_brief.hook / angle), as one plain, literal sentence -- no invented figure of "
        "speech. Item 1 answers the ad's main point. The skeleton decides the item roles after "
        "that, never the page's promise.",
    ]
    if display_name:
        lines.append(
            f"The brand on its own is written exactly {display_name!r}, every time; a product is "
            "always its full name (as in facts_pack) or its model name."
        )
    return lines


def myth_writer_line():
    return ("Myths: every item heading is a belief shoppers hold about the category, in their words "
            "(\"A home sauna needs special electrical work\") -- never a product spec, the warranty "
            "sentence or a brand fact, and never the brand or a model name.")


# ---------------------------------------------------------------------------
# Allowed numbers: every number the verified claims state, with their ids.
# ---------------------------------------------------------------------------

_NUMBER_WITH_UNIT_RE = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?(?:%|°F|°C|\s?(?:V|A|W|nm|in|inches|ft|feet|lbs?|years?|months?|weeks?|"
    r"business days|days?|minutes?|hours?)\b)?"
)


def allowed_numbers(facts_pack):
    """[{"number": "120V", "claim_ids": [...]}, ...] -- every number that a
    verified claim in this run's facts pack states (product-name digits such
    as "2-Person" excluded), in claim order, each with the ids that state
    it. The writer may state these and no others."""
    exempt = (facts_pack or {}).get("digit_exempt_terms")
    by_number = {}
    for claim in (facts_pack or {}).get("verified_claims", []):
        text = _strip_digit_exempt_tokens(claim.get("text") or "", exempt)
        for m in _NUMBER_WITH_UNIT_RE.finditer(text):
            token = _NUMBER_TOKEN_RE.match(m.group(0))
            key = token.group(0).replace(",", "") if token else m.group(0)
            entry = by_number.setdefault(key, {"number": m.group(0).strip(), "claim_ids": []})
            if claim["id"] not in entry["claim_ids"]:
                entry["claim_ids"].append(claim["id"])
    return list(by_number.values())
