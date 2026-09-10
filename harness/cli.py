"""`harness` console entry point: run / ingest / claims / review / score /
shopify-body / tenant / workflow.

The CLI resolves which tenant a command is for (--tenant > HARNESS_TENANT >
tenants/default.txt), activates it, and hands the pipeline a Tenant object. No
company-specific value is read from anywhere else.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from . import notify
from . import pipeline
from . import runstate
from . import shopify as shopify_body_mod
from . import tenant as tenant_mod
from . import vocab
from . import workflows
from .anthropic_client import make_client
from .budget import Budget, BudgetExceeded
from .claims import (
    ClaimsGateFailure,
    collect_claim_ids,
    gate_page_json,
    strip_leaked_claim_ids,
    warranty_claim_id,
)
from . import exits
from .config import FFMPEG_BIN, WHISPER_BIN, WHISPER_MODEL
from .errors import HarnessError
from .ingest import run_ingest
from .log import RunLog
from .publishers.export import ExportPublisher
from .publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher, rewrite_asset_srcs
from .review import cmd_review
from .runstate import UnknownReviewer
from .shopify import write_shopify_body
from .textutil import NON_PROSE_KEYS
from .write import parse_word_range, resolve_allowed_cta_texts, word_range_target, write_page


# Exit codes live in harness/exits.py. Kept as a name here because it was part
# of this module's surface before that module existed.
EXIT_TENANT_NOT_CONFIGURED = exits.TENANT_NOT_CONFIGURED

# Run-shaping helpers live in harness/pipeline.py, which owns the stage list.
# Re-exported here because they are part of the CLI's own surface.
discover_cartridges = pipeline.discover_cartridges
slugify = pipeline.slugify
make_run_id = pipeline.make_run_id
MAX_REPAIR_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# harness run
# ---------------------------------------------------------------------------

# Fix 10: page.json keys that hold structural/reference data, not prose --
# excluded from the main-content word count. cta_url (fix cycle 3 item 4's
# single top-level CTA field) is the flattened equivalent of the old nested
# cta.url -- excluded the same way.


def _collect_prose_strings(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in NON_PROSE_KEYS:
                continue
            _collect_prose_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_prose_strings(v, out)
    elif isinstance(node, str):
        out.append(node)


def find_forbidden_term_urls(facts_pack, terms=None):
    """Every URL this run uses whose own path contains one of the tenant's
    banned terms. A storefront handle is outside this harness's control, so a
    page still links to the product URL as-is -- but each such URL is logged
    per run and listed in REVIEW.md so it stays visible."""
    if terms is None:
        terms = vocab.VISIBLE_TEXT_FORBIDDEN_TERMS
    terms = [t.lower() for t in terms]
    if not terms:
        return []
    urls = []
    product_url = facts_pack.get("product", {}).get("url")
    if product_url and any(t in product_url.lower() for t in terms):
        urls.append(product_url)
    for claim in facts_pack.get("verified_claims", []):
        source = claim.get("source", "")
        if source.startswith("http") and any(t in source.lower() for t in terms) and source not in urls:
            urls.append(source)
    return urls


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


def check_page_gates(page, facts_pack, cartridge_name, *, financing_lender, speaker_pov, word_range, allowed_cta_texts, ad_brief=None):
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
        text = pattern.sub(lambda m: _case_preserving_replacement(m, replacement), text)
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


def _fix_warranty_violation(page, path, valid_claim_ids):
    """Replaces a warranty-wording gate failure at `path` with the fixed
    sentence (claim_ids attached on the sibling field), or, if `path` is a
    spec-table row's "value" field, sets the fixed label/value pair instead
    (the row's other allowed form). Returns True if the page was changed."""
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

    if key == "value" and "label" in node:
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
    trigger of its own, so no claim_id needs attaching."""
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

        if "warranty wording must be exactly" in issue:
            if _fix_warranty_violation(page, raw_path, valid_claim_ids):
                fixed += 1
                if log is not None and cartridge_name is not None:
                    log.event(f"write.{cartridge_name}", "deterministic fix applied: warranty sentence")
            continue

        if "financing_line must be exactly" in issue:
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
            substitute = lambda text: strip_leaked_claim_ids(text, valid_claim_ids)[0]
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
            substitute = lambda text, w=word, r=replacement: _generic_word_sub(text, w, r)

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
                         initial_page=None, initial_call_tokens=0):
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
        )

    revision_note = None
    attempts = []
    deterministic_fix_counts = []
    # Fix cycle 6 item 3: every failure seen so far in this cartridge,
    # deduplicated by (path, issue) -- carried into every REVISION REQUIRED
    # block so a repair that fixes one violation but reintroduces an earlier
    # one still shows up as still-open, instead of the writer only seeing
    # its most recent mistake.
    failures_seen = []
    failures_seen_keys = set()
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
            key = (item.get("path"), item.get("issue"))
            if key not in failures_seen_keys:
                failures_seen_keys.add(key)
                failures_seen.append(item)

        if attempt >= MAX_REPAIR_ATTEMPTS + 1:
            err = ClaimsGateFailure(f"page_json:{cartridge_name}", problems)
            err.attempts = attempts
            err.deterministic_fixes = deterministic_fix_counts
            raise err
        revision_note = build_revision_note(attempt, failures_seen)


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


def find_soft_check_warnings(pages, ad_brief):
    """One warning string per issue, across every written page. Never
    raised, never gated -- purely REVIEW.md's own advisory section."""
    warnings = []
    for cartridge_name, page in pages.items():
        headline_warning = find_headline_word_count_warning(page, cartridge_name)
        if headline_warning:
            warnings.append(headline_warning)
        warnings += find_missing_section_proof_warnings(page, cartridge_name)
        audience_warning = find_audience_headline_warning(page, cartridge_name, ad_brief)
        if audience_warning:
            warnings.append(audience_warning)
    return warnings


def _log_run_result(log, result, gate_log):
    """Fix cycle 4 item 5: `run_result: PASS|STOP attempts=<n> repairs=<m>`,
    summed across every cartridge write_and_gate_page got to before the run
    ended -- attempts/repairs are 0 for a STOP that happened before any
    cartridge was written (e.g. the ad_claims gate)."""
    total_attempts = sum(len(v["attempts"]) for v in gate_log.values())
    total_repairs = sum(max(0, len(v["attempts"]) - 1) for v in gate_log.values())
    log.result(result, total_attempts, total_repairs)


def write_review_md(run_dir, *, ad_brief, facts_pack, product_name, selected, pages, budget, cost, gate_matched, forbidden_urls=None, gate_log=None, product_warning=None, ad_not_repeated=None, ad_alternative_claims=None):
    forbidden_urls = forbidden_urls or []
    lines = [
        f"# REVIEW: {run_dir.name}",
        "",
        f"- Angle: {ad_brief.get('angle', '')}",
        f"- Product: {product_name}",
        f"- Cartridges: {', '.join(selected)}",
        "",
    ]
    # Fix cycle 8 problem 1b: no model name was found in the ad, so the run
    # defaulted -- surface that prominently, it's the kind of thing an
    # operator needs to catch before a page ships grounded on the wrong SKU.
    if product_warning:
        lines.append(f"**WARNING: {product_warning}**")
        lines.append("")
    # Fix cycle 12 item 3: only ever present under ad_overclaim_policy "warn"
    # -- under "stop" any unmatched/overclaimed claim raises ClaimsGateFailure
    # before this function is ever called, so this run never reaches here
    # with one. The page itself was already written with instructions never
    # to repeat these; this is the record that the ad itself still needs a
    # correction. Broadened from fix cycle 10's "AD OVERCLAIMS" (locked-topic
    # only) to cover every unmatched-or-overclaimed claim, plain or locked.
    if ad_not_repeated:
        lines.append("**AD CLAIMS NOT REPEATED ON PAGE — ad needs fixing**")
        for item in ad_not_repeated:
            lines.append(f"- {item['message']}")
        lines.append("")
    # Fix cycle 12 item 3: a claim about the alternative/comparison option
    # (claims.classify_ad_claim_about) never goes through matching at all --
    # listed here purely for visibility, under either policy.
    if ad_alternative_claims:
        lines.append("**Ad statements about alternatives (not repeated)**")
        for item in ad_alternative_claims:
            lines.append(f'- "{item["claim"]}"')
        lines.append("")
    lines.append("## Ad claims matched")
    for m in gate_matched:
        lines.append(f"- ad claim \"{m['claim']}\" -> verified `{m['matched_claim_id']}` (overlap {m['overlap']})")

    verified_by_id = {c["id"]: c for c in facts_pack["verified_claims"]}
    used_claim_ids = set()
    for page in pages.values():
        used_claim_ids |= collect_claim_ids(page)
    lines.append("")
    lines.append("## Claims used")
    if used_claim_ids:
        for cid in sorted(used_claim_ids):
            c = verified_by_id.get(cid)
            text = c["text"] if c else "(not found in facts_pack.verified_claims)"
            lines.append(f"- `{cid}`: {text}")
    else:
        lines.append("- (no claim_ids referenced by any page)")

    lines.append("")
    lines.append("## Sources")
    for c in facts_pack["verified_claims"]:
        lines.append(f"- `{c['id']}`: {c['text']} ({c['source']})")

    lines.append("")
    lines.append("## Assets used")
    for a in facts_pack.get("assets", []):
        lines.append(f"- `{a['id']}`: {a['url']}")

    lines.append("")
    lines.append("## Pages")
    for name in selected:
        lines.append(f"- {name}/index.html")

    lines.append("")
    lines.append("## Word counts (main content only, excludes disclosure and byline)")
    for name in selected:
        wc = count_words(pages[name])
        lines.append(f"- {name}: {wc} words")

    # Fix cycle 16: soft checks (headline formula, proof-inside-section,
    # audience-in-headline) -- advisory only, never a gate failure.
    lines.append("")
    lines.append("## Soft-check warnings (non-blocking)")
    soft_warnings = find_soft_check_warnings(pages, ad_brief)
    if soft_warnings:
        for w in soft_warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Review checklist")
    if ad_brief.get("speaker_pov") == "first_person":
        lines.append(
            "- First-person attribution: PASS -- the ad speaker's first-person story was "
            "attributed to a customer (or facts_pack.speaker_name), not written in the "
            "author's own first person (gate: claims.find_first_person_violations)."
        )
    else:
        lines.append("- First-person attribution: not applicable (ad_brief.speaker_pov is not first_person).")

    lines.append("")
    lines.append("## Banned-topic handling")
    dropped = ad_brief.get("_dropped_emf_claims") or []
    if dropped:
        lines.append("Dropped from ad_brief during ingest:")
        for text in dropped:
            lines.append(f"- dropped claim: {text}")
    else:
        lines.append("- no claims/features were dropped from ad_brief during ingest.")
    if forbidden_urls:
        lines.append("URLs that still contain a banned term (storefront handle; pages link to it as-is):")
        for url in forbidden_urls:
            lines.append(f"- {url}")
    else:
        lines.append("- no URL used by this run contains a banned term.")

    lines.append("")
    lines.append("## Gate history")
    lines.append("(fix cycle 4 item 5 -- attempts include the writer repair loop: attempt 1 is the")
    lines.append("initial write, attempts 2-3 are repairs made from a REVISION REQUIRED prompt.")
    lines.append("Fix cycle 6 item 5 -- \"deterministic fixes\" is how many fields the no-model-call")
    lines.append("pre-repair pass fixed on that attempt, before the gate was re-run.)")
    lines.append("")
    lines.append("| Cartridge | Attempts | Failures per attempt | Deterministic fixes | Result |")
    lines.append("|---|---|---|---|---|")
    for name in selected:
        entry = (gate_log or {}).get(name, {})
        attempts = entry.get("attempts", [[]])
        det_fixes = entry.get("deterministic_fixes", [0] * len(attempts))
        per_attempt = "; ".join(
            f"attempt {i + 1}: {len(failures)} failure(s)" for i, failures in enumerate(attempts)
        )
        det_str = "; ".join(f"attempt {i + 1}: {n}" for i, n in enumerate(det_fixes)) or "0"
        lines.append(f"| {name} | {len(attempts)} | {per_attempt} | {det_str} | PASS |")

    lines.append("")
    lines.append("## Budget use")
    summary = budget.summary()
    for k, v in summary.items():
        lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append(f"## Token totals / estimated cost\n- estimated_cost_usd (estimate): ${cost:.4f}")

    (run_dir / "REVIEW.md").write_text("\n".join(lines) + "\n")


def cmd_run(args):
    """ingest -> ground -> gate -> write -> render -> REVIEW.md, for one tenant.

    The stage list lives in harness/pipeline.py; `harness workflow run
    ad-to-pages` runs the same functions in the order workflows/ad-to-pages.yaml
    gives, so the two commands cannot drift apart."""
    tenant = tenant_mod.load_tenant(args.tenant, require=True)
    tenant_mod.activate(tenant)
    tenant.load_env()
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    return pipeline.execute(state, pipeline.DEFAULT_STAGES)


def cmd_workflow_run(args):
    """Run one workflow's pipeline stages, in the order its YAML gives."""
    tenant = tenant_mod.load_tenant(args.tenant, require=True)
    tenant_mod.activate(tenant)
    tenant.load_env()
    workflow = workflows.load_workflow(args.name)
    names = workflows.stage_names(workflow)
    if not names:
        print(f"workflow {args.name!r} runs no pipeline stages", file=sys.stderr)
        return 1
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    return pipeline.execute(state, names)


def cmd_workflow_list(args):
    for name in workflows.list_workflows():
        workflow = workflows.load_workflow(name)
        print(f"{name:16s} {(workflow.get('description') or '').strip().splitlines()[0] if workflow.get('description') else ''}")
    return 0


# ---------------------------------------------------------------------------
# harness ingest
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    tenant_mod.activate(tenant)
    tenant.load_env()
    client = make_client()
    budget = Budget()
    run_id = pipeline.make_run_id(pipeline.slugify(args.input))
    out_dir = tenant.out_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}.log")

    try:
        ad_brief = run_ingest(
            input_arg=args.input,
            workdir=out_dir,
            client=client,
            model=tenant.model_for("ingest"),
            budget=budget,
            log=log,
            ffmpeg_bin=args.ffmpeg_bin,
            whisper_bin=args.whisper_bin,
            whisper_model=args.whisper_model,
        )
    except BudgetExceeded as e:
        log.event("ingest", f"budget exceeded: {e}")
        log.close()
        print(f"budget exceeded: {e}", file=sys.stderr)
        return 3

    (out_dir / "ad_brief.json").write_text(json.dumps(ad_brief, indent=2))
    log.cost_estimate()
    log.close()
    print(json.dumps(ad_brief, indent=2))
    print(f"\nWrote {out_dir / 'ad_brief.json'}")
    return 0


# ---------------------------------------------------------------------------
# harness tenant init / list
# ---------------------------------------------------------------------------

def cmd_tenant_init(args):
    root = tenant_mod.init_tenant(args.name)
    print(f"Created {root} from {tenant_mod.TEMPLATE_DIR.name}.")
    print(f"Next: work through {root / 'README.md'}. Until its claims store is")
    print("filled in, a run for this tenant exits 4 with a 'tenant not configured' message.")
    return 0


def cmd_tenant_list(args):
    active = tenant_mod.resolve_tenant_name(None) if tenant_mod.DEFAULT_FILE.exists() else None
    for name in tenant_mod.list_tenants():
        tenant = tenant_mod.load_tenant(name)
        missing = tenant.missing_pieces()
        status = "ready" if not missing else f"not configured ({', '.join(missing)})"
        marker = "*" if name == active else " "
        print(f"{marker} {name:20s} {status}")
    return 0


# ---------------------------------------------------------------------------
# harness shopify-body
# ---------------------------------------------------------------------------

def cmd_shopify_body(args):
    """`harness shopify-body <run-dir>/<cartridge>`: writes shopify-body.html
    and shopify-body.assets.json next to that cartridge's index.html. No
    storefront API call anywhere in this path -- see `harness publish`
    (below) for the actual publish step, which refuses to run without an
    approved state and a packet stamped "ship"."""
    tenant_mod.activate(tenant_mod.load_tenant(args.tenant))
    cartridge_dir = Path(args.cartridge_dir)
    index_path = cartridge_dir / "index.html"
    if not index_path.exists():
        print(f"no index.html found under {cartridge_dir}", file=sys.stderr)
        return 1

    shopify_body_path, assets_manifest_path = write_shopify_body(cartridge_dir)
    print(f"Wrote {shopify_body_path}")
    print(f"Wrote {assets_manifest_path}")
    return 0


# ---------------------------------------------------------------------------
# harness approve / reject / packet / publish / digest (cycle 20)
# ---------------------------------------------------------------------------

def _tenant_name_from_run_dir(run_dir):
    """tenants/<name>/out/<run-id> -> <name>, when the path has that shape;
    None otherwise, so the caller falls back to normal --tenant resolution."""
    parts = Path(run_dir).parts
    if "tenants" in parts:
        i = parts.index("tenants")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _resolve_tenant_for_run(args):
    name = args.tenant or _tenant_name_from_run_dir(args.run_dir)
    return tenant_mod.load_tenant(name)


def cmd_approve(args):
    """`harness approve <run-dir> --by <email> [--pages a,b] [--note ...]`:
    approves the named pages (default: every page in the run) for a
    reviewer listed in the tenant's tenant.yaml `reviewers`. An unknown
    email is refused with a one-line message, exit 1, no traceback."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    pages = [p.strip() for p in args.pages.split(",") if p.strip()] if args.pages else None
    try:
        data = runstate.approve(run_dir, tenant, by=args.by, pages=pages, note=args.note or "")
    except (UnknownReviewer, FileNotFoundError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    approved_pages = pages or list(data["pages"])
    print(f"Approved {','.join(approved_pages)} for {run_dir} (state={data['state']})")
    notify.notify_approved(tenant, run_id=run_dir.name, by=args.by, pages=approved_pages, run_dir=str(run_dir))
    return 0


def cmd_reject(args):
    """`harness reject <run-dir> --by <email> --note ...`: rejects the whole
    run. Same reviewer check as approve."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    try:
        data = runstate.reject(run_dir, tenant, by=args.by, note=args.note)
    except (UnknownReviewer, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Rejected {run_dir} (state={data['state']})")
    return 0


def cmd_packet(args):
    """`harness packet <run-dir> --stamp ship|redo|kill --by <email> [--note
    ...]`: sets the packet stamp `harness publish` checks. A new run starts
    at "BOT DRAFT · NOT SENT" (harness/pipeline.py's prepare_run)."""
    run_dir = Path(args.run_dir)
    if not runstate.state_path(run_dir).exists():
        print(f"no state.json under {run_dir} -- not a run directory this harness produced", file=sys.stderr)
        return 1
    data = runstate.set_packet_stamp(run_dir, stamp=args.stamp, by=args.by, note=args.note or "")
    print(f"Packet for {run_dir} stamped {data['stamp']!r} by {data['by']}")
    return 0


def _make_publisher(tenant, *, export_dir):
    """tenant.yaml's `publisher` key picks the adapter; `export` (the
    default) needs no credentials at all."""
    kind = tenant.get("publisher") or "export"
    if kind == "shopify":
        return ShopifyPublisher()  # reads SHOPIFY_STORE/SHOPIFY_TOKEN from the tenant's .env
    return ExportPublisher(out_dir=export_dir)


def _read_asset_manifest_bytes(cartridge_dir, assets_manifest):
    out = []
    for item in assets_manifest:
        asset_path = cartridge_dir / item["local_path"]
        out.append({**item, "bytes": asset_path.read_bytes() if asset_path.exists() else b""})
    return out


def cmd_publish(args):
    """`harness publish <run-dir> --page <cartridge> [--live] [--dry-run]`:
    refuses unless state.json's `pages[<cartridge>]` is "approved" AND
    packet.json's stamp is "ship". Default publish is unpublished (a draft
    page) unless `--live`; `--live` also verifies the storefront cache with
    8 pulls, 2s apart (the cache-epoch trap in
    the tenant's own storefront notes). `--dry-run`
    only validates credentials and the page body (a GET on the shop
    endpoint for the Shopify adapter) -- no approval or stamp required, and
    nothing is created."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    cartridge_dir = run_dir / args.page
    if not (cartridge_dir / "index.html").exists():
        print(f"no index.html under {cartridge_dir}", file=sys.stderr)
        return 1

    shopify_body_html, assets_manifest = shopify_body_mod.build_shopify_body(cartridge_dir)
    export_dir = cartridge_dir / "export"

    if args.dry_run:
        publisher = _make_publisher(tenant, export_dir=export_dir)
        report = publisher.dry_run({"body_html": shopify_body_html, "assets": assets_manifest})
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    try:
        state_data = runstate.load_state(run_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    current_page_state = state_data["pages"].get(args.page)
    if current_page_state != "approved":
        print(
            f"refusing to publish {args.page!r}: state is {current_page_state!r}, not 'approved'. "
            f"Run `harness approve {run_dir} --by <email> --pages {args.page}` first.",
            file=sys.stderr,
        )
        return 1

    packet = runstate.load_packet(run_dir)
    if packet.get("stamp") != "ship":
        print(
            f"refusing to publish: packet stamp is {packet.get('stamp')!r}, not 'ship'. "
            f"Run `harness packet {run_dir} --stamp ship --by <email>` first.",
            file=sys.stderr,
        )
        return 1

    publisher = _make_publisher(tenant, export_dir=export_dir)
    manifest_with_bytes = _read_asset_manifest_bytes(cartridge_dir, assets_manifest)

    try:
        if isinstance(publisher, ShopifyPublisher):
            url_by_local_path = publisher.upload_assets(manifest_with_bytes)
            body_html = rewrite_asset_srcs(shopify_body_html, url_by_local_path)
        else:
            publisher.upload_assets(manifest_with_bytes)
            body_html = shopify_body_html
    except ShopifyCredentialsMissing as e:
        print(str(e), file=sys.stderr)
        return 1

    page_json = json.loads((cartridge_dir / "page.json").read_text())
    page_payload = {
        "title": page_json.get("headline") or f"{tenant.display_name} — {args.page}",
        "body_html": body_html,
        "storefront_host": tenant.get("site_host"),
    }

    try:
        result = publisher.publish(page_payload, unpublished=not args.live)
    except ShopifyCredentialsMissing as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Published {args.page} for {run_dir}: {json.dumps(result)}")

    runstate.mark_published(
        run_dir, page=args.page, by="operator",
        note=f"url={result.get('url')} live={bool(args.live)}",
    )
    notify.notify_published(
        tenant, run_id=run_dir.name, page=args.page,
        url=result.get("url") or result.get("export_dir"),
    )

    if args.live and result.get("url") and isinstance(publisher, ShopifyPublisher):
        hits, pulls = publisher.verify_cache(result["url"], marker=run_dir.name)
        print(f"Cache verification: {hits}/{pulls} pulls returned the new body.")

    return 0


def cmd_digest_needs_review(args):
    """`harness digest needs-review --tenant <t> [--days 3]`: every run in
    needs_review whose history shows it entered that state more than
    `--days` days ago -- the weekly backlog digest (crons/needs-review-digest,
    workflows/weekly-digest.yaml)."""
    tenant = tenant_mod.load_tenant(args.tenant)
    rows = runstate.needs_review_runs(tenant, older_than_days=args.days)
    if not rows:
        print(f"No runs in needs_review older than {args.days} day(s) for {tenant.name}.")
        return 0
    print(f"{len(rows)} run(s) in needs_review older than {args.days} day(s) for {tenant.name}:")
    for run_dir, data, entered_dt in rows:
        pending_pages = [p for p, s in data["pages"].items() if s == "needs_review"]
        print(f"  - {run_dir.name}: pending={','.join(pending_pages)} since {entered_dt.isoformat()}")
    return 0


# ---------------------------------------------------------------------------
# harness claims add / list
# ---------------------------------------------------------------------------

def cmd_claims_add(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    verified_path = tenant.claims_dir / "verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else []

    base_id = re.sub(r"[^a-z0-9]+", "-", args.text.lower()).strip("-")[:40] or "claim"
    existing_ids = {c["id"] for c in verified}
    cid, n = base_id, 2
    while cid in existing_ids:
        cid = f"{base_id}-{n}"
        n += 1

    entry = {
        "id": cid,
        "text": args.text,
        "category": args.category,
        "source": args.source,
        "approved_by": args.approved_by or (tenant.author("contributor") or {}).get("name") or "operator",
        "date": datetime.date.today().isoformat(),
    }
    verified.append(entry)
    verified_path.write_text(json.dumps(verified, indent=2) + "\n")
    print(f"Added claim {cid!r} to {verified_path}")
    return 0


def cmd_claims_list(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    verified_path = tenant.claims_dir / "verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else []
    for c in verified:
        print(f"{c['id']:32s} [{c['category']:10s}] {c['text']}  ({c['source']})")
    return 0


# ---------------------------------------------------------------------------
# harness score -- see evals/rubric.md for what each axis means.
# ---------------------------------------------------------------------------

def cmd_score(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    scores_path = tenant.evals_path
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "tenant": tenant.name,
        "run_dir": args.run_dir,
        "angle": args.angle,
        "brand": args.brand,
        "claims": args.claims,
        "publish": args.publish,
        "by": args.by or (tenant.author("contributor") or {}).get("name") or "operator",
        "note": args.note or "",
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(scores_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Recorded score for {args.run_dir} -> {scores_path}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def _add_tenant_flag(parser):
    parser.add_argument(
        "--tenant",
        help="which company this command is for; default: HARNESS_TENANT, else tenants/default.txt",
    )


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which is the code this harness
    reserves for a claims-gate STOP -- so `harness run --carrtidges x` and a
    run that legitimately refused to write a page were indistinguishable to
    any script checking $?. A usage error is exits.USAGE here.

    add_subparsers() builds every subparser from the calling parser's own
    class, so `harness run` with a missing argument gets this too."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(exits.USAGE, f"{self.prog}: error: {message}\n")


def build_parser():
    parser = _Parser(
        prog="harness",
        epilog=exits.HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="ingest -> ground -> gate -> write -> render")
    p_run.add_argument("input")
    p_run.add_argument("--cartridges", help="comma-separated cartridge names; default: 3 random from the tenant's pool")
    p_run.add_argument("--seed", type=int)
    p_run.add_argument("--product", help="product slug or name; default: inferred from the ad, else the tenant's default product")
    p_run.add_argument("--ffmpeg-bin", default=FFMPEG_BIN)
    p_run.add_argument("--whisper-bin", default=WHISPER_BIN)
    p_run.add_argument("--whisper-model", default=WHISPER_MODEL)
    p_run.add_argument(
        "--batch", action="store_true",
        help="submit the three cartridges' initial writes via the Message Batches API (50%% off) before repairs",
    )
    _add_tenant_flag(p_run)
    p_run.set_defaults(func=cmd_run)

    p_ingest = sub.add_parser("ingest", help="ad -> ad_brief.json only, for debugging")
    p_ingest.add_argument("input")
    p_ingest.add_argument("--ffmpeg-bin", default=FFMPEG_BIN)
    p_ingest.add_argument("--whisper-bin", default=WHISPER_BIN)
    p_ingest.add_argument("--whisper-model", default=WHISPER_MODEL)
    _add_tenant_flag(p_ingest)
    p_ingest.set_defaults(func=cmd_ingest)

    p_review = sub.add_parser("review", help="write a self-contained review.html per cartridge (images inlined)")
    p_review.add_argument("run_dir")
    _add_tenant_flag(p_review)
    p_review.set_defaults(func=cmd_review)

    p_shopify_body = sub.add_parser("shopify-body", help="write shopify-body.html + shopify-body.assets.json for one cartridge's output")
    p_shopify_body.add_argument("cartridge_dir", help="<run-dir>/<cartridge>, e.g. tenants/<t>/out/<run-id>/listicle")
    _add_tenant_flag(p_shopify_body)
    p_shopify_body.set_defaults(func=cmd_shopify_body)

    p_approve = sub.add_parser("approve", help="approve a run's page(s) for publish")
    p_approve.add_argument("run_dir")
    p_approve.add_argument("--by", required=True, help="reviewer email; must match a tenant.yaml reviewers entry")
    p_approve.add_argument("--pages", help="comma-separated cartridge names; default: every page in the run")
    p_approve.add_argument("--note")
    _add_tenant_flag(p_approve)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a run (every page)")
    p_reject.add_argument("run_dir")
    p_reject.add_argument("--by", required=True, help="reviewer email; must match a tenant.yaml reviewers entry")
    p_reject.add_argument("--note", required=True)
    _add_tenant_flag(p_reject)
    p_reject.set_defaults(func=cmd_reject)

    p_packet = sub.add_parser("packet", help="stamp a run's packet.json (the publish gate)")
    p_packet.add_argument("run_dir")
    p_packet.add_argument("--stamp", required=True, choices=["ship", "redo", "kill"])
    p_packet.add_argument("--by", required=True)
    p_packet.add_argument("--note")
    _add_tenant_flag(p_packet)
    p_packet.set_defaults(func=cmd_packet)

    p_publish = sub.add_parser("publish", help="publish one cartridge's page via the tenant's publisher adapter")
    p_publish.add_argument("run_dir")
    p_publish.add_argument("--page", required=True, help="cartridge name, e.g. article")
    p_publish.add_argument("--live", action="store_true", help="publish live (default: unpublished draft)")
    p_publish.add_argument("--dry-run", action="store_true", help="validate credentials and body only; creates nothing")
    _add_tenant_flag(p_publish)
    p_publish.set_defaults(func=cmd_publish)

    p_digest = sub.add_parser("digest", help="reviewer-backlog and score digests")
    digest_sub = p_digest.add_subparsers(dest="digest_command", required=True)
    p_digest_nr = digest_sub.add_parser("needs-review", help="runs in needs_review older than N days")
    p_digest_nr.add_argument("--days", type=int, default=3)
    _add_tenant_flag(p_digest_nr)
    p_digest_nr.set_defaults(func=cmd_digest_needs_review)

    p_claims = sub.add_parser("claims", help="manage a tenant's claims/verified.json")
    claims_sub = p_claims.add_subparsers(dest="claims_command", required=True)

    p_claims_add = claims_sub.add_parser("add")
    p_claims_add.add_argument("text")
    p_claims_add.add_argument("--category", required=True, choices=["spec", "price", "comparison", "health", "trust"])
    p_claims_add.add_argument("--source", required=True)
    p_claims_add.add_argument("--approved-by")
    _add_tenant_flag(p_claims_add)
    p_claims_add.set_defaults(func=cmd_claims_add)

    p_claims_list = claims_sub.add_parser("list")
    _add_tenant_flag(p_claims_list)
    p_claims_list.set_defaults(func=cmd_claims_list)

    p_score = sub.add_parser("score", help="record a human score for a run (see evals/rubric.md)")
    p_score.add_argument("run_dir")
    p_score.add_argument("--angle", type=int, required=True)
    p_score.add_argument("--brand", type=int, required=True)
    p_score.add_argument("--claims", type=int, required=True)
    p_score.add_argument("--publish", type=int, required=True)
    p_score.add_argument("--by")
    p_score.add_argument("--note")
    _add_tenant_flag(p_score)
    p_score.set_defaults(func=cmd_score)

    p_tenant = sub.add_parser("tenant", help="create and inspect tenants")
    tenant_sub = p_tenant.add_subparsers(dest="tenant_command", required=True)
    p_tenant_init = tenant_sub.add_parser("init", help="copy tenants/_template to tenants/<name>")
    p_tenant_init.add_argument("name")
    p_tenant_init.set_defaults(func=cmd_tenant_init)
    p_tenant_list = tenant_sub.add_parser("list", help="every tenant and whether it is configured")
    p_tenant_list.set_defaults(func=cmd_tenant_list)

    p_workflow = sub.add_parser("workflow", help="run a named pipeline from workflows/")
    workflow_sub = p_workflow.add_subparsers(dest="workflow_command", required=True)
    p_workflow_run = workflow_sub.add_parser("run", help="run one workflow's stages, in its own order")
    p_workflow_run.add_argument("name")
    p_workflow_run.add_argument("--input", required=True)
    p_workflow_run.add_argument("--cartridges")
    p_workflow_run.add_argument("--seed", type=int)
    p_workflow_run.add_argument("--product")
    p_workflow_run.add_argument("--ffmpeg-bin", default=FFMPEG_BIN)
    p_workflow_run.add_argument("--whisper-bin", default=WHISPER_BIN)
    p_workflow_run.add_argument("--whisper-model", default=WHISPER_MODEL)
    p_workflow_run.add_argument("--batch", action="store_true")
    _add_tenant_flag(p_workflow_run)
    p_workflow_run.set_defaults(func=cmd_workflow_run)
    p_workflow_list = workflow_sub.add_parser("list", help="every workflow in workflows/")
    p_workflow_list.set_defaults(func=cmd_workflow_list)

    return parser


def main(argv=None):
    """One place turns an expected failure into a message and an exit code.

    Everything raised from harness/errors.py's hierarchy is a failure with an
    operator action behind it -- an unconfigured tenant, an unknown workflow or
    --product, a writer that never returned valid JSON, a batch that timed out,
    a publish the storefront rejected. Each prints one line and returns its own
    exit_code.

    Anything else still raises. A traceback from an unexpected exception is
    deliberate: that is a bug in this harness, and dressing it up as a friendly
    message would only make it harder to report."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except HarnessError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code


def main_adv_alias(argv=None):
    """The old `adv` entry point, kept for one release. Same CLI, one warning."""
    print("adv is deprecated and will be removed; use `harness` instead.", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
