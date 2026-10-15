"""Cycle 110: the landing page's persuasion pieces -- the writer's `reasons`
(the Value Equation: outcome, likelihood, time, effort) and everything the
renderer adds from the facts_pack contract (BRIEF.md section 3.2):

  reviews_store      {text, rating, count, claim_ids}
  review_quotes      [{claim_id, text, author, date, stars, product}]
  financing.monthly  {text, amount, months, apr, disclosure, claim_ids}
  units              {last_12mo: {text, claim_ids}, all_time: {...} | None}
  included           [{text, claim_ids}]
  hsa_fsa            {text, claim_ids} | None
  offer.net_price, offer.list_price, offer.code, offer.deadline_short
  support            {text, claim_ids}

Every field has a fallback when the grounding step has not written it yet
(store rating -> the product rating; no monthly figure -> the price alone; no
quotes -> no review wall; no units -> no units line; no net price -> the
list price and the offer line as cycle 109 prints them). Nothing here
composes a claim: every line is a verified claim's own text or a fixed
label over a figure taken from that claim, shown only when every claim_id
behind it is in facts_pack.verified_claims.

Hero tokens: the writer may put {offer_amount}, {offer_code}, {net_price},
{price}, {monthly}, {deadline_day}, {deadline_date}, {rating}, {review_count}
in the first-screen lines and the closing headline; the renderer fills them
from the same verified claims (so the writer never types a figure and the
ad's own number can be the headline: "{offer_amount} off the <model>, today's
price ends {deadline_day}").

Tenant-neutral: names, figures and claim ids come from facts_pack and
tenant.yaml.
"""
import datetime
import re

from . import offer as offer_mod
from .prices import format_price
from .textutil import product_name_slug

REASONS_RANGE = (3, 5)
WRITER_REASONS_OFFER = (3, 4)   # offer-led: the renderer adds the offer as reason 1
REASON_HEADLINE_MAX = 10
REASON_BODY_MAX = 45
LEVERS = ("outcome", "likelihood", "time", "effort")
# asset_role -> the photo library shots (harness/images.py SHOTS) a reason's photo is picked from
ASSET_ROLES = {
    "people": ["lifestyle-person"],
    "room": ["room"],
    "detail": ["detail-macro"],
    "exterior": ["exterior", "hero-wide"],
}
LEVER_ROLE = {"outcome": "people", "likelihood": "room", "time": "detail", "effort": "exterior"}
QUOTE_WALL = 3
QUOTE_MAX_WORDS = 70
MODEL_CARDS = 3

TOKENS = ("offer_amount", "offer_code", "net_price", "price", "monthly", "deadline_day", "deadline_date",
          "rating", "review_count")
TOKEN_FIELDS = ("$.hero.eyebrow", "$.hero.headline", "$.hero.accent_phrase", "$.hero.subhead",
                "$.closing.headline")
_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def verified_ids(facts_pack):
    return {c["id"] for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}


def claims_by_id(facts_pack):
    return {c["id"]: c for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}


def _ok(node, have):
    ids = [c for c in ((node or {}).get("claim_ids") or []) if c]
    return bool(ids) and all(c in have for c in ids)


def _dollars(value):
    """"$458.33" for 458.33, "458.33", "$458.33" (None for nothing usable)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return format_price(value)
    text = str(value).strip()
    m = re.search(r"\$?\s?([\d,]+(?:\.\d+)?)", text)
    if not m:
        return None
    return format_price(float(m.group(1).replace(",", "")))


# ---------------------------------------------------------------------------
# Offer, prices, deadline
# ---------------------------------------------------------------------------

def deadline(facts_pack):
    """The offer's last day from the verified claim: {"day": "Thursday",
    "date": "Oct 15", "short": "Today's price ends Thu, Oct 15", "iso"} or
    None. "prices go up on Oct 16" -> the sale's last day is Oct 15;
    "ends Oct 20" -> Oct 20. offer_view's own `deadline_short` (grounding,
    agent A) wins for "short" when present."""
    fp = facts_pack or {}
    view = offer_mod.offer_view(fp)
    if view is None:
        return None
    when, raises = offer_mod._claim_date((fp.get("offer") or {}).get("text"), fp.get("offer"))
    if when is None:
        return None
    last = when - datetime.timedelta(days=1) if raises else when
    short = view.get("deadline_short") or (fp.get("offer") or {}).get("deadline_short") or \
        f"Today's price ends {last.strftime('%a')}, {last.strftime('%b')} {last.day}"
    short = short[0].upper() + short[1:]
    return {"day": last.strftime("%A"), "date": f"{last.strftime('%b')} {last.day}", "short": short,
            "iso": last.isoformat(), "claim_ids": [view["claim_id"]]}


def sale_prices(facts_pack, price):
    """{"list", "net", "code", "line", "claim_ids"} when the offer carries a
    net price (offer_view / facts_pack.offer `net_price`, grounding), else
    None: no net price -> nothing is computed here."""
    fp = facts_pack or {}
    view = offer_mod.offer_view(fp)
    if view is None:
        return None
    raw = fp.get("offer") or {}
    net = view.get("net_price") or raw.get("net_price")
    if not net:
        return None
    lst = view.get("list_price") or raw.get("list_price") or (price or {}).get("text")
    code = view.get("code") or raw.get("code")
    ids = [view["claim_id"]] + list((price or {}).get("claim_ids") or [])
    return {"list": _dollars(lst), "net": _dollars(net), "code": code,
            "line": f"with code {code}" if code else "", "claim_ids": ids}


def financing_monthly(facts_pack):
    """{"line": "$458.33/mo for 18 months at 0% APR*", "disclosure",
    "claim_ids", "amount"} from facts_pack financing.monthly (owner-approved
    wording, 2026-10-15), only with its disclosure sentence; else None."""
    fp = facts_pack or {}
    fm = (fp.get("financing") or {}).get("monthly") if isinstance(fp.get("financing"), dict) else None
    fm = fm or ((fp.get("product") or {}).get("financing") or {}).get("monthly")
    if not isinstance(fm, dict) or not fm.get("disclosure") or not _ok(fm, verified_ids(fp)):
        return None
    amount = _dollars(fm.get("amount"))
    if not amount:
        return None
    months, apr = fm.get("months"), fm.get("apr")
    apr_text = (f"{apr}" if str(apr).endswith("%") else f"{apr}%") if apr is not None else None
    line = f"{amount}/mo"
    if months:
        line += f" for {months} months"
    if apr_text:
        line += f" at {apr_text} APR"
    return {"line": line + "*", "short": f"{amount}/mo*", "amount": amount, "disclosure": str(fm["disclosure"]),
            "claim_ids": list(fm["claim_ids"])}


def token_values(facts_pack, price=None):
    """{token: {"text", "claim_ids"}} for every hero token this run can fill."""
    fp = facts_pack or {}
    out = {}
    view = offer_mod.offer_view(fp)
    if view:
        ids = [view["claim_id"]]
        if view.get("amount"):
            out["offer_amount"] = {"text": view["amount"], "claim_ids": ids}
        if view.get("code"):
            out["offer_code"] = {"text": view["code"], "claim_ids": ids}
        sale = sale_prices(fp, price)
        if sale and sale["net"]:
            out["net_price"] = {"text": sale["net"], "claim_ids": sale["claim_ids"]}
        dl = deadline(fp)
        if dl:
            out["deadline_day"] = {"text": dl["day"], "claim_ids": ids}
            out["deadline_date"] = {"text": dl["date"], "claim_ids": ids}
    if price:
        out["price"] = {"text": price["text"], "claim_ids": list(price["claim_ids"])}
    fm = financing_monthly(fp)
    if fm:
        out["monthly"] = {"text": f"{fm['amount']}/mo", "claim_ids": fm["claim_ids"]}
    rating = store_rating(fp)
    if rating:
        out["rating"] = {"text": rating["value"], "claim_ids": rating["claim_ids"]}
        if rating.get("count"):
            out["review_count"] = {"text": rating["count"], "claim_ids": rating["claim_ids"]}
    return out


def fill_tokens(text, values):
    """(filled text, claim_ids used, unknown or unfilled token names)."""
    ids, missing = [], []

    def sub(m):
        name = m.group(1)
        if name in values:
            ids.extend(values[name]["claim_ids"])
            return values[name]["text"]
        missing.append(name)
        return m.group(0)

    return _TOKEN_RE.sub(sub, str(text or "")), ids, missing


def _token_nodes(page):
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else None
    closing = page.get("closing") if isinstance(page.get("closing"), dict) else None
    for node, key, path in ((hero, "eyebrow", "$.hero.eyebrow"), (hero, "headline", "$.hero.headline"),
                            (hero, "accent_phrase", "$.hero.accent_phrase"), (hero, "subhead", "$.hero.subhead"),
                            (closing, "headline", "$.closing.headline")):
        if node is not None and isinstance(node.get(key), str):
            yield node, key, path


def fill_page_tokens(page, facts_pack, price=None):
    """Fills the hero tokens in place; returns (restore list, claim ids) --
    render.py puts the writer's own text back before page.json is written,
    so a re-render fills today's figures again."""
    values = token_values(facts_pack, price)
    restore, ids = [], set()
    for node, key, _path in _token_nodes(page if isinstance(page, dict) else {}):
        if "{" not in node[key]:
            continue
        filled, used, _missing = fill_tokens(node[key], values)
        if filled != node[key]:
            restore.append((node, key, node[key]))
            node[key] = filled
            ids |= set(used)
    return restore, ids


def find_token_violations(page, facts_pack, price=None, prefix="landing"):
    values = token_values(facts_pack, price)
    problems = []
    for node, key, path in _token_nodes(page):
        _filled, _ids, missing = fill_tokens(node[key], values)
        for name in missing:
            problems.append({"path": path, "key": f"{prefix}:hero_token:{path}",
                             "issue": f"{{{name}}} is not a figure this page can fill -- use only "
                                      + (", ".join("{" + t + "}" for t in values) or "no token on this page"),
                             "text": node[key]})
    for path, node in _all_strings(page):
        if path not in TOKEN_FIELDS and _TOKEN_RE.search(node):
            problems.append({"path": path, "key": f"{prefix}:hero_token:{path}",
                             "issue": "figure tokens like {price} are filled only in the first screen and the "
                                      "closing headline", "text": node})
    return problems


def _all_strings(page):
    from .textutil import NON_PROSE_KEYS, walk_page

    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if isinstance(node, str):
            yield path, node


# ---------------------------------------------------------------------------
# Proof
# ---------------------------------------------------------------------------

_RATING_RE = re.compile(r"(\d(?:\.\d+)?)\s*(?:out\s+of\s+5|★|stars?)", re.IGNORECASE)
_COUNT_RE = re.compile(r"([\d,]{2,})\s+reviews", re.IGNORECASE)


def store_rating(facts_pack):
    """{"value": "4.6", "count": "4,260", "text": "4.6 ★ · 4,260 reviews", "source", "claim_ids",
    "store": True} from facts_pack.reviews_store; else the product rating
    (first_screen.rating_stat) with store False; else None."""
    from . import first_screen

    fp = facts_pack or {}
    rs = fp.get("reviews_store")
    if isinstance(rs, dict) and _ok(rs, verified_ids(fp)):
        text = str(rs.get("text") or "")
        value = rs.get("rating")
        count = rs.get("count")
        if value is None:
            m = _RATING_RE.search(text)
            value = m.group(1) if m else None
        if count is None:
            m = _COUNT_RE.search(text)
            count = m.group(1) if m else None
        if value is not None:
            count_text = f"{int(str(count).replace(',', '')):,}" if count is not None and \
                str(count).replace(",", "").isdigit() else (str(count) if count else "")
            m = re.search(r"\bon\s+([A-Z][\w.]*\w)", text)
            source = m.group(1) if m else ""
            return {"value": f"{value}", "count": count_text, "store": True, "source": source,
                    "text": f"{value} ★" + (f" · {count_text} reviews" if count_text else ""),
                    "claim_ids": list(rs["claim_ids"])}
    stat = first_screen.rating_stat(fp)
    if stat:
        count = stat["label"].split(", ", 1)[-1].replace(" reviews", "")
        return {"value": stat["value"], "count": count, "store": False, "source": "",
                "text": f"{stat['value']} ★ · {count} reviews", "claim_ids": list(stat["claim_ids"])}
    return None


def product_rating(facts_pack):
    """The product's own rating for the buy box (only when the first-screen
    rating is the store's; else None so the rating shows at most twice)."""
    from . import first_screen

    store = store_rating(facts_pack)
    if not store or not store["store"]:
        return None
    stat = first_screen.rating_stat(facts_pack)
    if not stat:
        return None
    count = stat["label"].split(", ", 1)[-1]
    return {"text": f"{stat['value']} ★ · {count}", "claim_ids": list(stat["claim_ids"])}


_UNITS_RE = re.compile(r"([\d,]+\+?)\s+(?:\w+\s+){0,2}(?:delivered|sold)", re.IGNORECASE)


def units(facts_pack):
    """{"value": "2,000+", "label": "delivered in 12 months", "text", "claim_ids"} from
    facts_pack.units.last_12mo (all_time when there is no 12-month line), or None."""
    fp = facts_pack or {}
    u = fp.get("units") if isinstance(fp.get("units"), dict) else None
    if not u:
        return None
    have = verified_ids(fp)
    for key, label in (("last_12mo", "delivered in 12 months"), ("all_time", "delivered")):
        node = u.get(key)
        if isinstance(node, dict) and _ok(node, have):
            m = _UNITS_RE.search(str(node.get("text") or ""))
            if m:
                return {"value": m.group(1), "label": label, "text": str(node["text"]),
                        "claim_ids": list(node["claim_ids"])}
    return None


def review_quotes(facts_pack, n=QUOTE_WALL):
    """Up to n verbatim reviews for the wall: verified, 4+ stars, at most
    QUOTE_MAX_WORDS words, the page's own product first. [] when there are
    none (no wall)."""
    fp = facts_pack or {}
    have = verified_ids(fp)
    product = (fp.get("product") or {}).get("name") or ""
    slug = product_name_slug(product)
    rows = []
    for q in fp.get("review_quotes") or []:
        if not isinstance(q, dict) or not q.get("text"):
            continue
        cid = q.get("claim_id") or q.get("id")
        if not cid or cid not in have:
            continue
        stars = q.get("stars")
        if stars is not None and float(stars) < 4:
            continue
        if len(str(q["text"]).split()) > QUOTE_MAX_WORDS:
            continue
        own = bool(slug) and slug in product_name_slug(str(q.get("product") or ""))
        rows.append((0 if own else 1, len(rows), {"text": str(q["text"]).strip(), "author": q.get("author") or "",
                                                  "stars": stars, "date": q.get("date"), "claim_ids": [cid]}))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows[:n]]


def trust_block(facts_pack, tenant):
    """The founder accountability line, lifetime U.S.-based support, US-owned
    -- each only when verified (support: facts_pack.support, else the
    warranty-definition claim that states it)."""
    fp = facts_pack or {}
    claims = claims_by_id(fp)
    have = set(claims)
    items = []
    author = ((getattr(tenant, "authors", None) or {}).get("author") or {}) if tenant else {}
    founder = claims.get("founder-ceo")
    if author.get("name") and founder:
        title = author.get("title") or ""
        items.append({"kind": "founder", "text": f"{author['name']}{', ' + title if title else ''}, is "
                      "responsible for every claim on this page.", "claim_ids": ["founder-ceo"]})
    support = fp.get("support") if isinstance(fp.get("support"), dict) else None
    if support and _ok(support, have):
        items.append({"kind": "support", "text": str(support["text"]), "claim_ids": list(support["claim_ids"])})
    else:
        for cid, claim in claims.items():
            if re.search(r"lifetime\s+U\.?S\.?-based\s+support", claim.get("text") or "", re.IGNORECASE):
                items.append({"kind": "support", "text": "Lifetime U.S.-based support", "claim_ids": [cid]})
                break
    for cid, claim in claims.items():
        if re.fullmatch(r"\s*US-owned company\.?\s*", claim.get("text") or "", re.IGNORECASE):
            items.append({"kind": "us_owned", "text": "US-owned company", "claim_ids": [cid]})
            break
    return items


# ---------------------------------------------------------------------------
# Offer stack, HSA, delivery & setup
# ---------------------------------------------------------------------------

_PWC_VALUE_ID = "pwc-value-588"


def included(facts_pack, page=None, tenant=None):
    """"What's included": facts_pack.included (grounding); fallback, the
    model's verified feature lines (first_screen.value_stack) plus free
    shipping and the Wellness Club line. The Wellness Club item carries its
    value when the verified claim pwc-value-588 exists (owner, 2026-10-15),
    else "included free"."""
    from . import first_screen, listicle

    fp = facts_pack or {}
    claims = claims_by_id(fp)
    have = set(claims)
    rows = [dict(r) for r in fp.get("included") or [] if isinstance(r, dict) and r.get("text") and _ok(r, have)]
    if not rows:
        stack = first_screen.value_stack(fp, page or {}, tenant=tenant) if tenant is not None else None
        rows = [dict(i) for i in (stack or {}).get("items") or []]
        ship = listicle.free_shipping_claim(fp)
        if ship:
            rows.append({"text": "Free shipping", "claim_ids": [ship["id"]]})
    pwc_value = claims.get(_PWC_VALUE_ID)
    wc_rows = [r for r in rows if re.search(r"wellness\s+club", r["text"], re.IGNORECASE)]
    if pwc_value:
        line = {"text": str(pwc_value["text"]).rstrip("."), "claim_ids": [_PWC_VALUE_ID]}
        rows = [r for r in rows if r not in wc_rows] + [line]
    elif not wc_rows and "pwc-included-free" in claims:
        rows.append({"text": str(claims["pwc-included-free"]["text"]).rstrip("."), "claim_ids": ["pwc-included-free"]})
    seen, out = set(), []
    for r in rows:
        key = r["text"].lower()
        if key not in seen:
            seen.add(key)
            out.append({"text": r["text"].rstrip("."), "claim_ids": list(r["claim_ids"])})
    return out[:8]


def hsa_line(facts_pack):
    """facts_pack.hsa_fsa, else the verified HSA/FSA claim's own text; never
    a savings percentage."""
    from . import listicle

    fp = facts_pack or {}
    h = fp.get("hsa_fsa")
    if isinstance(h, dict) and h.get("text") and _ok(h, verified_ids(fp)):
        return {"text": str(h["text"]), "claim_ids": list(h["claim_ids"])}
    claim = listicle.hsa_claim(fp)
    if claim:
        return {"text": str(claim["text"]), "claim_ids": [claim["id"]]}
    return None


def delivery_setup(facts_pack):
    """The one "Delivery & setup" row: free freight delivery by appointment,
    clasp-together assembly, the electrical requirement, the dimensions --
    each a short label over its verified claim (or spec row) only."""
    from . import landing, listicle

    fp = facts_pack or {}
    claims = claims_by_id(fp)
    slug = product_name_slug((fp.get("product") or {}).get("name") or "")
    out = []
    ship = listicle.free_shipping_claim(fp)
    if ship:
        appt = bool(re.search(r"appointment", ship.get("text") or "", re.IGNORECASE))
        out.append({"label": "Delivery", "text": "Free shipping" + (", freight delivery by appointment" if appt else ""),
                    "claim_ids": [ship["id"]]})
    for cid, claim in claims.items():
        if cid.startswith(f"pdp-{slug}-") and re.search(r"clasp", claim.get("text") or "", re.IGNORECASE):
            out.append({"label": "Assembly", "text": "Clasp-together assembly", "claim_ids": [cid]})
            break
    rows = landing.pdp.spec_rows(fp, limit=50)
    for label, pattern in (("Power", r"electric|power|outlet|volt"), ("Size", r"dimension|footprint|size")):
        row = next((r for r in rows if re.search(pattern, r["label"], re.IGNORECASE)), None)
        if row:
            out.append({"label": label, "text": str(row["value"]), "claim_ids": list(row["claim_ids"])})
        elif label == "Size":
            claim = claims.get(f"gbrain-{slug}-dimensions")
            m = re.search(r":\s*(.+?)\.?$", claim.get("text") or "") if claim else None
            if m:
                out.append({"label": label, "text": m.group(1), "claim_ids": [claim["id"]]})
    return out


def model_cards(facts_pack, tenant, assets=None, asset_alt=None):
    """2-3 of the tenant's own models (facts_pack.models, joined when the
    look declares model_cards): the page's model first, then the closest
    in price; each with its verified price, a monthly figure when a
    verified financing-monthly-<slug> claim states one, its cut-out, and
    its own product URL. [] with fewer than 2."""
    from . import cutouts

    fp = facts_pack or {}
    claims = claims_by_id(fp)
    rows = [r for r in fp.get("models") or [] if isinstance(r, dict) and r.get("price_claim_id") in claims
            and r.get("price_text") and r.get("url")]
    run_slug = product_name_slug((fp.get("product") or {}).get("name") or "")

    def amount(r):
        try:
            return float(re.sub(r"[^\d.]", "", r["price_text"]))
        except ValueError:
            return 0.0

    own = next((r for r in rows if r.get("slug") == run_slug), None)
    base = amount(own) if own else 0.0
    others = sorted((r for r in rows if r is not own), key=lambda r: (abs(amount(r) - base), r.get("slug") or ""))
    picked = ([own] if own else []) + others
    cards = []
    for r in picked:
        if len(cards) == MODEL_CARDS:
            break
        asset = cutouts.cutout_asset(tenant, r.get("slug")) if tenant is not None else None
        if asset is not None and assets is not None and asset["id"] not in assets:
            assets[asset["id"]] = dict(asset, alt=asset_alt(asset, r.get("name")) if asset_alt else r.get("name"))
        ids = [r["price_claim_id"]]
        monthly = None
        fin = claims.get(f"financing-monthly-{r.get('slug')}")
        m = re.search(r"\$[\d,]+(?:\.\d\d)?/mo", fin.get("text") or "") if fin else None
        if m:
            monthly = m.group(0)
            ids.append(fin["id"])
        cards.append({"slug": r.get("slug"), "name": r.get("name") or r.get("slug"), "url": r["url"],
                      "price": r["price_text"], "monthly": monthly, "asset_id": asset["id"] if asset else None,
                      "capacity": r.get("capacity_text") if r.get("capacity_claim_id") in claims else None,
                      "is_run_model": r is own, "claim_ids": ids + ([r["capacity_claim_id"]]
                                                                    if r.get("capacity_claim_id") in claims else [])})
    return cards if len(cards) >= 2 else []


# ---------------------------------------------------------------------------
# Reasons: the writer's section
# ---------------------------------------------------------------------------

def offer_reason(facts_pack, price=None):
    """The verified offer as reason 1 on an offer-led page (renderer-owned):
    headline = the net price line or the offer line, body = the claim's
    deadline sentence and the CTA note."""
    fp = facts_pack or {}
    view = offer_mod.offer_view(fp)
    if view is None:
        return None
    sale = sale_prices(fp, price)
    headline = (f"{sale['net']} {sale['line']}".strip() if sale else view["line"])
    body = " ".join(x for x in (view.get("deadline") or "", view.get("cta_note") or "") if x)
    return {"headline": headline, "body": body, "claim_ids": [view["claim_id"]] + (sale["claim_ids"][1:] if sale else []),
            "lever": "outcome", "offer": True}


def reasons_view(page, facts_pack, price=None):
    """The reasons as the look renders them: the offer first on an
    offer-led page, then the writer's items whose claim ids all verify; each
    with its proof line (proof_ref: a review quote, the store rating or the
    units line) when that claim verifies."""
    fp = facts_pack or {}
    have = verified_ids(fp)
    quotes = {q["claim_ids"][0]: q for q in review_quotes(fp, n=100)}
    rating, unit = store_rating(fp), units(fp)
    out = []
    first = offer_reason(fp, price)
    if first:
        out.append(first)
    for r in (page or {}).get("reasons") or []:
        if not isinstance(r, dict) or not str(r.get("headline") or "").strip() or not _ok(r, have):
            continue
        item = {"headline": str(r["headline"]).strip(), "body": str(r.get("body") or "").strip(),
                "claim_ids": list(r["claim_ids"]), "lever": r.get("lever"), "asset_role": r.get("asset_role")}
        ref = r.get("proof_ref")
        if ref in quotes:
            item["proof"] = {"kind": "quote", "text": quotes[ref]["text"], "author": quotes[ref]["author"],
                             "claim_ids": [ref]}
        elif rating and ref in rating["claim_ids"]:
            item["proof"] = {"kind": "rating", "text": rating["text"], "claim_ids": rating["claim_ids"]}
        elif unit and ref in unit["claim_ids"]:
            item["proof"] = {"kind": "units", "text": f"{unit['value']} {unit['label']}", "claim_ids": unit["claim_ids"]}
        out.append(item)
    return out[:REASONS_RANGE[1]]


def proof_ref_ids(facts_pack):
    fp = facts_pack or {}
    ids = {q["claim_ids"][0] for q in review_quotes(fp, n=100)}
    for node in (store_rating(fp), units(fp)):
        if node:
            ids |= set(node["claim_ids"])
    return ids


def writer_reasons_range(facts_pack):
    return WRITER_REASONS_OFFER if offer_mod.offer_view(facts_pack) else REASONS_RANGE


def find_reasons_violations(page, facts_pack, prefix="landing"):
    """`reasons`: 3-5 items (3-4 on an offer-led page, the renderer adds the
    offer first), each {headline <= 10 words, body <= 45 words, claim_ids,
    lever, proof_ref?, asset_role?}; the levers cover the Value Equation
    (at least min(4, n) different ones). A page that still carries the
    retired `checked_claims` fails."""
    problems = []
    if "checked_claims" in page:
        problems.append({"path": "$.checked_claims", "key": f"{prefix}:checked_claims_retired",
                         "issue": "checked_claims is retired: write `reasons` (why the ad's promise is true), never "
                                  "a list of what the ad said"})
    items = page.get("reasons") if isinstance(page.get("reasons"), list) else []
    # the writer is told 3-4 on an offer-led page; a fifth is not refused
    # (the look shows at most five, the sale first)
    lo, hi = REASONS_RANGE
    if not lo <= len(items) <= hi:
        problems.append({"path": "$.reasons", "key": f"{prefix}:reasons_count",
                         "issue": f"reasons has {len(items)} items; it needs {lo}-{hi}"})
    have = verified_ids(facts_pack)
    refs = proof_ref_ids(facts_pack)
    levers = []
    for i, r in enumerate(items):
        path = f"$.reasons[{i}]"
        if not isinstance(r, dict):
            problems.append({"path": path, "key": f"{prefix}:reason_shape:{i}",
                             "issue": "each reason is {headline, body, claim_ids, lever}"})
            continue
        head, body = str(r.get("headline") or "").strip(), str(r.get("body") or "").strip()
        if not head or not body:
            problems.append({"path": path, "key": f"{prefix}:reason_shape:{i}",
                             "issue": "each reason needs a headline and a body"})
        if len(head.split()) > REASON_HEADLINE_MAX:
            problems.append({"path": f"{path}.headline", "key": f"{prefix}:reason_headline_words:{i}",
                             "issue": f"headline is {len(head.split())} words; at most {REASON_HEADLINE_MAX}",
                             "text": head})
        if len(body.split()) > REASON_BODY_MAX:
            problems.append({"path": f"{path}.body", "key": f"{prefix}:reason_body_words:{i}",
                             "issue": f"body is {len(body.split())} words; at most {REASON_BODY_MAX}", "text": body})
        ids = [c for c in (r.get("claim_ids") or []) if c]
        if not ids:
            problems.append({"path": f"{path}.claim_ids", "key": f"{prefix}:reason_ids:{i}",
                             "issue": "every reason cites at least one claim_id from facts_pack.verified_claims"})
        elif any(c not in have for c in ids):
            problems.append({"path": f"{path}.claim_ids", "key": f"{prefix}:reason_unverified:{i}",
                             "issue": f"claim_ids {[c for c in ids if c not in have]} are not verified claims"})
        lever = r.get("lever")
        if lever not in LEVERS:
            problems.append({"path": f"{path}.lever", "key": f"{prefix}:reason_lever:{i}",
                             "issue": f"lever is one of {list(LEVERS)}"})
        else:
            levers.append(lever)
        ref = r.get("proof_ref")
        if ref and ref not in refs:
            problems.append({"path": f"{path}.proof_ref", "key": f"{prefix}:reason_proof_ref:{i}",
                             "issue": "proof_ref is the claim_id of one review quote, the store rating or the units "
                                      f"line ({sorted(refs) or 'none on this page: omit proof_ref'})"})
        role = r.get("asset_role")
        if role and role not in ASSET_ROLES:
            problems.append({"path": f"{path}.asset_role", "key": f"{prefix}:reason_asset_role:{i}",
                             "issue": f"asset_role is one of {list(ASSET_ROLES)} (or omit it)"})
    need = min(len(LEVERS), len(items))
    if items and len(set(levers)) < need:
        problems.append({"path": "$.reasons", "key": f"{prefix}:reason_levers",
                         "issue": f"the reasons cover {sorted(set(levers))}; cover {need} different levers of "
                                  f"{list(LEVERS)} -- the outcome, why it will work for the reader, how fast, how "
                                  "little effort"})
    return problems


def reason_photos(reasons, facts_pack, tenant, *, assets, exclude=(), audience_block=None):
    """One library photo per reason by its asset_role (else its lever's
    role), never one already on the page; None where nothing fits. Picks go
    into `assets`. `audience_block(asset) -> bool` drops a people photo that
    does not fit the ad's audience."""
    from . import images

    used = list(exclude)
    out = []
    for r in reasons:
        role = r.get("asset_role") if r.get("asset_role") in ASSET_ROLES else LEVER_ROLE.get(r.get("lever") or "")
        if not role or r.get("offer"):
            out.append(None)
            continue
        try:
            res = images.fill_slots([{"name": f"reason-{len(out)}", "want": {"shot": ASSET_ROLES[role]}, "n": 3,
                                      "above_fold": False, "last_resort": None,
                                      "fallback": ["room", "detail-macro", "exterior"]}],
                                    facts_pack or {}, tenant, seed=0, used=tuple(used))[0]
        except Exception:  # noqa: BLE001 -- no library: no photo
            out.append(None)
            continue
        pick = next((p for p in res["picks"] if p.get("asset") and not (audience_block and audience_block(p["asset"]))),
                    None)
        if pick is None:
            out.append(None)
            continue
        asset = dict(pick["asset"])
        asset.setdefault("focal", pick.get("focal"))
        assets.setdefault(pick["id"], asset)
        used.append(pick["id"])
        out.append(pick["id"])
    return out


# ---------------------------------------------------------------------------
# Audience (cycle 110 hero subject rule)
# ---------------------------------------------------------------------------

_WOMEN_RE = re.compile(r"\b(?:women|woman|ladies|lady|moms?|mothers?|her|she|female|girls?)\b", re.IGNORECASE)
_MEN_RE = re.compile(r"\b(?:men|man|guys?|dads?|fathers?|him|he|male|boys?)\b", re.IGNORECASE)
_MALE_PHOTO_RE = re.compile(r"\b(?:man|men|male|guy|boy|his|he|shirtless|beard(?:ed)?)\b", re.IGNORECASE)
_FEMALE_PHOTO_RE = re.compile(r"\b(?:woman|women|female|girl|lady|her|she)\b", re.IGNORECASE)


def audience_gender(ad_brief):
    """"women" / "men" when the ad names who it is for in its hook or
    audience ("Women over 40, this is for you."), else None."""
    brief = ad_brief or {}
    text = " ".join(str(brief.get(k) or "") for k in ("audience", "hook"))
    w, m = bool(_WOMEN_RE.search(text)), bool(_MEN_RE.search(text))
    return "women" if w and not m else "men" if m and not w else None


def photo_mismatches_audience(asset, ad_brief, manifest_tags=None):
    """True for a people photo whose description names the other audience
    (a shirtless man on a "Women over 40" ad)."""
    gender = audience_gender(ad_brief)
    if gender is None:
        return False
    tags = manifest_tags or (asset or {}).get("tags") or {}
    desc = " ".join(str(x) for x in (tags.get("description"), (asset or {}).get("description"), (asset or {}).get("alt"))
                    if x)
    if not desc:
        return False
    if gender == "women":
        return bool(_MALE_PHOTO_RE.search(desc)) and not _FEMALE_PHOTO_RE.search(desc)
    return bool(_FEMALE_PHOTO_RE.search(desc)) and not _MALE_PHOTO_RE.search(desc)


def audience_excluded_ids(ad_brief, tenant):
    """Library photo ids whose people do not match the ad's named audience
    (photo_mismatches_audience over the manifest's descriptions); () when
    the ad names none or there is no library."""
    if audience_gender(ad_brief) is None or tenant is None:
        return ()
    from . import photo_library

    try:
        photos = photo_library.photos_by_id(photo_library.load_manifest(tenant))
    except Exception:  # noqa: BLE001 -- no library
        return ()
    return tuple(pid for pid, entry in photos.items()
                 if photo_mismatches_audience(entry, ad_brief, (entry or {}).get("tags") or {}))
