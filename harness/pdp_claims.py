"""Product-page (PDP) claim seeding -- fix cycle 9 item 1.

A product page's own body_html states facts that a hand-curated
claims/verified.json often never carried, so a true, on-the-page statement had
no verified claim to match against and STOPped a run at the ad_claims gate.

At the start of every run (after the live price refresh), extract a fixed,
conservative set of facts from each active product's body_html -- one phrase
check per fact, only if the page states it, text = the page's own sentence
containing the phrase. Which facts, and which phrases signal each, come from
the tenant's own tenant.yaml `pdp_facts` map; nothing is inferred.

These claims are in-memory only for the run that generated them: written to the
tenant's runs/pdp-claims-cache.json and merged into that run's verified-claims
universe -- never into claims/verified.json, which stays hand-curated.

Never produces a claim containing one of the tenant's banned terms.
"""
import html
import json
import re
import time
from pathlib import Path

from . import tenant as tenant_mod
from .textutil import product_name_slug

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
# A stripped closing tag right before punctuation leaves a stray space
# ("Free delivery</strong>," -> "Free delivery ,") -- tidy it back to
# "Free delivery,".
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CAPACITY_RE = re.compile(r"\b[1-6]-person\b", re.IGNORECASE)


def pdp_facts(tenant=None):
    """((fact, (phrase, ...)), ...) from tenant.yaml's pdp_facts map.

    A fact is only ever created if one of its own phrases is literally present
    on the page -- conservative by design, no inference. A fact with an empty
    phrase list is matched by _CAPACITY_RE instead, since the number varies per
    product."""
    tenant = tenant or tenant_mod.active()
    return tuple((k, tuple(v or ())) for k, v in (tenant.get("pdp_facts") or {}).items())


def banned_term_re(tenant=None):
    """Matches any of the tenant's absolute banned terms, or nothing when it
    has none."""
    from . import vocab

    terms = vocab.EMF_TERMS
    if not terms:
        return re.compile(r"(?!x)x")
    return re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)


def _plain_text(body_html):
    """body_html -> the text a reader sees: tags replaced with a space (so
    two paragraphs/list items never collapse into one run-on word) and HTML
    entities unescaped."""
    without_tags = _TAG_RE.sub(" ", body_html or "")
    collapsed = _WHITESPACE_RE.sub(" ", html.unescape(without_tags)).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", collapsed)


def _sentences(text):
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _best_matching_sentence(sentences, phrases):
    """The shortest sentence containing any of `phrases` (ties broken by
    document order). Shopify body copy typically states a fact once in a
    long, multi-fact intro paragraph and again in its own short, single-topic
    paragraph later on ("Plugs into a standard 120V household outlet -- no
    electrician, no dedicated circuit." vs. a run-on sentence covering the
    app, the outlet, the red light panel, and crate shipping all at once) --
    picking the shortest match favors the clean, single-topic sentence
    without needing to know the page's own structure."""
    matches = [s for s in sentences if any(phrase in s.lower() for phrase in phrases)]
    if not matches:
        return None
    return min(matches, key=len)


def extract_pdp_claims(product, raw_product, today_iso):
    """[] or a list of claim dicts (id, text, category, source, approved_by,
    date) for the fixed PDP_FACTS this product's body_html actually states.

    `product` is the curated claims/products.json entry (for its real
    `name`/`url`, so ids/sources match the rest of the claims store); `raw_
    product` is the matching raw Shopify feed entry (for `body_html`, which
    merge_products() strips out of the curated entry). Either missing
    `raw_product` or a product with no body_html yields no claims."""
    body_html = (raw_product or {}).get("body_html")
    if not body_html:
        return []

    sentences = _sentences(_plain_text(body_html))
    name_slug = product_name_slug(product["name"])

    banned_re = banned_term_re()
    claims = []
    for fact, phrases in pdp_facts():
        if not phrases:
            capacity_matches = [s for s in sentences if _CAPACITY_RE.search(s)]
            sentence = min(capacity_matches, key=len) if capacity_matches else None
        else:
            sentence = _best_matching_sentence(sentences, phrases)
        if not sentence or banned_re.search(sentence):
            continue
        claims.append(
            {
                "id": f"pdp-{name_slug}-{fact}",
                "text": sentence,
                "category": "spec",
                "source": product["url"],
                "approved_by": "site",
                "date": today_iso,
            }
        )
    return claims


def seed_pdp_claims(products, live_products_by_handle, today_iso):
    """One call per `adv run`, right after the live price refresh: every
    active product's PDP claims, flattened into one list. `products` is the
    merged claims/products.json dict (slug -> curated entry, as returned by
    prices.refresh_price_data); `live_products_by_handle` maps a product's
    handle/slug to its raw Shopify feed entry (also from refresh_price_data,
    which is where body_html actually lives)."""
    claims = []
    for slug, product in products.items():
        if not product.get("active", True):
            continue
        raw_product = live_products_by_handle.get(slug)
        claims.extend(extract_pdp_claims(product, raw_product, today_iso))
    return claims


def save_pdp_claims_cache(cache_path, claims):
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"generated_at": time.time(), "claims": claims}, indent=2))
