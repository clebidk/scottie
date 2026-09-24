"""Cycle 70: the listicle headline template library (owner request 2026-09-24).

cartridges/listicle/headlines.yaml holds 17 "pre-sell listicle" headline
templates plus the five cycle 41 style formulas (ids s-<style>), each with the
styles it fits, its named slots, and the evidence a page must be able to cite
before the template may be used. This module is everything that reads it:

  - load_library / validate_library: the file, checked on load;
  - eligibility / eligible_templates: which templates one run may use -- the
    run's style, the evidence in its facts_pack (a customer count, growth, an
    exclusivity claim or a named endorsement only when a verified claim holds
    one; never invented), then tenant.yaml's
    `cartridges.listicle.headline_templates: {include: [...], exclude: [...]}`;
  - resolve_plan / build_plan: the run's pick (seeded; `weights` is the hook
    the A/B/C results can steer later) and its PLAN -- a plain JSON-able dict
    with the template, the slots the writer fills, the parts the harness fills
    (brand, run year, verified count, named authority, "Safe"/"Better") and
    the claim ids the items must cite;
  - writer_lines: what harness/listicle.writer_style_lines tells the writer;
  - find_headline_violations / fix_headline_number: the gate
    harness/listicle.find_listicle_violations runs for a template plan (the
    s-<style> ids keep the cycle 41 checks in harness/listicle.py).

Tenant-neutral: every name comes from the tenant, its catalog, its vocab.yaml
and the run's facts_pack.
"""
import datetime
import functools
import random
import re

import yaml

from . import listicle
from . import tenant as tenant_mod
from . import vocab
from .errors import HarnessError

LIBRARY_PATH = tenant_mod.REPO_ROOT / "cartridges" / "listicle" / "headlines.yaml"

# Filled by the harness from the tenant, the run date and verified claims.
FIXED_KINDS = ("brand", "year", "count", "count_unit", "authority", "alternative")
# Filled by the writer.
FREE_KINDS = ("audience", "category", "product", "common_solution", "problem", "solution", "desire",
              "usp", "concern", "features", "age", "niche", "choice")
SLOT_KINDS = FIXED_KINDS + FREE_KINDS
# Slots that must read as an everyday, non-medical situation.
NON_MEDICAL_KINDS = ("problem", "solution", "desire", "usp", "concern", "features")
ITEM_GATES = ("fear", "medical")

_PLACEHOLDER_RE = re.compile(r"<([a-z_]+)>")
_SPLIT_RE = re.compile(r"(<[a-z_]+>)")
_N_RE = re.compile(r"(?<![A-Za-z])N(?![A-Za-z])")
_SLOT_KEYS = frozenset({"kind", "max_words", "description", "options"})
_NICHE_STOPWORDS = frozenset({"the", "a", "an", "for", "of", "and", "with", "in", "to", "on"})


class HeadlineLibraryError(HarnessError, ValueError):
    """cartridges/listicle/headlines.yaml is malformed."""


class HeadlineTemplateError(HarnessError, ValueError):
    """A headline template that does not exist, does not fit the run's
    style, or lacks the verified evidence it needs."""


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

def placeholders(pattern):
    return _PLACEHOLDER_RE.findall(pattern or "")


def validate_library(data):
    """Raises HeadlineLibraryError naming the first problem; returns `data`."""
    if not isinstance(data, dict):
        raise HeadlineLibraryError("headline library must be a mapping")
    evidence = data.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise HeadlineLibraryError("headline library `evidence` must be a mapping")
    for kind, spec in evidence.items():
        for pattern in (spec or {}).get("patterns") or []:
            try:
                re.compile(pattern)
            except re.error as e:
                raise HeadlineLibraryError(f"evidence {kind!r}: bad pattern {pattern!r}: {e}") from e
    templates = data.get("templates")
    if not isinstance(templates, list) or not templates:
        raise HeadlineLibraryError("headline library needs a non-empty `templates` list")
    seen, legacy = set(), {}
    for t in templates:
        tid = (t or {}).get("id")
        if not tid:
            raise HeadlineLibraryError(f"template without an id: {t!r}")
        if tid in seen:
            raise HeadlineLibraryError(f"duplicate template id {tid!r}")
        seen.add(tid)
        pattern = t.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise HeadlineLibraryError(f"template {tid}: no pattern")
        styles = t.get("styles")
        if not isinstance(styles, list) or not styles:
            raise HeadlineLibraryError(f"template {tid}: needs a list of styles")
        for style in styles:
            if style not in listicle.STYLES:
                raise HeadlineLibraryError(f"template {tid}: unknown style {style!r}")
        slots = t.get("slots")
        if not isinstance(slots, dict):
            raise HeadlineLibraryError(f"template {tid}: slots must be a mapping")
        named = placeholders(pattern)
        for name in named:
            if name not in slots:
                raise HeadlineLibraryError(f"template {tid}: pattern names undeclared slot <{name}>")
        for name, slot in slots.items():
            if name not in named:
                raise HeadlineLibraryError(f"template {tid}: slot {name!r} is not in the pattern")
            unknown = set(slot or {}) - _SLOT_KEYS
            if unknown:
                raise HeadlineLibraryError(f"template {tid}: slot {name!r} has unknown key(s) {sorted(unknown)}")
            kind = (slot or {}).get("kind")
            if kind not in SLOT_KINDS:
                raise HeadlineLibraryError(f"template {tid}: slot {name!r} has unknown kind {kind!r}")
            if kind == "choice" and not slot.get("options"):
                raise HeadlineLibraryError(f"template {tid}: choice slot {name!r} needs options")
        if len(_N_RE.findall(_PLACEHOLDER_RE.sub(" ", pattern))) != 1:
            raise HeadlineLibraryError(f"template {tid}: the pattern must contain the count N exactly once")
        for kind in t.get("requires") or []:
            if kind not in evidence:
                raise HeadlineLibraryError(f"template {tid}: requires unknown evidence kind {kind!r}")
        for gate in t.get("item_gates") or []:
            if gate not in ITEM_GATES:
                raise HeadlineLibraryError(f"template {tid}: unknown item gate {gate!r}")
        free = {n for n, s in slots.items() if s["kind"] in FREE_KINDS}
        if set((t.get("example") or {})) != free:
            raise HeadlineLibraryError(f"template {tid}: example must fill exactly the slots {sorted(free)}")
        if t.get("legacy_style"):
            if t["legacy_style"] not in listicle.STYLES or styles != [t["legacy_style"]]:
                raise HeadlineLibraryError(f"template {tid}: legacy_style must be its one style")
            legacy[t["legacy_style"]] = tid
    missing = [s for s in listicle.STYLES if s not in legacy]
    if missing:
        raise HeadlineLibraryError(f"headline library has no style formula for {missing}")
    return data


@functools.lru_cache(maxsize=4)
def _load(path_str):
    with open(path_str, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    validate_library(data)
    return {
        "templates": {t["id"]: t for t in data["templates"]},
        "order": [t["id"] for t in data["templates"]],
        "evidence": data.get("evidence") or {},
        "word_lists": data.get("word_lists") or {},
    }


def load_library(path=None, raw=False):
    """The validated library. `raw=True` returns the file's own YAML data
    (unvalidated), for tests that break it on purpose."""
    path = str(path or LIBRARY_PATH)
    if raw:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return _load(path)


def template_ids():
    return list(load_library()["order"])


def template(template_id):
    t = load_library()["templates"].get(template_id)
    if t is None:
        raise HeadlineTemplateError(f"unknown headline template {template_id!r}; choose one of {template_ids()}")
    return t


def legacy_id(style):
    return f"s-{style}"


def is_legacy(plan_or_template):
    return bool((plan_or_template or {}).get("legacy_style"))


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=32)
def _evidence_res(kind):
    spec = load_library()["evidence"][kind]
    return tuple(re.compile(p, re.IGNORECASE) for p in spec.get("patterns") or [])


def _claim_tags(claim):
    tags = claim.get("evidence")
    if isinstance(tags, str):
        return {tags}
    return set(tags or [])


def _claim_count(claim):
    """(count, unit) a customer_count claim states, or None."""
    if isinstance(claim.get("count"), int) and not isinstance(claim.get("count"), bool):
        count, unit = claim["count"], claim.get("unit") or "customers"
    else:
        count = unit = None
        for regex in _evidence_res("customer_count"):
            m = regex.search(claim.get("text") or "")
            if m and "count" in m.groupdict():
                count = int(m.group("count").replace(",", ""))
                unit = m.groupdict().get("unit") or "customers"
                break
        if count is None:
            return None
    minimum = load_library()["evidence"]["customer_count"].get("min_count") or 0
    return (count, unit) if count >= minimum else None


def _claim_authority(claim):
    if claim.get("authority"):
        return str(claim["authority"]).strip()
    for regex in _evidence_res("endorsement"):
        m = regex.search(claim.get("text") or "")
        if m and m.groupdict().get("authority"):
            return m.group("authority").strip()
    return None


def claims_of_kind(kind, claims):
    """The verified claims that are evidence of `kind` (see headlines.yaml
    `evidence`). A count claim must state a count and an endorsement a named
    authority -- a claim that only mentions the topic is not evidence."""
    spec = load_library()["evidence"][kind]
    categories = set(spec.get("categories") or [])
    out = []
    for claim in claims or []:
        if not isinstance(claim, dict) or not claim.get("id"):
            continue
        text = claim.get("text") or ""
        if not (kind in _claim_tags(claim) or claim.get("category") in categories
                or any(r.search(text) for r in _evidence_res(kind))):
            continue
        if kind == "customer_count" and _claim_count(claim) is None:
            continue
        if kind == "endorsement" and not _claim_authority(claim):
            continue
        out.append(claim)
    return out


def round_down_count(n):
    """A verified count as a headline shows it: rounded DOWN to two
    significant figures (tens below 100) with a "+" -- 12,480 -> "12,000+".
    Never rounded up."""
    n = int(n)
    step = 10 if n < 100 else 10 ** (len(str(n)) - 2)
    return f"{n // step * step:,}+"


# ---------------------------------------------------------------------------
# eligibility and selection
# ---------------------------------------------------------------------------

def _tenant(tenant):
    return tenant or tenant_mod.active()


def _brand(tenant):
    return getattr(tenant, "display_name", None) or tenant.get("name") or getattr(tenant, "name", "")


def eligibility(template_id, style, facts_pack, tenant=None):
    """(True, why) when this template may head a `style` page with this
    facts_pack's evidence, else (False, why not)."""
    t = template(template_id)
    if style not in t["styles"]:
        return False, f"its items are {'/'.join(t['styles'])} items, not {style!r}"
    claims = (facts_pack or {}).get("verified_claims") or []
    found = []
    for kind in t.get("requires") or []:
        hits = claims_of_kind(kind, claims)
        if not hits:
            desc = load_library()["evidence"][kind].get("description") or kind
            return False, f"needs a verified {kind} claim ({desc}); none is verified"
        found.append(f"{kind}: {', '.join(c['id'] for c in hits[:3])}{' ...' if len(hits) > 3 else ''}")
    return True, ("verified " + "; ".join(found)) if found else "needs no extra evidence"


def _tenant_setting(tenant):
    cfg = tenant.get("cartridges.listicle.headline_templates") if tenant else None
    if not isinstance(cfg, dict):
        return (), ()
    return tuple(cfg.get("include") or ()), tuple(cfg.get("exclude") or ())


def eligible_templates(style, facts_pack, tenant=None):
    """The template ids a `style` run may pick from, in library order:
    eligible by style and evidence, then the tenant's include (a pin) and
    exclude lists. Never empty -- a filter that leaves nothing falls back to
    the style's own formula, the same "a pin that names nothing usable is
    ignored" rule as listicle.tenant_styles."""
    tenant = _tenant(tenant)
    ids = [tid for tid in template_ids() if eligibility(tid, style, facts_pack, tenant)[0]]
    include, exclude = _tenant_setting(tenant)
    if include:
        ids = [tid for tid in ids if tid in include]
    ids = [tid for tid in ids if tid not in exclude]
    return ids or [legacy_id(style)]


def resolve_plan(style, facts_pack, tenant=None, *, seed=0, today=None, requested=None, weights=None):
    """The run's headline plan. `requested` (`harness run --headline-template`)
    wins over the tenant's include/exclude -- an operator's deliberate choice,
    like an explicit --style -- but never over the style or the evidence.
    Otherwise a pick from eligible_templates, seeded from the run seed.
    `weights` ({template_id: weight}, default 1.0 each) is the hook the A/B/C
    results can use to favour templates that win."""
    tenant = _tenant(tenant)
    if requested:
        ok, why = eligibility(requested, style, facts_pack, tenant)
        if not ok:
            raise HeadlineTemplateError(f"headline template {requested!r} cannot head this {style} page: {why}")
        return build_plan(requested, style, facts_pack, tenant=tenant, today=today)
    ids = eligible_templates(style, facts_pack, tenant)
    rng = random.Random(f"listicle-headline:{seed}:{style}")
    pick = None
    if weights:
        w = [max(float(weights.get(tid, 1.0)), 0.0) for tid in ids]
        if sum(w) > 0:
            pick = rng.choices(ids, weights=w)[0]
    if pick is None:
        pick = rng.choice(ids)
    return build_plan(pick, style, facts_pack, tenant=tenant, today=today)


def _as_date(today):
    if today is None:
        return datetime.date.today()
    if isinstance(today, datetime.date):
        return today
    return datetime.date.fromisoformat(str(today)[:10])


def _product_names(facts_pack, tenant):
    """(full names, model names) for every product the tenant sells plus this
    run's own -- tenant.product_names, the cycle 64 naming rule."""
    catalog = getattr(tenant, "catalog_products", None)
    products = list(catalog()) if catalog else []
    fp = facts_pack or {}
    if fp.get("product"):
        products.append(fp["product"])
    products += [p for p in fp.get("model_options") or [] if isinstance(p, dict)]
    full, models = set(), set()
    for product in products:
        names = tenant_mod.product_names(product, tenant)
        if names["short_name"]:
            models.add(names["short_name"])
            full.add(names["full_name"] or names["short_name"])
    return sorted(full), sorted(models)


def _competitor_names():
    aliases = dict(vocab.COMPETITOR_ALIASES or {})
    names = list(vocab.BANNED_NAMES or ()) + list(aliases) + [str(v) for v in aliases.values()]
    return sorted({n.strip() for n in names if n and n.strip()}, key=str.lower)


def build_plan(template_id, style, facts_pack, tenant=None, today=None):
    """The plan for one template on one page (see the module docstring).
    Raises HeadlineTemplateError when the template is not eligible."""
    tenant = _tenant(tenant)
    ok, why = eligibility(template_id, style, facts_pack, tenant)
    if not ok:
        raise HeadlineTemplateError(f"headline template {template_id!r} cannot head this {style} page: {why}")
    t = template(template_id)
    claims = (facts_pack or {}).get("verified_claims") or []
    year = str(_as_date(today).year)
    requires = list(t.get("requires") or [])
    evidence = {kind: [c["id"] for c in claims_of_kind(kind, claims)] for kind in requires}
    evidence_texts = {kind: [c.get("text") or "" for c in claims_of_kind(kind, claims)] for kind in requires}
    fixed, fixed_sources = {}, {}
    for name, slot in t["slots"].items():
        kind = slot["kind"]
        if kind == "brand":
            fixed[name] = _brand(tenant)
        elif kind == "year":
            fixed[name] = year
        elif kind in ("count", "count_unit"):
            best = max(claims_of_kind("customer_count", claims), key=lambda c: _claim_count(c)[0])
            count, unit = _claim_count(best)
            fixed[name] = round_down_count(count) if kind == "count" else str(unit).title()
            fixed_sources[name] = best["id"]
        elif kind == "authority":
            claim = claims_of_kind("endorsement", claims)[0]
            fixed[name] = _claim_authority(claim)
            fixed_sources[name] = claim["id"]
        elif kind == "alternative":
            safety = claims_of_kind("safety", claims)
            fixed[name] = "Safe" if safety else "Better"
            if safety:
                requires.append("safety")
                evidence["safety"] = [c["id"] for c in safety]
                evidence_texts["safety"] = [c.get("text") or "" for c in safety]
                fixed_sources[name] = safety[0]["id"]
    full_names, model_names = _product_names(facts_pack, tenant)
    competitors = _competitor_names()
    # the run's own product first; never a name the tenant's vocab bans
    own = tenant_mod.product_names((facts_pack or {}).get("product") or {}, tenant)["full_name"]
    usable = [n for n in [own, *full_names] if n and not _mentions(n, competitors)]
    plan = {
        "id": template_id,
        "style": style,
        "legacy_style": t.get("legacy_style"),
        "pattern": t["pattern"],
        "headline_pattern": _fill(t["pattern"], fixed),
        "slots": {n: dict(s) for n, s in t["slots"].items() if s["kind"] in FREE_KINDS},
        "fixed": fixed,
        "fixed_sources": fixed_sources,
        "year": year,
        "requires": requires,
        "evidence": evidence,
        "evidence_texts": evidence_texts,
        "item_pattern": " ".join((t.get("item_pattern") or "").split()) or None,
        "item_gates": list(t.get("item_gates") or []),
        "allowed_headline_terms": list(t.get("allowed_headline_terms") or []),
        "brand": _brand(tenant),
        "full_names": full_names,
        "model_names": model_names,
        "other_names": sorted({n for n in (facts_pack or {}).get("digit_exempt_terms") or [] if n}),
        "competitor_names": competitors,
        "example_full_name": usable[0] if usable else None,
    }
    return plan


def plan_for_page(page, facts_pack, tenant=None, today=None):
    """The plan a stored page.json was written to (its "headline_template_id",
    stamped by the pipeline), or None -- used by `harness revise`, whose
    writer returns a whole new page.json the gate must still hold to the
    same template."""
    if not isinstance(page, dict):
        return None
    tid = page.get("headline_template_id")
    if not tid or tid not in load_library()["templates"]:
        return None
    style = page.get("style")
    try:
        return build_plan(tid, style, facts_pack, tenant=tenant, today=today)
    except HeadlineTemplateError:
        return None


# ---------------------------------------------------------------------------
# rendering and parsing
# ---------------------------------------------------------------------------

def _fill(pattern, values):
    return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), pattern)


def render(template_or_plan, n, values):
    """The headline with N and every slot in `values` filled in; slots not
    in `values` stay as <slot>."""
    pattern = template_or_plan.get("headline_pattern") or template_or_plan["pattern"]
    parts = _SPLIT_RE.split(pattern)
    parts = [p if _PLACEHOLDER_RE.fullmatch(p) else _N_RE.sub(str(n), p) for p in parts]
    return _fill("".join(parts), values)


def example_headline(plan, n):
    """The library's own example for this template, with the plan's fixed
    parts -- documentation, the offline demo and the fake run use it."""
    return render(plan, n, template(plan["id"]).get("example") or {})


_PUNCT = {",": ",?", "(": r"\(?", ")": r"\)?", "'": "['’]", "’": "['’]", "-": r"[-\s]?", ".": r"\.?", ":": ":?"}
_TOKEN_RE = re.compile(r"\s+|[^\s,()'’.:\-]+|[,()'’.:\-]")


def _literal_re(text):
    """Regex for fixed template words: any case, flexible spacing, a dropped
    comma/parenthesis/period, straight or curly apostrophes, "Must-Have" or
    "Must Have"."""
    tokens = _TOKEN_RE.findall(text)
    out = []
    for i, tok in enumerate(tokens):
        if tok.isspace():
            prev = tokens[i - 1] if i else None
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            out.append(r"\s*" if prev in _PUNCT or nxt in _PUNCT else r"\s+")
        elif tok in _PUNCT:
            out.append(_PUNCT[tok])
        else:
            out.append(re.escape(tok))
    return "".join(out)


def _segment_re(segment):
    pieces = _N_RE.split(segment)
    if len(pieces) == 1:
        return _literal_re(segment)
    return _literal_re(pieces[0]) + r"(?P<n>\d+)" + _literal_re(pieces[1])


def _headline_re(plan, loose=False):
    fixed = plan.get("fixed") or {}
    out = [r"^\s*"]
    for part in _SPLIT_RE.split(plan["pattern"]):
        m = _PLACEHOLDER_RE.fullmatch(part)
        if not m:
            out.append(_segment_re(part))
            continue
        name = m.group(1)
        options = (plan.get("slots", {}).get(name) or {}).get("options")
        if name in fixed and not loose:
            out.append(f"(?P<{name}>{_literal_re(fixed[name])})")
        elif options:
            # a choice slot next to another slot ("<problem>, <with_without>")
            # is only unambiguous as its own alternation, longest first
            alts = "|".join(_literal_re(str(o)) for o in sorted(options, key=len, reverse=True))
            out.append(f"(?<![A-Za-z])(?P<{name}>{alts})(?![A-Za-z])")
        else:
            out.append(f"(?P<{name}>.+?)")
    out.append(r"\s*[.!?]?\s*$")
    return re.compile("".join(out), re.IGNORECASE)


def parse_headline(headline, plan, loose=False):
    """{"n": ..., slot: value} for a headline that follows the plan's
    template, else None."""
    m = _headline_re(plan, loose=loose).match(headline or "")
    if not m:
        return None
    return {k: (v or "").strip() for k, v in m.groupdict().items()}


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def _words_re(words):
    if not words:
        return re.compile(r"(?!x)x")
    body = "|".join(str(w).replace(" ", r"\s+") for w in words)
    return re.compile(r"(?<![A-Za-z])(?:" + body + r")(?![A-Za-z])", re.IGNORECASE)


def _medical_re(which="medical"):
    return _words_re(load_library()["word_lists"].get(which) or [])


def _fear_re():
    """headlines.yaml's fear list plus the tenant's own EMF terms
    (vocab.yaml emf_terms)."""
    words = list(load_library()["word_lists"].get("fear") or [])
    return _words_re(words + [re.escape(t) for t in (vocab.EMF_TERMS or ())])


def _mentions(value, names):
    for name in names:
        if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", value, re.IGNORECASE):
            return name
    return None


def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


def _items(page):
    reasons = page.get("reasons")
    return reasons if isinstance(reasons, list) else []


def _norm(text):
    return " ".join(str(text or "").casefold().replace("’", "'").strip(" .").split())


def _fixed_mismatch(plan, parsed):
    """One message per harness-filled part the headline changed."""
    out = []
    for name, want in (plan.get("fixed") or {}).items():
        got = parsed.get(name, "")
        if _norm(got).replace(",", "") == _norm(want).replace(",", ""):
            continue
        kind = template(plan["id"])["slots"][name]["kind"]
        source = plan.get("fixed_sources", {}).get(name)
        if kind == "year":
            why = f'the year must be "{want}", this run\'s own year'
        elif kind in ("count", "count_unit"):
            why = (f'the count is fixed at "{plan["fixed"].get("count")} {plan["fixed"].get("count_unit")}" -- the '
                   f"verified count from claim {source}, rounded down; never change or raise it")
        elif kind == "authority":
            why = f'the authority is fixed: "{want}", named by verified claim {source}; never another name'
        elif kind == "alternative":
            why = (f'write "a {want} Alternative"' + (" -- no safety claim is verified, so never \"Safe\""
                                                     if want == "Better" else f" (claim {source})"))
        else:
            why = f'<{name}> is fixed: "{want}"'
        out.append(f'{why}, not "{got}"')
    return out


def find_headline_violations(page, plan):
    """The headline against the plan's template (formula, N = item count,
    the harness-filled parts, every writer slot's rules, fear words), the
    template's item gates, and the evidence its items must cite. Keys:
    listicle:headline_formula (fixable when only N is wrong),
    listicle:headline_slots, listicle:headline_medical, listicle:headline_fear,
    listicle:headline_evidence:<kind>, listicle:item_fear:<i>,
    listicle:item_medical:<i>."""
    problems = []
    headline = page.get("headline") or ""
    tid, shape = plan["id"], plan["headline_pattern"]
    fear_re = _fear_re()
    medical_re = _medical_re()
    m = fear_re.search(headline)
    if m:
        problems.append(_problem(
            "$.headline", "listicle:headline_fear",
            f"headline uses {m.group(0)!r} -- never EMF, radiation, toxins, chemicals, off-gassing or any other "
            "health, safety or fear wording; name a concrete, checkable fact instead",
            text=headline,
        ))
    parsed = parse_headline(headline, plan)
    if parsed is None:
        loose = parse_headline(headline, plan, loose=True)
        reasons = _fixed_mismatch(plan, loose) if loose else []
        if reasons:
            detail = "; ".join(reasons)
        else:
            detail = "fill in N and each <slot>, and keep every other word exactly"
        problems.append(_problem(
            "$.headline", "listicle:headline_formula",
            f'headline {headline!r} does not follow this run\'s headline template "{tid}": "{shape}" -- {detail}',
        ))
        parsed = loose
    else:
        stated, actual = int(parsed["n"]), len(_items(page))
        if stated != actual:
            problems.append(_problem(
                "$.headline", "listicle:headline_formula",
                f"headline says {stated} but the page has {actual} items; the number in the headline must be the "
                f'item count (template "{tid}": "{shape}")',
            ))
    if parsed:
        problems += _slot_problems(plan, parsed, medical_re, fear_re)
    problems += _item_gate_problems(page, plan, _medical_re("medical_items"), fear_re)
    problems += _evidence_problems(page, plan)
    return problems


def _slot_problems(plan, parsed, medical_re, fear_re):
    problems = []
    tid = plan["id"]
    names = [plan["brand"], *plan.get("model_names", []), *plan.get("other_names", [])]

    def bad(name, why, key="listicle:headline_slots"):
        problems.append(_problem("$.headline", key, f'template "{tid}" <{name}> {why}'))

    for name, slot in plan["slots"].items():
        value = (parsed.get(name) or "").strip(" ,.")
        kind = slot["kind"]
        if not value:
            bad(name, "is empty")
            continue
        if slot.get("max_words") and len(value.split()) > slot["max_words"]:
            bad(name, f"is {len(value.split())} words ({value!r}); at most {slot['max_words']}")
        if kind == "choice":
            options = [str(o) for o in slot.get("options") or []]
            if value.casefold() not in {o.casefold() for o in options}:
                bad(name, f"is {value!r}; it must be one of {options}")
            continue
        if kind == "age":
            if not (re.fullmatch(r"\d{2}", value) and 18 <= int(value) <= 99):
                bad(name, f"is {value!r}; it must be a plain age as two digits, e.g. \"40\"")
            continue
        if re.search(r"\d", value):
            bad(name, f"contains a number ({value!r}) -- a number in a headline needs a verified claim; only N, "
                      "the year and a verified count may appear")
        rival = _mentions(value, plan.get("competitor_names", []))
        if rival:
            bad(name, f"names the competitor {rival!r} ({value!r}) -- name a category (e.g. \"gym saunas\"), "
                      "never a competitor brand")
        named = _mentions(value, names)
        if named and kind == "product":
            core = re.sub(r"^(?:the|a|an|this)\s+", "", value, flags=re.IGNORECASE)
            if core.casefold() not in {n.casefold() for n in plan.get("full_names", [])}:
                bad(name, f"is {value!r} -- name the product category, or a model by its full name exactly "
                          f"({', '.join(plan.get('full_names', [])[:4])}, ...), never the model alone or a long title")
        elif named:
            bad(name, f"contains {named!r} -- never the brand or a model name in this slot")
        if kind == "audience":
            normalized = value.lower()
            if normalized in listicle.GENERIC_AUDIENCE_WORDS or normalized + "s" in listicle.GENERIC_AUDIENCE_WORDS:
                bad(name, f"is just {value!r} -- that names no one in particular; name people by their "
                          "situation or goal")
        if kind in NON_MEDICAL_KINDS:
            hit = medical_re.search(value) or vocab.TRIGGER_WORD_RE.search(value.lower())
            if hit:
                bad(name, f"uses {hit.group(0)!r} ({value!r}) -- an everyday, non-medical situation only: never a "
                          "disease, condition, symptom, cure, treat, heal or other health word",
                    key="listicle:headline_medical")
        if kind == "niche":
            words = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", value.lower())} - _NICHE_STOPWORDS
            texts = plan.get("evidence_texts", {}).get("exclusivity", [])
            if not any(words <= {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", t.lower())} for t in texts):
                bad(name, f"is {value!r} -- it must be the niche a verified exclusivity claim names, in its words")
    return problems


def _item_strings(item):
    for field in ("heading", "text"):
        if isinstance(item.get(field), str):
            yield item[field]
    proof = item.get("proof")
    if isinstance(proof, dict) and isinstance(proof.get("text"), str):
        yield proof["text"]


def _item_gate_problems(page, plan, medical_re, fear_re):
    gates = plan.get("item_gates") or []
    problems = []
    for i, item in enumerate(_items(page)):
        if not isinstance(item, dict):
            continue
        for text in _item_strings(item):
            if "fear" in gates:
                m = fear_re.search(text)
                if m:
                    problems.append(_problem(
                        f"$.reasons[{i}]", f"listicle:item_fear:{i}",
                        f'item uses {m.group(0)!r} -- template "{plan["id"]}" items are concrete, checkable category '
                        "facts (shared equipment, schedule limits, extra electrical work, hidden fees), never EMF, "
                        "radiation, toxins, chemicals, off-gassing or any health, safety or fear claim",
                        text=text,
                    ))
                    break
            if "medical" in gates:
                m = medical_re.search(text)
                if m:
                    problems.append(_problem(
                        f"$.reasons[{i}]", f"listicle:item_medical:{i}",
                        f'item uses {m.group(0)!r} -- template "{plan["id"]}" items never make a health claim: no '
                        "disease, condition, symptom, cure, treat, heal, pain or other medical wording",
                        text=text,
                    ))
                    break
    return problems


def _evidence_problems(page, plan):
    cited = set()
    for item in _items(page):
        if not isinstance(item, dict):
            continue
        cited.update(item.get("claim_ids") or [])
        proof = item.get("proof")
        if isinstance(proof, dict):
            cited.update(proof.get("claim_ids") or [])
    problems = []
    for kind, ids in (plan.get("evidence") or {}).items():
        if ids and not cited & set(ids):
            shown = ", ".join(ids[:8]) + (" ..." if len(ids) > 8 else "")
            problems.append(_problem(
                "$.reasons", f"listicle:headline_evidence:{kind}",
                f'template "{plan["id"]}" needs the items to cite its verified {kind} evidence: cite one of '
                f"{shown} in an item's claim_ids or proof",
            ))
    return problems


def fix_headline_number(page, plan):
    """The headline with its leading count set to the item count, when that
    is all that is wrong with it (a spelled-out or stale N), else None --
    the template-plan twin of listicle.fix_headline_number."""
    headline = page.get("headline") or ""
    count = len(_items(page))
    prefix = _N_RE.split(_SPLIT_RE.split(plan["pattern"])[0])[0]
    words = "|".join(listicle._COUNT_WORDS)
    m = re.match(r"^\s*" + _literal_re(prefix) + r"\s*(\d+|" + words + r")\b", headline, re.IGNORECASE)
    if not m:
        return None
    fixed = headline[: m.start(1)] + str(count) + headline[m.end(1):]
    if fixed == headline:
        return None
    parsed = parse_headline(fixed, plan)
    if not parsed or int(parsed["n"]) != count:
        return None
    return fixed


# ---------------------------------------------------------------------------
# what the writer is told
# ---------------------------------------------------------------------------

_KIND_RULES = {
    "audience": ('names people by their situation or goal, derived from the ad brief -- never a bare "people", '
                 '"buyers", "shoppers", "customers" or "everyone", never a real-estate term, never the brand or a '
                 "model name"),
    "category": "is the product category -- never the brand and never a model name",
    "product": ("is the product category, or one model's full name exactly as the facts pack gives it "
                "-- never the model name alone and never the long catalog title"),
    "common_solution": "is a CATEGORY the reader uses or considers now -- never a competitor or brand name",
    "age": "is a plain age as two digits (e.g. 40) -- never tie a health claim to the age, in the headline or items",
    "niche": "is the niche the verified exclusivity claim names, in its own words",
}
_NON_MEDICAL_RULE = ("is everyday, non-medical wording -- never a disease, condition, symptom, cure, treat, heal "
                     "or other health word")


def _term_as_written(plan, term):
    m = re.search(re.escape(term).replace(r"\-", r"[-\s]").replace(r"\ ", r"[-\s]"), plan["pattern"], re.IGNORECASE)
    return m.group(0) if m else term


def writer_lines(plan):
    """The hard-constraint lines for a template plan (the style formula plans
    keep listicle.writer_style_lines' own)."""
    tid, style = plan["id"], plan["style"]
    lines = [
        f'This page\'s style is "{style}" and its headline template is "{tid}". The headline must follow this '
        f'template exactly: "{plan["headline_pattern"]}" -- write N as the item count and fill each <slot> as '
        f'defined below; keep every other word as written. Set page.json\'s "style" field to "{style}".',
    ]
    for name, slot in plan["slots"].items():
        rule = _KIND_RULES.get(slot["kind"]) or (_NON_MEDICAL_RULE if slot["kind"] in NON_MEDICAL_KINDS else "")
        if slot["kind"] == "choice":
            rule = "is exactly one of: " + ", ".join(str(o) for o in slot.get("options") or [])
        if slot["kind"] == "product" and plan.get("example_full_name"):
            rule += f' (a full name is e.g. "{plan["example_full_name"]}")'
        desc = slot.get("description")
        limit = f" At most {slot['max_words']} words." if slot.get("max_words") else ""
        lines.append(f"<{name}> {rule}" + (f" ({desc})" if desc else "") + "." + limit)
    if plan["fixed"]:
        parts = ", ".join(f'"{v}"' for v in plan["fixed"].values())
        lines.append(
            f"The template above already carries its fixed parts ({parts}) -- copy them exactly; never change, "
            "round or replace them."
        )
    allowed_numbers = ["N"] + (["the year"] if "year" in plan["fixed"] else []) + \
        (["the verified count"] if "count" in plan["fixed"] else []) + \
        (["the age"] if any(s["kind"] == "age" for s in plan["slots"].values()) else [])
    lines.append(f"No other number may appear in the headline -- only {', '.join(allowed_numbers)}.")
    if "count" in plan["fixed"]:
        shown = f'{plan["fixed"]["count"]} {plan["fixed"].get("count_unit", "")}'.strip()
        lines.append(
            f'"{shown}" is the verified count from claim {plan["fixed_sources"].get("count")}, rounded down -- '
            "never a bigger or rounder number."
        )
    if "authority" in plan["fixed"]:
        lines.append(
            f'The authority is "{plan["fixed"]["authority"]}", named by verified claim '
            f'{plan["fixed_sources"].get("authority")} -- never another person, title or institution.'
        )
    if "alternative" in plan["fixed"]:
        if plan["fixed"]["alternative"] == "Safe":
            lines.append(f'The headline says "a Safe Alternative" because claim {plan["fixed_sources"]["alternative"]} '
                         "is verified; an item must cite it.")
        else:
            lines.append('The headline says "a Better Alternative" -- never "Safe": no safety claim is verified.')
    lib = load_library()["evidence"]
    for kind, ids in plan["evidence"].items():
        desc = lib.get(kind, {}).get("description") or kind
        lines.append(
            f"This template needs {desc}: at least one numbered item must cite one of these claim_ids in its "
            f"claim_ids or proof: {', '.join(ids)}. Every sentence that states that fact cites it."
        )
    for term in plan.get("allowed_headline_terms") or []:
        if " " in term:
            continue
        shown = _term_as_written(plan, term)
        lines.append(
            f'The headline may use "{shown}" -- this template\'s own wording, in the headline only; it stays '
            "forbidden everywhere else on the page (dek, items, FAQ, closing)."
        )
    if "fear" in plan.get("item_gates", []):
        lines.append(
            "Never write EMF, radiation, toxins, chemicals, off-gassing, or any other health, safety or fear "
            "wording in the headline or any item -- each item is a concrete, checkable category fact (shared "
            "equipment, schedule limits, extra electrical work, hidden fees)."
        )
    if "medical" in plan.get("item_gates", []):
        lines.append(
            "Items never make a health claim: no disease, condition, symptom, cure, treat, heal, pain or other "
            "medical wording in any item heading, body or proof line."
        )
    return lines


def eligibility_rows(facts_pack, tenant=None):
    """[(id, styles, eligible, why)] for every template, each checked
    against its first style -- the docs table and the cycle 70 report."""
    rows = []
    for tid in template_ids():
        t = template(tid)
        ok, why = eligibility(tid, t["styles"][0], facts_pack, tenant)
        rows.append((tid, list(t["styles"]), ok, why))
    return rows
