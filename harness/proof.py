"""Cycle 110: the proof and offer facts a pre-sell page is built on.

facts_pack contract (docs/GENERATOR.md; the landing renderer reads these and
falls back when one is absent):

    reviews_store: {text, rating, count, claim_ids}
    review_quotes: [{claim_id, text, author, date, stars, product}]
    financing.monthly: {text, amount, months, apr, disclosure, claim_ids}   (under product.financing)
    units: {last_12mo: {text, claim_ids}, all_time: {text, claim_ids} | null}
    included: [{text, claim_ids}]
    hsa_fsa: {text, claim_ids} | null
    support: {text, claim_ids}
    founder: {text, claim_ids}, us_owned: {text, claim_ids}

Every field is built from a verified claim (claims/verified.json, a claim
fetched live this run, or the units cache) and every backing claim joins
facts_pack.verified_claims, so the Sources list cites it and the gates accept
its words. Nothing is invented: a field whose claim is missing is left out.

Review quotes are verbatim. A review is quoted only when every rule in
quote_problems() passes; the text is never cut, mended or paraphrased.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from . import tenant as tenant_mod
from .prices import format_price
from .textutil import product_name_slug

QUOTES_MAX = 6
QUOTE_MIN_WORDS = 12
QUOTE_MAX_WORDS = 70
QUOTE_MIN_STARS = 4
QUOTE_FULL_WORDS = 20

# A review that talks about a health outcome is never quoted (the brand never
# makes one, so a customer's words may not make it on the brand's page).
HEALTH_RE = re.compile(
    r"\b(?:pain\w*|ache[sd]?|aching|achy|sore(?:ness)?|arthriti\w*|rheumat\w*|lupus|RA|fibromyalgia|apnea|"
    r"cur(?:e|es|ed|ing)|treat(?:s|ed|ing|ment|ments)?|heal(?:s|ed|ing|th|thy|thier)?|inflam\w*|diseases?|"
    r"symptoms?|diagnos\w*|doctors?|physicians?|medications?|meds|blood pressure|cholesterol|diabet\w*|"
    r"depress\w*|anxiety|anxious|insomnia|sleep\w*|detox\w*|toxins?|immun\w*|injur\w*|surgery|cancer|"
    r"weight|pounds|lbs|migraines?|headaches?|neuropath\w*|circulation|eczema|psoriasis|acne|skin|"
    r"recover\w*|muscles?|joints?|stress\w*|chronic|illness|sick|cold|flu|covid|therap(?:ist|ists))\b",
    re.I)
# Prices, discounts and codes belong to the verified offer, never to a quote.
MONEY_RE = re.compile(r"\$\s?\d|\d\s?%|\b\d+\s?(?:dollars|bucks)\b|\b(?:discount\w*|coupon\w*|promo\w*|"
                      r"code|sale|deal|financ\w*|affirm|klarna|afterpay)\b", re.I)
# A quote sells only when the whole review is a plain good word: no complaint,
# no hedge, no delay story (the review stays on the platform, just not here).
NEGATIVE_RE = re.compile(
    r"\b(?:unfortunately|however|although|though|except|disappoint\w*|problems?|issues?|wish\w*|"
    r"should(?:n't| not)? (?:be|have)|only thing|took (?:so|too) long|delay\w*|late|broken|damaged?|missing|"
    r"defect\w*|bad|poor\w*|worst|complain\w*|challeng\w*|frustrat\w*|returns?|returned|refund\w*|"
    r"cancel\w*|hassle|difficult|hard to|wait(?:ed|ing)?)\b", re.I)
# Any figure in a customer's words reads as a spec or a delivery promise the
# page has not verified ("warms up to 120 in 15 mins", "came 2 weeks after").
DIGIT_RE = re.compile(r"\d")
CONTACT_RE = re.compile(r"https?://|www\.|@\w|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b", re.I)
_NAME_RE = re.compile(r"^([A-Za-z][A-Za-z'\-]+)\s+([A-Za-z])")


def author_label(name):
    """"susan l." -> "Susan L."; None when the name has no last initial
    ("Anonymous", "Steve", "MY")."""
    name = str(name or "").strip()
    m = _NAME_RE.match(name)
    if not m or name.lower().startswith("anonymous"):
        return None
    first = m.group(1)
    return first[0].upper() + first[1:].lower() + " " + m.group(2).upper() + "."


def _banned_terms():
    from . import vocab

    v = vocab.active()
    terms = list(v.emf_terms) + list(v.banned_names) + list(v.forbidden_lender_names) + list(v.hype_words)
    terms += list(v.competitor_aliases)
    return [t for t in terms if t]


def quote_problems(review, banned_terms=()):
    """[] when the review may be quoted, else the reasons it may not."""
    body = review.get("body") or ""
    problems = []
    if not review.get("verified"):
        problems.append("not a verified buyer")
    if review.get("ai_media"):
        problems.append("marked as machine-made media")
    if int(review.get("stars") or 0) < QUOTE_MIN_STARS:
        problems.append("under 4 stars")
    words = len(body.split())
    if words < QUOTE_MIN_WORDS:
        problems.append("under 12 words")
    if words > QUOTE_MAX_WORDS:
        problems.append(f"over {QUOTE_MAX_WORDS} words")
    if not author_label(review.get("author")):
        problems.append("no first name and last initial")
    if HEALTH_RE.search(body):
        problems.append("health outcome")
    if MONEY_RE.search(body):
        problems.append("price, discount or financing")
    if CONTACT_RE.search(body):
        problems.append("link or contact detail")
    if NEGATIVE_RE.search(body):
        problems.append("complaint or hedge")
    if DIGIT_RE.search(body):
        problems.append("a figure")
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", body)
    if tokens and sum(1 for t in tokens if t[0].isupper()) > len(tokens) / 2:
        problems.append("not a sentence (a title or a list)")
    low = body.lower()
    for term in banned_terms:
        if re.search(r"(?<![a-z])" + re.escape(term.lower()) + r"(?![a-z])", low):
            problems.append(f"banned term {term!r}")
    return problems


def _product_label(review, products_by_path):
    path = str(review.get("product_url") or "").split("?")[0]
    path = path[path.find("/products/"):] if "/products/" in path else path
    product = products_by_path.get(path)
    if product:
        return tenant_mod.active().product_names(product)["full_name"]
    return tenant_mod.active().display_product_name(review.get("product_title") or "") or None


def select_quotes(product_reviews, store_reviews, *, products=(), limit=QUOTES_MAX, banned_terms=None):
    """The quotable reviews: this product's own, then the store's other
    reviews; within each, the ones of QUOTE_FULL_WORDS words or more first,
    newest first. Returns (picked, skipped_counts)."""
    banned = _banned_terms() if banned_terms is None else banned_terms
    by_path = {}
    for p in products:
        url = str(p.get("url") or "")
        if "/products/" in url:
            by_path[url[url.find("/products/"):]] = p
    picked, seen, skipped = [], set(), {}
    for group in (product_reviews, store_reviews):
        ranked = sorted(group, key=lambda r: (len((r.get("body") or "").split()) >= QUOTE_FULL_WORDS,
                                              r.get("date") or ""), reverse=True)
        for r in ranked:
            if len(picked) >= limit:
                break
            key = r.get("uuid") or (r.get("author"), r.get("body"))
            if key in seen:
                continue
            seen.add(key)
            problems = quote_problems(r, banned)
            if problems:
                for reason in problems:
                    skipped[reason] = skipped.get(reason, 0) + 1
                continue
            picked.append({**r, "author_label": author_label(r["author"]),
                           "product_label": _product_label(r, by_path)})
    return picked, skipped


def quote_claims(picked, today_iso, *, tenant=None):
    """One verified claim per picked review: id review-quote-<n>, the
    verbatim text, sourced to the review platform."""
    from .sources import judgeme

    tenant = tenant or tenant_mod.active()
    platform = judgeme.platform_name(tenant)
    url = judgeme.store_url(tenant)
    claims = []
    for n, r in enumerate(picked, 1):
        about = f", {r['product_label']}" if r.get("product_label") else ""
        claims.append({
            "id": f"review-quote-{n}",
            "text": r["body"],
            "category": "review",
            "source": f"{platform} verified-buyer review {r.get('uuid') or ''} by {r['author_label']}, "
                      f"{r['date']}, {r['stars']} stars{about} ({url})",
            "approved_by": "live-fetch",
            "date": today_iso,
            "quote": {"author": r["author_label"], "date": r["date"], "stars": r["stars"],
                      "product": r.get("product_label")},
        })
    return claims


# ---------------------------------------------------------------------------
# financing per model (claims/config.json financing_monthly)
# ---------------------------------------------------------------------------

def _money(value):
    return Decimal(str(value).replace("$", "").replace(",", ""))


def _cents(value):
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${q:,.2f}"


def financing_claim(product, price, config, today_iso):
    """The verified claim financing-monthly-<model> for one product at
    `price`, built from claims/config.json's financing_monthly (the live
    product page's own financing line and disclosure), or None when the
    tenant has none, the lender is unset, or the price is under the
    lender's minimum purchase."""
    spec = (config or {}).get("financing_monthly") or {}
    lender = (config or {}).get("financing_lender")
    if not spec or not lender or price in (None, ""):
        return None
    price = _money(price)
    if spec.get("min_purchase") is not None and price < _money(spec["min_purchase"]):
        return None
    months = int(spec["months"])
    monthly = _cents(price / months)
    fill = {"monthly": monthly, "price": format_price(price), "months": months, "apr": spec["apr"],
            "lender": lender}
    model = product_name_slug(product["name"])
    return {
        "id": f"financing-monthly-{model}",
        "text": spec["claim_template"].format(**fill),
        "category": "price",
        "source": spec["source"].format(url=product.get("url") or ""),
        "approved_by": spec.get("approved_by") or "site",
        "date": today_iso,
        "monthly": {"text": spec["line_template"].format(**fill), "amount": monthly, "months": months,
                    "apr": spec["apr"], "disclosure": spec["disclosure_template"].format(**fill)},
    }


# ---------------------------------------------------------------------------
# the facts_pack fields
# ---------------------------------------------------------------------------

def _entry(claim):
    return {k: claim[k] for k in ("id", "text", "category", "source")}


def _join(pack, claims):
    have = {c["id"] for c in pack["verified_claims"]}
    for c in claims:
        if c["id"] not in have:
            pack["verified_claims"].append(_entry(c))
            have.add(c["id"])


def _first(by_id, ids, model):
    for cid in ids:
        claim = by_id.get(cid.format(model=model))
        if claim:
            return claim
    return None


def enrich(pack, product, verified, *, config=None, live_claims=(), price=None, today_iso=None, tenant=None):
    """Adds the cycle 110 fields to `pack` in place (see the module
    docstring). `verified` is claims/verified.json (plus this run's page
    claims); `live_claims` this run's fetched claims (reviews-store-live,
    review-quote-<n>, units-*). Returns the pack."""
    tenant = tenant or tenant_mod.active()
    cfg = tenant.get("proof") or {}
    model = product_name_slug(product["name"])
    by_id = {c["id"]: c for c in verified}
    by_id.update({c["id"]: c for c in live_claims or ()})
    joined = []

    store = by_id.get("reviews-store-live")
    if store:
        pack["reviews_store"] = {"text": store["text"], "rating": store.get("rating"), "count": store.get("count"),
                                 "claim_ids": [store["id"]]}
        joined.append(store)

    quotes = sorted((c for c in live_claims or () if c["id"].startswith("review-quote-")),
                    key=lambda c: int(c["id"].rsplit("-", 1)[1]))
    if quotes:
        pack["review_quotes"] = [{"claim_id": c["id"], "text": c["text"], **c["quote"]} for c in quotes]
        joined.extend(quotes)

    fin = financing_claim(product, price if price is not None else product.get("price"), config, today_iso)
    if fin:
        financing = pack["product"].setdefault("financing", {"available": True})
        financing["monthly"] = {**fin["monthly"], "claim_ids": [fin["id"]]}
        joined.append(fin)

    last, total = by_id.get("units-delivered-12mo"), by_id.get("units-delivered-all-time")
    if last:
        pack["units"] = {"last_12mo": {"text": last["text"], "claim_ids": [last["id"]]},
                         "all_time": {"text": total["text"], "claim_ids": [total["id"]]} if total else None}
        joined.extend(c for c in (last, total) if c)

    for field in ("hsa_fsa", "support", "founder", "us_owned"):
        spec = cfg.get(field)
        if not spec:
            continue
        claim = _first(by_id, spec.get("claims") or (), model)
        if claim:
            pack[field] = {"text": spec.get("text") or claim["text"], "claim_ids": [claim["id"]]}
            joined.append(claim)
        elif field == "hsa_fsa":
            pack[field] = None

    included = []
    for item in cfg.get("included") or ():
        claim = _first(by_id, item.get("claims") or (), model)
        if not claim:
            continue
        also = [c for c in (_first(by_id, [cid], model) for cid in item.get("also") or ()) if c]
        included.append({"text": item["text"], "claim_ids": [claim["id"]] + [c["id"] for c in also]})
        joined.extend([claim] + also)
    if included:
        pack["included"] = included

    _join(pack, joined)
    return pack


# ---------------------------------------------------------------------------
# the run's live claims (pipeline.ground)
# ---------------------------------------------------------------------------

def live_claims(tenant, product, *, today_iso, live_products=(), reviews=True, fetch=None, log=None):
    """This run's fetched claims: reviews-store-live and review-quote-<n>
    (when `reviews`), plus the cached unit claims. Never raises for a
    network failure; a claim that cannot be read is simply absent (and the
    log says so)."""
    from . import units as units_mod
    from .sources import judgeme

    claims = []
    if reviews:
        kwargs = {"fetch": fetch or judgeme.http_fetch}  # looked up per call (tests swap it)
        store = judgeme.fetch_store_claim(today_iso, log=log, tenant=tenant, **kwargs)
        if store:
            claims.append(store)
        product_id = next((p.get("id") for p in live_products or () if p.get("handle") == product.get("slug")),
                          None)
        product_reviews, store_reviews = judgeme.fetch_reviews(product_id, log=log, tenant=tenant, **kwargs)
        picked, skipped = select_quotes(product_reviews, store_reviews, products=tenant.catalog_products(),
                                        limit=int(tenant.get("reviews.quotes_max") or QUOTES_MAX))
        claims.extend(quote_claims(picked, today_iso, tenant=tenant))
        if log is not None:
            why = ", ".join(f"{k} {v}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
            log.event("reviews", f"{len(picked)} review quote(s) from {len(product_reviews)} product and "
                                 f"{len(store_reviews)} store review(s)" + (f"; not quoted: {why}" if why else ""))
    claims.extend(units_mod.load_claims(tenant))
    return claims
