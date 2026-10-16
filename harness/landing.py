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
from . import looks as looks_mod
from . import offer as offer_mod
from . import pdp
from . import landing_sell
from . import persuasion
from . import quote_fidelity
from . import tenant as tenant_mod
from .prices import format_price
from .textutil import product_name_slug, walk_page

CHECKED_RANGE = (4, 6)
AD_SAID_MAX_WORDS = 18   # cycle 82c: room for a quoted ad sentence
FACT_MAX_WORDS = 36   # cycle 82b: a full logistics fact (shipping) is ~35 words
FAQ_RANGE = (3, 5)
QUESTION_MAX_WORDS = 12
ANSWER_MAX_WORDS = 35
SUBHEAD_MAX_WORDS = 30
CLOSING_MAX_WORDS = 10
EYEBROW_MAX_WORDS = 8
SPEC_CELLS = 4

# Cycle 85: the `proof` look (cartridges/landing/looks/proof/) -- six ideas
# from a Shopify product-page post the owner shared, 2026-10-07, in the
# harness's terms: the rating first, a verified quote under the subhead, the
# trust facts under the CTA, checkmark benefits, the room photo after them,
# an icon row before the closing band. Its two writer fields are optional
# in schema.json; their gates run only when a page carries them.
BENEFITS_RANGE = (4, 6)
BENEFIT_MAX_WORDS = 8
ICON_RANGE = (3, 4)
ICON_LABEL_MAX_WORDS = 3
PROOF_TRUST_MAX = 3
RETURNS_TERM_MAX_WORDS = 6

# The line icons the proof look ships, and what each stands for (told to
# the writer). The template's own `icons` map draws exactly these keys
# (tests/test_landing_proof_cycle85.py checks the two agree).
ICONS = {
    "shipping": "free shipping",
    "warranty": "the warranty",
    "red_light": "red light therapy",
    "full_spectrum": "full-spectrum infrared heat",
    "wood": "the cabin's wood",
    "us_owned": "a US-owned company",
    "delivery": "the delivery appointment",
    "assembly": "assembly",
}

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


_EDGE_PUNCT = "\"'\u201c\u201d\u2018\u2019.,;:!?()[]-\u2013\u2014 "


def accent_span(headline, phrase):
    """The headline's own text for `phrase`, matched case-insensitively and
    ignoring punctuation around the phrase and runs of whitespace; "" when
    the headline does not contain it. Cycle 82b: a phrase that does not
    match is a layout miss, not a gate failure."""
    words = str(phrase or "").strip(_EDGE_PUNCT).split()
    if not words or not headline:
        return ""
    m = re.search(r"\s+".join(re.escape(w) for w in words), headline, re.IGNORECASE)
    return m.group(0) if m else ""


def returns_term(facts_pack, tenant=None):
    """Cycle 85: the returns policy's first clause, verbatim, with its claim
    id -- only when that clause is a short trust fact (at most
    RETURNS_TERM_MAX_WORDS words, e.g. "30-day returns"). A long clause (a
    returns procedure, not a promise) is never cut down into one."""
    tenant = tenant or tenant_mod.active()
    cid = tenant.get("returns_claim_id") or "returns-policy"
    claim = _verified(facts_pack).get(cid)
    if not claim:
        return None
    first = re.split(r"[;,]|\.(?:\s|$)", str(claim.get("text") or ""), maxsplit=1)[0].strip()
    if not first or _words(first) > RETURNS_TERM_MAX_WORDS:
        return None
    return {"text": first, "claim_ids": [cid]}


def proof_trust_items(facts_pack, tenant=None):
    """Cycle 85: the proof look's trust line under the CTA -- free shipping,
    the warranty term and the returns term, each only when a verified claim
    states it. The financing sentence stays verbatim in the price block just
    above (vocab.yaml: the fixed sentence is the only way financing may be
    stated), and nothing says "secure payment" or a guarantee no claim states."""
    items = []
    ship = listicle.free_shipping_claim(facts_pack)
    if ship:
        items.append({"text": "Free shipping", "claim_ids": [ship["id"]]})
    for item in (warranty_term(facts_pack), returns_term(facts_pack, tenant)):
        if item:
            items.append(item)
    return items[:PROOF_TRUST_MAX]


def _ids_verified(ids, verified):
    ids = [c for c in (ids or []) if c]
    return bool(ids) and all(c in verified for c in ids)


def proof_blocks(page, facts_pack):
    """The proof look's writer blocks as the template shows them: benefit
    rows and icon cells whose claim ids all verify (and known icon keys),
    and the review quote only when review_quote() passes it. The
    gate already fails anything else; this keeps a hand-edited page.json
    from showing it."""
    verified = _verified(facts_pack)
    benefits = [
        {"text": str(r["text"]).strip(), "claim_ids": list(r["claim_ids"])}
        for r in (page.get("benefits") or []) if isinstance(r, dict)
        and str(r.get("text") or "").strip() and _ids_verified(r.get("claim_ids"), verified)
    ][:BENEFITS_RANGE[1]]
    icons = [
        {"icon_key": c["icon_key"], "label": str(c["label"]).strip(), "claim_ids": list(c["claim_ids"])}
        for c in (page.get("icon_row") or []) if isinstance(c, dict) and c.get("icon_key") in ICONS
        and str(c.get("label") or "").strip() and _ids_verified(c.get("claim_ids"), verified)
    ][:ICON_RANGE[1]]
    return {"benefits": benefits, "icon_row": icons, "review_quote": review_quote(page, facts_pack)}


def review_quote(page, facts_pack):
    """proof.quote as the template shows it: only a customer review the gate
    passes (verbatim in facts_pack.review_quotes, never the rating sentence),
    else None. Both looks render this, not page.proof.quote (cycle 82e)."""
    proof = page.get("proof") if isinstance(page.get("proof"), dict) else {}
    q = proof.get("quote") if isinstance(proof.get("quote"), dict) else None
    bad = {"landing:quote_verbatim", "landing:quote_not_review"}
    ok = q and not any(p["key"] in bad for p in find_quote_violations(page, facts_pack))
    return q if ok else None


def headline_view(page):
    """The landing headline as first_screen's helpers read it: {"headline",
    "accent_phrase" (the span the renderer colours), "eyebrow", "phrase"
    (the writer's own accent_phrase)}. Cycle 94: shared by the look's first
    screen and every hero composition (harness/heroes/)."""
    hero = (page or {}).get("hero") or {}
    headline = str(hero.get("headline") or "")
    phrase = str(hero.get("accent_phrase") or "").strip()
    span = accent_span(headline, phrase)
    return {
        "headline": headline,
        # no phrase given: the longform rule picks one; a phrase that is not
        # in the headline: no accent at all (render.py logs it)
        "accent_phrase": span if phrase else first_screen.longform_accent(headline),
        "eyebrow": hero.get("eyebrow") or "",
        "phrase": phrase,
    }


def allowed_cta_texts(schema, facts_pack, ad_brief=None, tenant=None):
    """Cycle 110: schema.json `allowed_cta_texts` is a map by the ad's angle
    (persuasion.ad_angle): sale -> "Get {offer_amount} off the {short_name}"
    / "Shop the sale"; financing -> "See monthly payments"; story -> "See the
    {short_name} {pronoun} uses" (only with an evidenced pronoun); default ->
    "Shop the {short_name}" / "See the {short_name} price"."""
    from . import first_screen

    tenant = tenant or tenant_mod.active()
    fp = facts_pack or {}
    name = (fp.get("product") or {}).get("name") or ""
    angle = persuasion.ad_angle(ad_brief, fp)
    view = offer_mod.offer_view(fp)
    pronoun = first_screen.speaker_pronoun(ad_brief=ad_brief or {}, tenant=tenant) if angle == "story" else None
    return persuasion.cta_texts_for_angle(schema.get("allowed_cta_texts") or [], angle, short_name=name,
                                          offer_amount=(view or {}).get("amount"),
                                          pronoun=pronoun if pronoun in ("she", "he") else None)


def sell_context(page, facts_pack, *, ad_brief=None, tenant=None, base=None):
    """Cycle 110: the persuasion pieces every look may print (all renderer-
    owned, from facts_pack; see harness/landing_sell.py for each fallback)."""
    fp = facts_pack or {}
    base = base or {}
    pr = base.get("price")
    store = landing_sell.store_rating(fp)
    fm = landing_sell.financing_monthly(fp)
    sale = landing_sell.sale_prices(fp, pr)
    dl = landing_sell.deadline(fp)
    unit = landing_sell.units(fp)
    view = base.get("offer")
    bar_price = (sale or {}).get("net") or (pr or {}).get("text")
    term = warranty_term(fp)
    strip = []
    if store:
        strip.append({"value": f"{store['value']} ★", "label": f"{store['count']} reviews" if store["count"] else
                      "rating", "claim_ids": store["claim_ids"]})
    if unit:
        strip.append({"value": unit["value"], "label": unit["label"], "claim_ids": unit["claim_ids"]})
    ship = listicle.free_shipping_claim(fp)
    if ship:
        strip.append({"value": "Free", "label": "shipping", "claim_ids": [ship["id"]]})
    if term:
        m = re.match(r"(\d+)-year", term["text"])
        strip.append({"value": f"{m.group(1)}-year" if m else term["text"], "label": "warranty" if m else "",
                      "claim_ids": term["claim_ids"]})
    if len(strip) < 4:
        for row in spec_strip(fp):
            if len(strip) == 4:
                break
            strip.append({"value": row["value"], "label": row["label"].lower(), "claim_ids": row["claim_ids"]})
    return {
        "angle": persuasion.ad_angle(ad_brief, fp),
        "store_rating": store,
        "product_rating": landing_sell.product_rating(fp),
        "units": unit,
        "monthly": fm,
        "sale": sale,
        "deadline": dl,
        # one line at 375px: the sale, the amount (the code is in the price line), the last day
        "offer_bar": (" · ".join(x for x in ((view or {}).get("name"),
                                                 (f"{view['amount']} off" if view.get("amount") else view.get("line")),
                                                 (f"ends {dl['short'].split('ends ', 1)[-1]}") if dl else None) if x)
                      if view else ""),
        "stats": strip[:4],
        "reasons": landing_sell.reasons_view(page, fp, pr),
        "included": landing_sell.included(fp, page, tenant),
        "hsa": landing_sell.hsa_line(fp),
        "quotes": landing_sell.review_quotes(fp),
        "trust_block": landing_sell.trust_block(fp, tenant),
        "delivery": landing_sell.delivery_setup(fp),
        "bar": {"price": bar_price, "was": (sale or {}).get("list"), "monthly": (fm or {}).get("short"),
                "code": (view or {}).get("bar") if view and not sale else ((sale or {}).get("code") and
                                                                          f"Code {sale['code']}")},
    }


def sell_claim_ids(sell):
    ids = set()
    for key in ("store_rating", "product_rating", "units", "monthly", "sale", "deadline", "hsa"):
        if sell.get(key):
            ids |= set(sell[key].get("claim_ids") or [])
    for key in ("stats", "reasons", "included", "quotes", "trust_block", "delivery"):
        for row in sell.get(key) or []:
            ids |= set(row.get("claim_ids") or [])
            if isinstance(row.get("proof"), dict):
                ids |= set(row["proof"].get("claim_ids") or [])
    return ids


def render_context(page, facts_pack, *, ad_brief=None, tenant=None, look=None):
    """Everything the landing template renders that the writer did not
    write, plus the first screen's eyebrow and headline markup. Cycle 85:
    look "proof" adds its trust line and checked writer blocks."""
    tenant = tenant or tenant_mod.active()
    view = headline_view(page)
    headline, phrase, span = view["headline"], view["phrase"], view["accent_phrase"]
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
        # Cycle 110 (teardown D9): "Text us" moved from the hero, beside the
        # only button, to the closing band (`closing_link`, data-el="sms").
        "secondary": None,
        "closing_link": secondary_link(tenant, facts_pack),
        "quote": first_screen.hero_quote(page, ad_brief, facts_pack),
        "review_quote": review_quote(page or {}, facts_pack),
        "room_kinds": ROOM_KINDS,
        "accent_note": (f"accent_phrase {phrase!r} is not in the headline; rendered with no accent"
                        if phrase and not span else ""),
    }
    if look == "proof":
        ctx["proof_trust"] = proof_trust_items(facts_pack, tenant)
        ctx.update(proof_blocks(page or {}, facts_pack))
    # Cycle 109: an ad about a verified offer gets an offer-led page: the sale
    # name is the eyebrow, and the price block, the first CTA, the sticky bar
    # and the first checked claim carry the offer (harness/offer.py).
    ctx["offer"] = offer_mod.offer_view(facts_pack)
    flags = looks_mod.sell_flags("landing", look, tenant) if look else {}
    if ctx["offer"] and ctx["offer"]["name"] and not flags.get("offer_bar"):
        ctx["eyebrow"] = ctx["offer"]["name"]
    # Cycle 110: the persuasion pieces (reasons, proof, offer stack, sale
    # prices, monthly figure); a pre-sell look (look.json "sell") prints them.
    ctx["sell"] = sell_context(page or {}, facts_pack, ad_brief=ad_brief, tenant=tenant, base=ctx)
    ctx["sell_flags"] = flags
    ctx["claim_ids"] = context_claim_ids(ctx) | sell_claim_ids(ctx["sell"])
    return ctx


def tidy_for_render(page, facts_pack):
    """Cycle 110 phase 0: what a page already written may not show, removed
    at render time (so `harness rerender` fixes a live page with no model
    call) -- an item of checked_claims/reasons that cites the verified offer
    claim (the renderer adds the offer itself) or carries placeholder text,
    and a FAQ entry about returns, with placeholder text, or saying a coded
    offer applies automatically. In place; returns the notes to log."""
    if not isinstance(page, dict):
        return []
    fp = facts_pack or {}
    view = offer_mod.offer_view(fp)
    claim_id = view["claim_id"] if view else None
    auto = persuasion.offer_has_code(fp) or bool(fp.get("offer"))
    notes = []

    def strings(node):
        return [v for _p, v in walk_page(node, skip_keys=persuasion.NON_PROSE_KEYS) if isinstance(v, str)]

    for section in ("checked_claims", "reasons"):
        items = page.get(section)
        if not isinstance(items, list):
            continue
        kept = []
        offer_in_ad = bool(fp.get("offer") or fp.get("offer_unverified"))
        for i, item in enumerate(items):
            if isinstance(item, dict) and claim_id and claim_id in (item.get("claim_ids") or []):
                notes.append(f"dropped {section}[{i}]: it cites the offer claim (the renderer adds the offer)")
            elif isinstance(item, dict) and any(persuasion.PLACEHOLDER_RE.search(t) for t in strings(item)):
                notes.append(f"dropped {section}[{i}]: placeholder text")
            elif section == "reasons" and offer_in_ad and isinstance(item, dict) and (
                    not str(item.get("headline") or "").strip() or not str(item.get("body") or "").strip()):
                notes.append(f"dropped reasons[{i}]: no {'headline' if not str(item.get('headline') or '').strip() else 'body'} "
                             "(an offer placeholder)")
            elif section == "reasons" and offer_in_ad and isinstance(item, dict) and any(
                    offer_mod._OFFER_WORDS_RE.search(t) for t in strings(item)):
                notes.append(f"dropped reasons[{i}]: the offer (the renderer adds it)")
            else:
                kept.append(item)
        if len(kept) != len(items):
            page[section] = kept
    faq = page.get("faq") if isinstance(page.get("faq"), dict) else None
    questions = faq.get("questions") if faq else None
    if isinstance(questions, list):
        kept = []
        for i, entry in enumerate(questions):
            texts = strings(entry) if isinstance(entry, dict) else []
            if any(persuasion.RETURNS_RE.search(t) for t in texts):
                notes.append(f"dropped faq.questions[{i}]: about returns")
            elif any(persuasion.PLACEHOLDER_RE.search(t) for t in texts):
                notes.append(f"dropped faq.questions[{i}]: placeholder text")
            elif auto and any(persuasion.says_automatic_offer(t) for t in texts):
                notes.append(f"dropped faq.questions[{i}]: says the offer applies automatically")
            else:
                kept.append(entry)
        if len(kept) != len(questions):
            faq["questions"] = kept
    return notes


def template_page(page, ctx):
    """Cycle 109: the page as the look renders it -- on an offer-led page the
    offer is the first checked claim (the ad's sentence, the verified claim's
    text). page.json itself is unchanged; every look (shipped or imported)
    loops page.checked_claims, so each one gets the item."""
    offer = (ctx or {}).get("offer")
    if not isinstance(page, dict):
        return page
    if isinstance(page.get("reasons"), list):
        # Cycle 110: the reasons as the look prints them -- the offer first.
        return dict(page, reasons=list(((ctx or {}).get("sell") or {}).get("reasons") or []))
    items = [c for c in (page.get("checked_claims") or []) if isinstance(c, dict)]
    if ((ctx or {}).get("sell_flags") or {}).get("facts_only_legacy"):
        # a page written before cycle 110 in a pre-sell look: its verified
        # facts only, never a line that grades the ad ("not lifetime coverage")
        return dict(page, checked_claims=[c for c in items if c.get("fact") and not
                                          persuasion.AD_REFERENCE_RE.search(str(c["fact"]))])
    if not offer:
        return page
    return dict(page, checked_claims=[offer["check"]] + items)


def context_claim_ids(ctx):
    ids = set()
    for key in ("price", "warranty", "rating"):
        if ctx.get(key):
            ids.update(ctx[key].get("claim_ids") or [])
    if ctx.get("offer"):
        ids.add(ctx["offer"]["claim_id"])
    for row in (ctx.get("trust") or []) + (ctx.get("specs") or []) + (ctx.get("proof_trust") or []):
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


def proof_writer_lines(facts_pack, tenant=None):
    """Cycle 85: what the writer is told when the page renders in the proof
    look -- stated from the constants find_benefits_violations and
    find_icon_row_violations measure."""
    tenant = tenant or tenant_mod.active()
    blo, bhi = BENEFITS_RANGE
    ilo, ihi = ICON_RANGE
    have = _verified(facts_pack)
    allow = [cid for cid in (tenant.get("benefit_allowlist_ids") or ()) if cid in have]
    return [
        "This page renders in the PROOF look: also write `benefits` and `icon_row` (below). Their words "
        "count toward the page's word range, so keep every other section tight.",
        f"benefits has {blo}-{bhi} rows, each {{text, claim_ids}}: text is a plain noun phrase of at most "
        f"{BENEFIT_MAX_WORDS} words (no verb, no full stop), e.g. \"Canadian red cedar, inside and out\"; "
        "claim_ids cite the claim that states it, and at least one id per row is a product spec or benefit "
        "claim (not price, shipping, warranty terms or returns) or one of the tenant's benefit allowlist ids "
        + json.dumps(allow) + ". The renderer draws a check mark before each row.",
        f"icon_row has {ilo}-{ihi} cells, each {{icon_key, label, claim_ids}}: icon_key is one of these keys, "
        "chosen by what the cell's claim states -- " + json.dumps(ICONS) + f" -- label at most "
        f"{ICON_LABEL_MAX_WORDS} words, claim_ids from facts_pack.verified_claims that state it. No two cells "
        "share an icon_key; never pick an icon the verified claims do not back.",
    ] + (["On this look proof.quote sits directly under the subhead: the same rule, word for word from "
          "facts_pack.review_quotes or omitted."] if review_quote_texts(facts_pack) else [])


NO_REVIEW_QUOTES_LINE = ("There are no customer review quotes in the facts pack: do not write proof.quote; "
                         "the rating row is shown by the renderer.")


def quote_writer_line(facts_pack):
    """Cycle 82f: with no review_quotes, one explicit "do not write" line;
    else the quotes themselves (verbatim, with their ids) and the rule."""
    quotes = [{"id": q.get("id") or f"review_quotes[{i}]", "text": str(q["text"]),
               "author": q.get("author") or q.get("attribution")}
              for i, q in enumerate((facts_pack or {}).get("review_quotes") or [])
              if isinstance(q, dict) and q.get("text")]
    if not quotes:
        return NO_REVIEW_QUOTES_LINE
    return ("proof.quote (optional): at most one customer's review, copied word for word from one of these "
            "facts_pack.review_quotes -- " + json.dumps(quotes, ensure_ascii=False) + " -- with its author "
            "and claim_ids. The rating sentence (facts_pack.reviews_summary, \"Rated ... out of 5 ...\") is "
            "never a quote -- the renderer already shows it. Never invent a quote.")


def checked_range(facts_pack, *, writer=False):
    """CHECKED_RANGE, or on an offer-led page (cycle 109b) the writer's own
    3-5 items (the renderer adds the offer before them) and a gate that
    accepts 3-6."""
    if offer_mod.offer_view(facts_pack) is None:
        return CHECKED_RANGE
    return offer_mod.WRITER_CHECKED_RANGE if writer else offer_mod.GATE_CHECKED_RANGE


def name_words(facts_pack, tenant=None):
    """The brand and product names, which never count as shared words with
    the hook and never as hard words for the reading level."""
    tenant = tenant or tenant_mod.active()
    product = (facts_pack or {}).get("product") or {}
    names = {str(product.get("name") or ""), str(getattr(tenant, "display_name", "") or "")}
    try:
        names.add(tenant.product_names(product)["full_name"] or "")
    except Exception:  # noqa: BLE001 -- a bare test tenant
        pass
    return tuple(sorted((n for n in names if n), key=len, reverse=True))


def _exclude_words(facts_pack, tenant=None):
    words = set()
    for n in name_words(facts_pack, tenant):
        words |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", n)}
    return words


def token_line(facts_pack):
    values = landing_sell.token_values(facts_pack, price(facts_pack))
    if not values:
        return ("This page has no figure tokens: write the first screen in words; the renderer shows the price "
                "under it.")
    shown = ", ".join(f"{{{k}}} = {v['text']}" for k, v in values.items())
    return ("FIGURES IN THE FIRST SCREEN: hero.eyebrow, hero.headline, hero.subhead and closing.headline may carry "
            "a figure only as a token the renderer fills from the verified claim -- " + shown + ". Write the words "
            "around them, e.g. \"{offer_amount} off the <model>, today's price ends {deadline_day}\" or \"Rated "
            "{rating} by {review_count} owners\". Never type a digit, price or date there yourself; never a token "
            "anywhere else.")


def offer_writer_lines(facts_pack):
    """Cycle 110: the offer-led page in the reasons model -- the renderer
    adds the sale (net price or offer line, code, deadline) as the first
    reason, under the price, in the CTA note and the sticky bar."""
    fp = facts_pack or {}
    if fp.get("offer_unverified"):
        return [offer_mod.UNVERIFIED_LINE.replace("checked_claims", "reasons")]
    view = offer_mod.offer_view(fp)
    if view is None:
        return []
    dl = landing_sell.deadline(fp)
    return [
        f"This page is OFFER-LED (verified claim {view['claim_id']}: " + json.dumps(fp["offer"]["text"]) + "). "
        f"THE RENDERER ADDS from that claim: the sale name \"{view['name'] or ''}\" in the offer bar, the offer "
        f"line \"{view['line']}\" under the price" + (f", \"{dl['short']}\"" if dl else "") + ", the CTA note, "
        "the sticky bar, and the sale as the FIRST reason. Your reasons are about the product; no reason, FAQ "
        "entry or placeholder about the sale, a discount, a code or a deadline, and never that there is none "
        "(\"no discount\", \"full price\", \"not on sale\" are refused).",
        "hero.headline states the saving (and the deadline when the ad leads with it) with the figure tokens -- "
        "\"{offer_amount} off the <model>, today's price ends {deadline_day}\" -- or names the sale in words. "
        "The offer needs its code at checkout: never say it applies automatically. No countdown, no \"last "
        "chance\", no \"hurry\", no invented scarcity.",
    ] + deadline_wording_lines(fp)


def deadline_wording_lines(facts_pack):
    """Cycle 110 round 4 (run zt6o): the two deadline wordings the urgency
    gate lets through, verbatim, and the phrases that fail it."""
    allowed = offer_mod.allowed_deadline_sentences(facts_pack)
    if not allowed:
        return []
    return [
        "DEADLINE WORDING: the page may state the sale's deadline in exactly two ways, word for word: the "
        f"verified claim's own sentence {json.dumps(allowed[0])}, or the headline token form "
        "\"today's price ends {deadline_day}\". Every other deadline or urgency phrase fails the gate, e.g. "
        "\"before prices go up\", \"today only\", \"last chance\", \"hurry\", \"don't miss\".",
    ]


def writer_lines(facts_pack, tenant=None, look=None, ad_brief=None):
    tenant = tenant or tenant_mod.active()
    # cycle 110 integration F1: the quote frame names the speaker's pronoun only from evidence
    pronoun = first_screen.evidence_pronoun(ad_brief, None, tenant) if ad_brief else None
    frame = f"In the ad, {pronoun} says" if pronoun else "In the ad, the creator says"
    frame_rule = "" if pronoun else (" (or the speaker's name when the ad gives it); nothing shows the "
                                     "speaker's pronoun, so never she/he")
    lo, hi = landing_sell.writer_reasons_range(facts_pack)
    flo, fhi = FAQ_RANGE
    refs = sorted(landing_sell.proof_ref_ids(facts_pack))
    hsa = landing_sell.hsa_line(facts_pack)
    lines = [
        "This is a pre-sell page for a visitor who just clicked the ad, mostly on a phone. Its job: pay off the "
        "ad's promise in the ad's own words, prove it will work for this reader, show the deal, and send the click "
        "to the product page. The renderer shows the price, the offer, financing, the rating, customer reviews, "
        "what is included, delivery and setup and the spec strip from facts_pack -- never write a price, a figure, "
        "a rating, a phone number or a link yourself.",
        "VOICE: write to the reader's outcome first (recovery, sleep, the evening ritual, the deal, the founder's "
        "note), then the spec that makes it true. Grade 5-7 reading level: sentences under 14 words, everyday "
        "words of one or two syllables. Never quote, paraphrase, grade or correct the ad -- never \"the ad\", "
        "\"actually\", \"real price\", \"plainly\", \"not verified\", \"not lifetime\", \"no separate\", "
        "\"nothing to submit\", \"no form required\", \"what holds up\". An ad claim the facts do not support "
        "is simply left out. The one way to name the ad is to credit the speaker's own words: "
        f"\"{frame}, ...\"{frame_rule}.",
        f"hero.headline (6-12 words) restates ad_brief.hook as closely as the verified facts allow: same words, "
        f"same number, same person. hero.eyebrow (at most {EYEBROW_MAX_WORDS} words) names who the page is for, "
        f"or the sale. hero.subhead (at most 20 words is best, never over {SUBHEAD_MAX_WORDS}): the outcome and the "
        "time frame; for a testimonial ad (speaker_pov first_person) the speaker's strongest line from "
        f"transcript_or_text, word for word in quotes, credited \"{frame}\"{frame_rule}. "
        "hero.accent_phrase: 1-4 words copied from the headline. The first screen shares at least two content "
        "words with the hook, angle and promise, or quotes the speaker (gate landing:hook_match); the HEADLINE "
        "itself repeats at least one word of the hook or promise. An ad that sells paying over time gets a "
        "headline in its own words about payments (with {monthly} when this page has it). "
        "hero.hero_image.asset_id: the product photo to show when the ad has no usable still.",
        token_line(facts_pack),
        f"reasons has {lo}-{hi} items, each {{headline, body, claim_ids, lever, proof_ref, asset_role}}: headline "
        f"at most {landing_sell.REASON_HEADLINE_MAX} words, the outcome first (\"Heat that reaches your whole "
        f"body\"); body at most {landing_sell.REASON_BODY_MAX} words -- one sentence on how it works, one with "
        "the spec that makes it true; claim_ids from facts_pack.verified_claims, and across the reasons at least 3 "
        "product spec or benefit claims. lever is one of outcome (the ad's promise), likelihood (proof it works "
        "for this reader), time (how fast -- ships in days, heats quickly, short sessions, only what a verified "
        "claim states), effort (how little effort -- assembly, the outlet, app control); cover "
        f"{min(4, hi)} different levers. proof_ref (optional) is the claim_id of one review quote, the store "
        f"rating or the units line that backs that reason: {json.dumps(refs) if refs else 'none on this page, omit it'}. "
        "asset_role (optional) is the photo shown beside it: people, room, detail or exterior.",
        "Power and outlet: state only what this product's own electrical claim says, in its own terms. "
        "Never say an electrician or a dedicated circuit is or is not needed unless that claim says so.",
        quote_writer_line(facts_pack),
        f"faq.questions has {flo}-{fhi} questions a buyer asks before spending this much: will I use it, how long "
        "is a session or how fast does it heat (only with a verified claim), will it fit (the dimensions claim), "
        "can I pay over time (the fixed financing sentence)"
        + (f", is it HSA/FSA eligible (answer with the claim's own words: {json.dumps(hsa['text'])})" if hsa else
           "; no HSA/FSA question (none verified)")
        + ". At most one logistics question (delivery, assembly, power, warranty): the renderer shows a Delivery "
        "and setup row. Never a question about returns, refunds or a trial. Each question at most "
        f"{QUESTION_MAX_WORDS} words; answer first, at most {ANSWER_MAX_WORDS} words; any answer with a number, "
        "price, spec or trigger word carries claim_ids.",
        f"closing.headline: at most {CLOSING_MAX_WORDS} words, the outcome or the offer in one line (the renderer "
        "adds the price, the deadline and the CTA under it). cta_text/cta_url are the page's ONE call to action "
        "(the renderer repeats it down the page) -- never add a second link, button, url or phone number anywhere.",
        "room_photo, checked_claims and ad_said are retired: omit them (the renderer picks every photo).",
    ]
    lines += (proof_writer_lines(facts_pack, tenant) if look == "proof" else
              ["benefits and icon_row are for the proof look only: omit both."])
    if persuasion.unverified_offer(ad_brief, facts_pack or {}):   # cycle 110 hotfix H2
        lines.append("The ad's offer is not verified; do not state it; lead with the ad's promise instead.")
    return lines + offer_writer_lines(facts_pack)


# ---------------------------------------------------------------------------
# Gate: writer-owned structural checks
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?][\"'\u201d\u2019)]*(?=\s|$)")
_CLAUSE_BREAK_RE = re.compile(r"\s*(?:;|\s--\s|\s\u2014\s|\u2014)\s*")


def trim_to_limit(text, limit):
    """`text` cut to at most `limit` words at its last sentence end that
    fits, else at its last clause break (";", " -- ", an em dash) that fits,
    closed with a full stop -- never mid-word and never mid-clause. None when
    no such cut exists, or the text did not end with terminal punctuation in
    the first place. Cycle 82b: the landing page's last-resort fix for a
    subhead or fact a few words over its limit."""
    text = str(text or "").strip()
    if len(text.split()) <= limit:
        return text
    if not re.search(r"[.!?][\"'\u201d\u2019)]*$", text):
        return None
    best = None
    for m in _SENTENCE_END_RE.finditer(text):
        cut = text[: m.end()].strip()
        if cut != text and len(cut.split()) <= limit:
            best = cut
    if best:
        return best
    for m in _CLAUSE_BREAK_RE.finditer(text):
        cut = text[: m.start()].rstrip(" ,")
        if cut and len(cut.split()) <= limit:
            best = cut + "."
    return best


# gate key prefix -> (page path pattern, limit) the last-resort trim may cut
_TRIMMABLE = {
    "landing:subhead_words": SUBHEAD_MAX_WORDS,
    "landing:fact_words": FACT_MAX_WORDS,
    "landing:ad_said_words": AD_SAID_MAX_WORDS,
}


def trim_word_limit_violation(page, item):
    """Applies trim_to_limit to the field a landing:subhead_words,
    landing:fact_words or landing:ad_said_words failure names. Returns (path, old, new) or None."""
    key = str(item.get("key") or "")
    limit = next((v for k, v in _TRIMMABLE.items() if key == k or key.startswith(k + ":")), None)
    path = item.get("path") or ""
    if limit is None:
        return None
    m = re.match(r"^\$\.(?:hero\.subhead|checked_claims\[(\d+)\]\.(fact|ad_said))$", path)
    if not m:
        return None
    if m.group(1) is None:
        node, field = page.get("hero"), "subhead"
    else:
        items = page.get("checked_claims") or []
        i = int(m.group(1))
        node, field = (items[i] if i < len(items) else None), m.group(2)
    if not isinstance(node, dict) or not isinstance(node.get(field), str):
        return None
    new = trim_to_limit(node[field], limit)
    if not new or new == node[field]:
        return None
    old, node[field] = node[field], new
    return path, old, new


# Cycle 82c: ad_said quotes the ad, and the harness cannot verify a promo:
# no digit, currency symbol, percent sign or code-like all-caps token.
_AD_SAID_FIGURE_RE = re.compile(r"[0-9$\u20ac\u00a3\u00a5%]|\b[A-Z0-9]{5,}\b")
AD_SAID_FIGURES_LINE = (
    "checked_claims[].ad_said never states a price, discount, percentage, figure or code: paraphrase a "
    "price or promo claim without figures (\"The ad said: a fall sale with money off every model\") or "
    "pick another ad claim."
)


def find_checked_claims_violations(page, facts_pack):
    items = page.get("checked_claims")
    items = items if isinstance(items, list) else []
    lo, hi = checked_range(facts_pack)
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
        said = str(item.get("ad_said") or "")
        if _AD_SAID_FIGURE_RE.search(said):
            problems.append(_problem(f"{path}.ad_said", f"ad_said_figures:{i}",
                                     "ad_said states a figure, price, discount or code the page cannot verify -- "
                                     + AD_SAID_FIGURES_LINE, text=said))
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


# Cycle 82e: proof.quote is one customer's own review, word for word from
# facts_pack.review_quotes (the listicle pull quote's source). The rating
# sentence ("Rated 4.8 out of 5 across 49 reviews on <site> (fetched ...)")
# is never a quote, wherever it appears. No facts source fills review_quotes
# yet, so today the quote is omitted and the rating row stays alone.
_RATING_SENTENCE_RE = re.compile(
    r"\bout\s+of\s+5\b|\breviews?\s+on\b|\bfetched\b|\brated\s+\d|\b\d(?:\.\d+)?\s*(?:/\s*5|stars?)\b",
    re.IGNORECASE)


def review_quote_texts(facts_pack):
    return [str(q["text"]) for q in (facts_pack or {}).get("review_quotes") or []
            if isinstance(q, dict) and q.get("text")]


def _is_rating_sentence(text, facts_pack):
    fp = facts_pack or {}
    ratings = [(fp.get("reviews_summary") or {}).get("text")] + [
        c.get("text") for c in fp.get("verified_claims") or [] if c.get("id") == "reviews-live"]
    return bool(_RATING_SENTENCE_RE.search(text)) or any(r and _norm(text) in _norm(r) for r in ratings)


def find_quote_violations(page, facts_pack):
    proof = page.get("proof") if isinstance(page.get("proof"), dict) else {}
    q = proof.get("quote")
    if not q:
        return []
    text = q.get("text") if isinstance(q, dict) else q
    problems = []
    if text and _is_rating_sentence(str(text), facts_pack):
        problems.append(_problem("$.proof.quote.text", "quote_not_review",
                                 "proof.quote is the rating sentence, not a customer's review -- the renderer "
                                 "already shows the rating; omit proof.quote unless facts_pack.review_quotes "
                                 "has a review to copy", text=text))
    elif not text or not any(_norm(text) in _norm(t) for t in review_quote_texts(facts_pack)):
        problems.append(_problem("$.proof.quote.text", "quote_verbatim",
                                 "proof.quote must appear word for word in one of facts_pack.review_quotes -- "
                                 "or omit proof", text=text))
    if isinstance(q, dict) and not q.get("claim_ids"):
        problems.append(_problem("$.proof.quote.claim_ids", "quote_claims", "proof.quote needs claim_ids"))
    return problems


def drop_unverifiable_quote(page, facts_pack):
    """Cycle 82f: a proof.quote the harness cannot verify (no review_quotes,
    not word for word in one of them, or the rating sentence) is dropped
    in place -- never a gate failure that costs a repair attempt. The rest
    of `proof` stays. True when it dropped one."""
    proof = page.get("proof") if isinstance(page, dict) else None
    if not isinstance(proof, dict) or not proof.get("quote"):
        return False
    bad = {"landing:quote_verbatim", "landing:quote_not_review"}
    if not any(p["key"] in bad for p in find_quote_violations(page, facts_pack)):
        return False
    del proof["quote"]
    return True


def _benefit_claim_ids(facts_pack, tenant=None):
    """Verified ids a benefit row may lean on: the product's spec/benefit
    claims (claims._is_benefit_claim_id, the rule behind the "at least 3
    product-benefit claims" gates) plus the tenant's benefit allowlist."""
    from .claims import _is_benefit_claim_id

    tenant = tenant or tenant_mod.active()
    verified = _verified(facts_pack)
    allow = set(tenant.get("benefit_allowlist_ids") or ())
    return {cid for cid in verified if cid in allow or _is_benefit_claim_id(cid, verified)}


def find_benefits_violations(page, facts_pack, tenant=None):
    """Cycle 85: the proof look's checkmark rows. No-op when page.json has
    no `benefits` (the open look)."""
    if "benefits" not in page:
        return []
    rows = page.get("benefits") if isinstance(page.get("benefits"), list) else []
    lo, hi = BENEFITS_RANGE
    problems = []
    if not lo <= len(rows) <= hi:
        problems.append(_problem("$.benefits", "benefits_count", f"benefits has {len(rows)} rows; it needs {lo}-{hi}"))
    verified = _verified(facts_pack)
    allowed = _benefit_claim_ids(facts_pack, tenant)
    for i, row in enumerate(rows):
        path = f"$.benefits[{i}]"
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            problems.append(_problem(path, f"benefit_shape:{i}", "each benefit is {text, claim_ids} with a text"))
            continue
        if _words(row["text"]) > BENEFIT_MAX_WORDS:
            problems.append(_problem(f"{path}.text", f"benefit_words:{i}",
                                     f"benefit is {_words(row['text'])} words; at most {BENEFIT_MAX_WORDS}",
                                     text=row["text"]))
        ids = [c for c in (row.get("claim_ids") or []) if c]
        if not ids:
            problems.append(_problem(f"{path}.claim_ids", f"benefit_claims:{i}",
                                     "every benefit row cites at least one claim_id"))
        elif any(c not in verified for c in ids):
            problems.append(_problem(f"{path}.claim_ids", f"benefit_claims_unverified:{i}",
                                     f"claim_ids {[c for c in ids if c not in verified]} are not verified claims"))
        elif not set(ids) & allowed:
            problems.append(_problem(f"{path}.claim_ids", f"benefit_not_product:{i}",
                                     "a benefit row cites a product spec or benefit claim or a benefit allowlist "
                                     "id -- not only price, shipping, warranty terms or returns"))
    return problems


def find_icon_row_violations(page, facts_pack):
    """Cycle 85: the proof look's icon cells. No-op when page.json has no
    `icon_row`."""
    if "icon_row" not in page:
        return []
    cells = page.get("icon_row") if isinstance(page.get("icon_row"), list) else []
    lo, hi = ICON_RANGE
    problems = []
    if not lo <= len(cells) <= hi:
        problems.append(_problem("$.icon_row", "icon_count", f"icon_row has {len(cells)} cells; it needs {lo}-{hi}"))
    verified = _verified(facts_pack)
    seen = set()
    for i, cell in enumerate(cells):
        path = f"$.icon_row[{i}]"
        if not isinstance(cell, dict):
            problems.append(_problem(path, f"icon_shape:{i}", "each cell is {icon_key, label, claim_ids}"))
            continue
        key = cell.get("icon_key")
        if key not in ICONS:
            problems.append(_problem(f"{path}.icon_key", f"icon_key:{i}",
                                     f"icon_key {key!r} is not one of {list(ICONS)}"))
        elif key in seen:
            problems.append(_problem(f"{path}.icon_key", f"icon_duplicate:{i}", f"icon_key {key!r} is used twice"))
        seen.add(key)
        label = str(cell.get("label") or "").strip()
        if not label or _words(label) > ICON_LABEL_MAX_WORDS:
            problems.append(_problem(f"{path}.label", f"icon_label_words:{i}",
                                     f"an icon label is 1-{ICON_LABEL_MAX_WORDS} words", text=label))
        ids = [c for c in (cell.get("claim_ids") or []) if c]
        if not ids:
            problems.append(_problem(f"{path}.claim_ids", f"icon_claims:{i}",
                                     "every icon label cites at least one claim_id"))
        elif any(c not in verified for c in ids):
            problems.append(_problem(f"{path}.claim_ids", f"icon_claims_unverified:{i}",
                                     f"claim_ids {[c for c in ids if c not in verified]} are not verified claims"))
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


_AD_SAID_PATH_RE = re.compile(r"^\$\.checked_claims\[\d+\]\.ad_said$")

CONTRADICTED_AD_CLAIM_LINE = (
    "Never put an ad claim the facts contradict anywhere on the page: leave it out (cycle 110: the page never "
    "quotes or corrects the ad)."
)


def find_power_violations(page, facts_pack):
    """pdp's power rule (no sentence may contradict the product's own
    electrical claim), except checked_claims[].ad_said: it quotes the ad, and
    is exactly where an ad's wrong outlet claim belongs so `fact` can correct
    it (cycle 82b). Cycle 82e: for a dedicated-circuit product the issue
    ends with the one fix to make, quoting the claim that states it."""
    problems = []
    claim = pdp.dedicated_claim_text(facts_pack)
    fix = f" Say 'dedicated circuit' in the same sentence, for example: '{claim}'" if claim else ""
    for p in pdp.find_power_violations(page, facts_pack):
        if _AD_SAID_PATH_RE.match(p["path"]):
            continue
        problems.append(dict(
            p, key=p["key"].replace("product-page:", f"{_PROBLEM_PREFIX}:", 1),
            issue=p["issue"] + ". The page never repeats an ad claim the verified facts contradict: state the "
            "product's own electrical claim instead." + fix,
        ))
    return problems


def filled_hero(page, facts_pack):
    """The first-screen lines with the figure tokens filled (what the reader
    sees), for the hook-match gate."""
    hero = dict(page.get("hero") or {}) if isinstance(page.get("hero"), dict) else {}
    values = landing_sell.token_values(facts_pack, price(facts_pack))
    for key in ("eyebrow", "headline", "subhead"):
        if isinstance(hero.get(key), str):
            hero[key] = landing_sell.fill_tokens(hero[key], values)[0]
    return hero


def find_persuasion_violations(page, facts_pack=None, ad_brief=None, tenant=None):
    """Cycle 110: the reasons, the figure tokens, the ad-reference ban, the
    hook match, the reading level and the buyer FAQ (brief section 3.3)."""
    from .claims import safe_quote_candidates

    problems = landing_sell.find_reasons_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    problems += landing_sell.find_token_violations(page, facts_pack, price(facts_pack), prefix=_PROBLEM_PREFIX)
    problems += persuasion.find_ad_reference_violations(page, prefix=_PROBLEM_PREFIX)
    if ad_brief:
        quotes = [q["text"] for q in safe_quote_candidates(ad_brief, facts_pack or {})]
        problems += persuasion.find_hook_match_violations(
            page, ad_brief, facts_pack=facts_pack or {}, filled_hero=filled_hero(page, facts_pack),
            exclude=_exclude_words(facts_pack, tenant),
            quotes=quotes, prefix=_PROBLEM_PREFIX)
    problems += persuasion.find_reading_level_violations(page, exclude=name_words(facts_pack, tenant),
                                                         prefix=_PROBLEM_PREFIX)
    problems += persuasion.find_faq_rule_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    return problems


def quality(page, facts_pack, ad_brief, html, *, tenant=None):
    """Cycle 110: the copy-side quality numbers of a rendered landing page
    (runstate.record_quality): hook_match (share of the hook's content words
    the first screen restates, 0-1), grade_level (Flesch-Kincaid of the
    writer's copy), cta_count (CTA placements carrying data-el, the sticky bar
    included, "Text us" not), proof_count (rating, units line, review quotes,
    the ad speaker's quote, trust items the page prints)."""
    from .claims import safe_quote_candidates

    hero = filled_hero(page, facts_pack)
    text = " ".join(str(hero.get(k) or "") for k in ("eyebrow", "headline", "subhead"))
    quotes = [q["text"] for q in safe_quote_candidates(ad_brief or {}, facts_pack or {})] if ad_brief else []
    match = persuasion.hook_match(text, ad_brief or {}, exclude=_exclude_words(facts_pack, tenant), quotes=quotes,
                                  headline=str(hero.get("headline") or ""))
    els = re.findall(r'data-el="(\w+)"', html or "")
    proof = 0
    proof += 1 if re.search(r'class="pf-proof"|class="ld-stars"|ld-rating|bk-stat_strip', html or "") else 0
    proof += 1 if "pf-proof" in (html or "") and " delivered" in (html or "") else 0
    proof += len(re.findall(r'class="pk-quote"', html or ""))
    proof += len(re.findall(r'class="pk-founder"', html or ""))
    return {"hook_match": 1.0 if match["quoted"] else match["score"],
            "grade_level": persuasion.grade_level(persuasion.reading_text(page, name_words(facts_pack, tenant))),
            "cta_count": len([e for e in els if e != "sms"]),
            "proof_count": proof}


HERO_TEXT_FIELDS = ("eyebrow", "headline", "subhead")


def find_speaker_pronoun_violations(page, ad_brief, tenant=None):
    """landing:speaker_pronoun -- the listicle rule (first_screen.
    find_speaker_pronoun_violations) on hero.eyebrow/headline/subhead: a
    quote frame says she/he only when the evidence (ad_brief.speaker_pronoun
    or the tenant override) says so; no evidence -> "the creator"."""
    hero = (page or {}).get("hero") or {}
    fields = {k: hero[k] for k in HERO_TEXT_FIELDS if isinstance(hero.get(k), str)}
    evidence = {"speaker_pronoun": first_screen.evidence_pronoun(ad_brief, None, tenant)}
    out = []
    for item in first_screen.find_speaker_pronoun_violations(fields, evidence):
        path = "$.hero." + item["path"][2:]
        out.append(_problem(path, f"speaker_pronoun:{path}", item["issue"], text=item.get("text")))
    return out


def find_landing_violations(page, facts_pack=None, ad_brief=None, tenant=None):
    """Every landing structural check, combined. Wired into
    harness/repair.py's check_page_gates for the landing cartridge. The
    product-benefit minimum is claims.find_benefit_claim_shortfall (via
    gate_page_json), as for longform. Cycle 110: `reasons` replaces the
    checked claims (find_persuasion_violations)."""
    if not isinstance(page, dict):
        return []
    problems = []
    problems += find_persuasion_violations(page, facts_pack, ad_brief=ad_brief, tenant=tenant)
    problems += find_word_limit_violations(page)
    problems += find_faq_violations(page)
    problems += find_one_cta_violations(page)
    problems += find_room_photo_violations(page, facts_pack)
    problems += find_quote_violations(page, facts_pack)
    problems += find_benefits_violations(page, facts_pack)
    problems += find_icon_row_violations(page, facts_pack)
    problems += find_renderer_owned_violations(page)
    problems += find_power_violations(page, facts_pack)
    problems += find_number_violations(page, facts_pack)
    problems += listicle.find_urgency_violations(page, prefix=_PROBLEM_PREFIX,
                                                 allowed_deadlines=offer_mod.allowed_deadline_sentences(facts_pack))
    # Cycle 109: an offer-led page's headline is about the offer; no page
    # denies an offer the ad states (verified or not).
    problems += offer_mod.find_offer_headline_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    problems += offer_mod.find_denial_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    problems += offer_mod.find_offer_in_checked_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    # Cycle 110 phase 0: placeholder text, returns copy, "applies
    # automatically" next to a coded offer, a blank first-screen line.
    problems += persuasion.find_copy_hygiene_violations(page, facts_pack, prefix=_PROBLEM_PREFIX)
    problems += persuasion.find_hero_required_violations(page, prefix=_PROBLEM_PREFIX)
    if ad_brief:
        problems += find_speaker_pronoun_violations(page, ad_brief, tenant)
        hero = page.get("hero") or {}
        problems += quote_fidelity.find_unquoted_first_screen(
            [(f"$.hero.{k}", hero.get(k)) for k in HERO_TEXT_FIELDS], ad_brief, facts_pack, _PROBLEM_PREFIX)
    return problems

