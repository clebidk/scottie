"""Cycle 65: quote fidelity. An attributed_to_customer sentence must say what
the ad speaker actually said -- not a writer's embellishment of it.

The claims gate (claims.find_missing_attribution, speaker_numbers) already
checks that an attributed sentence *reads* as attributed and that its numbers
are the speaker's own. It never checked the words in between, so a writer
could publish "One customer told us she almost gave up on her search
entirely..." when the speaker never said anything like it (live page
listicle-test-1, run 20260922-191713-hidden-costs-v2-55n2). This module is
that missing check. Deterministic, no model call:

1. Content: the sentence minus its attribution frame ("One customer said",
   "In the ad, she says", "as one shopper put it") -- every remaining content
   word is something the page claims the speaker said.
2. Grounding: lowercase, drop stopwords, stem; the share of the content's
   distinct content words found inside ONE contiguous window of the
   transcript (or one ad_brief.speaker_experience line) of similar length
   must be >= FIDELITY_THRESHOLD. A quoted span ("...") must clear the
   stricter QUOTE_THRESHOLD -- quotation marks promise her exact words.
3. Embellishment: an intensifier/outcome word from EMBELLISHMENTS that the
   speaker never used anywhere ("almost", "gave up", "finally", "never"...)
   fails the sentence outright, whatever the overlap score.
4. Framing: unless the tenant sets ad_speaker_is_verified_customer: true,
   the ad speaker is only the person in the ad -- nothing tells us she
   bought anything or ever spoke to the brand. "told us", "customer",
   "buyer", "owner" and the like are misleading then, and fail.
"""

import re

from .textutil import walk_page

# Tuned on all 59 attributed nodes in the first tenant's run output (cycle 65
# table in docs/FIXLOG.md): faithful paraphrases scored 0.71-0.86 and verbatim
# quotes 1.00; the listicle-test-1 embellishment scored 0.50, and no invented
# line scored above 0.67. A false reject costs one repair attempt (the writer
# then quotes her words, which scores 1.00); a false accept publishes an
# invented testimonial -- so the cut sits just under the faithful band.
FIDELITY_THRESHOLD = 0.7
QUOTE_THRESHOLD = 0.9

# Window length, in content words, for a content of n distinct content words:
# a faithful paraphrase is about as long as what it paraphrases.
def _window_len(n):
    return max(n + 4, int(n * 1.5) + 2)


_STOPWORDS = frozenset("""
a about above after again against all am an and any are aren as at be because been before being below
between both but by can cannot could couldn did didn do does doesn doing don down during each few for from
further had hadn has hasn have haven having he her here hers herself him himself his how i if in into is isn
it its itself just me more most my myself no nor not now of off on once or other our ours ourselves out over
own same she should shouldn so some such than that the their theirs them themselves then there these they
this those through to too under until up very was wasn we were weren what when where which while who whom
why will with won would wouldn you your yours yourself yourselves us s t ll ve re m d o y also even really
very much many lot lots thing things something anything nothing get got getting gets go going gone went
like um uh oh yeah okay ok well actually sort mean
""".split())

# Words that frame WHO said something rather than WHAT was said. Dropped
# from the content before scoring -- they are the attribution itself.
_FRAME_WORDS = frozenset("""
customer customers buyer buyers shopper shoppers owner owners person woman man one another ad video
told tell tells telling said say says saying put puts way explained explains mentioned mentions noted
notes described describes recalled recalls admitted admits estimated estimates wrote writes shared
shares according quote quoted words own
""".split())

_IRREGULAR = {
    "gave": "give", "given": "give", "found": "find", "came": "come", "told": "tell", "made": "make",
    "said": "say", "bought": "buy", "spent": "spend", "felt": "feel", "thought": "think", "took": "take",
    "taken": "take", "left": "leave", "kept": "keep", "paid": "pay", "saw": "see", "seen": "see",
    "knew": "know", "known": "know", "wanted": "want", "hated": "hate", "ran": "run", "sat": "sit",
    "better": "good", "best": "good",
}

# A few near-synonyms a faithful paraphrase swaps in, keyed by stem.
_SYNONYMS = {"cost": "pric", "kind": "typ", "purchas": "buy"}

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_QUOTED_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def _stem(word):
    word = _IRREGULAR.get(word, word)
    for suffix in ("ingly", "edly", "ing", "ies", "ied", "ed", "es", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if suffix in ("ies", "ied"):
                word += "y"
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiou":
        word = word[:-1]  # shopp -> shop
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]  # hate/hated -> hat
    if len(word) > 3 and word.endswith("y"):
        word = word[:-1] + "i"  # easy/easily -> easi
    return _SYNONYMS.get(word, word)


def _tokens(text):
    """Lowercased words, apostrophe suffixes dropped ("didn't" -> "didn",
    "brand's" -> "brand"), numbers with commas/$ normalized ("$2,400" -> "2400")."""
    text = text.lower().replace("’", "'").replace(",", "")
    out = []
    for w in _WORD_RE.findall(text):
        out.append(w.split("'")[0])
    return out


def _content_stems(text, drop_frame=False):
    stems = []
    for w in _tokens(text):
        if w in _STOPWORDS or (drop_frame and w in _FRAME_WORDS):
            continue
        stems.append(_stem(w))
    return stems


def speaker_sources(ad_brief):
    """The ad speaker's own words: the raw transcript, plus each
    ad_brief.speaker_experience line as its own source. [] when there is
    none."""
    if not ad_brief or ad_brief.get("speaker_pov") in ("brand", "none"):
        return []  # a brand-voice ad (e.g. a still) has no speaker to quote
    parts = []
    transcript = ad_brief.get("transcript_or_text")
    if isinstance(transcript, str) and transcript.strip():
        parts.append(transcript)
    parts += [p for p in (ad_brief.get("speaker_experience") or []) if isinstance(p, str) and p.strip()]
    return parts


def grounding_score(content, sources, drop_frame=True):
    """Share (0..1) of `content`'s distinct content words found together in
    one contiguous window of one source. 1.0 for content with no content
    words at all (nothing is being claimed)."""
    wanted = set(_content_stems(content, drop_frame=drop_frame))
    if not wanted:
        return 1.0
    size = _window_len(len(wanted))
    best = 0
    for source in sources:
        seq = _content_stems(source)
        if len(seq) <= size:
            windows = [seq]
        else:
            windows = [seq[i:i + size] for i in range(len(seq) - size + 1)]
        for window in windows:
            best = max(best, len(wanted & set(window)))
            if best == len(wanted):
                return 1.0
    return best / len(wanted)


# Intensifier/outcome words a writer adds to make a testimonial land harder.
# Each group: the variants that count as the same word. A group fires only
# when the sentence uses one of its variants AND the speaker's own words
# never use any of them.
EMBELLISHMENTS = (
    ("almost",),
    ("nearly",),
    ("gave up", "give up", "giving up", "given up", "gives up"),
    ("finally",),
    ("life-changing", "life changing", "changed my life", "changed her life", "changed his life",
     "changed their life", "game-changer", "game changer"),
    ("every day", "everyday", "every single day", "daily"),
    ("never",),
    ("always",),
    ("best",),
    ("only",),
    ("entirely",),
    ("completely",),
    ("totally",),
    ("instantly", "immediately"),
    ("forever",),
    ("obsessed",),
    ("love", "loves", "loved", "loving"),
)


def _phrase_re(phrase):
    return re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])")


def find_embellishments(text, sources):
    """Embellishment words in `text` that no source ever uses."""
    said = " ".join(sources).lower().replace("’", "'")
    lowered = text.lower().replace("’", "'")
    found = []
    for group in EMBELLISHMENTS:
        used = [p for p in group if _phrase_re(p).search(lowered)]
        if used and not any(_phrase_re(p).search(said) for p in group):
            found.append(used[0])
    return found


# Framing that claims more than the ad shows: that the speaker contacted the
# brand ("told us"), or bought from it ("customer", "buyer", "owner").
_MISLEADING_FRAME_RE = re.compile(
    r"\b(?:told|tell|tells|telling)\s+(?:us|me|our\s+team)\b"
    r"|\bwrote\s+(?:to|in\s+to)\s+us\b|\bshared\s+with\s+us\b|\bsent\s+us\b|\breached\s+out\b"
    r"|\b(?:customer|customers|buyer|buyers|owner|owners|purchaser|purchasers|client|clients)\b",
    re.IGNORECASE,
)
# "told us" is misleading even for a verified customer: the ad speaker spoke
# in an ad, not to the brand.
_TOLD_US_RE = re.compile(
    r"\b(?:told|tell|tells|telling)\s+(?:us|me|our\s+team)\b"
    r"|\bwrote\s+(?:to|in\s+to)\s+us\b|\bshared\s+with\s+us\b|\bsent\s+us\b|\breached\s+out\b",
    re.IGNORECASE,
)


def find_misleading_frames(text, ad_speaker_verified=False):
    """Each misleading frame phrase in `text`, outside its quoted spans (her
    own "Just tell me..." is not a frame), lowercased and deduplicated."""
    unquoted = _QUOTED_RE.sub(" ", text)
    pattern = _TOLD_US_RE if ad_speaker_verified else _MISLEADING_FRAME_RE
    return list(dict.fromkeys(m.group(0).lower() for m in pattern.finditer(unquoted)))


def quoted_spans(text):
    return [a or b for a, b in _QUOTED_RE.findall(text)]


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# A sentence that reports the speaker: it names or quotes her, or says what
# she said/did. A sentence with none of these is the page author's own
# narration (governed by the ordinary claim_id rules, not this check).
_REPORTED_CUE_RE = re.compile(
    r"\b(?:she|he|her|his|customer|customers|buyer|shopper|owner|said|says|told|estimated|put it|in the ad)\b"
    r"|\x00",
    re.IGNORECASE,
)


def reported_sentences(text):
    """(sentence, quoted_spans) for each sentence of `text` that reports the
    speaker. Quoted spans are cut out before splitting (a quote's own
    periods never split it) and returned separately; the sentence keeps a
    NUL placeholder where each quote was."""
    spans = quoted_spans(text)
    # A quote that ends its own sentence ("...costs.") keeps that sentence
    # break once masked.
    masked = _QUOTED_RE.sub(
        lambda m: "\x00" + ("." if (m.group(1) or m.group(2)).rstrip().endswith((".", "!", "?")) else ""),
        text,
    )
    out = []
    span_iter = iter(spans)
    for sentence in _SENTENCE_SPLIT_RE.split(masked):
        own = [next(span_iter) for _ in range(sentence.count("\x00"))]
        if _REPORTED_CUE_RE.search(sentence):
            out.append((sentence, own))
    return out


def score_attributed_text(text, sources):
    """The lowest grounding score across `text`'s reported sentences (1.0
    when none) -- the number the gate compares to FIDELITY_THRESHOLD."""
    scores = [grounding_score(s.replace("\x00", " "), sources) for s, _ in reported_sentences(text)]
    return min(scores) if scores else 1.0


def check_attributed_text(text, sources, ad_speaker_verified=False):
    """Every problem with one attributed_to_customer text, as short strings
    ([] if it is faithful). `sources` from speaker_sources()."""
    problems = []
    for frame in find_misleading_frames(text, ad_speaker_verified):
        problems.append(f'misleading attribution frame "{frame}"')
    if not sources:
        problems.append("the ad has no speaker transcript to quote")
        return problems
    for sentence, spans in reported_sentences(text):
        for span in spans:
            score = grounding_score(span, sources, drop_frame=False)
            if score < QUOTE_THRESHOLD:
                problems.append(
                    f'quoted words "{span}" are not the speaker\'s own (match {score:.2f} < {QUOTE_THRESHOLD})'
                )
        score = grounding_score(sentence.replace("\x00", " "), sources)
        if score < FIDELITY_THRESHOLD:
            problems.append(f"not what the speaker said (match {score:.2f} < {FIDELITY_THRESHOLD})")
    for word in find_embellishments(text, sources):
        problems.append(f'adds "{word}", which the speaker never said')
    return problems


def repair_instruction(ad_speaker_verified=False):
    frames = '"In the ad, she says ...", "As one shopper put it, ..."'
    if ad_speaker_verified:
        frames += ', "a customer said ...", or a direct quote credited to a "<brand name> customer"'
        never = 'never "told us"'
    else:
        never = ('never "told us", and never "customer", "buyer" or "owner" -- nothing shows the ad '
                 "speaker bought one or spoke to the brand")
    return (
        "quote the speaker's own words from ad_brief.transcript_or_text (in quotation marks, word for "
        f"word), or drop the attribution and the statement with it. Frame it as {frames}; {never}."
    )


def find_unfaithful_attribution(page_json, ad_brief, ad_speaker_verified=False):
    """One {"path", "key", "issue", "text"} per attributed_to_customer node
    whose text is not grounded in the ad speaker's own words, embellishes
    them, or frames her misleadingly. Same shape as the claims gate's other
    find_* checks; "key" keeps one entry per node across repair attempts."""
    sources = speaker_sources(ad_brief)
    hits = []
    for path, node in walk_page(page_json):
        if not (isinstance(node, dict) and node.get("attributed_to_customer") is True):
            continue
        text = node.get("text")
        if not isinstance(text, str):
            continue
        problems = check_attributed_text(text, sources, ad_speaker_verified)
        if problems:
            hits.append({
                "path": path,
                "key": f"quote_fidelity:{path}",
                "issue": (
                    "attributed_to_customer text is not faithful to the ad speaker's own words ("
                    + "; ".join(problems) + ") -- "
                    + repair_instruction(ad_speaker_verified)
                ),
                "text": text,
            })
    return hits
