"""Review-statistics adapter for a storefront review widget.

Fetches the chosen product's page HTML and parses a JSON-LD `aggregateRating`,
falling back to the widget's own data attributes. Produces one in-memory
"reviews-live" claim -- never a hardcoded placeholder number. If nothing is
found, no review claim is produced and no review numbers appear anywhere on the
page.

The rating and count are read off the product page, but the claim's own source
points at the review platform's store page (tenant.yaml's reviews.store_url), so
the Sources list gets its own distinct line instead of colliding with the
product page's.
"""
import html as html_mod
import json
import re
import urllib.request

from .. import tenant as tenant_mod

_JSONLD_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_WIDGET_RATING_RE = re.compile(r'data-average-rating=["\']([\d.]+)["\']', re.I)
_WIDGET_COUNT_RE = re.compile(r'data-number-of-reviews=["\']([\d,]+)["\']', re.I)

def store_url(tenant=None):
    tenant = tenant or tenant_mod.active()
    return tenant.get("reviews.store_url") or ""


def platform_name(tenant=None):
    tenant = tenant or tenant_mod.active()
    return tenant.get("reviews.platform_name") or "the review platform"


_BROWSER_HEADERS = {
    # A storefront behind a bot challenge rejects a non-browser User-Agent --
    # a plain browser UA gets the real page through.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def http_fetch(url):
    req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _find_aggregate_rating(obj):
    if isinstance(obj, dict):
        ar = obj.get("aggregateRating")
        if isinstance(ar, dict):
            rating = ar.get("ratingValue")
            count = ar.get("reviewCount") or ar.get("ratingCount")
            if rating and count:
                return rating, count
        for v in obj.values():
            found = _find_aggregate_rating(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_aggregate_rating(item)
            if found:
                return found
    return None


def parse_review_data(html):
    """{"rating": float, "count": int} from JSON-LD aggregateRating, else the
    the review widget's data-average-rating/data-number-of-reviews attributes,
    else None."""
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        found = _find_aggregate_rating(data)
        if found:
            rating, count = found
            try:
                return {"rating": float(rating), "count": int(str(count).replace(",", ""))}
            except (TypeError, ValueError):
                continue

    rating_m = _WIDGET_RATING_RE.search(html)
    count_m = _WIDGET_COUNT_RE.search(html)
    if rating_m and count_m:
        return {"rating": float(rating_m.group(1)), "count": int(count_m.group(1).replace(",", ""))}
    return None


def build_reviews_claim(product_url, review_data, today_iso, tenant=None):
    tenant = tenant or tenant_mod.active()
    rating = review_data["rating"]
    count = review_data["count"]
    text = tenant.format(
        "reviews.claim_template",
        rating=f"{rating:g}",
        count=f"{count:,}",
        platform_name=platform_name(tenant),
        date=today_iso,
    )
    return {
        "id": "reviews-live",
        "text": text,
        "category": "trust",
        "source": store_url(tenant) or product_url,
        "approved_by": "live-fetch",
        "date": today_iso,
    }


def fetch_reviews_claim(product_url, today_iso, fetch=http_fetch, log=None):
    """The in-memory "reviews-live" claim for product_url, or None if the
    fetch fails or no review data is found on the page (never a placeholder)."""
    try:
        html = fetch(product_url)
    except Exception as e:
        if log:
            log.event("reviews", f"fetch failed for {product_url} ({e}); no review claim")
        return None

    data = parse_review_data(html)
    if not data:
        if log:
            log.event("reviews", f"no aggregateRating or widget data found on {product_url}; no review claim")
        return None
    return build_reviews_claim(product_url, data, today_iso)


# ---------------------------------------------------------------------------
# Cycle 110: the store-level rating and verbatim review texts
# ---------------------------------------------------------------------------
# The review platform's public store page states the store-wide rating in its
# meta description ("... gave <Store> ... 4.6 out of 5 stars based on 4260
# reviews."). The product reviews come from the platform's public widget
# endpoint (the same JSON the storefront widget loads; no token), the
# store-wide ones from its all-reviews endpoint (HTML). Both need the store's
# *.myshopify.com domain (tenant.yaml reviews.shop_domain, else SHOPIFY_STORE).

_STORE_SUMMARY_RE = re.compile(
    r"([0-5](?:\.\d+)?)\s+out of 5 stars?\s+based on\s+([\d,]+)\s+reviews", re.I)
_META_DESC_RE = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']'
                           r'|<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.I)
# The two endpoint URL templates are tenant.yaml reviews.widget_url_template
# ({shop}, {page}, {per_page}, {product_id}) and reviews.all_reviews_url_template
# ({shop}, {page}, {per_page}); with neither set, no review text is fetched.
# A review whose own data says its media came from a trained model (IPTC
# "TrainedAlgorithmicMediaDigitalSource") is never quoted.
AI_SOURCE_MARK = "TrainedAlgorithmicMediaDigitalSource"


def shop_domain(tenant=None):
    import os

    tenant = tenant or tenant_mod.active()
    return (tenant.get("reviews.shop_domain") or os.environ.get("SHOPIFY_STORE") or "").strip()


def parse_store_summary(html):
    """{"rating": float, "count": int} from the store page's meta description
    (then anywhere in the page), or None."""
    texts = [a or b for a, b in _META_DESC_RE.findall(html or "")] + [html or ""]
    for text in texts:
        m = _STORE_SUMMARY_RE.search(text)
        if m:
            return {"rating": float(m.group(1)), "count": int(m.group(2).replace(",", ""))}
    return None


def build_store_claim(review_data, today_iso, tenant=None):
    tenant = tenant or tenant_mod.active()
    template = tenant.get("reviews.store_claim_template") or \
        "Rated {rating} out of 5 from {count} reviews on {platform_name} (fetched {date})."
    text = template.format(rating=f"{review_data['rating']:g}", count=f"{review_data['count']:,}",
                           platform_name=platform_name(tenant), date=today_iso)
    return {
        "id": "reviews-store-live",
        "text": text,
        "category": "trust",
        "source": store_url(tenant),
        "approved_by": "live-fetch",
        "date": today_iso,
        "rating": review_data["rating"],
        "count": review_data["count"],
    }


def fetch_store_claim(today_iso, fetch=http_fetch, log=None, tenant=None):
    """The in-memory "reviews-store-live" claim, or None (fetch failed, no
    store page configured, or no rating found -- never a placeholder)."""
    url = store_url(tenant)
    if not url:
        return None
    try:
        html = fetch(url)
    except Exception as e:
        if log:
            log.event("reviews", f"store page fetch failed ({e}); no store review claim")
        return None
    data = parse_store_summary(html)
    if not data:
        if log:
            log.event("reviews", f"no store rating found on {url}; no store review claim")
        return None
    return build_store_claim(data, today_iso, tenant)


def _plain(body_html):
    text = re.sub(r"<br\s*/?>|</p>\s*<p[^>]*>", " ", str(body_html or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def parse_widget_reviews(raw):
    """The product widget's JSON -> [{uuid, author, body, stars, date, verified,
    product_title, product_url, ai_media}]."""
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    out = []
    for r in (data or {}).get("reviews") or []:
        try:
            stars = int(r.get("rating"))
        except (TypeError, ValueError):
            continue
        out.append({
            "uuid": r.get("uuid") or "",
            "author": (r.get("reviewer_name") or "").strip(),
            "body": _plain(r.get("body_html")),
            "stars": stars,
            "date": str(r.get("created_at") or "")[:10],
            "verified": r.get("verified_buyer") is True,
            "product_title": (r.get("product_title") or "").strip(),
            "product_url": r.get("product_url") or "",
            "ai_media": AI_SOURCE_MARK in str(r.get("ai_content_digital_source_type") or ""),
        })
    return out


_REV_SPLIT_RE = re.compile(r"(?=<div class=['\"]jdgm-rev )")


def _attr(chunk, pattern):
    m = re.search(pattern, chunk, re.S)
    return m.group(1) if m else ""


def parse_all_reviews_html(raw):
    """The store-wide endpoint's JSON {"html": ...} (or the HTML itself) -> the
    same review dicts as parse_widget_reviews."""
    text = raw
    if isinstance(raw, (str, bytes)):
        try:
            text = json.loads(raw).get("html", "")
        except (ValueError, AttributeError):
            text = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
    out = []
    for chunk in _REV_SPLIT_RE.split(text or "")[1:]:
        score = _attr(chunk, r"data-score=['\"](\d)['\"]")
        if not score:
            continue
        stamp = _attr(chunk, r"jdgm-rev__timestamp[^>]*data-content=['\"]([^'\"]+)['\"]") or \
            _attr(chunk, r"jdgm-rev__timestamp[^>]*datetime=['\"]([^'\"]+)['\"]")
        out.append({
            "uuid": _attr(chunk, r"data-review-id=['\"]([^'\"]+)['\"]"),
            "author": html_mod.unescape(_attr(chunk, r"<span class=['\"]jdgm-rev__author['\"]>([^<]*)<")).strip(),
            "body": _plain(_attr(chunk, r"<div class=['\"]jdgm-rev__body['\"]>(.*?)</div>")),
            "stars": int(score),
            "date": stamp[:10],
            "verified": "data-verified-buyer='true'" in chunk or 'data-verified-buyer="true"' in chunk,
            "product_title": html_mod.unescape(_attr(chunk, r"class=['\"]jdgm-rev__prod-link['\"]>([^<]*)<")).strip(),
            "product_url": _attr(chunk, r"<a href=['\"]([^'\"]+)['\"][^>]*class=['\"]jdgm-rev__prod-link['\"]"),
            "ai_media": AI_SOURCE_MARK in chunk,
        })
    return out


def fetch_reviews(product_id, *, fetch=http_fetch, log=None, tenant=None, per_page=30, pages=2,
                  store_pages=1):
    """(product_reviews, store_reviews) read from the platform's public
    endpoints; ([], []) when the shop domain is unknown or every fetch fails.
    Never raises."""
    tenant = tenant or tenant_mod.active()
    widget_url = tenant.get("reviews.widget_url_template") or ""
    all_url = tenant.get("reviews.all_reviews_url_template") or ""
    shop = shop_domain(tenant)
    if not shop or not (widget_url or all_url):
        if log:
            log.event("reviews", "no reviews.shop_domain / SHOPIFY_STORE or no review URL template; no review quotes")
        return [], []
    product_reviews, store_reviews = [], []
    if product_id and widget_url:
        for page in range(1, pages + 1):
            try:
                batch = parse_widget_reviews(fetch(widget_url.format(shop=shop, page=page, per_page=per_page,
                                                                     product_id=product_id)))
            except Exception as e:
                if log:
                    log.event("reviews", f"product reviews fetch failed (page {page}: {e})")
                break
            product_reviews.extend(batch)
            if len(batch) < per_page:
                break
    for page in range(1, (store_pages if all_url else 0) + 1):
        try:
            store_reviews.extend(parse_all_reviews_html(fetch(all_url.format(
                shop=shop, page=page, per_page=25))))
        except Exception as e:
            if log:
                log.event("reviews", f"store reviews fetch failed (page {page}: {e})")
            break
    return product_reviews, store_reviews
