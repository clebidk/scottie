"""The writer repair loop and the page gate it runs.

Review 2026-09-11 R2: this module is the domain logic that used to sit in
harness/cli.py (~750 lines before any argparse) -- the page gates, the
deterministic pre-repair pass, the repair loop itself, and the soft checks.
cli.py is back to being a CLI; nothing here imports it, which is also what
kills the pipeline.py <-> cli.py import cycle (R1): pipeline.py imports this
module at top level now.

write_and_gate_page: write_page, then check_page_gates; on failure, first
the deterministic pre-repair pass (apply_deterministic_fixes -- no model
call) and re-gate, then, only if failures remain, a model repair with a
REVISION REQUIRED block, up to MAX_REPAIR_ATTEMPTS times. Ad-claim gate
failures (gate_ad_brief_claims, in pipeline.py's gate_ad_claims stage) are
unaffected -- those still STOP immediately, no retry.
"""
import json
import re
from pathlib import Path

from . import listicle
from . import pagechecks
from . import simplicity
from . import tenant as tenant_mod
from . import vocab
from .claims import (
    ClaimsGateFailure,
    default_warmup_window_words,
    find_warmup_violations,
    gate_page_json,
    strip_leaked_claim_ids,
    warranty_claim_id,
)
from .textutil import NON_PROSE_KEYS, walk_page
from .write import parse_word_range, resolve_allowed_cta_texts, word_range_target, write_page


MAX_REPAIR_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# harness run
# ---------------------------------------------------------------------------

# Fix 10: page.json keys that hold structural/reference data, not prose --
# excluded from the main-content word count. cta_url (fix cycle 3 item 4's
# single top-level CTA field) is the flattened equivalent of the old nested
# cta.url -- excluded the same way.


def _collect_prose_strings(node, out):
    for _path, n in walk_page(node, skip_keys=NON_PROSE_KEYS):
        if isinstance(n, str):
            out.append(n)


def count_words(page_json):
    """Word count of a page's main content only: every prose string in
    page.json (the byline and disclosure blocks are renderer-injected and
    never appear in page.json, so they're excluded automatically)."""
    strings = []
    _collect_prose_strings(page_json, strings)
    return sum(len(s.split()) for s in strings)


# ---------------------------------------------------------------------------
# Fix cycle 4: writer repair loop. After write_page, run every page-level
# gate check (claims.gate_page_json's checks, plus the two new hard checks
# below); on failure, call the writer again with the original prompt plus a
# "REVISION REQUIRED" block listing each failure verbatim, up to
# MAX_REPAIR_ATTEMPTS times. Ad-claim gate failures (gate_ad_brief_claims, in
# cmd_run) are unaffected -- those still STOP immediately, no retry.
# ---------------------------------------------------------------------------

# article's CTA lives at page.cta.text (fix cycle 3 item 4 left article's
# single nested cta object alone); longform and product-page have a flat
# top-level cta_text.
def get_cta_text(page_json, cartridge_name):
    if cartridge_name == "article":
        return (page_json.get("cta") or {}).get("text")
    return page_json.get("cta_text")


def find_cta_violation(page_json, cartridge_name, allowed_cta_texts):
    """[] if allowed_cta_texts is empty/None (cartridge has no allowlist) or
    the page's CTA text matches one of the allowed, already-substituted
    options exactly; otherwise one problem dict in the same shape
    claims.gate_page_json's checks use."""
    if not allowed_cta_texts:
        return []
    cta_text = get_cta_text(page_json, cartridge_name)
    if cta_text in allowed_cta_texts:
        return []
    path = "$.cta.text" if cartridge_name == "article" else "$.cta_text"
    return [{
        "path": path,
        "issue": f"CTA text {cta_text!r} is not one of the allowed options: {allowed_cta_texts}",
    }]


def find_word_range_violation(page_json, word_range):
    """[] if word_range is None or the page's word count (count_words) falls
    inside it; otherwise one problem dict."""
    if not word_range:
        return []
    lo, hi = word_range
    wc = count_words(page_json)
    if lo <= wc <= hi:
        return []
    target = word_range_target(word_range)
    if wc < lo:
        detail = f"Expand sections with real substance until you're near {target} words, not just barely over {lo}."
    else:
        detail = f"Trim sections down toward {target} words."
    return [{
        "path": "$.word_count",
        "issue": f"Body is {wc} words; required {lo}-{hi}. {detail}",
    }]


def resolve_warmup_window(tenant, schema_default=None):
    """tenant.yaml's cartridges.article.warmup_window_words override, else
    the caller's own schema_default (write_and_gate_page already has
    schema in scope), else cartridges/article/schema.json's own default."""
    override = tenant.get("cartridges.article.warmup_window_words")
    if override:
        return override
    if schema_default:
        return schema_default
    return default_warmup_window_words()


def check_page_gates(page, facts_pack, cartridge_name, *, financing_lender, speaker_pov, word_range, allowed_cta_texts, ad_brief=None, block_slots=None, tenant=None, warmup_window_words=None, listicle_style=None):
    """Every page-level gate check, combined into one list of problem dicts
    (empty if the page passes everything). Never raises -- the repair loop
    decides what to do with the result."""
    problems = []
    try:
        gate_page_json(
            page, facts_pack, cartridge_name,
            financing_lender=financing_lender, speaker_pov=speaker_pov, ad_brief=ad_brief,
        )
    except ClaimsGateFailure as e:
        problems += e.items
    problems += find_word_range_violation(page, word_range)
    problems += find_cta_violation(page, cartridge_name, allowed_cta_texts)
    # Kimi long-run phase 2: the writer owns a page's asset ids and CTA urls,
    # so these two pagechecks live here where the repair loop can fix them.
    # The rendered-HTML checks stay post-render backstops in render_page --
    # see harness/pagechecks.py's module docstring for why.
    problems += pagechecks.find_image_allowlist_violations(page, facts_pack)
    problems += pagechecks.find_internal_link_violations(page)
    # Kimi long-run phase 3: the writer's block-variant picks (page.json's
    # "blocks" map) are writer-owned too -- same repair-loop home.
    problems += pagechecks.find_block_violations(page, cartridge_name, block_slots)
    # Design-skills pack (elayadesign/ai-design-skills): the taken hard
    # checks (filler copy, leftover AI cliches, dead '#' links). Soft
    # counterparts stay in find_soft_check_warnings.
    problems += pagechecks.find_design_skill_violations(page)
    # Cycle 30: warm-up window -- article only, and only a hard gate when
    # this tenant's cartridges.article.warmup_mode is "enforce" (default
    # "warn": advisory REVIEW.md line only, see find_soft_check_warnings).
    if cartridge_name == "article":
        tenant = tenant or tenant_mod.active()
        if tenant.get("cartridges.article.warmup_mode", "warn") == "enforce":
            window = resolve_warmup_window(tenant, schema_default=warmup_window_words)
            problems += find_warmup_violations(page, tenant, window)
    # Cycle 41: the listicle cartridge's own structural checks (style and
    # headline formula, item count/numbering/length/proof, hero, audience-fit
    # block, FAQ, recap, urgency vocabulary, renderer-owned sections). Writer-
    # owned and writer-fixable, so they gate here rather than post-render; each
    # carries a stable "key" the repair loop dedupes on.
    if cartridge_name == "listicle":
        tenant = tenant or tenant_mod.active()
        # Cycle 43: find_headline_slot_violations' tenant_name/product_names
        # come from this run's own tenant and facts_pack -- digit_exempt_terms
        # already carries every product's short_name/title/name across the
        # whole catalog (see harness/ground.py), so it doubles as the "any
        # product name" list here with no new facts_pack field.
        problems += listicle.find_listicle_violations(
            page, style=listicle_style,
            tenant_name=tenant.display_name,
            product_names=facts_pack.get("digit_exempt_terms"),
        )
    # Cycle 32: simplicity gate (above-fold links, headline word band, one
    # offer element) -- hard gate only when this tenant's simplicity_mode is
    # "enforce" (default "warn": advisory REVIEW.md line only, see
    # simplicity.simplicity_review_lines).
    tenant = tenant or tenant_mod.active()
    if tenant.get("simplicity_mode", "warn") == "enforce":
        problems += simplicity.find_simplicity_violations(page, cartridge_name)
    return problems


def _format_gate_failure(item):
    detail = f" (text: {item['text']!r})" if "text" in item else ""
    return f"- {item.get('path', '')}: {item['issue']}{detail}"


def build_revision_note(attempt, failures):
    """`failures` (fix cycle 6 item 3) is every failure seen across every
    attempt so far in this cartridge, deduplicated -- not just this attempt's
    -- so a repair that fixes one violation but reintroduces an earlier one
    still shows up as a still-open item next time, instead of the writer
    forgetting about it. The forbidden-word list is quoted verbatim both
    near the top of this block and (via write.write_page) at the top of the
    system prompt, so a repair attempt can't claim it forgot the list."""
    lines = [
        f"## REVISION REQUIRED (repair attempt {attempt} of {MAX_REPAIR_ATTEMPTS})",
        vocab.forbidden_words_block(),
        "",
        "Your page.json failed the gate checks below (every failure seen across every attempt "
        "so far on this page, not just your most recent one). Fix every one of them and return "
        "a complete, corrected page.json in the same schema -- the full page, not a diff or a "
        "patch. Fixing a flagged sentence by rewriting it often introduces a new, unflagged "
        "violation nearby (a different sentence using a forbidden word, or another unsourced "
        "number) -- re-read every sentence you touch, and every sentence next to it, against "
        "the system prompt's forbidden-term and claim_id rules before returning.",
        "",
    ]
    # Fix cycle 5: a leaked-claim-id failure was observed recurring across
    # repair attempts because the rewrite moved the same parenthetical id
    # onto a different sentence elsewhere on the page instead of removing
    # it -- call this out explicitly rather than relying on the generic
    # guidance above.
    if any("claim id leaked into copy" in f["issue"] for f in failures):
        lines.append(
            "At least one failure below is a claim id printed as text (e.g. "
            '"(spec-fuji-capacity)"). Delete the id from that sentence -- do not replace it '
            "with a different parenthetical, and do not move the same id onto another "
            "sentence anywhere else on the page. The id belongs only in that sentence's "
            "claim_ids array; no parenthetical is needed at all unless it's a plain-English "
            '"(source name, year)" citation.'
        )
        lines.append("")
    lines += [_format_gate_failure(item) for item in failures]
    lines.append("")
    lines.append(
        "Fix all of these. Do not introduce any new violation. Before answering, re-read the "
        "forbidden word list and remove every occurrence."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fix cycle 6 item 1: deterministic pre-repair pass. Before ever calling the
# writer again, try a handful of safe, no-model-call text fixes on exactly
# the fields the gate flagged, then re-run the gate -- only failures that
# survive this pass ever reach a REVISION REQUIRED prompt / real repair
# call. This is what breaks the "fix A, break B, re-break A" cycle observed
# on the hidden-costs-v2 verification run: attempt 1 hit the hype word
# "unlock"; attempt 2's rewrite fixed it but tripped the digit/claim_id gate
# on the product's own short_name (which carries its capacity digit);
# attempt 3's rewrite fixed that but reintroduced "unlock" -- exhausting
# MAX_REPAIR_ATTEMPTS on two bugs that each had a one-line deterministic fix.
# ---------------------------------------------------------------------------

# Case-preserving, whole-word substitution only -- never rewrites inside
# another word ("unlocking" is matched as its own key, not as "unlock" plus
# leftover "ing").
def _hype_synonyms():
    return vocab.HYPE_SYNONYMS


def _case_preserving_replacement(match, replacement):
    original = match.group(0)
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_hype_synonyms(text):
    """Whole-word, case-preserving substitution of every hype word the tenant's
    vocab.yaml gives a safe synonym for, plus exclamation mark -> period (a
    rewrite sometimes just swaps the sentence's punctuation instead of its
    wording)."""
    for word, replacement in _hype_synonyms().items():
        pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        # `replacement` bound as a default: the lambda is consumed inside this
        # same iteration today, but closing over a loop variable is one
        # refactor away from being a bug (ruff B023).
        text = pattern.sub(lambda m, r=replacement: _case_preserving_replacement(m, r), text)
    text = text.replace("!", ".")
    return re.sub(r"\.{2,}", ".", text)


# Minimal JSONPath-shaped navigator for the "$.a.b[0].c" paths claims.py's
# gate checks emit -- just enough to get/set the exact string node a failure
# points at.
_PATH_SEGMENT_RE = re.compile(r"\.([^.\[\]]+)|\[(\d+)\]")


def _path_segments(path):
    return [
        int(m.group(2)) if m.group(2) is not None else m.group(1)
        for m in _PATH_SEGMENT_RE.finditer(path[1:])
    ]


def _get_at_path(page, path):
    node = page
    for seg in _path_segments(path):
        node = node[seg]
    return node


def _set_at_path(page, path, value):
    segs = _path_segments(path)
    node = page
    for seg in segs[:-1]:
        node = node[seg]
    node[segs[-1]] = value


def _remove_redundant_nested_cta_url(page, raw_path):
    """True and page mutated (the nested cta_url at raw_path deleted) when
    it equals page's own top-level cta_url; False (page untouched) when it
    names a different url, or the path no longer resolves -- see the
    cycle 43 comment at this function's one call site."""
    segs = _path_segments(raw_path)
    if not segs or segs[-1] != "cta_url":
        return False
    node = page
    for seg in segs[:-1]:
        try:
            node = node[seg]
        except (KeyError, IndexError, TypeError):
            return False
    if not isinstance(node, dict) or node.get("cta_url") != page.get("cta_url"):
        return False
    del node["cta_url"]
    return True


# A trigger word (claims.TRIGGER_WORDS) that always needs a claim_id has a
# generic, non-trigger replacement safe enough to substitute blind: "study"/
# "studies" recur constantly in the article cartridge's buyer-education
# prose ("a careful buyer treats research as useful background...") with
# nothing in facts_pack.verified_claims to cite -- swapping in "research"
# (itself not a trigger word) drops the requirement without changing the
# sentence's meaning. Deliberately small: the other trigger words
# (medical/clinical/proven/rated/reviews/emf) either already have prompt-
# level guidance (reviews) or don't have a safe drop-in synonym, so a real
# repair call still handles those.
def _trigger_word_synonyms():
    return vocab.TRIGGER_WORD_SYNONYMS

# claims.validate_page_claim_ids's trigger-word issue text, e.g. 'text needs
# at least one claim_id (uses the word "study") -- cite a verified claim_id,
# or rewrite the sentence without it'.
_TRIGGER_WORD_ISSUE_RE = re.compile(r'uses the word "([a-z]+)"')


def _generic_word_sub(text, word, replacement):
    pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
    return pattern.sub(lambda m: _case_preserving_replacement(m, replacement), text)


# ---------------------------------------------------------------------------
# Fix cycle 12 item 2: incidental numerals. "driving to a studio at 7 a.m."
# tripped the plain digit rule -- 7 a.m. isn't a claim, it's an illustrative
# time with nothing to cite, but the gate can't tell that apart from an
# invented fact. Rather than force a claim_id (there isn't one) or a real
# repair call every time, a small deterministic pass converts a numeral time
# (1-12 followed by a.m./p.m./o'clock) or a standalone numeral 1-12 to
# words before ever calling the writer again -- this only ever runs on a
# text field that already has no claim_ids (that's the only way the "contains
# a number" failure fires at all -- see claims._trigger_reason), so it never
# touches a claim-cited sentence. A number outside 1-12, or one already
# formatted as a price/percentage/thousands-separated figure, is left alone
# for a real repair call -- those usually ARE an invented fact, not
# incidental phrasing.
# ---------------------------------------------------------------------------

_NUMERAL_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}

# Trailing lookahead, not \b: "a.m."/"p.m." end in a period (a non-word
# char), so a \b right after one fails to match whenever the next character
# is also non-word (a space, end of sentence) -- exactly the common case
# ("at 7 a.m. when it happened").
_TIME_NUMERAL_RE = re.compile(
    r"\b([1-9]|1[0-2])\s?(a\.m\.|am|p\.m\.|pm|o'clock)(?![a-zA-Z0-9])", re.IGNORECASE
)

# A standalone 1-12, not part of a larger figure: not preceded by a digit,
# '.', ',', '$', '%', or '-' (a price, a decimal, a thousands group, or a
# capacity token like "2-Person" -- those are real numbers, not incidental
# phrasing), and not followed by ',digit' (thousands separator), '.digit'
# (decimal), '%', or '-Person'/'-person' (capacity token).
_STANDALONE_NUMERAL_RE = re.compile(
    r"(?<![\d.,$%-])\b([1-9]|1[0-2])\b(?!\s*(?:[.,]\d|%|-[Pp]erson))"
)


def _capitalize_if_sentence_start(text, start, replacement):
    """True if `replacement` should be capitalized because it opens the
    string or follows sentence-ending punctuation."""
    before = text[:start].rstrip()
    return before == "" or before[-1:] in ".!?"


def convert_incidental_numerals(text):
    """Fix cycle 12 item 2: numerals 1-12 followed by a.m./p.m./o'clock, and
    standalone numerals 1-12 elsewhere in non-cited narrative text, written
    out as words -- "7 a.m." -> "seven in the morning", "2 hours" ->
    "two hours". Everything else (13+, a price, a percentage, a
    thousands-grouped or decimal figure, a product capacity token) is left
    untouched."""

    def time_repl(m):
        word = _NUMERAL_WORDS[int(m.group(1))]
        suffix = m.group(2).lower()
        if suffix in ("a.m.", "am"):
            phrase = f"{word} in the morning"
        elif suffix in ("p.m.", "pm"):
            phrase = f"{word} in the afternoon"
        else:
            phrase = f"{word} o'clock"
        if _capitalize_if_sentence_start(text, m.start(), phrase):
            phrase = phrase[:1].upper() + phrase[1:]
        return phrase

    text = _TIME_NUMERAL_RE.sub(time_repl, text)

    def standalone_repl(m):
        word = _NUMERAL_WORDS[int(m.group(1))]
        if _capitalize_if_sentence_start(text, m.start(), word):
            word = word[:1].upper() + word[1:]
        return word

    return _STANDALONE_NUMERAL_RE.sub(standalone_repl, text)


# ---------------------------------------------------------------------------
# Fix cycle 13 item 1: warranty wording, deterministic pre-repair. Sweep
# 2026-09-10b (still-levelup-4x5.png, still-unforgettable-4x5.png) STOPped
# because the writer's own repair attempt reproduced the same
# claims.find_warranty_violations failure it was asked to fix -- a short
# label ("Limited lifetime warranty", "Backed by a limited lifetime
# warranty...") or honest-sounding paraphrase, never the exact allowed
# sentence. Rather than spend a real repair call on a violation whose fix is
# always the same fixed sentence, resolve it here, before any model call.
# ---------------------------------------------------------------------------


def _warranty_claim_id(valid_claim_ids):
    """The verified claim id to cite for the fixed warranty sentence -- the
    tenant's own warranty_claim_id if this run has it, else the first id in
    valid_claim_ids that looks like a warranty claim, else None (attach no
    claim_ids rather than guess)."""
    configured = warranty_claim_id()
    if configured in valid_claim_ids:
        return configured
    for cid in sorted(valid_claim_ids):
        if "warranty" in cid.lower():
            return cid
    return None


def _is_warranty_spec_label(label):
    """True when `label` is already the allowed warranty spec label or
    otherwise names a warranty row. Cycle 34: a {label,value} row whose
    *value* mentions a lifetime warranty (e.g. Coverage / Heaters) must not
    be rewritten into the Warranty pair."""
    if not isinstance(label, str):
        return False
    stripped = label.strip().lower()
    if not stripped:
        return False
    if stripped == vocab.ALLOWED_WARRANTY_SPEC_LABEL.lower():
        return True
    return "warrant" in stripped


def _fix_warranty_violation(page, path, valid_claim_ids):
    """Replaces a warranty-wording gate failure at `path` with the fixed
    sentence (claim_ids attached on the sibling field), or, if `path` is a
    warranty spec-table row's "value" field, sets the fixed label/value pair
    instead (the row's other allowed form). Returns True if the page was
    changed.

    Cycle 34: only rewrite the label+value pair when the existing label is
    already a warranty label. Other {label,value} rows (Coverage, Heaters,
    …) whose value trips the warranty gate get the prose sentence on the
    flagged field only, so the original fact is not silently replaced by a
    duplicate Warranty row."""
    try:
        segs = _path_segments(path)
        node = page
        for seg in segs[:-1]:
            node = node[seg]
        key = segs[-1]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(node, dict) or not isinstance(node.get(key), str):
        return False

    if key == "value" and _is_warranty_spec_label(node.get("label")):
        node["label"] = vocab.ALLOWED_WARRANTY_SPEC_LABEL
        node["value"] = vocab.ALLOWED_WARRANTY_SPEC_VALUE
        if "claim_id" in node:
            node["claim_id"] = _warranty_claim_id(valid_claim_ids) or node["claim_id"]
        return True

    node[key] = vocab.ALLOWED_WARRANTY_SENTENCE
    claim_id = _warranty_claim_id(valid_claim_ids)
    if "claim_ids" in node and claim_id:
        node["claim_ids"] = [claim_id]
    return True


# ---------------------------------------------------------------------------
# Fix cycle 21: financing wording, deterministic pre-repair -- same rationale
# and shape as _fix_warranty_violation above (fix cycle 13 item 1): a
# financing_line gate failure always has the same fix (the one allowed
# sentence for this run, no-lender or with-lender), so resolve it here,
# before spending a real repair call on it.
# ---------------------------------------------------------------------------


def _fix_financing_violation(page, path, financing_lender):
    """Replaces a financing-wording gate failure at `path` with the one
    allowed financing sentence for this run (vocab.allowed_financing_sentence,
    formatted with financing_lender when set). Returns True if the page was
    changed. Unlike warranty, the fixed sentence carries no digit/lender-name
    trigger of its own, so no claim_id needs attaching. Fix cycle 25: `path`
    may now point at an ordinary prose field (claims.find_financing_violations'
    new cartridge-independent prose scan), not just the dedicated
    `financing_line` field -- same whole-field replacement either way, same
    as _fix_warranty_violation above for warranty copy outside its own
    dedicated fields."""
    try:
        segs = _path_segments(path)
        node = page
        for seg in segs[:-1]:
            node = node[seg]
        key = segs[-1]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(node, dict) or not isinstance(node.get(key), str):
        return False
    node[key] = vocab.allowed_financing_sentence(financing_lender)
    return True


def apply_deterministic_fixes(page, failures, valid_claim_ids, log=None, cartridge_name=None,
                               financing_lender=None):
    """Mutates `page` in place, resolving exactly the failures that a safe
    text substitution can fix -- a forbidden hype word/exclamation mark, a
    claim id leaked into a parenthetical, a trigger word with a safe
    generic synonym (_TRIGGER_WORD_SYNONYMS), a warranty-wording violation
    (fix cycle 13 item 1), or a financing-wording violation (fix cycle 21) --
    and leaving everything else (a missing claim_id with no safe rewrite, a
    word-count or CTA violation, EMF, a banned name) for a real repair call.
    Returns the number of fields changed. `log`/`cartridge_name`, when both
    given, get one "deterministic fix applied: warranty sentence" (or
    "...: financing sentence") event per field fixed. `financing_lender`
    (fix cycle 21) is the run's configured lender, if any -- passed straight
    through to vocab.allowed_financing_sentence for the financing fix."""
    fixed = 0
    for item in failures:
        raw_path = item.get("path")
        if not raw_path:
            continue
        term = item.get("term")
        issue = item.get("issue", "")

        # Cycle 41: a listicle headline whose leading count is spelled out,
        # or no longer matches the item count after another fix changed it.
        # One token, re-checked against the style's own formula -- see
        # listicle.fix_headline_number for why this is deterministic rather
        # than a repair call.
        if cartridge_name == "listicle" and item.get("key") == "listicle:headline_formula":
            corrected = listicle.fix_headline_number(page)
            if corrected:
                page["headline"] = corrected
                fixed += 1
                if log is not None:
                    log.event(f"write.{cartridge_name}", "deterministic fix applied: headline count")
            continue

        # Cycle 43: a writer that echoes the page's own cta_url into a
        # nested object (closing.cta_url observed on a real run) trips
        # claims.find_second_cta_violation even though it names the same,
        # single destination -- delete the redundant copy rather than
        # spending a repair call on it. A nested cta_url naming a DIFFERENT
        # url is a real second offer card and is left for the gate to
        # report (_remove_redundant_nested_cta_url returns False).
        if cartridge_name == "listicle" and raw_path.endswith(".cta_url") and "second CTA url" in issue:
            if _remove_redundant_nested_cta_url(page, raw_path):
                fixed += 1
                if log is not None:
                    log.event(
                        f"write.{cartridge_name}",
                        f"deterministic fix applied: removed redundant nested cta_url at {raw_path}",
                    )
            continue

        if "warranty wording must be exactly" in issue:
            if _fix_warranty_violation(page, raw_path, valid_claim_ids):
                fixed += 1
                if log is not None and cartridge_name is not None:
                    log.event(f"write.{cartridge_name}", "deterministic fix applied: warranty sentence")
            continue

        if "financing_line must be exactly" in issue or "financing wording must be exactly" in issue:
            if _fix_financing_violation(page, raw_path, financing_lender):
                fixed += 1
                if log is not None and cartridge_name is not None:
                    log.event(f"write.{cartridge_name}", "deterministic fix applied: financing sentence")
            continue

        if term in _hype_synonyms() or term == "!":
            path = raw_path
            substitute = apply_hype_synonyms
        elif "claim id leaked into copy" in issue:
            path = raw_path

            def substitute(text, ids=valid_claim_ids):
                return strip_leaked_claim_ids(text, ids)[0]
        elif "(contains a number)" in issue:
            # Fix cycle 12 item 2: same path-shape as the trigger-word case
            # below -- validate_page_claim_ids points at the containing
            # node, not the "text" string itself.
            path = raw_path if raw_path.endswith(".text") else f"{raw_path}.text"
            substitute = convert_incidental_numerals
        else:
            m = _TRIGGER_WORD_ISSUE_RE.search(issue)
            word = m.group(1) if m else None
            replacement = _trigger_word_synonyms().get(word)
            if not replacement:
                continue
            # validate_page_claim_ids's trigger-word path points at the
            # containing node (the one with the "text" field), not the
            # string itself -- unlike the forbidden-term/leaked-id paths
            # above, which already point at the string.
            path = raw_path if raw_path.endswith(".text") else f"{raw_path}.text"

            def substitute(text, w=word, r=replacement):
                return _generic_word_sub(text, w, r)

        try:
            current = _get_at_path(page, path)
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(current, str):
            continue

        new_text = substitute(current)
        if new_text != current:
            _set_at_path(page, path, new_text)
            fixed += 1
    return fixed


def cartridge_write_constraints(cartridge_name, cartridges_dir, facts_pack, ad_brief, tenant):
    """(schema, word_range, allowed_cta_texts) for one cartridge -- shared by
    write_and_gate_page below and harness/batch.py's --batch path, so both
    compute the exact same hard constraints for the exact same cartridge."""
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md = tenant.render((cartridge_dir / "cartridge.md").read_text())
    schema = json.loads(tenant.render((cartridge_dir / "schema.json").read_text()))
    word_range = parse_word_range(cartridge_md)
    allowed_cta_texts = resolve_allowed_cta_texts(
        schema, facts_pack["product"]["short_name"], model_name=facts_pack["product"]["name"],
        tenant=tenant, ad_angle=(ad_brief or {}).get("angle"),
    )
    return schema, word_range, allowed_cta_texts


def write_and_gate_page(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, client, model, budget, log,
                         financing_lender, speaker_pov, ad_not_repeated=None, tenant=None,
                         repair_first_model=None, repair_next_model=None,
                         initial_page=None, initial_call_tokens=0, listicle_style=None):
    """write_page, then check_page_gates; on failure, first tries the
    deterministic pre-repair pass (apply_deterministic_fixes -- no model
    call) and re-gates, then, only if failures remain, retries write_page
    with a REVISION REQUIRED block up to MAX_REPAIR_ATTEMPTS times. Every
    write_page call (initial + repairs) counts against the run's budget like
    any other model call; the deterministic pass does not. Returns (page,
    attempts, deterministic_fix_counts) on success -- attempts is a list of
    that attempt's failure list (empty for the winning attempt, after any
    deterministic fix has already been applied), deterministic_fix_counts
    is the parallel list of how many fields the pre-repair pass fixed on
    that attempt. Raises ClaimsGateFailure (stage page_json:<cartridge>,
    with .attempts and .deterministic_fixes set) if every attempt fails.

    repair_first_model/repair_next_model (fix cycle 17 item 4, model
    tiering): the model attempt 2 (the first repair) and attempt 3+ (any
    further repair) use -- each falls back to `model` when not given, so a
    caller that only passes `model` keeps every attempt on that one model,
    exactly as before this cycle.

    initial_page/initial_call_tokens (fix cycle 17 item 5, batch mode):
    when `initial_page` is given, attempt 1 gates this page instead of
    calling write_page itself -- the caller (cli.py's --batch path) already
    got it from a batched initial write and already logged/budgeted its
    usage; initial_call_tokens feeds the same repair-skip budget heuristic
    a normal write_page call's own token cost does."""
    tenant = tenant or tenant_mod.active()
    schema, word_range, allowed_cta_texts = cartridge_write_constraints(
        cartridge_name, cartridges_dir, facts_pack, ad_brief, tenant
    )
    valid_claim_ids = {c["id"] for c in facts_pack["verified_claims"]}

    def _gate(page):
        return check_page_gates(
            page, facts_pack, cartridge_name,
            financing_lender=financing_lender, speaker_pov=speaker_pov,
            word_range=word_range, allowed_cta_texts=allowed_cta_texts,
            ad_brief=ad_brief,
            block_slots=schema.get("block_slots"),
            tenant=tenant,
            warmup_window_words=schema.get("warmup_window_words"),
            listicle_style=listicle_style,
        )

    revision_note = None
    attempts = []
    deterministic_fix_counts = []
    # Fix cycle 6 item 3: every failure seen so far in this cartridge,
    # deduplicated -- carried into every REVISION REQUIRED block so a repair
    # that fixes one violation but reintroduces an earlier one still shows
    # up as still-open, instead of the writer only seeing its most recent
    # mistake. Cycle 32: dedup key is the failure's own stable "key" when it
    # has one (e.g. "warmup:brand" -- the same violation category recognized
    # across attempts even though its word-index/budget detail changes),
    # else (path, issue) as before. A repeat under the same key REPLACES the
    # stored item (ordered dict: position stays, value updates), so the
    # REVISION REQUIRED block always states the latest detail for a
    # still-open violation instead of a stale one from an earlier attempt.
    failures_seen_by_key = {}
    # Fix cycle 11 problem C: tokens actually spent by each write_page call
    # made for THIS cartridge so far (initial write + repairs) -- used below
    # to estimate whether the budget remaining can afford another one.
    call_token_costs = []
    attempt = 0
    while True:
        attempt += 1
        remaining = budget.token_limit - budget.tokens_used
        log.event(
            f"write.{cartridge_name}",
            f"attempt {attempt}: budget remaining {remaining} tokens "
            f"({budget.tokens_used}/{budget.token_limit} used)",
        )
        # Fix cycle 11 problem C: a repair attempt (attempt > 1 -- attempt 1
        # always goes ahead) is skipped, and the run STOPs with a clear
        # reason, once the remaining token budget can no longer afford the
        # average cost of one write_page call on this cartridge so far --
        # instead of thrashing into a hard BudgetExceeded mid-call (the
        # price-comparison-v2.mov failure this cycle is fixing: 5 real
        # attempts, ~$2.2, the 5th hitting the cap outright).
        if call_token_costs:
            avg_call_cost = sum(call_token_costs) / len(call_token_costs)
            if remaining < avg_call_cost:
                log.event(
                    f"write.{cartridge_name}",
                    f"repair skipped: budget (remaining {remaining} tokens < average call cost "
                    f"{avg_call_cost:.0f} tokens)",
                )
                err = ClaimsGateFailure(f"page_json:{cartridge_name}", attempts[-1] if attempts else [])
                err.attempts = attempts
                err.deterministic_fixes = deterministic_fix_counts
                err.budget_skipped = True
                raise err
        budget.check()
        # Fix cycle 17 item 4: attempt 1 stays on `model` (initial writes
        # never move off sonnet); attempt 2 (the first repair) tries
        # repair_first_model; attempt 3+ (a further repair) uses
        # repair_next_model. Either falls back to `model` when not given.
        if attempt == 1:
            model_for_attempt = model
        elif attempt == 2:
            model_for_attempt = repair_first_model or model
        else:
            model_for_attempt = repair_next_model or model

        if attempt == 1 and initial_page is not None:
            # Fix cycle 17 item 5 (batch mode): a batched initial write
            # already ran and was already logged/budgeted by the caller.
            page = initial_page
            call_token_costs.append(initial_call_tokens)
        else:
            tokens_before = budget.tokens_used
            page = write_page(
                cartridge_name=cartridge_name,
                cartridges_dir=cartridges_dir,
                ad_brief=ad_brief,
                facts_pack=facts_pack,
                client=client,
                model=model_for_attempt,
                budget=budget,
                log=log,
                word_range=word_range,
                allowed_cta_texts=allowed_cta_texts,
                revision_note=revision_note,
                ad_not_repeated=ad_not_repeated,
                tenant=tenant,
                listicle_style=listicle_style,
            )
            call_token_costs.append(budget.tokens_used - tokens_before)
        problems = _gate(page)

        fixed = (
            apply_deterministic_fixes(
                page, problems, valid_claim_ids, log=log, cartridge_name=cartridge_name,
                financing_lender=financing_lender,
            )
            if problems else 0
        )
        if fixed:
            log.event(f"write.{cartridge_name}", f"deterministic fix applied: {fixed} field(s)")
            problems = _gate(page)
        deterministic_fix_counts.append(fixed)
        attempts.append(problems)

        if not problems:
            log.event(f"write.{cartridge_name}", f"gate PASS on attempt {attempt}")
            log.gate_result("PASS", f"page_json:{cartridge_name} attempt={attempt} repairs={attempt - 1}")
            return page, attempts, deterministic_fix_counts

        log.event(f"write.{cartridge_name}", f"gate FAIL on attempt {attempt}: {problems}")
        for item in problems:
            dedup_key = item.get("key") or (item.get("path"), item.get("issue"))
            failures_seen_by_key[dedup_key] = item

        if attempt >= MAX_REPAIR_ATTEMPTS + 1:
            err = ClaimsGateFailure(f"page_json:{cartridge_name}", problems)
            err.attempts = attempts
            err.deterministic_fixes = deterministic_fix_counts
            raise err
        revision_note = build_revision_note(attempt, list(failures_seen_by_key.values()))


# ---------------------------------------------------------------------------
# Fix cycle 16: soft checks. Every one of these is advisory only -- a
# REVIEW.md warning line, never a gate failure, never a repair-loop trigger.
# They exist so an operator sees a formula/rule miss at review time without
# the run STOPping or spending a repair call on something that isn't a
# claims/policy violation.
# ---------------------------------------------------------------------------

# Design note 1 (item A1): article's headline formula, 8-14 words. Not
# enforced as a hard gate -- a real headline can reasonably land a word or
# two outside the target and still be a perfectly good headline.
HEADLINE_WORD_RANGE = (8, 14)


def find_headline_word_count_warning(page, cartridge_name, word_range=HEADLINE_WORD_RANGE):
    if cartridge_name != "article":
        return None
    headline = page.get("headline") or ""
    wc = len(headline.split())
    lo, hi = word_range
    if lo <= wc <= hi:
        return None
    return f"{cartridge_name}: headline is {wc} word(s), outside the {lo}-{hi} target: {headline!r}"


# Swipe-file item 6: proof inside each reason/section -- article's
# body_sections and listicle's reasons should each carry at least one
# claim_id, or an attributed customer statement, somewhere inside.
_PROOF_WARNING_SECTIONS = {
    "article": lambda page: [
        (f"body_sections[{i}]", section.get("paragraphs") or [])
        for i, section in enumerate(page.get("body_sections") or [])
    ],
    "listicle": lambda page: [(f"reasons[{i}]", [reason]) for i, reason in enumerate(page.get("reasons") or [])],
}


def _node_has_proof(node):
    return isinstance(node, dict) and (bool(node.get("claim_ids")) or node.get("attributed_to_customer") is True)


def find_missing_section_proof_warnings(page, cartridge_name):
    get_sections = _PROOF_WARNING_SECTIONS.get(cartridge_name)
    if not get_sections:
        return []
    warnings = []
    for label, nodes in get_sections(page):
        if not any(_node_has_proof(n) for n in nodes):
            warnings.append(
                f"{cartridge_name}: {label} has no claim_id and no attributed customer statement"
            )
    return warnings


# Swipe-file item 7: when ad_brief.audience names a specific audience, the
# headline should name it -- checked only for cartridges with a plain
# top-level "headline" string.
def find_audience_headline_warning(page, cartridge_name, ad_brief):
    audience = (ad_brief or {}).get("audience") or ""
    if not audience or cartridge_name not in ("article", "listicle"):
        return None
    headline = page.get("headline") or ""
    if audience.lower() in headline.lower():
        return None
    return f'{cartridge_name}: ad_brief.audience {audience!r} is not named in the headline: {headline!r}'


def find_warmup_warning_lines(page, cartridge_name, tenant=None):
    """Cycle 30: ["Warm-up window: brand appears at word N", ...] -- only
    for the article cartridge, and only when this tenant's
    cartridges.article.warmup_mode is "warn" (the "enforce" case is a hard
    gate in check_page_gates above, never a soft warning too)."""
    if cartridge_name != "article":
        return []
    tenant = tenant or tenant_mod.active()
    if tenant.get("cartridges.article.warmup_mode", "warn") != "warn":
        return []
    window = resolve_warmup_window(tenant)
    from .claims import warmup_first_mentions

    mentions = warmup_first_mentions(page, tenant)
    lines = []
    if mentions["brand_word"] is not None and mentions["brand_word"] <= window:
        lines.append(f"Warm-up window: brand appears at word {mentions['brand_word']}")
    if mentions["price_word"] is not None and mentions["price_word"] <= window:
        lines.append(f"Warm-up window: price appears at word {mentions['price_word']}")
    if mentions["cta_word"] is not None and mentions["cta_word"] <= window:
        lines.append(f"Warm-up window: CTA appears at word {mentions['cta_word']}")
    return lines


def find_soft_check_warnings(pages, ad_brief):
    """One warning string per issue, across every written page. Never
    raised, never gated -- purely REVIEW.md's own advisory section."""
    from .design_skills import gate as design_gate

    warnings = []
    for cartridge_name, page in pages.items():
        headline_warning = find_headline_word_count_warning(page, cartridge_name)
        if headline_warning:
            warnings.append(headline_warning)
        warnings += find_missing_section_proof_warnings(page, cartridge_name)
        audience_warning = find_audience_headline_warning(page, cartridge_name, ad_brief)
        if audience_warning:
            warnings.append(audience_warning)
        warnings += design_gate.find_generic_cta_warnings(page, cartridge_name)
        warnings += design_gate.find_tagline_warnings(page, cartridge_name)
        warnings += design_gate.find_design_reference_warnings(page, cartridge_name)
        warnings += find_warmup_warning_lines(page, cartridge_name)
    return warnings
