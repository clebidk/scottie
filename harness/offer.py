"""Cycle 109: an ad that states an offer (a discount, a code, a deadline or a
sale name) gets an offer-led page -- when the offer is a verified claim.

Owner instruction, 2026-10-14, verbatim: "we need to regenerate new landing
pages. if the ad is about a sale, the landing page 100% needs to have that as
primary focus". On 2026-10-13/14 the pipeline built five sale ads ("$300 off
every indoor sauna. $500 off outdoor. Prices go up October 16, ...") with no
verified sale claim, and their pages wrote "no discount applied" next to the
full price. The sale was real.

Four parts, all deterministic (no model call):

  - extract_offer: the ad brief's `offer` block, read from the ad's own words
    (transcript and the copy around the media): amounts, codes, a deadline, a
    sale name, and the ad's own sentence that states the offer.
  - match_offer / apply_to_run: the claims step. A verified claim of category
    "promo" whose text holds every amount and code the ad states (and the
    ad's deadline date, when both give one) and whose own deadline has not
    passed is the offer's claim; its id is `offer.claim_id`, and facts_pack
    gets `offer` (the claim and the ad's sentence). No match: facts_pack gets
    `offer_unverified` and the writer is told to say nothing about discounts.
  - offer_view: what the renderer prints, all from the claim text -- the sale
    name (eyebrow), the product's amount and code in one line, the deadline as
    one plain sentence, the note under the first CTA, the sticky bar's code,
    and the first checked claim (the ad's sentence, the claim text).
  - the gates: no page may deny an offer the ad states (find_denial_violations);
    a deadline sentence is allowed only when it is the verified claim's own
    words (deadline_violations, used by listicle.find_urgency_violations).

Tenant-neutral: amounts, codes, names and dates come from the ad and from
claims/verified.json; no company or product word lives here.
"""
import datetime
import json
import os
import re

from .textutil import NON_PROSE_KEYS, walk_page

PROMO_CATEGORY = "promo"
HOLD_REASON = "held: offer not verified"

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december")
_MONTH_RE = (r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
             r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DATE_RE = re.compile(_MONTH_RE + r"\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>20\d\d))?",
                      re.IGNORECASE)
# a dollar or percent amount followed by "off", or "save <amount>"
_AMOUNT = r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\$\s?\d+(?:\.\d{2})?|\b\d{1,2}\s?%"
_AMOUNT_RE = re.compile(r"(?P<a>" + _AMOUNT + r")\s+off\b|\bsave\s+(?P<b>" + _AMOUNT + r")", re.IGNORECASE)
# "code INDOOR300", "promo code: FALL10", "with coupon SAVE5" -- the token is caps/digits with a letter
_CODE_RE = re.compile(r"(?i:\b(?:promo\s+|discount\s+|coupon\s+)?(?:code|coupon))\s*[:\-]?\s*[\"\u201c']?"
                      r"(?P<code>(?=[A-Z0-9]*[A-Z])[A-Z0-9]{3,20})\b")
# "Community Savings Sale", "Black Friday Sale", "Labor Day Savings Event"
_NAME_RE = re.compile(r"\b(?P<name>(?:[A-Z][A-Za-z'&\-]+\s+){1,4}(?:Sale|Event|Days|Weekend))\b")
_NAME_LEAD = {"The", "Our", "This", "Your", "A", "An", "Big", "Now", "Shop", "Join"}
# what makes a date in the ad a deadline
_DEADLINE_CUE_RE = re.compile(
    r"\b(?:prices?\s+(?:go(?:es)?|will\s+go|are\s+going)\s+up|price\s+increase|prices?\s+rise|"
    r"(?:sale|offer|deal|savings?)\s+ends?|ends?\s+(?:on\s+)?(?=[A-Z])|ending|until|through|thru|before|"
    r"expires?|last\s+day|deadline)\b", re.IGNORECASE)
_RAISE_CUE_RE = re.compile(r"\b(?:go(?:es)?\s+up|increase|rise|before)\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# The deadline phrases the urgency gate refuses unless the sentence is the
# verified claim's own deadline sentence (cycle 109; tenant-neutral).
DEADLINE_PHRASE_RE = re.compile(
    r"\b(?:prices?\s+(?:go(?:es)?|will\s+go|are\s+going)\s+up|price\s+increase|prices?\s+rise|"
    r"(?:sale|offer|deal|savings?)\s+ends?\b|(?:ends?|ending|until|through|before)\s+" + _MONTH_RE + r"\.?\s+\d)",
    re.IGNORECASE)

# Wording that tells the reader there is no offer. With an offer in the ad
# (verified or not) no page may say it (the prod pages said "no discount
# applied" next to the full price).
# "sales call", "sales team", "no sales pressure" are not about a sale (the
# landing page's own subhead says "without a sales call").
_NOT_SALES_TALK = r"(?!\s+(?:calls?|pitch(?:es)?|pressure|team|reps?|people|person|tax|staff|process))"
_DENIAL_RE = re.compile(
    r"\bno\s+(?:discounts?|sales?|promo(?:tion)?s?|coupons?|codes?)\b" + _NOT_SALES_TALK + r"(?:\s+(?:is\s+|are\s+)?"
    r"(?:applied|needed|required|available|offered|running))?|\bnot\s+(?:currently\s+)?on\s+sale\b|"
    r"\bfull\s+(?:retail\s+)?price\b|\bdiscounts?\s+(?:do|does)\s+not\s+apply\b|\bwithout\s+(?:a|any)\s+"
    r"(?:discount|sale|code|coupon)\b|\bnever\s+(?:discounted|on\s+sale)\b|\bthere\s+is\s+no\s+(?:sale|discount)\b",
    re.IGNORECASE)


def _norm_amount(text):
    text = re.sub(r"\s+", "", str(text or ""))
    return text if text.startswith("$") or text.endswith("%") else text


def _month_index(token):
    token = token.lower().rstrip(".")
    return next((i for i, m in enumerate(_MONTHS, 1) if m.startswith(token[:3])), None)


def _date_label(m):
    month = _MONTHS[_month_index(m.group("month")) - 1].capitalize()
    label = f"{month} {int(m.group('day'))}"
    return label + (f", {m.group('year')}" if m.group("year") else "")


def _sentences(text):
    return [s.strip() for s in _SENTENCE_RE.split(str(text or "").replace("\n", " ")) if s.strip()]


def _is_urgent(sentence):
    from .listicle import URGENCY_PHRASES

    low = sentence.lower()
    return any(p in low for p in URGENCY_PHRASES)


# ---------------------------------------------------------------------------
# the ad brief's offer block
# ---------------------------------------------------------------------------

def extract_offer(*texts):
    """The `offer` block for an ad whose words state a discount, a code, a
    deadline or a sale name, else None: {"amounts", "amount", "codes",
    "code", "deadline", "name", "sentence"}. `sentence` is the ad's own
    sentence(s) that state the offer: the ones with an amount or a code (at
    most two, in order), else the one with the sale name, else the deadline
    one -- never a sentence with an urgency phrase ("last chance") when
    another one states the offer."""
    amounts, codes, names, deadline = [], [], [], None
    offer_sentences, name_sentences, deadline_sentences = [], [], []
    for text in texts:
        for sentence in _sentences(text):
            found = False
            for m in _AMOUNT_RE.finditer(sentence):
                amount = _norm_amount(m.group("a") or m.group("b"))
                if amount not in amounts:
                    amounts.append(amount)
                found = True
            for m in _CODE_RE.finditer(sentence):
                if m.group("code") not in codes:
                    codes.append(m.group("code"))
                found = True
            if found:
                offer_sentences.append(sentence)
            for m in _NAME_RE.finditer(sentence):
                words = m.group("name").split()
                while words and words[0] in _NAME_LEAD:
                    words = words[1:]
                if len(words) >= 2 and " ".join(words) not in names:
                    names.append(" ".join(words))
                    name_sentences.append(sentence)
            date = _DATE_RE.search(sentence)
            if date and _DEADLINE_CUE_RE.search(sentence):
                deadline = deadline or _date_label(date)
                deadline_sentences.append(sentence)
    if not (amounts or codes or names or deadline):
        return None
    calm = [s for s in offer_sentences if not _is_urgent(s)] or offer_sentences
    pick = calm[:2] or [s for s in name_sentences if not _is_urgent(s)][:1] or name_sentences[:1] or \
        deadline_sentences[:1]
    return {
        "amounts": amounts, "amount": amounts[0] if amounts else None,
        "codes": codes, "code": codes[0] if codes else None,
        "deadline": deadline, "name": names[0] if names else None,
        "sentence": " ".join(dict.fromkeys(pick)),
    }


def offer_from_brief(ad_brief, ad_copy=None):
    """extract_offer over the copy around the media (primary text, headline,
    description) and the transcript or on-image text."""
    copy = ad_copy or {}
    return extract_offer(*(copy.get(k) or "" for k in ("primary_text", "headline", "description")),
                         (ad_brief or {}).get("transcript_or_text") or "")


# ---------------------------------------------------------------------------
# the claims step: the offer's verified promo claim
# ---------------------------------------------------------------------------

def _claim_date(text, claim):
    """(date, raises) for the claim's own deadline: the first date in its text
    next to a deadline cue (the year from the text, else the claim's date).
    `raises` is True for "prices go up on <date>" (the sale is over ON that
    date) and False for "ends <date>" / "through <date>" (over AFTER it).
    Cycle 111: a claim whose deadline was synced from the store's countdown
    (harness/sale_sync.py) carries `last_sale_day`; that day wins."""
    synced = (claim or {}).get("last_sale_day")
    if synced:
        try:
            return datetime.date.fromisoformat(str(synced)[:10]), False
        except ValueError:
            pass
    for clause in re.split(r"[;.]\s+|;", str(text or "")):
        m = _DATE_RE.search(clause)
        if not m or not _DEADLINE_CUE_RE.search(clause):
            continue
        year = m.group("year") or str((claim or {}).get("date") or "")[:4]
        try:
            when = datetime.date(int(year), _month_index(m.group("month")), int(m.group("day")))
        except (TypeError, ValueError):
            return None, False
        return when, bool(_RAISE_CUE_RE.search(clause))
    return None, False


def _instant(value):
    """An aware datetime from an ISO string (None when there is none)."""
    try:
        when = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else None


def claim_expired(claim, today, now=None):
    """True when the promo claim's own deadline has passed on `today`.
    Cycle 111: with `now` (an aware datetime), a synced `deadline_end`
    instant that has passed also ends it (the sale ends at the countdown's
    instant, not at midnight)."""
    end = _instant(claim.get("deadline_end")) if now is not None else None
    if end is not None and now >= end:
        return True
    when, raises = _claim_date(claim.get("text"), claim)
    if when is None or today is None:
        return False
    today = today if isinstance(today, datetime.date) else datetime.date.fromisoformat(str(today)[:10])
    return today >= when if raises else today > when


def _amount_in(amount, text):
    if amount.endswith("%"):
        return re.search(r"(?<!\d)" + re.escape(amount[:-1]) + r"\s?%", text) is not None
    digits = amount.lstrip("$")
    return re.search(r"\$\s?" + re.escape(digits) + r"(?![\d,]|\.\d)", text) is not None


def match_offer(offer, claims, today=None, now=None):
    """The verified promo claim the ad's offer states, or None. Every amount
    and code the ad states must be in the claim's text; with neither, the
    sale name must be; when the ad and the claim both give a deadline date
    they must be the same day; a claim whose deadline has passed never
    matches."""
    if not offer:
        return None
    for claim in claims or []:
        if claim.get("category") != PROMO_CATEGORY:
            continue
        text = str(claim.get("text") or "")
        if claim_expired(claim, today, now):
            continue
        if not all(_amount_in(a, text) for a in offer.get("amounts") or []):
            continue
        if not all(re.search(r"\b" + re.escape(c) + r"\b", text, re.IGNORECASE) for c in offer.get("codes") or []):
            continue
        if not (offer.get("amounts") or offer.get("codes")):
            if not offer.get("name") or offer["name"].lower() not in text.lower():
                continue
        if offer.get("deadline"):
            when, _ = _claim_date(text, {"date": claim.get("date")})   # cycle 111: the text's own date
            m = _DATE_RE.search(offer["deadline"])
            if when and m and (when.month, when.day) != (_month_index(m.group("month")), int(m.group("day"))):
                continue
        return claim
    return None


def apply_to_run(ad_brief, facts_pack, all_verified, today=None, log=None, now=None):
    """The claims step for one run: matches ad_brief.offer to a verified promo
    claim. Match: ad_brief.offer.claim_id is set, the claim joins
    facts_pack.verified_claims (so the page may cite it and Sources lists
    it) and facts_pack.offer = {claim_id, text, sentence, ...}. No match:
    facts_pack.offer_unverified = {sentence, ...}. No offer in the ad:
    nothing changes. Returns the claim or None."""
    offer = (ad_brief or {}).get("offer")
    facts_pack.pop("offer", None)
    facts_pack.pop("offer_unverified", None)
    if not offer:
        return None
    claim = match_offer(offer, all_verified, today, now)
    if claim is None:
        offer.pop("claim_id", None)
        facts_pack["offer_unverified"] = {k: offer.get(k) for k in ("sentence", "amounts", "codes", "deadline",
                                                                     "name")}
        if log is not None:
            log.event("ground", f"offer in the ad, no verified promo claim matches: {offer.get('sentence')!r}")
        return None
    offer["claim_id"] = claim["id"]
    entry = {k: claim.get(k) for k in ("id", "text", "category", "source")}
    claims = facts_pack.setdefault("verified_claims", [])
    if not any(c.get("id") == claim["id"] for c in claims):
        claims.append(entry)
    facts_pack["offer"] = {"claim_id": claim["id"], "text": claim["text"], "sentence": offer.get("sentence"),
                           "amounts": offer.get("amounts"), "codes": offer.get("codes"),
                           "deadline": offer.get("deadline"), "name": offer.get("name")}
    # Cycle 111: the deadline synced from the store's countdown travels with the offer
    facts_pack["offer"].update({k: claim[k] for k in ("last_sale_day", "deadline_end") if claim.get(k)})
    # Cycle 110: the figures the page prints (facts_pack contract: offer.net_price,
    # offer.list_price, offer.code, offer.deadline_short), from offer_view.
    view = offer_view(facts_pack, today=today)
    if view:   # None only when no grounding date was given and the claim has expired by the render date
        facts_pack["offer"].update({k: view[k] for k in ("net_price", "list_price", "code", "deadline_short")})
    if log is not None:
        log.event("ground", f"offer matched verified claim {claim['id']}: {offer.get('sentence')!r}")
    return claim


def without_offer(facts_pack, ad_brief=None):
    """Cycle 111: deep copies of (facts_pack, ad_brief) for the run's
    non-sale twin page: no offer, the promo claim out of verified_claims,
    the ad's offer marked unverified (the writer says nothing about
    discounts, sales, codes or deadlines; the hook gate leaves the offer's
    figures out)."""
    import copy

    fp, brief = copy.deepcopy(facts_pack or {}), copy.deepcopy(ad_brief or {})
    offer = fp.pop("offer", None) or {}
    fp["verified_claims"] = [c for c in fp.get("verified_claims") or [] if c.get("id") != offer.get("claim_id")]
    fp["offer_unverified"] = {k: offer.get(k) for k in ("sentence", "amounts", "codes", "deadline", "name")}
    if isinstance(brief.get("offer"), dict):
        brief["offer"].pop("claim_id", None)
    return fp, brief


# ---------------------------------------------------------------------------
# what the renderer prints
# ---------------------------------------------------------------------------

_PAIR_RE = re.compile(
    r"(?P<amount>" + _AMOUNT + r")\s+off\b(?P<scope>[^,;.]*?)"
    r"(?:\s+(?:with|using)\s+(?:the\s+)?(?:promo\s+|discount\s+)?code\s+(?P<code>[A-Z0-9]{3,20}))?"
    r"(?=\s*(?:[,;.]|\band\b|$))", re.IGNORECASE)
_SCOPE_FILLER = {"sauna", "saunas", "all", "every", "any", "the", "on", "of", "models", "model", "orders", "order",
                 "products", "product", "items", "item", "site", "sitewide", "everything"}


def _pairs(text):
    return [{"amount": _norm_amount(m.group("amount")), "scope": m.group("scope").strip(),
             "code": m.group("code")} for m in _PAIR_RE.finditer(text)]


def _product_words(product):
    product = product or {}
    blob = " ".join(str(product.get(k) or "") for k in ("slug", "title", "name", "short_name", "url"))
    return set(re.findall(r"[a-z]+", blob.lower()))


def _deadline_sentence(text, claim):
    for clause in re.split(r"[;.]\s+|;", str(text or "")):
        clause = clause.strip().rstrip(".")
        if _DATE_RE.search(clause) and _DEADLINE_CUE_RE.search(clause):
            return clause[0].upper() + clause[1:] + "."
    return None


def _sale_name(text, offer):
    head = str(text or "").split(":", 1)
    if len(head) == 2 and 1 <= len(head[0].split()) <= 6 and not re.search(r"\d|\$", head[0]):
        return head[0].strip()
    return (offer or {}).get("name")


def _price_figures(facts_pack, amount):
    """(list_price, net_price) as "$8,250" / "$7,950" from the product's price
    and the offer amount ("$300" or "10%"); net is None without a single
    amount for this product or when it would not be a positive price."""
    from decimal import Decimal, InvalidOperation

    from .prices import format_price

    raw = ((facts_pack or {}).get("product") or {}).get("price")
    try:
        price = Decimal(str(raw).replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None, None
    if not amount:
        return format_price(price), None
    if amount.endswith("%"):
        net = price * (100 - Decimal(amount[:-1])) / 100
    else:
        net = price - Decimal(amount.lstrip("$").replace(",", ""))
    return format_price(price), (format_price(net.quantize(Decimal("0.01"))) if net > 0 else None)


def deadline_short(text, claim=None):
    """The claim's last sale day as "today's price ends Thu, Oct 15", or None.
    "prices go up on <date>" ends the day BEFORE that date; "ends <date>" /
    "through <date>" ends ON it (claim_expired's rule)."""
    when, raises = _claim_date(text, claim)
    if when is None:
        return None
    last = when - datetime.timedelta(days=1) if raises else when
    return (f"today's price ends {('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[last.weekday()]}, "
            f"{_MONTHS[last.month - 1][:3].capitalize()} {last.day}")


def tenant_tz(tenant=None):
    """The tenant's timezone (tenant.yaml `timezone`), else UTC."""
    from zoneinfo import ZoneInfo

    from . import tenant as tenant_mod

    tenant = tenant or tenant_mod.active_or_none()
    name = tenant.get("timezone") if tenant is not None else None
    try:
        return ZoneInfo(str(name)) if name else datetime.timezone.utc
    except (ValueError, KeyError):
        return datetime.timezone.utc


def expires_iso(offer, tenant=None):
    """Cycle 111: the instant a run's dated offer ends, as an ISO string, or
    None: facts_pack.offer `expires` (grounding: the store's countdown when
    fresh), else the claim's synced `deadline_end`, else the end of the
    claim's last sale day in the tenant's timezone."""
    offer = offer or {}
    if offer.get("expires") or offer.get("deadline_end"):
        return offer.get("expires") or offer.get("deadline_end")
    when, raises = _claim_date(offer.get("text"), offer)
    if when is None:
        return None
    last = when - datetime.timedelta(days=1) if raises else when
    return datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time(0),
                                     tenant_tz(tenant)).isoformat()


def render_today(tenant=None, now=None):
    """Today in the tenant's timezone (tenant.yaml `timezone`, else UTC):
    the render date a sale's deadline is compared with (cycle 110
    integration F4) and every run date (pipeline today_iso, the spend
    ledger; hotfix H1). `now`: an aware datetime instead of the clock.
    tests/conftest.py pins it."""
    tz = tenant_tz(tenant)
    return (now.astimezone(tz) if now is not None else datetime.datetime.now(tz)).date()


def offer_view(facts_pack, today=None):
    """The offer as the renderer prints it, from facts_pack.offer (the
    verified claim) and the product, or None when the page is not
    offer-led: {name, line, code, codes, amount, deadline, cta_note, bar,
    check, claim_id}. `line` is the product's own amount and code ("$300 off
    with code INDOOR300"); a claim with several amounts whose scope matches
    none (or several) of the product's words shows its whole offer clause."""
    fp = facts_pack or {}
    offer = fp.get("offer")
    if not offer or not offer.get("claim_id"):
        return None
    # cycle 110 integration F4: a sale whose deadline has passed is not shown (a rerender reuses facts_pack)
    if claim_expired(offer, today or render_today()):
        return None
    text = str(offer.get("text") or "")
    product_words = _product_words(fp.get("product"))
    pairs = _pairs(text)

    def applies(pair):
        words = set(re.findall(r"[a-z]+", pair["scope"].lower())) - _SCOPE_FILLER
        return not words or words <= product_words

    mine = [p for p in pairs if applies(p)]
    if len(mine) == 1 or (len(pairs) == 1 and mine):
        pair = mine[0]
        line = f"{pair['amount']} off" + (f" with code {pair['code']}" if pair["code"] else "")
        codes = [pair["code"]] if pair["code"] else []
        amount = pair["amount"]
    else:
        body = text.split(":", 1)[1] if ":" in text.split(";")[0] else text
        line = re.split(r";", body, maxsplit=1)[0].strip().rstrip(".")
        line = re.sub(r",?\s*applied at checkout$", "", line, flags=re.IGNORECASE)
        codes = [p["code"] for p in pairs if p["code"]] or [m.group("code") for m in _CODE_RE.finditer(text)]
        amount = None
    at_checkout = bool(re.search(r"\bat\s+checkout\b", text, re.IGNORECASE))
    bar = ("Code " + codes[0]) if len(codes) == 1 else ("Codes " + " / ".join(codes)) if codes else line
    list_price, net_price = _price_figures(fp, amount)
    return {
        "claim_id": offer["claim_id"],
        "name": _sale_name(text, offer),
        # Cycle 110: the deal in figures, all from the claim and the product's
        # price: "$7,950" (None when the line holds no single amount for this
        # product), "$8,250", and the last sale day in short form.
        "net_price": net_price,
        "list_price": list_price,
        "deadline_short": deadline_short(text, offer),
        "line": line,
        "amount": amount,
        "code": codes[0] if len(codes) == 1 else None,
        "codes": codes,
        "deadline": _deadline_sentence(text, offer),
        "cta_note": (("Offer applied at checkout with the code." if codes else "Offer applied at checkout.")
                     if at_checkout else ""),
        "bar": bar,
        "check": {"ad_said": offer.get("sentence") or "", "fact": text, "claim_ids": [offer["claim_id"]],
                  "offer": True},
    }


# ---------------------------------------------------------------------------
# writer lines and gates
# ---------------------------------------------------------------------------

UNVERIFIED_LINE = (
    "The ad states an offer (a sale, a discount, a code or a deadline) that is not a verified claim: say nothing "
    "about discounts, sales, codes, coupons, promotions or deadlines anywhere on the page -- neither that the "
    "offer exists nor that there is none. Never write \"no discount\", \"full price\" or \"not on sale\". Leave "
    "the offer out of checked_claims and the FAQ."
)


# Cycle 109b: the offer-led page's checked claims. The renderer adds the
# offer item before the writer's own items, so the writer is asked for 3-5
# and the gate accepts 3-6 (a writer that still writes six is not refused).
WRITER_CHECKED_RANGE = (3, 5)
GATE_CHECKED_RANGE = (3, 6)


def writer_lines(facts_pack, cartridge="landing"):
    """The writer's offer lines: what the renderer adds and what the writer
    must not write (offer-led), or the unverified rule. Cycle 109b: stated
    exactly, after prod's proof-look runs left a half-filled offer item in
    checked_claims[0], repeated the offer in a later item and denied it in
    the FAQ."""
    fp = facts_pack or {}
    if fp.get("offer_unverified"):
        return [UNVERIFIED_LINE]
    view = offer_view(fp)
    if view is None:
        return []
    added = (f"the line \"{view['line']}\" under the price"
             + (f", the sentence \"{view['deadline']}\" under it" if view["deadline"] else "")
             + (f", \"{view['cta_note']}\" under the first CTA" if view["cta_note"] else "")
             + f", \"{view['bar']}\" in the sticky bar")
    if cartridge != "landing":
        return [f"The ad is about a verified offer (claim {view['claim_id']}). The renderer adds {added}. Do not "
                "write about discounts, sales, codes, prices going up or deadlines anywhere -- not that there is "
                "an offer and not that there is none -- and no FAQ question about them."]
    lo, hi = WRITER_CHECKED_RANGE
    return [
        f"This page is OFFER-LED (verified claim {view['claim_id']}: " + json.dumps(fp["offer"]["text"]) + "). "
        f"THE RENDERER ADDS, from that claim: the sale name \"{view['name'] or ''}\" as the eyebrow, {added}, and "
        "the FIRST checked claim (the ad said: " + json.dumps(view["check"]["ad_said"]) + "; the fact: the claim "
        "text).",
        f"YOU WRITE checked_claims with {lo}-{hi} items about the ad's OTHER claims (the product, power, size, "
        "delivery, warranty): every item has ad_said, fact and claim_ids; no offer item, no placeholder or empty "
        "item for the offer, no item about the sale, a discount, a code or a deadline.",
        "Nowhere outside hero.headline and hero.subhead do you write about discounts, sales, codes, money off, "
        "prices going up or deadlines -- not that there is an offer and never that there is none (\"no discount\", "
        "\"full price\", \"not on sale\" are refused). No FAQ question about discounts, sales, codes or deadlines.",
        "hero.headline is about the offer, in words (no figures, no code, no date): name what the reader saves or "
        "the sale itself, e.g. \"Money off every indoor sauna during the sale\". hero.subhead may say the offer is "
        "applied at checkout. No countdown, no \"last chance\", no \"hurry\", no invented scarcity.",
    ]


def _cites(item, claim_id):
    return claim_id is not None and claim_id in (item.get("claim_ids") or [])


def _strip_denials(text):
    """`text` without the sentences that deny an offer, or None when none."""
    kept = [s for s in _sentences(text) if not _DENIAL_RE.search(s)]
    if len(kept) == len(_sentences(text)):
        return None
    return " ".join(kept)


def tidy_page(page, facts_pack, cartridge="landing"):
    """Cycle 109b: the offer rules applied in place before the gate, so a
    writer slip never costs a repair or STOPs a run. With an offer in the
    ad (verified or not):
      - checked_claims (landing): an item that cites the promo claim or
        states the offer, and an item left without ad_said or fact (the
        writer's placeholder for the offer), are dropped -- the renderer
        adds the verified offer itself;
      - FAQ: an entry whose question is about a discount, sale, code or
        deadline is dropped;
      - every prose field: a sentence that denies the offer ("no discount
        applied", "full price") is removed; a FAQ answer or checked fact left
        empty drops its entry.
    Returns the notes it logged (empty when nothing changed)."""
    fp = facts_pack or {}
    if not isinstance(page, dict) or not (fp.get("offer") or fp.get("offer_unverified")):
        return []
    claim_id = (fp.get("offer") or {}).get("claim_id")
    notes = []
    items = page.get("checked_claims")
    if cartridge == "landing" and isinstance(items, list):
        kept = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            said, fact = str(item.get("ad_said") or "").strip(), str(item.get("fact") or "").strip()
            if _cites(item, claim_id) or _OFFER_WORDS_RE.search(said) or _OFFER_WORDS_RE.search(fact):
                notes.append(f"dropped checked_claims[{i}]: the offer (the renderer adds it)")
            elif not said or not fact:
                notes.append(f"dropped checked_claims[{i}]: no {'ad_said' if not said else 'fact'} (an offer "
                             "placeholder)")
            else:
                kept.append(item)
        if len(kept) != len(items):
            page["checked_claims"] = kept
    faq = page.get("faq") if isinstance(page.get("faq"), dict) else None
    questions = faq.get("questions") if faq else None
    if isinstance(questions, list):
        kept = []
        for i, entry in enumerate(questions):
            if isinstance(entry, dict) and (_OFFER_WORDS_RE.search(str(entry.get("question") or ""))
                                            or _cites(entry, claim_id)):
                notes.append(f"dropped faq.questions[{i}]: a question about the offer")
                continue
            kept.append(entry)
        if len(kept) != len(questions):
            faq["questions"] = kept

    def scrub(node, path):
        if isinstance(node, dict):
            for key in list(node):
                if key in NON_PROSE_KEYS:
                    continue
                value = node[key]
                if isinstance(value, str):
                    new = _strip_denials(value)
                    if new is not None:
                        notes.append(f"removed a denial of the offer from {path}.{key}")
                        node[key] = new
                else:
                    scrub(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                if isinstance(value, str):
                    new = _strip_denials(value)
                    if new is not None:
                        notes.append(f"removed a denial of the offer from {path}[{i}]")
                        node[i] = new
                else:
                    scrub(value, f"{path}[{i}]")

    scrub(page, "$")
    if isinstance(questions, list) and faq:
        before = faq["questions"]
        faq["questions"] = [q for q in before if not (isinstance(q, dict) and not str(q.get("answer") or "").strip())]
        notes += ["dropped a FAQ entry whose answer was only a denial"] * (len(before) - len(faq["questions"]))
    if cartridge == "landing" and isinstance(page.get("checked_claims"), list):
        before = page["checked_claims"]
        page["checked_claims"] = [c for c in before if not (isinstance(c, dict) and not str(c.get("fact") or "").strip())]
        notes += ["dropped a checked claim whose fact was only a denial"] * (len(before) - len(page["checked_claims"]))
    return notes


OFFER_HEADLINE_RE = re.compile(r"\b(?:sale|savings?|save|saving|off|discount\w*|deal|offer|code|price[sd]?|"
                               r"pric(?:e|ing))\b", re.IGNORECASE)


def find_offer_headline_violations(page, facts_pack, prefix="landing"):
    """Offer-led landing page: the headline is about the offer (it names a
    saving, the sale, a price or the code in words, or a word of the sale's
    name)."""
    view = offer_view(facts_pack)
    if view is None:
        return []
    hero = page.get("hero") if isinstance(page.get("hero"), dict) else {}
    headline = str(hero.get("headline") or "")
    name_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", view["name"] or "")} - {"sale"}
    if OFFER_HEADLINE_RE.search(headline) or any(w in headline.lower().split() for w in name_words):
        return []
    return [{"path": "$.hero.headline", "key": f"{prefix}:offer_headline",
             "issue": "the ad is about a sale, so the headline is about the offer: name the saving or the sale in "
                      "words (no figures; the renderer shows the amount and the code)", "text": headline}]


def find_denial_violations(page, facts_pack, prefix="landing"):
    """With an offer in the ad (verified or not), no line may tell the reader
    there is no offer ("no discount applied", "full price")."""
    fp = facts_pack or {}
    if not (fp.get("offer") or fp.get("offer_unverified")):
        return []
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if isinstance(node, str) and _DENIAL_RE.search(node):
            problems.append({"path": path, "key": f"{prefix}:offer_denial:{path}",
                             "issue": "the ad states an offer; never tell the reader there is no discount or sale "
                                      "-- " + ("leave discounts out of the page entirely" if fp.get("offer_unverified")
                                               else "the renderer shows the verified offer"), "text": node})
    return problems


_OFFER_WORDS_RE = re.compile(r"\b(?:sales?\b" + _NOT_SALES_TALK + r"|discount\w*|promo\w*|coupons?|money\s+off|"
                             r"promo\s+codes?|(?:discount|coupon)\s+codes?)|(?:\$\s?\d[\d,]*|\d{1,2}\s?%)\s+off\b",
                             re.IGNORECASE)


def find_offer_in_checked_violations(page, facts_pack, prefix="landing"):
    """With an offer in the ad, the writer's checked_claims never carry it:
    on an offer-led page the renderer shows it as the first item (from the
    verified claim); with an unverified offer the page leaves it out."""
    fp = facts_pack or {}
    if not (fp.get("offer") or fp.get("offer_unverified")):
        return []
    problems = []
    claim_id = (fp.get("offer") or {}).get("claim_id")
    # Cycle 110: the same rule over `reasons`, and an item that cites the
    # promo claim is the offer even with no offer word ("placeholder").
    for section, fields in (("checked_claims", ("ad_said", "fact")), ("reasons", ("headline", "body"))):
        for i, item in enumerate(page.get(section) or []):
            if not isinstance(item, dict):
                continue
            hits = [(f, str(item.get(f) or "")) for f in fields if _OFFER_WORDS_RE.search(str(item.get(f) or ""))]
            if not hits and _cites(item, claim_id):
                hits = [("claim_ids", str(item.get(fields[0]) or ""))]
            if hits:
                field, text = hits[0]
                key = f"{prefix}:offer_in_checked:{i}" if section == "checked_claims" else \
                    f"{prefix}:offer_in_reasons:{i}"
                problems.append({
                    "path": f"$.{section}[{i}].{field}", "key": key,
                    "issue": (f"the renderer shows the verified offer as the first item of {section}; pick another "
                              "claim for this item" if fp.get("offer") else
                              f"the ad's offer is not verified: leave it out of {section} and pick another claim"),
                    "text": text})
    return problems


def allowed_deadline_sentences(facts_pack):
    """The verified promo claim's own deadline sentence, the one deadline
    wording the urgency gate allows (empty when there is none)."""
    view = offer_view(facts_pack)
    return [view["deadline"]] if view and view["deadline"] else []


def _norm_sentence(text):
    return re.sub(r"\s+", " ", re.sub(r"[.!?\"'\u201c\u201d]+", "", str(text or ""))).strip().lower()


def deadline_violations(text, allowed=()):
    """The sentences of `text` that state a deadline ("prices go up ...",
    "sale ends ...", "until October 16") and are not one of `allowed`."""
    ok = {_norm_sentence(a) for a in allowed}
    bad = []
    for sentence in _sentences(text):
        if DEADLINE_PHRASE_RE.search(sentence) and _norm_sentence(sentence) not in ok:
            bad.append(sentence)
    return bad


# ---------------------------------------------------------------------------
# the owner's claim form (pipeline hold)
# ---------------------------------------------------------------------------

def promo_claim_entry(text, *, by, today, existing_ids):
    """A verified.json entry for the owner's promo claim: id promo-<slug>-<date>
    (made unique), category promo, source "owner <email> <date>"."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("the claim text is empty")
    slug = re.sub(r"[^a-z0-9]+", "-", text.split(":", 1)[0].lower()).strip("-")[:40].strip("-") or "offer"
    base = f"promo-{slug}-{today}"
    cid, n = base, 2
    while cid in existing_ids:
        cid, n = f"{base}-{n}", n + 1
    return {"id": cid, "text": text, "category": PROMO_CATEGORY, "source": f"owner {by} {today}",
            "approved_by": by, "date": today}


def add_promo_claim(tenant, text, *, by, today=None, offer=None):
    """Writes the owner's promo claim to claims/verified.json (atomic, the
    file's own JSON style). With `offer` (the held ad's offer block), the text
    must match it (match_offer), so the hold is released by this claim.
    Returns the entry; raises ValueError with the reason otherwise."""
    today = today or datetime.date.today().isoformat()
    path = tenant.claims_dir / "verified.json"
    verified = json.loads(path.read_text()) if path.exists() else []
    entry = promo_claim_entry(text, by=by, today=today, existing_ids={c.get("id") for c in verified})
    if offer and match_offer(offer, [entry], today) is None:
        need = ", ".join((offer.get("amounts") or []) + (offer.get("codes") or [])) or offer.get("name") or "the offer"
        raise ValueError(f"the claim does not state the ad's offer ({need}"
                         + (f", deadline {offer['deadline']}" if offer.get("deadline") else "") + ") or its date "
                         "has passed")
    verified.append(entry)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(verified, indent=2) + "\n")
    os.replace(tmp, path)
    return entry


def run_offer(run_dir):
    """(offer block, matched claim id or None) from a built run's
    ad_brief.json, or (None, None) when the ad states no offer."""
    try:
        brief = json.loads((run_dir / "ad_brief.json").read_text())
    except (OSError, ValueError, TypeError):
        return None, None
    offer = brief.get("offer") if isinstance(brief, dict) else None
    if not isinstance(offer, dict):
        return None, None
    return offer, offer.get("claim_id")
