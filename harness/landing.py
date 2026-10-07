"""Cycle 82: the landing cartridge -- the ad-first, mobile-first page for a
high-AOV product (cartridges/landing/).

Why it exists (owner, 2026-10-07): about 74% of the traffic is on a phone,
the average visit is 22 seconds and the scroll depth 13%, so the page must
answer the three buyer questions in the first two thumb-scrolls -- what is
it, what does it cost, what does the ad's claim really mean -- and then offer
one easy next step.

Two halves, the same split harness/pdp.py and harness/listicle.py use:

  - render_context: everything the page shows that did NOT come from the
    writer -- the price, the fixed financing and warranty sentences, the
    trust line (rating, free shipping, warranty term), the spec strip, the
    rating, the secondary "text us" link from tenant.yaml, and the first
    screen's eyebrow and accent span. Built from facts_pack, the ad brief
    and tenant.yaml alone, so the model never types a price, a spec, a
    rating or a phone number.
  - find_landing_violations: the writer-owned checks the repair loop runs
    (harness/repair.py's check_page_gates) -- the checked claims, the FAQ,
    the word limits, one CTA, the room photo, the quote, renderer-owned keys.

Tenant-neutral: any company, product, lender or phone value comes from
facts_pack or tenant.yaml.
"""
import json
import re
from urllib.parse import quote

from . import first_screen
from . import listicle
from . import pdp
from . import tenant as tenant_mod
from .prices import format_price
from .textutil import product_name_slug, walk_page

CHECKED_RANGE = (4, 6)
AD_SAID_MAX_WORDS = 14
FACT_MAX_WORDS = 30
FAQ_RANGE = (3, 5)
QUESTION_MAX_WORDS = 12
ANSWER_MAX_WORDS = 35
SUBHEAD_MAX_WORDS = 25
CLOSING_MAX_WORDS = 10
EYEBROW_MAX_WORDS = first_screen.EYEBROW_MAX_WORDS
SPEC_CELLS = 4

# An asset kind that shows the product in a real room or yard (photo
# library: lifestyle / people-in-use; brand/assets.json: installation). A
# studio render, a cut-out or a plain product shot never fills the slot.
ROOM_KINDS = ("lifestyle", "installation", "photo_install")

# The spec strip's order: the first SPEC_CELLS of facts_pack.specs (verified
# rows only) whose label matches, in this order. Labels are the tenant's own
# (facts_pack.specs[].label); a label that matches none is never shown here.
SPEC_PRIORITY = (
    ("capacity", re.compile(r"capacity|persons?|seats?", re.I)),
    ("max_temperature", re.compile(r"max(?:imum)?\s+temp", re.I)),
    ("infrared", re.compile(r"infrared|wavelength", re.I)),
    ("material", re.compile(r"material|wood|cabin", re.I)),
    ("electrical", re.compile(r"electric|power|outlet|volt", re.I)),
    ("dimensions", re.compile(r"dimension|footprint|size", re.I)),
)

# page.json keys the RENDERER owns (a writer that adds one is inventing data
# the gate cannot check -- same rule as pdp.RENDERER_OWNED_KEYS).
RENDERER_OWNED_KEYS = (
    "price", "price_line", "financing_line", "trust_line", "spec_strip", "specs", "rating", "rating_line",
    "secondary_cta", "sticky_cta", "warranty_line", "eyebrow_html",
)

# A second CTA: any link field but the one top-level cta_url/cta_text.
_LINK_KEYS = {"cta_url", "cta_text", "url", "href", "link", "button", "cta", "sms", "tel", "phone"}

_PROBLEM_PREFIX = "landing"


def _problem(path, key, issue, **extra):
    return {"path": path, "key": f"{_PROBLEM_PREFIX}:{key}", "issue": issue, **extra}


def _words(text):
    return len(str(text or "").split())


def _verified(facts_pack):
    return {c["id"]: c for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}


# ---------------------------------------------------------------------------
# Renderer-owned sections
# ---------------------------------------------------------------------------

def price(facts_pack):
    """{"text", "claim_ids"} from the product's own verified price claim
    (price-<model>, the claim the listicle value stack cites), or None. Never
    from page.json."""
    product = (facts_pack or {}).get("product") or {}
    price_id = f"price-{product_name_slug(product.get('name') or '')}"
    if price_id in _verified(facts_pack) and product.get("price"):
        return {"text": format_price(product["price"]), "claim_ids": [price_id]}
    return None


def warranty_term(facts_pack):
    """The trust line's short warranty term: the fixed warranty sentence's
    first clause ("Limited lifetime warranty"), with the warranty claim id."""
    line = pdp.warranty_line(facts_pack)
    if not line:
        return None
    term = re.split(r"[;.]", line["text"], maxsplit=1)[0].strip()
    return {"text": term, "claim_ids": line["claim_ids"]} if term else None


def trust_items(facts_pack):
    """Small-type trust line: rating + count, free shipping, warranty term --
    verified claims only, each with the ids that back it."""
    items = []
    rating = first_screen.rating_stat(facts_pack)
    if rating:
        count = rating["label"].split(", ", 1)[-1]
        items.append({"text": f"{rating['value']} ★ · {count}", "claim_ids": rating["claim_ids"]})
    ship = listicle.free_shipping_claim(facts_pack)
    if ship:
        items.append({"text": "Free shipping", "claim_ids": [ship["id"]]})
    term = warranty_term(facts_pack)
    if term:
        items.append(term)
    return items


def spec_strip(facts_pack, cells=SPEC_CELLS):
    """Up to `cells` {label, value, claim_ids} from facts_pack.specs (rows
    with a verified claim id only), in SPEC_PRIORITY order."""
    rows = pdp.spec_rows(facts_pack, limit=50)
    out, used = [], set()
    for _key, pattern in SPEC_PRIORITY:
        for i, row in enumerate(rows):
            if i in used or not pattern.search(row["label"]):
                continue
            out.append(row)
            used.add(i)
            break
        if len(out) == cells:
            break
    return out


def secondary_link(tenant, facts_pack):
    """{"text", "href"} from tenant.yaml `cartridges.landing.secondary_cta`,
    or None. `url` wins when given; else an sms: link built from `sms` (E.164)
    and `body` ({product_name} = the product's full name), in the
    `sms:<number>?&body=` form iOS and Android both read. A text link, never
    a button, and never a CTA (not in abtest.CTA_CLASSES)."""
    cfg = tenant.get("cartridges.landing.secondary_cta") if tenant else None
    if not isinstance(cfg, dict) or not str(cfg.get("text") or "").strip():
        return None
    text = str(cfg["text"]).strip()
    url = str(cfg.get("url") or "").strip()
    if url:
        return {"text": text, "href": url}
    number = re.sub(r"[^\d+]", "", str(cfg.get("sms") or ""))
    if not number:
        return None
    product = (facts_pack or {}).get("product") or {}
    name = tenant.product_names(product)["full_name"] or product.get("name") or ""
    body = str(cfg.get("body") or "").replace("{product_name}", name).strip()
    href = f"sms:{number}" + (f"?&body={quote(body, safe='')}" if body else "")
    return {"text": text, "href": href}


def room_asset_ok(asset):
    return bool(asset) and asset.get("kind") in ROOM_KINDS and not asset.get("ai_generated")


def render_context(page, facts_pack, *, ad_brief=None, tenant=None):
    """Everything the landing template renders that the writer did not
    write, plus the first screen's eyebrow and headline markup."""
    tenant = tenant or tenant_mod.active()
    hero = (page or {}).get("hero") or {}
    headline = str(hero.get("headline") or "")
    phrase = str(hero.get("accent_phrase") or "").strip()
    view = {
        "headline": headline,
        "accent_phrase": phrase if phrase and phrase in headline else first_screen.longform_accent(headline),
        "eyebrow": hero.get("eyebrow") or "",
    }
    ctx = {
        "eyebrow": first_screen.eyebrow(view, ad_brief),
        "headline_html": first_screen.headline_markup(view, accent_class="ld-accent"),
        "headline_fits": first_screen.fits_display(headline),
        "price": price(facts_pack),
        "financing": pdp.financing_sentence(facts_pack),
        "warranty": pdp.warranty_line(facts_pack),
        "trust": trust_items(facts_pack),
        "specs": spec_strip(facts_pack),
        "rating": first_screen.rating_stat(facts_pack),
        "secondary": secondary_link(tenant, facts_pack),
        "quote": first_screen.hero_quote(page, ad_brief, facts_pack),
        "room_kinds": ROOM_KINDS,
    }
    ctx["claim_ids"] = context_claim_ids(ctx)
    return ctx


def context_claim_ids(ctx):
    ids = set()
    for key in ("price", "warranty", "rating"):
        if ctx.get(key):
            ids.update(ctx[key].get("claim_ids") or [])
    for row in (ctx.get("trust") or []) + (ctx.get("specs") or []):
        ids.update(row.get("claim_ids") or [])
    return ids


# ---------------------------------------------------------------------------
# Writer prompt lines -- stated from the same constants the gate measures
# ---------------------------------------------------------------------------

def _room_candidates(facts_pack, tenant):
    """The facts_pack assets that may fill room_photo, with the photo
    library's own tags when the manifest is on this machine."""
    from . import photo_library

    try:
        manifest = photo_library.photos_by_id(photo_library.load_manifest(tenant))
    except Exception:  # no library for this tenant: kinds only
        manifest = {}
    out = []
    for asset in (facts_pack or {}).get("assets") or []:
        if not room_asset_ok(asset):
            continue
        entry = {"asset_id": asset["id"], "kind": asset.get("kind")}
        tags = (manifest.get(asset["id"]) or {}).get("tags") or {}
        for key in ("setting", "shot", "description"):
            if tags.get(key):
                entry[key] = tags[key]
        out.append(entry)
    return out[:8]


def writer_lines(facts_pack, tenant=None):
    tenant = tenant or tenant_mod.active()
    clo, chi = CHECKED_RANGE
    flo, fhi = FAQ_RANGE
    candidates = _room_candidates(facts_pack, tenant)
    room = (
        "room_photo.asset_id: one of these photos of the product in a real room or yard -- "
        + json.dumps(candidates) + " -- the one whose setting best fits the ad's buyer. Never a studio "
        "render, a cut-out or a plain product shot."
        if candidates else
        "room_photo: this run has no photo of the product in a real room -- omit room_photo entirely."
    )
    return [
        "This is a short landing page for a visitor who just saw the ad, mostly on a phone. Its job: "
        "say what the product is, what the ad's claims really mean, and offer one next step. The "
        "renderer shows the price, financing, warranty, rating, shipping and the spec strip from "
        "facts_pack -- never write a price, a spec value, a rating, a phone number or a link yourself.",
        f"hero.eyebrow: the ad's hook in at most {EYEBROW_MAX_WORDS} words (no number). hero.headline: "
        "6-12 words from the ad's angle; hero.accent_phrase: 1-4 words copied exactly from the headline "
        f"(shown in brand red). hero.subhead: the ad's promise in plain words, at most {SUBHEAD_MAX_WORDS} "
        "words. hero.hero_image.asset_id: the product photo to show when the ad has no usable still.",
        f"checked_claims has {clo}-{chi} items, each {{ad_said, fact, claim_ids}}: ad_said is what the "
        f"ad said or implied, at most {AD_SAID_MAX_WORDS} words; fact is the verified statement that "
        f"answers it, at most {FACT_MAX_WORDS} words; claim_ids has at least one id from "
        "facts_pack.verified_claims. At least 3 items cite a product spec or benefit claim (not price, "
        "shipping, warranty terms or returns). Cover the buyer's logistics where facts exist: the "
        "electrical requirement, the footprint/dimensions, delivery and shipping, assembly, the warranty.",
        "Power and outlet: state only what this product's own electrical claim says, in its own terms. "
        "Never say an electrician or a dedicated circuit is or is not needed unless that claim says so.",
        room,
        "proof.quote: only a sentence that appears word for word in facts_pack.reviews_summary, with "
        "its author and claim_ids; omit proof entirely when there is none. Never invent a quote.",
        f"faq.questions has {flo}-{fhi} entries from the ad's objections and the buyer's logistics: "
        f"each question at most {QUESTION_MAX_WORDS} words; each answer first, at most "
        f"{ANSWER_MAX_WORDS} words; any answer with a number, price, spec or trigger word carries claim_ids.",
        f"closing.headline: at most {CLOSING_MAX_WORDS} words. cta_text/cta_url are the page's ONE "
        "call to action -- never add a second link, button, url or phone number anywhere.",
    ]


# ---------------------------------------------------------------------------
# Gate: writer-owned structural checks
# ---------------------------------------------------------------------------

def find_checked_claims_violations(page, facts_pack):
    items = page.get("checked_claims")
    items = items if isinstance(items, list) else []
    lo, hi = CHECKED_RANGE
    problems = []
    if not lo <= len(items) <= hi:
        problems.append(_problem(
            "$.checked_claims", "checked_count",
            f"checked_claims has {len(items)} items; it needs {lo}-{hi}",
        ))
    verified = _verified(facts_pack)
    for i, item in enumerate(items):
        path = f"$.checked_claims[{i}]"
        if not isinstance(item, dict):
            problems.append(_problem(path, f"checked_shape:{i}", "each item is {ad_said, fact, claim_ids}"))
            continue
        if not str(item.get("ad_said") or "").strip() or not str(item.get("fact") or "").strip():
            problems.append(_problem(path, f"checked_shape:{i}", "each item needs both ad_said and fact"))
        if _words(item.get("ad_said")) > AD_SAID_MAX_WORDS:
            problems.append(_problem(f"{path}.ad_said", f"ad_said_words:{i}",
                                     f"ad_said is {_words(item.get('ad_said'))} words; at most {AD_SAID_MAX_WORDS}",
                                     text=item.get("ad_said")))
        if _words(item.get("fact")) > FACT_MAX_WORDS:
            problems.append(_problem(f"{path}.fact", f"fact_words:{i}",
                                     f"fact is {_words(item.get('fact'))} words; at most {FACT_MAX_WORDS}",
                                     text=item.get("fact")))
        ids = [c for c in (item.get("claim_ids") or []) if c]
        if not ids:
            problems.append(_problem(f"{path}.claim_ids", f"checked_claims_ids:{i}",
                                     "every checked claim cites at least one claim_id from facts_pack.verified_claims"))
        elif any(c not in verified for c in ids):
            problems.append(_problem(f"{path}.claim_ids", f"checked_claims_unverified:{i}",
                                     f"claim_ids {[c for c in ids if c not in verified]} are not verified claims"))
    return problems


def find_word_limit_violations(page):
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else {}
    closing = page.get("closing") if isinstance(page.get("closing"), dict) else {}
    checks = (
        ("$.hero.eyebrow", hero.get("eyebrow"), EYEBROW_MAX_WORDS, "eyebrow_words"),
        ("$.hero.subhead", hero.get("subhead"), SUBHEAD_MAX_WORDS, "subhead_words"),
        ("$.closing.headline", closing.get("headline"), CLOSING_MAX_WORDS, "closing_words"),
    )
    problems = []
    for path, text, limit, key in checks:
        if _words(text) > limit:
            problems.append(_problem(path, key, f"{_words(text)} words; at most {limit}", text=text))
    phrase = str(hero.get("accent_phrase") or "").strip()
    if phrase and phrase not in str(hero.get("headline") or ""):
        problems.append(_problem("$.hero.accent_phrase", "accent_phrase",
                                 "accent_phrase must be copied exactly from hero.headline", text=phrase))
    if not str(closing.get("headline") or "").strip():
        problems.append(_problem("$.closing.headline", "closing_headline", "closing.headline is required"))
    return problems


def find_faq_violations(page):
    problems = listicle.find_faq_violations(page, prefix=_PROBLEM_PREFIX, count_range=FAQ_RANGE)
    faq = page.get("faq")
    questions = faq.get("questions") if isinstance(faq, dict) else []
    for i, entry in enumerate(questions if isinstance(questions, list) else []):
        if not isinstance(entry, dict):
            continue
        if _words(entry.get("question")) > QUESTION_MAX_WORDS:
            problems.append(_problem(f"$.faq.questions[{i}].question", f"faq_question_words:{i}",
                                     f"question is {_words(entry.get('question'))} words; at most {QUESTION_MAX_WORDS}",
                                     text=entry.get("question")))
        if _words(entry.get("answer")) > ANSWER_MAX_WORDS:
            problems.append(_problem(f"$.faq.questions[{i}].answer", f"faq_answer_words:{i}",
                                     f"answer is {_words(entry.get('answer'))} words; at most {ANSWER_MAX_WORDS}",
                                     text=entry.get("answer")))
    return problems


def find_one_cta_violations(page):
    """One CTA: the top-level cta_text/cta_url only. Any other link-shaped
    key anywhere (a second cta_url, a url, a button, a phone field) fails."""
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        for key in node:
            if key in _LINK_KEYS and f"{path}.{key}" not in ("$.cta_url", "$.cta_text"):
                problems.append(_problem(
                    f"{path}.{key}", f"second_cta:{path}.{key}",
                    "a landing page has exactly one CTA (top-level cta_text/cta_url); the renderer adds "
                    "every other link -- remove this field",
                ))
    return problems


def find_room_photo_violations(page, facts_pack):
    room = page.get("room_photo")
    if room is None:
        return []
    asset_id = room.get("asset_id") if isinstance(room, dict) else None
    assets = {a.get("id"): a for a in (facts_pack or {}).get("assets") or [] if isinstance(a, dict)}
    if not asset_id or not room_asset_ok(assets.get(asset_id)):
        return [_problem("$.room_photo.asset_id", "room_photo",
                         f"room_photo must be a photo of the product in a real room (asset kind one of "
                         f"{list(ROOM_KINDS)}, not AI-generated) -- or omit room_photo")]
    return []


def _norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[\"“”‘’']", "", str(text or ""))).strip().lower()


def find_quote_violations(page, facts_pack):
    proof = page.get("proof") if isinstance(page.get("proof"), dict) else {}
    q = proof.get("quote")
    if not q:
        return []
    text = q.get("text") if isinstance(q, dict) else q
    source = _norm(json.dumps((facts_pack or {}).get("reviews_summary") or "", ensure_ascii=False))
    problems = []
    if not text or _norm(text) not in source:
        problems.append(_problem("$.proof.quote.text", "quote_verbatim",
                                 "proof.quote must appear word for word in facts_pack.reviews_summary -- "
                                 "or omit proof", text=text))
    if isinstance(q, dict) and not q.get("claim_ids"):
        problems.append(_problem("$.proof.quote.claim_ids", "quote_claims", "proof.quote needs claim_ids"))
    return problems


def find_renderer_owned_violations(page):
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        for key in node:
            if key in RENDERER_OWNED_KEYS:
                problems.append(_problem(
                    f"{path}.{key}", f"renderer_owned:{path}.{key}",
                    f"{key!r} is built by the renderer from facts_pack or tenant.yaml, never written here",
                ))
    return problems


_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")


def find_number_violations(page, facts_pack):
    """The first-screen and closing lines carry no claim_ids, so they carry
    no number, price or trigger word either (claims._trigger_reason, the
    shared gate's own test); and no field anywhere carries a phone number --
    the renderer builds the one secondary link from tenant.yaml."""
    from .claims import _trigger_reason

    exempt = (facts_pack or {}).get("digit_exempt_terms")
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else {}
    closing = page.get("closing") if isinstance(page.get("closing"), dict) else {}
    problems = []
    for path, text in (("$.hero.eyebrow", hero.get("eyebrow")), ("$.hero.headline", hero.get("headline")),
                       ("$.hero.subhead", hero.get("subhead")), ("$.closing.headline", closing.get("headline"))):
        reason = _trigger_reason(text, exempt) if isinstance(text, str) and text else None
        if reason:
            problems.append(_problem(path, f"number:{path}",
                                     f"this line has no claim_ids, so it cannot state {reason} -- the renderer "
                                     "shows the price and specs; rewrite it without the number", text=text))
    for path, node in walk_page(page):
        if isinstance(node, str) and _PHONE_RE.search(node):
            problems.append(_problem(path, f"phone:{path}", "no phone number in page.json -- the renderer adds "
                                     "the tenant's contact link", text=node))
    return problems


def find_power_violations(page, facts_pack):
    return [dict(p, key=p["key"].replace("product-page:", f"{_PROBLEM_PREFIX}:", 1))
            for p in pdp.find_power_violations(page, facts_pack)]


def find_landing_violations(page, facts_pack=None):
    """Every landing structural check, combined. Wired into
    harness/repair.py's check_page_gates for the landing cartridge. The
    product-benefit minimum is claims.find_benefit_claim_shortfall (via
    gate_page_json), as for longform."""
    if not isinstance(page, dict):
        return []
    problems = []
    problems += find_checked_claims_violations(page, facts_pack)
    problems += find_word_limit_violations(page)
    problems += find_faq_violations(page)
    problems += find_one_cta_violations(page)
    problems += find_room_photo_violations(page, facts_pack)
    problems += find_quote_violations(page, facts_pack)
    problems += find_renderer_owned_violations(page)
    problems += find_power_violations(page, facts_pack)
    problems += find_number_violations(page, facts_pack)
    problems += listicle.find_urgency_violations(page, prefix=_PROBLEM_PREFIX)
    return problems

