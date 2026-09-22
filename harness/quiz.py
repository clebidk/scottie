"""Cycle 57: the quiz cartridge -- a self-assessment page that recommends one
of the tenant's own active models.

The recommendation is DATA, never prose. A tenant keeps its scoring rubric
in tenants/<t>/quiz/rubric.yaml: 5-7 questions, each with 2-4 options, and
each option scores one or more model slugs (the product-name slug the claim
ids are namespaced under). The page's one inline script adds up the scores
of the chosen options and reveals the winning model's card; this module
holds the same rule in Python (pick_model) so the gate can prove, for EVERY
combination of answers, that some active model wins and that every active
model can win (rubric_reachability).

Who owns what:

  - the tenant owns the rubric (questions, option labels, scores, where the
    interstitials go, the tiebreak order);
  - the writer owns the words: H1, dek, a rephrased prompt per question, the
    interstitial lines and the FAQ. It echoes the rubric's option labels
    verbatim and in order -- a mismatch is fixed deterministically
    (apply_fix), never by a repair call;
  - the renderer owns every result card (name, verified price, capacity,
    image, CTA), the "why this matches you" list (built by the script from
    the labels the reader chose), the trust line, the financing, HSA and
    warranty lines. None of them has a page.json field, so none can be
    invented; find_renderer_owned_violations rejects a page that adds one.

Tenant-neutral: nothing here names a company or a model. Every stable gate
key starts with "quiz:" so the repair loop dedupes the same violation
across attempts (harness/repair.py's failures_seen_by_key).
"""
import itertools
import re
from pathlib import Path

import yaml

from . import listicle
from . import tenant as tenant_mod
from . import vocab
from .claims import _trigger_reason
from .textutil import DOLLAR_AMOUNT_RE, NON_PROSE_KEYS, walk_page

RUBRIC_RELPATH = Path("quiz") / "rubric.yaml"

QUESTION_COUNT_RANGE = (5, 7)
OPTION_COUNT_RANGE = (2, 4)
FAQ_COUNT_RANGE = (3, 5)
# The product of every question's option count -- an upper bound on the
# combinations rubric_reachability enumerates (4 ** 7 = 16384 at most).
MAX_COMBINATIONS = OPTION_COUNT_RANGE[1] ** QUESTION_COUNT_RANGE[1]

# Cycle 61: sentence case, and no "Take the 60-Second Quiz" tail -- the
# page's own note under the start button says how long the quiz is. The
# fixed words are matched case-sensitively, so a Title Case headline fails
# the formula; find_headline_violations adds a case check on <category>.
HEADLINE_FORMULA = "Which <category> is right for <audience>?"
_HEADLINE_RE = re.compile(
    r"^\s*Which\s+(?P<category>.+?)\s+is\s+right\s+for\s+(?P<audience>.+?)\?\s*$"
)
# The cycle-57 formula. Pages written to it before cycle 61 still render;
# display_headline shows them in the current form without a new writer call.
_LEGACY_HEADLINE_RE = re.compile(
    r"^\s*Which\s+(?P<category>.+?)\s+Is\s+Right\s+for\s+(?P<audience>.+?)\?\s+"
    r"Take\s+the\s+60-Second\s+Quiz\.?\s*$",
    re.IGNORECASE,
)

# Cycle 61 length limits (owner review: the page read as an AI-written form).
DEK_MAX_WORDS = 20
INTERSTITIAL_MAX_WORDS = 25
# An option tile's text. Option labels are tenant data (rubric.yaml) the
# writer echoes verbatim, so a longer label carries a short `display` text.
OPTION_DISPLAY_MAX_WORDS = 6

# The start link above the fold, and the retake/continue controls. Fixed
# interface text, not copy -- the writer never writes them.
START_TEXT = "Start the quiz"

# page.json keys for what the RENDERER builds from facts_pack.
RENDERER_OWNED_KEYS = (
    "models", "model_cards", "cards", "result", "results", "scores", "tiebreak",
    "why", "why_this_matches_you", "trust_line", "hsa_line", "financing_line",
    "warranty_line", "price", "prices",
)

_DISCOUNT_RE = re.compile(
    r"(\bdiscount\w*|\bcoupon\w*|\bpromo\s+code|\d+\s*%\s*off\b|\bon\s+sale\b|\bsale\s+price\b)",
    re.IGNORECASE,
)

# Script checks (post-render): the page's quiz script is inline and talks to
# nothing -- no src=, no network call of any kind.
_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_NETWORK_TOKENS = ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "import(",
                   "http://", "https://")
_RENDERED_MODEL_RE = re.compile(r'data-qz-model="([^"]+)"')
_RENDERED_QUESTION_RE = re.compile(r'data-qz-question="([^"]+)"')


def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


# ---------------------------------------------------------------------------
# Rubric: load, validate, score
# ---------------------------------------------------------------------------

def rubric_path(tenant_root):
    return Path(tenant_root) / RUBRIC_RELPATH


def load_rubric(path):
    """(rubric_dict_or_None, error_or_None). A missing file or a YAML error
    is returned, not raised: the pipeline turns it into a quiz:rubric STOP
    before any writer call is spent."""
    path = Path(path)
    if not path.exists():
        return None, f"no quiz rubric at {path}"
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return None, f"quiz rubric does not parse: {e}"
    if not isinstance(data, dict):
        return None, "quiz rubric is not a mapping"
    return data, None


def rubric_questions(rubric):
    questions = (rubric or {}).get("questions")
    return questions if isinstance(questions, list) else []


def option_display(option):
    """The text an option's tile shows: the rubric's optional `display`
    (a short form of the label), else the label itself. Scores, the gate's
    verbatim echo check and apply_fix all keep using `label`."""
    if not isinstance(option, dict):
        return ""
    return str(option.get("display") or option.get("label") or "").strip()


def rubric_interstitials(rubric):
    entries = (rubric or {}).get("interstitials")
    return entries if isinstance(entries, list) else []


def option_scores(option):
    scores = option.get("scores") if isinstance(option, dict) else None
    return scores if isinstance(scores, dict) else {}


def pick_model(chosen_options, tiebreak, allowed=None):
    """The winning model slug for one set of chosen options, or None when no
    model scores above zero. Highest total wins; a tie goes to the model
    earliest in `tiebreak`, then to the alphabetically first slug. The
    page's inline script (cartridges/quiz/template.html) implements exactly
    this rule -- change one, change both."""
    totals = {}
    for option in chosen_options:
        for slug, points in option_scores(option).items():
            if allowed is not None and slug not in allowed:
                continue
            totals[slug] = totals.get(slug, 0) + int(points)
    best = max(totals.values(), default=0)
    if best <= 0:
        return None
    rank = {slug: i for i, slug in enumerate(tiebreak or [])}
    tied = [slug for slug, total in totals.items() if total == best]
    return min(tied, key=lambda slug: (rank.get(slug, len(rank)), slug))


def rubric_reachability(rubric, allowed=None):
    """({slug: number of answer combinations it wins}, combinations with no
    result, total combinations). Enumerates every combination -- bounded by
    MAX_COMBINATIONS, which validate_rubric's count limits guarantee."""
    questions = rubric_questions(rubric)
    tiebreak = (rubric or {}).get("tiebreak") or []
    wins, empty, total = {}, 0, 0
    option_lists = [q.get("options") or [] for q in questions if isinstance(q, dict)]
    for combo in itertools.product(*option_lists):
        total += 1
        winner = pick_model(combo, tiebreak, allowed)
        if winner is None:
            empty += 1
        else:
            wins[winner] = wins.get(winner, 0) + 1
    return wins, empty, total


def validate_rubric(rubric, active_slugs, *, load_error=None):
    """Every structural rule a rubric must meet before a run may write a
    quiz page. `active_slugs` are the tenant's active models that carry a
    verified price (ground.py's quiz models) -- a slug outside it is a
    retired or unknown model and fails. Returns problem dicts ([] = pass)."""
    if load_error or rubric is None:
        return [_problem("$.rubric", "quiz:rubric:load", load_error or "no quiz rubric loaded")]
    active = set(active_slugs or ())
    problems = []
    questions = rubric.get("questions")
    if not isinstance(questions, list):
        return [_problem("$.rubric.questions", "quiz:rubric:questions", "rubric has no questions list")]
    lo, hi = QUESTION_COUNT_RANGE
    if not lo <= len(questions) <= hi:
        problems.append(_problem(
            "$.rubric.questions", "quiz:rubric:question_count",
            f"rubric has {len(questions)} questions; a quiz needs {lo}-{hi}",
        ))
    seen_ids = set()
    olo, ohi = OPTION_COUNT_RANGE
    for i, q in enumerate(questions):
        path = f"$.rubric.questions[{i}]"
        if not isinstance(q, dict) or not q.get("id") or not q.get("prompt"):
            problems.append(_problem(path, f"quiz:rubric:question_shape:{i}",
                                     "every question needs an id and a prompt"))
            continue
        qid = str(q["id"])
        if qid in seen_ids:
            problems.append(_problem(path, f"quiz:rubric:duplicate_id:{qid}", f"question id {qid!r} repeats"))
        seen_ids.add(qid)
        options = q.get("options")
        options = options if isinstance(options, list) else []
        if not olo <= len(options) <= ohi:
            problems.append(_problem(
                f"{path}.options", f"quiz:rubric:option_count:{qid}",
                f"question {qid!r} has {len(options)} options; each needs {olo}-{ohi}",
            ))
        for j, option in enumerate(options):
            opath = f"{path}.options[{j}]"
            if not isinstance(option, dict) or not str(option.get("label") or "").strip():
                problems.append(_problem(opath, f"quiz:rubric:option_shape:{qid}:{j}", "option needs a label"))
                continue
            display = option.get("display")
            if display is not None and len(str(display).split()) > OPTION_DISPLAY_MAX_WORDS:
                problems.append(_problem(
                    f"{opath}.display", f"quiz:rubric:option_display:{qid}:{j}",
                    f"option display text {display!r} is over {OPTION_DISPLAY_MAX_WORDS} words",
                ))
            scores = option_scores(option)
            unknown = sorted(s for s in scores if s not in active)
            if unknown:
                problems.append(_problem(
                    f"{opath}.scores", f"quiz:rubric:unknown_model:{qid}:{j}",
                    f"option {option['label']!r} scores {unknown}, which are not active models with a "
                    f"verified price (active: {sorted(active)})",
                ))
            bad = sorted(s for s, v in scores.items() if isinstance(v, bool) or not isinstance(v, int))
            if bad:
                problems.append(_problem(
                    f"{opath}.scores", f"quiz:rubric:score_type:{qid}:{j}",
                    f"option {option['label']!r} has non-integer scores for {bad}",
                ))
            if not any(isinstance(v, int) and not isinstance(v, bool) and v > 0 and s in active
                       for s, v in scores.items()):
                problems.append(_problem(
                    f"{opath}.scores", f"quiz:rubric:option_scores:{qid}:{j}",
                    f"option {option['label']!r} scores no active model above zero",
                ))
    for k, entry in enumerate(rubric_interstitials(rubric)):
        after = entry.get("after") if isinstance(entry, dict) else None
        if after not in seen_ids:
            problems.append(_problem(
                f"$.rubric.interstitials[{k}]", f"quiz:rubric:interstitial:{k}",
                f"interstitial {k} comes after {after!r}, which is not a question id",
            ))
    tiebreak = rubric.get("tiebreak") or []
    if not isinstance(tiebreak, list) or any(s not in active for s in tiebreak):
        problems.append(_problem(
            "$.rubric.tiebreak", "quiz:rubric:tiebreak",
            f"tiebreak must list only active model slugs; got {tiebreak!r}",
        ))
    if problems:
        return problems  # reachability over a malformed rubric means nothing

    wins, empty, total = rubric_reachability(rubric, active)
    for slug in sorted(active - set(wins)):
        problems.append(_problem(
            "$.rubric", f"quiz:rubric:unreachable:{slug}",
            f"no combination of answers recommends {slug!r} ({total} combinations checked)",
        ))
    if empty:
        problems.append(_problem(
            "$.rubric", "quiz:rubric:no_result",
            f"{empty} of {total} answer combinations score no model above zero",
        ))
    return problems


# ---------------------------------------------------------------------------
# Writer guidance
# ---------------------------------------------------------------------------

def writer_lines(facts_pack):
    """Hard-constraint lines write.py adds for this cartridge. They quote the
    rubric this run carries, so the prompt states exactly what
    find_quiz_violations measures."""
    rubric = ((facts_pack or {}).get("quiz") or {}).get("rubric") or {}
    questions = rubric_questions(rubric)
    lo, hi = FAQ_COUNT_RANGE
    lines = [
        f'The headline follows this formula exactly, in sentence case: "{HEADLINE_FORMULA}". '
        "<category> is the product category in lower case (never the company or a model name); "
        "<audience> names people by their situation or goal, 2-5 words, derived from the ad brief "
        "(never a bare \"people\", \"buyers\", \"shoppers\", \"customers\" or \"everyone\"). "
        "Nothing after the question mark: the page itself says how long the quiz takes.",
        f"The dek is one plain sentence of at most {DEK_MAX_WORDS} words on what the quiz does for "
        "the reader. No \"whether you're\" opener and no list of three.",
        f'Write "questions" as exactly {len(questions)} entries, in this order, each with the same '
        '"id", a "prompt" you rephrase from the rubric prompt (one short question ending in "?"), '
        'and "options": the rubric labels copied verbatim, same count, same order:',
    ]
    for q in questions:
        labels = " | ".join(str(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict))
        lines.append(f'  - id "{q.get("id")}": rubric prompt "{q.get("prompt")}"; options: {labels}')
    inter = rubric_interstitials(rubric)
    if inter:
        lines.append(
            f'Write "interstitials" as exactly {len(inter)} entries, in this order, each '
            '{"after": <question id>, "line": <1-2 short sentences>, "claim_ids": [...]}. A line '
            f"is at most {INTERSTITIAL_MAX_WORDS} words and teaches one verified fact about the topic below. Any line that states a number, a "
            "price, a measurement or a trigger word carries claim_ids; a line with no claim_ids "
            "has no digit in it at all."
        )
        for entry in inter:
            lines.append(f'  - after "{entry.get("after")}": {entry.get("topic")}')
    lines += [
        f"FAQ: {lo}-{hi} questions a buyer asks before choosing a model, each answer 2-4 "
        "sentences; any answer that states a number, a price, a spec or a trigger word carries "
        "claim_ids.",
        "Never write a result card, a model's price, a capacity line, a \"why this matches you\" "
        "list, a trust line, or a financing, HSA or warranty line -- the renderer builds all of "
        "them from verified data.",
        'Set "cta_url" to facts_pack.product.url exactly. The CTA text is the allowed option '
        "for the featured model.",
        "No urgency, no countdown, no discount or sale language, no email request anywhere.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Gate: structural checks over the writer's page.json
# ---------------------------------------------------------------------------

def _page_questions(page):
    questions = page.get("questions")
    return questions if isinstance(questions, list) else []


def _page_interstitials(page):
    entries = page.get("interstitials")
    return entries if isinstance(entries, list) else []


def _norm(text):
    return " ".join(str(text or "").split()).casefold()


def _is_title_word(word):
    """A capitalised word that is not an all-caps token (a brand or an
    acronym such as "NYC" is not title case)."""
    letters = re.sub(r"[^A-Za-z]", "", word)
    return bool(letters) and letters[0].isupper() and not letters.isupper()


def _sentence_case(text):
    """Lower-case every title-case word; all-caps tokens stay as written."""
    return " ".join(w.lower() if _is_title_word(w) else w for w in str(text).split())


def display_headline(headline):
    """The H1 as the page shows it. A headline written to the cycle-57
    formula ("Which <Category> Is Right for <Audience>? Take the 60-Second
    Quiz") is shown in the current sentence-case form, without the tail, so
    `harness rerender` brings an existing page up to date with no writer
    call. Any other headline is returned unchanged -- current pages are
    gated to the new form before they are ever rendered."""
    text = str(headline or "")
    legacy = _LEGACY_HEADLINE_RE.match(text)
    if not legacy:
        return text
    return (f"Which {_sentence_case(legacy.group('category'))} is right for "
            f"{_sentence_case(legacy.group('audience'))}?")


def find_headline_violations(page, tenant_name=None, product_names=None):
    headline = page.get("headline") or ""
    match = _HEADLINE_RE.match(headline)
    if not match:
        return [_problem(
            "$.headline", "quiz:headline_formula",
            f'headline {headline!r} does not follow the formula "{HEADLINE_FORMULA}" (sentence '
            "case, nothing after the question mark)",
        )]
    problems = []
    category = match.group("category")
    if any(_is_title_word(w) for w in category.split()):
        problems.append(_problem(
            "$.headline", "quiz:headline_case",
            f"<category> {category!r} is in title case; write the headline in sentence case "
            "(\"Which home infrared sauna is right for ...?\")",
        ))
    for name in [n for n in [tenant_name, *(product_names or [])] if n]:
        if re.search(r"\b" + re.escape(name) + r"\b", category, re.IGNORECASE):
            problems.append(_problem(
                "$.headline", "quiz:headline_slots",
                f"<category> contains {name!r}; it names the product category only, never the "
                "company or a model",
            ))
            break
    audience = match.group("audience").strip()
    if audience.strip(" .,!?").lower() in listicle.GENERIC_AUDIENCE_WORDS or not audience:
        problems.append(_problem(
            "$.headline", "quiz:headline_slots",
            f"<audience> is {audience!r}; name people by their situation or goal instead",
        ))
    return problems


def find_question_violations(page, rubric):
    rq = rubric_questions(rubric)
    pq = _page_questions(page)
    problems = []
    if len(pq) != len(rq):
        problems.append(_problem(
            "$.questions", "quiz:question_count",
            f"page has {len(pq)} questions; the rubric has {len(rq)} -- write exactly one per "
            "rubric question, in rubric order",
        ))
        return problems
    for i, (wq, q) in enumerate(zip(pq, rq, strict=True)):
        path = f"$.questions[{i}]"
        if not isinstance(wq, dict):
            problems.append(_problem(path, f"quiz:question_shape:{i}", "question is not an object"))
            continue
        if wq.get("id") != q.get("id"):
            problems.append(_problem(
                f"{path}.id", f"quiz:question_id:{i}",
                f"question {i} has id {wq.get('id')!r}; the rubric's question {i} is {q.get('id')!r}",
            ))
        prompt = str(wq.get("prompt") or "").strip()
        if not prompt.endswith("?"):
            problems.append(_problem(
                f"{path}.prompt", f"quiz:question_prompt:{i}",
                "every prompt is one short question ending in \"?\"", text=prompt,
            ))
        labels = [str(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict)]
        written = wq.get("options")
        written = written if isinstance(written, list) else []
        written_labels = [o.get("label") if isinstance(o, dict) else o for o in written]
        if [_norm(x) for x in written_labels] != [_norm(x) for x in labels]:
            problems.append(_problem(
                f"{path}.options", f"quiz:options:{q.get('id')}",
                f"options must be the rubric labels verbatim, same count and order: {labels}; "
                f"got {written_labels}",
            ))
    return problems


def find_interstitial_violations(page, rubric):
    expected = rubric_interstitials(rubric)
    written = _page_interstitials(page)
    problems = []
    if len(written) != len(expected):
        problems.append(_problem(
            "$.interstitials", "quiz:interstitial_count",
            f"page has {len(written)} interstitials; the rubric places {len(expected)}",
        ))
    for i, entry in enumerate(written):
        path = f"$.interstitials[{i}]"
        if not isinstance(entry, dict):
            continue
        if i < len(expected) and entry.get("after") != expected[i].get("after"):
            problems.append(_problem(
                f"{path}.after", f"quiz:interstitial_after:{i}",
                f"interstitial {i} must come after {expected[i].get('after')!r}",
            ))
        line = str(entry.get("line") or "")
        if not line.strip():
            problems.append(_problem(f"{path}.line", f"quiz:interstitial_line:{i}", "interstitial has no line"))
            continue
        if len(line.split()) > INTERSTITIAL_MAX_WORDS:
            problems.append(_problem(
                f"{path}.line", f"quiz:interstitial_length:{i}",
                f"interstitial is {len(line.split())} words; keep it to {INTERSTITIAL_MAX_WORDS} or fewer",
                text=line,
            ))
        reason = _trigger_reason(line)
        if reason and not entry.get("claim_ids"):
            problems.append(_problem(
                f"{path}.line", f"quiz:interstitial_claims:{i}",
                f"interstitial line needs a claim_id ({reason}) -- cite a verified claim_id, or "
                "rewrite it with no digit or trigger word",
                text=line,
            ))
    return problems


def find_faq_violations(page):
    faq = page.get("faq")
    questions = faq.get("questions") if isinstance(faq, dict) else faq
    questions = questions if isinstance(questions, list) else []
    lo, hi = FAQ_COUNT_RANGE
    problems = []
    if not lo <= len(questions) <= hi:
        problems.append(_problem(
            "$.faq.questions", "quiz:faq_count", f"FAQ has {len(questions)} questions; it needs {lo}-{hi}",
        ))
    for i, entry in enumerate(questions):
        if not isinstance(entry, dict):
            continue
        answer = entry.get("answer") or ""
        reason = _trigger_reason(answer)
        if reason and not entry.get("claim_ids"):
            problems.append(_problem(
                f"$.faq.questions[{i}].answer", f"quiz:faq_claims:{i}",
                f"FAQ answer needs at least one claim_id ({reason}) -- cite a verified claim_id, "
                "or rewrite the answer without it",
                text=answer,
            ))
    return problems


def find_offer_violations(page):
    """No urgency (listicle's shared phrase list) and no discount/sale."""
    problems = [
        dict(p, key=p["key"].replace("listicle:", "quiz:", 1))
        for p in listicle.find_urgency_violations(page)
    ]
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if isinstance(node, str) and _DISCOUNT_RE.search(node):
            problems.append(_problem(
                path, "quiz:discount",
                "discount or sale language found; this page never offers one", text=node,
            ))
    return problems


def find_renderer_owned_violations(page):
    problems = []
    for key in RENDERER_OWNED_KEYS:
        if key in page:
            problems.append(_problem(
                f"$.{key}", f"quiz:renderer_owned:{key}",
                f"{key!r} is built by the renderer from verified data, never written here -- remove it",
            ))
    return problems


def find_quiz_violations(page, facts_pack, tenant_name=None, product_names=None):
    """Every quiz-specific writer-owned check, combined -- wired into
    harness/repair.py's check_page_gates for this cartridge only."""
    if not isinstance(page, dict):
        return []
    quiz = (facts_pack or {}).get("quiz") or {}
    rubric = quiz.get("rubric") or {}
    problems = []
    problems += find_headline_violations(page, tenant_name, product_names)
    dek_words = len(str(page.get("dek") or "").split())
    if dek_words > DEK_MAX_WORDS:
        problems.append(_problem(
            "$.dek", "quiz:dek_length",
            f"dek is {dek_words} words; keep it to one sentence of {DEK_MAX_WORDS} words or fewer",
            text=page.get("dek"),
        ))
    problems += find_question_violations(page, rubric)
    problems += find_interstitial_violations(page, rubric)
    problems += find_faq_violations(page)
    problems += find_offer_violations(page)
    problems += find_renderer_owned_violations(page)
    if not ((page.get("hero") or {}).get("asset_id")):
        problems.append(_problem("$.hero.asset_id", "quiz:hero",
                                 "page has no hero.asset_id; the header carries one hero image"))
    product_url = ((facts_pack or {}).get("product") or {}).get("url")
    if product_url and page.get("cta_url") != product_url:
        problems.append(_problem(
            "$.cta_url", "quiz:cta_url",
            f"cta_url must be the featured model's own url {product_url!r}",
        ))
    return problems


# Deterministic fixes: the writer echoes rubric data here, so a mismatch has
# exactly one right answer and never needs a repair call.
_FIXABLE_KEY_PREFIXES = ("quiz:options:", "quiz:question_id:", "quiz:interstitial_after:", "quiz:cta_url")


def is_fixable(item):
    return str(item.get("key") or "").startswith(_FIXABLE_KEY_PREFIXES)


def apply_fix(page, facts_pack, item):
    """Apply the one safe fix for `item` (a find_quiz_violations problem
    whose key is_fixable). Returns True when page changed."""
    key = str(item.get("key") or "")
    quiz = (facts_pack or {}).get("quiz") or {}
    rubric_q = rubric_questions(quiz.get("rubric"))
    page_q = _page_questions(page)
    if key.startswith("quiz:options:"):
        qid = key.split(":", 2)[2]
        for wq, q in zip(page_q, rubric_q, strict=False):
            if q.get("id") == qid and isinstance(wq, dict):
                labels = [str(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict)]
                if wq.get("options") != labels:
                    wq["options"] = labels
                    return True
        return False
    if key.startswith("quiz:question_id:"):
        i = int(key.rsplit(":", 1)[1])
        if len(page_q) == len(rubric_q) and isinstance(page_q[i], dict):
            page_q[i]["id"] = rubric_q[i].get("id")
            return True
        return False
    if key.startswith("quiz:interstitial_after:"):
        i = int(key.rsplit(":", 1)[1])
        expected = rubric_interstitials(quiz.get("rubric"))
        written = _page_interstitials(page)
        if i < len(expected) and i < len(written) and isinstance(written[i], dict):
            written[i]["after"] = expected[i].get("after")
            return True
        return False
    if key == "quiz:cta_url":
        url = ((facts_pack or {}).get("product") or {}).get("url")
        if url and page.get("cta_url") != url:
            page["cta_url"] = url
            return True
    return False


# ---------------------------------------------------------------------------
# Renderer-owned context
# ---------------------------------------------------------------------------

def _scores_attr(option, allowed):
    return " ".join(
        f"{slug}:{int(points)}" for slug, points in sorted(option_scores(option).items())
        if slug in allowed and int(points) != 0
    )


# Option tiles sit two to a row on desktop only when every label is short.
_GRID_MAX_CHARS = 26


def render_context(facts_pack, page, *, cta_text_for, warranty_claim_id=None, tenant=None,
                   all_models_url=None):
    """Everything cartridges/quiz/template.html renders that the writer did
    not write. `cta_text_for(model_name, short_name)` resolves a card's CTA
    text from the cartridge's allowlist (render.py owns the schema and the
    tenant). Cycle 64: a card's title is the model's full name and its
    descriptor line the capacity/style words (tenant.product_names), and its
    CTA names the model alone. `all_models_url` is the tenant's own
    all-models page for the result's secondary link. Returns a dict;
    "claim_ids" is every claim the renderer-built sections cite, for the
    Sources list."""
    tenant = tenant or tenant_mod.active()
    facts_pack = facts_pack or {}
    page = page or {}
    quiz = facts_pack.get("quiz") or {}
    rubric = quiz.get("rubric") or {}
    models = quiz.get("models") or []
    allowed = {m["slug"] for m in models}
    written = {q.get("id"): q for q in _page_questions(page) if isinstance(q, dict)}
    lines_after = {}
    for entry in _page_interstitials(page):
        if isinstance(entry, dict) and entry.get("line"):
            lines_after.setdefault(entry.get("after"), []).append(entry["line"])

    steps = []
    for q in rubric_questions(rubric):
        qid = q.get("id")
        prompt = str((written.get(qid) or {}).get("prompt") or "").strip() or q.get("prompt")
        options = [
            {"label": option_display(o), "scores": _scores_attr(o, allowed)}
            for o in q.get("options") or [] if isinstance(o, dict)
        ]
        steps.append({
            "kind": "question",
            "id": qid,
            "prompt": prompt,
            # the short noun phrase a result's "why it fits you" row leads
            # with (rubric display field; the row is the answer alone without it)
            "result_label": str(q.get("result_label") or "").strip(),
            "layout": "grid" if all(len(o["label"]) <= _GRID_MAX_CHARS for o in options) else "stack",
            "options": options,
        })
        for line in lines_after.get(qid, []):
            steps.append({"kind": "note", "line": line})

    featured = quiz.get("featured")
    cards, claim_ids = [], set()
    for m in models:
        card = dict(m)
        card["featured"] = m["slug"] == featured
        names = tenant.product_names(m)
        card["title"] = names["full_name"] or m.get("name")
        card["descriptor"] = names["descriptor"] or None
        if card["featured"] and page.get("cta_text"):
            card["cta_text"] = page["cta_text"]
        else:
            card["cta_text"] = cta_text_for(names["short_name"], names["short_name"])
        cards.append(card)
        claim_ids.update(cid for cid in (m.get("price_claim_id"), m.get("capacity_claim_id")) if cid)

    trust_items = listicle.trust_line_items(facts_pack)
    for item in trust_items:
        claim_ids.update(item.get("claim_ids") or [])
    hsa = listicle.hsa_claim(facts_pack)
    if hsa:
        claim_ids.add(hsa["id"])
    lender = ((facts_pack.get("product") or {}).get("financing") or {}).get("lender")
    warranty = vocab.ALLOWED_WARRANTY_SENTENCE if warranty_claim_id else None
    if warranty:
        claim_ids.add(warranty_claim_id)
    # The verified rating is the FEATURED product's (facts_pack.reviews_summary),
    # so the template shows it on the featured card only, and the trust line
    # under the result drops it rather than say it twice.
    rating = listicle.rating_line(facts_pack)
    if rating:
        claim_ids.update(rating.get("claim_ids") or [])
        trust_items = [i for i in trust_items if i.get("text") != rating["text"]]
    return {
        "headline": display_headline(page.get("headline")),
        "steps": steps,
        "question_count": sum(1 for s in steps if s["kind"] == "question"),
        "rating_line": rating,
        "all_models_url": all_models_url,
        "cards": cards,
        "featured": featured,
        "tiebreak": " ".join(s for s in (rubric.get("tiebreak") or []) if s in allowed),
        "start_text": START_TEXT,
        "trust_items": trust_items,
        "hsa_claim": hsa,
        "financing_sentence": vocab.allowed_financing_sentence(lender),
        "warranty_sentence": warranty,
        "claim_ids": claim_ids,
    }


def price_text_from_claim(claim_text):
    """The first dollar amount in a price claim's own text, as written there
    ("$8,250"), so a card never shows a number its cited claim does not."""
    m = DOLLAR_AMOUNT_RE.search(claim_text or "")
    if not m:
        return None
    amount = float(m.group(1).replace(",", ""))
    return f"${amount:,.0f}"


# ---------------------------------------------------------------------------
# Post-render backstop (a renderer/template bug class -- STOPs, never repairs)
# ---------------------------------------------------------------------------

def find_rendered_quiz_violations(html, context):
    problems = []
    scripts = _SCRIPT_TAG_RE.findall(html)
    quiz_scripts = [body for attrs, body in scripts if "data-qz" in body]
    if len(quiz_scripts) != 1:
        problems.append(_problem("$.html.script", "quiz:script",
                                 f"expected exactly one inline quiz script, found {len(quiz_scripts)}"))
    for attrs, body in scripts:
        if re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
            problems.append(_problem("$.html.script", "quiz:script_external",
                                     "a <script src=...> is on the page; the quiz is self-contained"))
        if 'type="application/ld+json"' in attrs:
            continue
        for token in _NETWORK_TOKENS:
            if token in body:
                problems.append(_problem("$.html.script", "quiz:script_network",
                                         f"script contains {token!r}; the quiz makes no network request"))
    expected = {c["slug"] for c in (context or {}).get("cards") or []}
    rendered = set(_RENDERED_MODEL_RE.findall(html))
    if rendered != expected or not rendered:
        problems.append(_problem("$.html.cards", "quiz:result_cards",
                                 f"result cards {sorted(rendered)} do not match the active models {sorted(expected)}"))
    rendered_q = _RENDERED_QUESTION_RE.findall(html)
    if len(rendered_q) != (context or {}).get("question_count"):
        problems.append(_problem("$.html.questions", "quiz:rendered_questions",
                                 f"{len(rendered_q)} questions rendered; the rubric has "
                                 f"{(context or {}).get('question_count')}"))
    return problems
