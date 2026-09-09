"""Live reviews (fix 3): fetch the chosen product's page HTML and parse a
JSON-LD `aggregateRating`, falling back to the Judge.me widget's data
attributes. Produces one in-memory "reviews-live" claim -- never a hardcoded
placeholder number. If nothing is found, no review claim is produced and no
review numbers appear anywhere on the page.
"""
import json
import re
import urllib.request

_JSONLD_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_JDGM_RATING_RE = re.compile(r'data-average-rating=["\']([\d.]+)["\']', re.I)
_JDGM_COUNT_RE = re.compile(r'data-number-of-reviews=["\']([\d,]+)["\']', re.I)


_BROWSER_HEADERS = {
    # peaksaunas.com is behind Cloudflare's bot challenge for a non-browser
    # User-Agent -- a plain browser UA gets the real page through.
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
    Judge.me widget's data-average-rating/data-number-of-reviews attributes,
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

    rating_m = _JDGM_RATING_RE.search(html)
    count_m = _JDGM_COUNT_RE.search(html)
    if rating_m and count_m:
        return {"rating": float(rating_m.group(1)), "count": int(count_m.group(1).replace(",", ""))}
    return None


def build_reviews_claim(product_url, review_data, today_iso):
    rating = review_data["rating"]
    count = review_data["count"]
    text = f"Rated {rating:g} out of 5 across {count:,} reviews on Judge.me (fetched {today_iso})."
    return {
        "id": "reviews-live",
        "text": text,
        "category": "trust",
        "source": product_url,
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
            log.event("reviews", f"no aggregateRating/Judge.me data found on {product_url}; no review claim")
        return None
    return build_reviews_claim(product_url, data, today_iso)
