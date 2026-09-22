"""Cycle 54: the product-page cartridge's `pdp` look -- renderer-owned
sections and the writer-owned structural gates.

The `pdp` look lays a product page out like the brand's own storefront
product detail page (see the tenant's docs/PDP-STRUCTURE-*.md for what was
measured), but its promise band and FAQ answer the ad the visitor clicked.
Cycle 62 made it simpler: the live PDP's bones only -- gallery + buy panel,
one promise band, a short "what's included" list, the specs in a
details row, exactly 5 FAQs, one closing CTA. No compare table, proof
tiles, benefit cards or chips. Two halves, the same split
harness/listicle.py uses:

  - render_context: everything the look shows that did NOT come from the
    writer -- the gallery, the price, the fixed financing and warranty
    sentences, the rating line, the HSA/FSA line, the lead-time line, the
    spec table. Each one is built from facts_pack alone, so "omitted when
    unverified" is structural: there is no page.json field to invent one
    in, and find_renderer_owned_violations rejects a page that adds one.
    It also trims the writer's sections to what the look shows (2 promise
    paragraphs, 6 included items, 5 FAQs), so a page.json written before
    cycle 62 still renders -- its extra fields are ignored.
  - find_product_page_violations: the writer-owned checks the repair loop
    runs (harness/repair.py's check_page_gates) -- an included list with
    claim ids, exactly 5 FAQs whose factual answers carry claim ids,
    exactly 2 promise paragraphs, a promise line with no uncited number,
    power/outlet copy that agrees with the product's electrical claim, no
    stock marketing phrases, no urgency vocabulary.

Tenant-neutral: every rule here is structural or plain English; any
company, product or lender word comes from facts_pack or tenant.yaml.
"""
import re

from . import claims as claims_mod
from . import listicle
from . import tenant as tenant_mod
from . import vocab
from .ground import HERO_NEVER_KINDS
from .prices import format_price
from .textutil import NON_PROSE_KEYS, product_name_slug, walk_page

FAQ_COUNT_RANGE = (5, 5)
INCLUDED_RANGE = (3, 6)
PROMISE_PARAGRAPHS = 2
GALLERY_MAX = 6
GALLERY_MIN_UNIQUE = 4
SPEC_TABLE_MAX_ROWS = 10

# Stock marketing phrases the owner named as "AI-generated" (cycle 62), on
# top of the tenant's own vocab hype words. Matched on word stems, so
# "transforms" and "sanctuaries" count too.
_STOCK_PHRASE_RES = tuple(
    (phrase, re.compile(pattern, re.IGNORECASE)) for phrase, pattern in (
        ("whether you're", r"\bwhether\s+you(?:'|’)?(?:re|\s+are)\b"),
        ("elevate", r"\belevat(?:e|es|ed|ing)\b"),
        ("unlock", r"\bunlock\w*"),
        ("transform", r"\btransform\w*"),
        ("sanctuary", r"\bsanctuar(?:y|ies)\b"),
        ("game-changer", r"\bgame[\s-]?chang\w*"),
    )
)

# Power/outlet wording (cycle 62: one demo page said "no electrician needed"
# while that model's own claim says it needs a dedicated 20A outlet). The
# product's own claims decide which way round: a claim that says a
# dedicated outlet or circuit is required makes the "plugs in anywhere"
# phrases a contradiction; otherwise a claim that says a standard outlet
# makes the "dedicated" phrases one.
_NO_DEDICATED_RE = re.compile(r"\bno\s+dedicated\b", re.IGNORECASE)
_DEDICATED_RE = re.compile(r"\bdedicated\b", re.IGNORECASE)
_STANDARD_OUTLET_RES = (
    re.compile(r"\b(?:no|without)\b[^.]{0,30}\belectrician", re.IGNORECASE),
    # "a standard 15A outlet is not sufficient" states the dedicated rule,
    # so a negated outlet phrase is not a contradiction
    re.compile(r"\b(?:standard|normal|regular|ordinary|any)\s+(?:[\w/-]+\s+){0,2}outlet"
               r"(?!\s+(?:is\s+not|isn't|will\s+not|won't|does\s+not|doesn't)\b)", re.IGNORECASE),
    _NO_DEDICATED_RE,
)
# Stated requirements only: a question ("Do I need an electrician?") or a
# negated clause ("no electrician or dedicated circuit is required") is
# not one -- _NEGATED_CLAUSE_RE removes those clauses before the match.
_DEDICATED_PAGE_RES = (
    re.compile(r"\b(?:requires?|needs|runs\s+on|must\s+(?:have|use))\s+(?:a\s+|an\s+|its\s+own\s+)?dedicated\b", re.IGNORECASE),
    re.compile(r"\bdedicated\s+(?:[\w/-]+\s+){0,3}(?:outlet|circuit|line)\s+(?:is\s+)?(?:required|needed)", re.IGNORECASE),
    re.compile(r"\b(?:requires|needs)\s+an?\s+electrician", re.IGNORECASE),
)
_NEGATED_CLAUSE_RE = re.compile(r"\b(?:no|not|without|never)\b[^.;?!]*", re.IGNORECASE)

# page.json keys for sections the RENDERER owns. A writer that adds one is
# inventing data the gate cannot check (same rule as
# listicle.RENDERER_OWNED_KEYS).
RENDERER_OWNED_KEYS = (
    "gallery", "model_compare", "model_options", "rating_line", "hsa_line",
    "sticky_cta", "shipping_line", "price_panel", "spec_rows",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LEAD_TIME_RE = re.compile(r"\b(?:business\s+)?days?\b", re.IGNORECASE)


def _problem(path, key, issue, **extra):
    return {"path": path, "key": key, "issue": issue, **extra}


# ---------------------------------------------------------------------------
# Gallery (the product's own storefront images)
# ---------------------------------------------------------------------------

def _hero_id(page):
    return ((page.get("hero") or {}).get("hero_image") or {}).get("asset_id")


def _angle_image_id(page):
    angle = page.get("angle_section") if isinstance(page.get("angle_section"), dict) else {}
    image = angle.get("image") if isinstance(angle.get("image"), dict) else {}
    return image.get("asset_id")


def _page_asset_ids_in_order(page):
    ids = []
    for _path, node in walk_page(page):
        if isinstance(node, dict) and node.get("asset_id") and node["asset_id"] not in ids:
            ids.append(node["asset_id"])
    return ids


def _eligible(assets, allow_ai_renders):
    def check(asset_id):
        asset = assets.get(asset_id)
        return bool(asset) and asset.get("kind") not in HERO_NEVER_KINDS and (
            allow_ai_renders or not asset.get("ai_generated")
        )
    return check


def gallery_asset_ids(page, facts_pack, *, allow_ai_renders=False, limit=GALLERY_MAX):
    """The gallery's asset ids, in order, at most `limit`:

      1. the writer's hero (page.hero.hero_image) -- the image the page
         already leads with;
      2. the writer's optional page.gallery_order picks, in order;
      3. the product's own storefront images (facts_pack.product.slug's
         `asset-<slug>-<n>` manifest ids) other than the promise band's own
         image (page.angle_section.image);
      4. only if that leaves fewer than GALLERY_MIN_UNIQUE images: storefront
         images the page names anywhere else, in page order.

    Every id is checked against facts_pack.assets; a never-eligible kind (a
    logo) or a disallowed AI render is skipped. Called before assets are
    downloaded, so render_page downloads exactly these."""
    assets = {a["id"]: a for a in facts_pack.get("assets", [])}
    slug = (facts_pack.get("product") or {}).get("slug") or ""
    prefix = f"asset-{slug}-"

    eligible = _eligible(assets, allow_ai_renders)
    order = []

    def add(asset_id):
        if asset_id and asset_id not in order and eligible(asset_id) and len(order) < limit:
            order.append(asset_id)

    add(_hero_id(page))
    for asset_id in page.get("gallery_order") or []:
        if isinstance(asset_id, str):
            add(asset_id)
    promise_image = _angle_image_id(page)
    storefront = [a["id"] for a in facts_pack.get("assets", []) if slug and a["id"].startswith(prefix)]
    for asset_id in storefront:
        if asset_id != promise_image:
            add(asset_id)
    if len(order) < GALLERY_MIN_UNIQUE:
        for asset_id in _page_asset_ids_in_order(page):
            if asset_id in storefront:
                add(asset_id)
    return order


# ---------------------------------------------------------------------------
# Renderer-owned buy-panel and table data
# ---------------------------------------------------------------------------

def _verified(facts_pack):
    return {c["id"]: c for c in (facts_pack or {}).get("verified_claims", [])}


def price_panel(page, facts_pack):
    """{"text", "compare_at", "claim_ids"} from the product's verified price
    claim, or None. The compare-at figure is shown only when facts_pack
    carries one -- harness/ground.py nulls it unless claims/config.json's
    show_compare_at_price allows it. With no verified price claim, the
    writer's own hero.price_line is used only when every claim id on it is
    verified; otherwise there is no price line at all."""
    product = facts_pack.get("product") or {}
    verified = _verified(facts_pack)
    price_id = f"price-{product_name_slug(product.get('name') or '')}"
    if price_id in verified and product.get("price"):
        compare_at = product.get("compare_at_price")
        return {
            "text": format_price(product["price"]),
            "compare_at": format_price(compare_at) if compare_at else None,
            "claim_ids": [price_id],
        }
    line = (page.get("hero") or {}).get("price_line") or {}
    ids = line.get("claim_ids") or []
    if line.get("text") and ids and all(i in verified for i in ids):
        return {"text": line["text"], "compare_at": None, "claim_ids": list(ids)}
    return None


def financing_sentence(facts_pack):
    """The one allowed financing sentence for this run, verbatim, or None
    when the product has no financing at all."""
    financing = (facts_pack.get("product") or {}).get("financing") or {}
    if not financing.get("available"):
        return None
    return vocab.allowed_financing_sentence(financing.get("lender"))


def warranty_line(facts_pack):
    """The fixed warranty sentence, with the tenant's warranty claim id, or
    None when this run's facts carry no warranty claim."""
    wid = claims_mod.warranty_claim_id()
    if wid not in _verified(facts_pack):
        return None
    return {"text": vocab.ALLOWED_WARRANTY_SENTENCE, "claim_ids": [wid]}


def spec_rows(facts_pack, limit=SPEC_TABLE_MAX_ROWS):
    """facts_pack.specs rows that carry a verified claim id, in order."""
    verified = _verified(facts_pack)
    rows = []
    for spec in facts_pack.get("specs") or []:
        if spec.get("claim_id") in verified and spec.get("label") and spec.get("value"):
            rows.append({"label": spec["label"], "value": spec["value"], "claim_ids": [spec["claim_id"]]})
    return rows[:limit]


def _retired_names(tenant):
    return [n for n in (tenant.get("brand.retired_names") or ()) if n]


def shipping_line(facts_pack, tenant=None):
    """The buy panel's shipping/lead-time line, from verified shipping
    claims only: "Free shipping." when a verified claim says so, plus the
    first sentence of a verified shipping claim that states a lead time in
    days. A sentence naming a retired brand name is never shown. None when
    nothing is verified."""
    tenant = tenant or tenant_mod.active()
    retired = [n.lower() for n in _retired_names(tenant)]
    parts, ids = [], []
    free = listicle.free_shipping_claim(facts_pack)
    if free:
        parts.append("Free shipping.")
        ids.append(free["id"])
    for claim in (facts_pack or {}).get("verified_claims", []):
        if "shipping" not in claim["id"].lower():
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(claim.get("text", "")):
            sentence = sentence.strip()
            if not sentence or not re.search(r"\d", sentence) or not _LEAD_TIME_RE.search(sentence):
                continue
            if any(n in sentence.lower() for n in retired):
                continue
            parts.append(sentence if sentence.endswith(".") else sentence + ".")
            if claim["id"] not in ids:
                ids.append(claim["id"])
            break
        if len(parts) >= 2:
            break
    if not parts:
        return None
    return {"text": " ".join(parts), "claim_ids": ids}


def promise_asset_id(page, facts_pack, gallery_ids, *, allow_ai_renders=False):
    """The promise band's one image, or None: the writer's own
    angle_section.image when it is eligible and not already in the gallery;
    else an eligible lifestyle or installation photo that is neither in the
    gallery nor anywhere else on the page. Called before assets are
    downloaded, with gallery_asset_ids' result, so render_page downloads
    exactly this one."""
    assets = {a["id"]: a for a in facts_pack.get("assets", [])}
    eligible = _eligible(assets, allow_ai_renders)
    own = _angle_image_id(page)
    if own and own not in gallery_ids and eligible(own):
        return own
    taken = set(gallery_ids) | set(_page_asset_ids_in_order(page))
    for asset in facts_pack.get("assets", []):
        if asset.get("kind") in ("lifestyle", "installation") and asset["id"] not in taken and eligible(asset["id"]):
            return asset["id"]
    return None


def display_cta_text(page, facts_pack):
    """The page's one CTA text, as every button shows it. A page written
    before fix cycle 58 filled {short_name} with the long catalog title
    ("Shop the <Company> <Model> 2-Person ..."); that exact string is shown
    with the short model name instead -- the same allowed text
    harness/repair.py resolves today. Any other text is shown verbatim, and
    page.json is never changed."""
    text = page.get("cta_text") or ""
    product = facts_pack.get("product") or {}
    long_name = product.get("seo_title") or product.get("short_name") or ""
    short = product.get("name") or ""
    if long_name and short and long_name != short and long_name in text:
        return text.replace(long_name, short)
    return text


def _paragraph_texts(page, limit=PROMISE_PARAGRAPHS):
    angle = page.get("angle_section") if isinstance(page.get("angle_section"), dict) else {}
    paragraphs = angle.get("paragraphs") if isinstance(angle.get("paragraphs"), list) else []
    texts = [p.get("text") for p in paragraphs if isinstance(p, dict) and (p.get("text") or "").strip()]
    return texts[:limit]


def included_items(page):
    """The "what's included" list: page.included's texts, at most
    INCLUDED_RANGE[1]. A page written before cycle 62 has no included list;
    its proof bullets' labels (each one a verified claim) stand in."""
    items = page.get("included") if isinstance(page.get("included"), list) else None
    key = "text"
    if items is None:
        items = page.get("proof_bullets") if isinstance(page.get("proof_bullets"), list) else []
        key = "label"
    texts = [i.get(key) for i in items if isinstance(i, dict) and (i.get(key) or "").strip()]
    return texts[:INCLUDED_RANGE[1]]


def faq_entries(page):
    faq = page.get("faq")
    questions = faq.get("questions") if isinstance(faq, dict) else faq
    questions = questions if isinstance(questions, list) else []
    return [q for q in questions if isinstance(q, dict) and q.get("question")][:FAQ_COUNT_RANGE[1]]


def render_context(page, facts_pack, assets, gallery_ids, promise_id=None, *, tenant=None):
    """Everything the `pdp` look renders that came from facts_pack or
    tenant.yaml rather than from the writer, plus the writer's sections
    trimmed to what the look shows. `assets` is render_page's assets_by_id
    after downloading (a failed download is simply absent);
    `gallery_ids`/`promise_id` are gallery_asset_ids'/promise_asset_id's
    results from before the download."""
    tenant = tenant or tenant_mod.active()
    product = facts_pack.get("product") or {}
    min_reviews = tenant.get("reviews.min_count")
    angle = page.get("angle_section") if isinstance(page.get("angle_section"), dict) else {}
    names = tenant.product_names(product)
    return {
        # Cycle 64: the buy panel and the document title show the product's
        # full name ("Acme One"); only the JSON-LD keeps the catalog title.
        "product_name": names["full_name"] or tenant.display_product_name(product.get("name") or ""),
        "page_title": names["full_name"] or tenant.display_product_name(product.get("name") or ""),
        "cta_text": display_cta_text(page, facts_pack),
        "gallery": [assets[i] for i in gallery_ids if i in assets],
        "price": price_panel(page, facts_pack),
        "financing": financing_sentence(facts_pack),
        "rating": listicle.rating_line(facts_pack, min_reviews=int(min_reviews) if min_reviews else None),
        "hsa": listicle.hsa_claim(facts_pack),
        "shipping": shipping_line(facts_pack, tenant),
        "warranty": warranty_line(facts_pack),
        "specs": spec_rows(facts_pack),
        "promise": {
            "heading": angle.get("heading") or "",
            "paragraphs": _paragraph_texts(page),
            "asset": assets.get(promise_id) if promise_id else None,
        },
        "included": included_items(page),
        "faq": faq_entries(page),
    }


def context_claim_ids(ctx):
    """Every claim id the renderer-owned sections cite, for the page's
    Sources list."""
    ids = set()
    for key in ("price", "rating", "shipping", "warranty"):
        if ctx.get(key):
            ids.update(ctx[key].get("claim_ids") or [])
    if ctx.get("hsa"):
        ids.add(ctx["hsa"]["id"])
    for row in ctx.get("specs") or []:
        ids.update(row.get("claim_ids") or [])
    return ids


# ---------------------------------------------------------------------------
# Writer prompt lines -- stated from the same constants the gate measures
# ---------------------------------------------------------------------------

def writer_rules_lines():
    ilo, ihi = INCLUDED_RANGE
    return [
        "This is a product page whose promise band and FAQ answer the ad the visitor clicked. "
        "hero.promise is the one-line descriptor under the model name: the ad's main point, said "
        "about the product in plain words -- a number, price or percentage in it needs its "
        "claim_id in hero.claim_ids, so prefer a promise with no number at all.",
        f"angle_section.paragraphs has exactly {PROMISE_PARAGRAPHS} short paragraphs (at most 45 "
        "words each) under one heading (at most 10 words). They carry the ad's angle: what the ad "
        "said, then what this product does about it.",
        f"included has {ilo}-{ihi} items: what comes with the sauna, each a plain noun phrase of at "
        "most 8 words (\"Full-body red light panel\"), each with at least one claim_id from "
        "facts_pack.verified_claims.",
        "faq.questions has exactly 5 entries: the questions a visitor from this ad asks before "
        "buying (its objections, its hidden worries). Each question is at most 12 words; each "
        "answer is 1-2 sentences, at most 35 words, answer first. Any answer stating a number, a "
        "price, a spec or a trigger word carries claim_ids.",
        "Power and outlet: state only what this product's own electrical claim says, in its own "
        "terms (dedicated or standard outlet, volts, amps). Never say an electrician or a dedicated "
        "circuit is or is not needed unless that claim says so.",
        "Style: take the voice of the brand's own product pages -- short, concrete, product first. "
        "Sentence case in every heading, question and item. No lists of three joined by \"and\" "
        "for rhythm, no second-person scenario openers, no stock marketing verbs; name the part, "
        "the number or the step instead.",
        "Never write the gallery, the price panel, the rating, the spec table, a lead-time line or "
        "an HSA/FSA line -- the renderer builds them from facts_pack. No urgency: no countdown, no "
        "\"limited time\", no discount push.",
    ]


# ---------------------------------------------------------------------------
# Gate: writer-owned structural checks
# ---------------------------------------------------------------------------

def find_included_violations(page):
    """3-6 included items, each one a verified claim (at least one
    claim_id; that each id exists is the shared claims gate's job)."""
    items = page.get("included")
    items = items if isinstance(items, list) else []
    lo, hi = INCLUDED_RANGE
    problems = []
    if not lo <= len(items) <= hi:
        problems.append(_problem(
            "$.included", "product-page:included_count",
            f"included has {len(items)} items; it needs {lo}-{hi}",
        ))
    for i, item in enumerate(items):
        path = f"$.included[{i}]"
        if not isinstance(item, dict) or not (item.get("text") or "").strip():
            problems.append(_problem(path, f"product-page:included_shape:{i}",
                                     "an included item needs a text"))
            continue
        if not item.get("claim_ids"):
            problems.append(_problem(
                path, f"product-page:included_claims:{i}",
                "every included item is one verified claim -- give it at least one claim_id from "
                "facts_pack.verified_claims, or name a different item",
                text=item.get("text"),
            ))
    return problems


def find_angle_paragraph_violations(page):
    angle = page.get("angle_section") if isinstance(page.get("angle_section"), dict) else {}
    paragraphs = angle.get("paragraphs") if isinstance(angle.get("paragraphs"), list) else []
    if len(paragraphs) != PROMISE_PARAGRAPHS:
        return [_problem(
            "$.angle_section.paragraphs", "product-page:angle_paragraphs",
            f"angle_section has {len(paragraphs)} paragraphs; it needs exactly {PROMISE_PARAGRAPHS} "
            "short ones",
        )]
    return []


def _product_power_mode(facts_pack):
    """"dedicated", "standard" or None: what this product's own verified
    claims say about its outlet. Only claims about THIS product count -- a
    claim whose id carries the product's name slug, or a facts_pack.specs
    row -- so another model's claim in the same facts pack (the compare
    data) never decides it."""
    product = (facts_pack or {}).get("product") or {}
    slug = product_name_slug(product.get("name") or "")
    spec_ids = {s.get("claim_id") for s in (facts_pack or {}).get("specs") or []}
    texts = [
        c.get("text") or "" for c in (facts_pack or {}).get("verified_claims", [])
        if c["id"] in spec_ids or (slug and re.search(r"(?:^|-)" + re.escape(slug) + r"(?:-|$)", c["id"]))
    ]
    if any(_DEDICATED_RE.search(_NO_DEDICATED_RE.sub("", t)) for t in texts):
        return "dedicated"
    if any(r.search(t) for t in texts for r in _STANDARD_OUTLET_RES):
        return "standard"
    return None


def find_power_violations(page, facts_pack):
    """No writer sentence may contradict the product's own outlet claim."""
    mode = _product_power_mode(facts_pack)
    if not mode:
        return []
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str):
            continue
        if mode == "dedicated":
            hit = any(r.search(node) for r in _STANDARD_OUTLET_RES)
            says = "needs a dedicated outlet or circuit"
        else:
            hit = any(r.search(_NEGATED_CLAUSE_RE.sub("", node)) for r in _DEDICATED_PAGE_RES)
            says = "runs on a standard outlet"
        if hit:
            problems.append(_problem(
                path, f"product-page:power:{path}",
                f"this product's own electrical claim says it {says}; this sentence says otherwise -- "
                "state the outlet exactly as the cited claim does, or drop the power detail",
                text=node,
            ))
    return problems


def find_stock_phrase_violations(page):
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str):
            continue
        for phrase, pattern in _STOCK_PHRASE_RES:
            if pattern.search(node):
                problems.append(_problem(
                    path, f"product-page:stock_phrase:{path}",
                    f"stock marketing phrase {phrase!r} -- say what the product is or does instead",
                    text=node,
                ))
                break
    return problems


def find_promise_violations(page, digit_exempt_terms=None):
    """hero.promise is a plain string with no claim_ids of its own. A promise
    that states a number, a price, a percentage or a trigger word must cite
    it in hero.claim_ids -- or be rewritten without it."""
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else {}
    promise = hero.get("promise") or ""
    reason = claims_mod._trigger_reason(promise, digit_exempt_terms) if promise else None
    if reason and not hero.get("claim_ids"):
        return [_problem(
            "$.hero.promise", "product-page:promise_claims",
            f"the promise line needs a claim_id ({reason}) -- put the verified claim_id in "
            "hero.claim_ids, or rewrite the promise without it",
            text=promise,
        )]
    return []


def find_renderer_owned_violations(page):
    problems = []
    for key in RENDERER_OWNED_KEYS:
        if key in page:
            problems.append(_problem(
                f"$.{key}", f"product-page:renderer_owned:{key}",
                f"{key!r} is built by the renderer from facts_pack, never written here -- "
                "remove it from page.json",
            ))
    return problems


def find_product_page_violations(page, facts_pack=None):
    """Every product-page structural check, combined. Wired into
    harness/repair.py's check_page_gates for the product-page cartridge."""
    if not isinstance(page, dict):
        return []
    digit_exempt_terms = (facts_pack or {}).get("digit_exempt_terms")
    problems = []
    problems += find_included_violations(page)
    problems += listicle.find_faq_violations(page, prefix="product-page", count_range=FAQ_COUNT_RANGE)
    problems += find_angle_paragraph_violations(page)
    problems += find_promise_violations(page, digit_exempt_terms)
    problems += find_power_violations(page, facts_pack)
    problems += find_stock_phrase_violations(page)
    problems += listicle.find_urgency_violations(page, prefix="product-page")
    problems += find_renderer_owned_violations(page)
    return problems
