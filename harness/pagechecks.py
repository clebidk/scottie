"""Deterministic page checks that are not claim checks (docs/KIMI-LONG-RUN.md
phase 2): image allowlist, internal links, JSON-LD validity, HTML validity.

Two homes, by who can fix the failure:

- The page.json-level checks (image allowlist, internal links) run inside
  repair.check_page_gates, so the writer repair loop can fix them -- the writer
  owns a page's asset ids and CTA urls.
- The rendered-HTML checks (HTML validity, rendered JSON-LD parse/type, the
  rendered internal-link count) are post-render backstops called from
  render.render_page, next to the existing html_visible_text backstop.
  Writer prose is autoescaped (review R37) and JSON-LD goes through Jinja's
  | tojson, so invalid HTML or bad JSON-LD is a template/renderer/tenant-file
  bug no writer repair can fix -- it STOPs the run (exit 2, no partial page)
  instead of burning repair calls.

Every check returns a list of problem dicts in the same shape
claims.gate_page_json's checks use ({path, issue, ...}); empty means pass.
"""
import json
import re
from urllib.parse import urlparse

import html5lib

from . import tenant as tenant_mod
from . import vocab
from .textutil import walk_page

# page.json keys that carry a link target. Every cartridge's CTA is one of
# these (article nests it at cta.url; the others use a flat cta_url); image
# fields reference asset ids, never urls, and the Sources list is built by
# the renderer from claim sources -- page.json itself never carries a
# legitimate external link.
_URL_KEYS = ("url", "cta_url")

# The schema.org type each cartridge's rendered JSON-LD block must carry --
# mirrors render.build_json_ld's branches. A cartridge with no branch
# produces no JSON-LD at all, which this check treats as a failure: the
# renderer contract is that every page ships structured data.
JSON_LD_TYPES = {
    "article": "Article",
    "product-page": "Product",
    "listicle": "ItemList",
    "longform": "FAQPage",
    "comparison": "FAQPage",
}

_LD_SCRIPT_RE = re.compile(r'<script\s+type="application/ld\+json">(.*?)</script>', re.DOTALL)
_ANCHOR_HREF_RE = re.compile(r'<a\s[^>]*?href="([^"]*)"', re.IGNORECASE)


def _walk_keyed_strings(node, keys, path="$"):
    """Yield (path, key, value) for every dict entry whose key is in `keys`
    and whose value is a non-empty string, anywhere in page.json."""
    for node_path, n in walk_page(node, path):
        if isinstance(n, dict):
            for k, v in n.items():
                if k in keys and isinstance(v, str) and v:
                    yield f"{node_path}.{k}", k, v


def _is_internal_url(url, site_host):
    """A relative path or same-page anchor is internal; an absolute URL is
    internal when its host (www. stripped) is the tenant's site_host."""
    if url.startswith(("/", "#")):
        return True
    host = urlparse(url).netloc.replace("www.", "")
    return bool(site_host) and host == site_host


def find_image_allowlist_violations(page, facts_pack):
    """Every asset_id referenced in page.json must exist in this run's
    facts_pack.assets (the run's manifest). Today an unknown id is silently
    dropped at render (the template's `{% if asset %}` guards), shipping a
    page with a missing image and no failure -- this makes it loud while the
    writer can still repair it."""
    allowed = {a.get("id") for a in facts_pack.get("assets", [])}
    problems = []
    for path, _key, asset_id in _walk_keyed_strings(page, ("asset_id",)):
        if asset_id not in allowed:
            problems.append({
                "path": path,
                "issue": f"asset id {asset_id!r} is not in this run's asset manifest; "
                         "use only asset ids from facts_pack.assets",
            })
    return problems


def find_internal_link_violations(page, *, tenant=None):
    """Every link target in page.json must be internal (a relative path, a
    same-page anchor, or the tenant's own site_host), and the page must carry
    at least one internal link. External absolute urls in writer-controlled
    fields are never correct -- even a claim's source url reaches the page
    only through the renderer's Sources list."""
    tenant = tenant or tenant_mod.active()
    site_host = (tenant.get("site_host") or "").replace("www.", "")
    problems = []
    internal = 0
    for path, _key, url in _walk_keyed_strings(page, _URL_KEYS):
        if _is_internal_url(url, site_host):
            internal += 1
            continue
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https"):
            problems.append({"path": path, "issue": f"link target {url!r} is not an http(s) url or a relative path"})
        else:
            problems.append({
                "path": path,
                "issue": f"link target {url!r} is not an internal link ({site_host or 'tenant site_host'}); "
                         "link to the tenant's own site or a relative path",
            })
    if internal == 0 and not problems:
        problems.append({"path": "$", "issue": "page has no internal link; every page needs at least one (the CTA)"})
    return problems


def find_html_validity_violations(html):
    """The rendered page must parse with zero errors under an HTML5 parser
    (html5lib strict mode: unclosed tags, bad nesting, malformed comments).
    Known-good pages pass; a template or tenant-injected block that breaks
    the document fails here, before index.html is written."""
    parser = html5lib.HTMLParser(strict=True)
    try:
        parser.parse(html)
    except Exception as e:
        return [{"path": "$.html", "issue": f"rendered HTML does not parse: {e}"}]
    return []


def find_rendered_json_ld_violations(html, cartridge_name):
    """The rendered page's JSON-LD block(s) must parse as JSON and carry the
    cartridge's schema.org @type (JSON_LD_TYPES). Runs on the rendered HTML,
    not the pre-render dict, so what is checked is exactly what ships."""
    expected = JSON_LD_TYPES.get(cartridge_name)
    problems = []
    blocks = _LD_SCRIPT_RE.findall(html)
    if not blocks:
        return [{"path": "$.json_ld", "issue": "no JSON-LD block in the rendered page"}]
    for i, block in enumerate(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError as e:
            problems.append({"path": f"$.json_ld[{i}]", "issue": f"JSON-LD does not parse: {e}"})
            continue
        if data.get("@context") != "https://schema.org":
            problems.append({"path": f"$.json_ld[{i}]", "issue": f"JSON-LD @context is {data.get('@context')!r}, not https://schema.org"})
        actual = data.get("@type")
        if expected is None:
            problems.append({"path": f"$.json_ld[{i}]", "issue": f"cartridge {cartridge_name!r} has no registered JSON-LD type (got {actual!r})"})
        elif actual != expected:
            problems.append({"path": f"$.json_ld[{i}]", "issue": f"JSON-LD @type is {actual!r}; {cartridge_name} pages must be {expected!r}"})
    return problems


def find_rendered_internal_link_violations(html, *, tenant=None):
    """The rendered page must contain at least one internal <a href> -- the
    post-render count guard behind find_internal_link_violations, catching a
    template regression that drops the CTA anchor even when page.json was
    fine."""
    tenant = tenant or tenant_mod.active()
    site_host = (tenant.get("site_host") or "").replace("www.", "")
    hrefs = _ANCHOR_HREF_RE.findall(html)
    if any(_is_internal_url(href, site_host) for href in hrefs):
        return []
    return [{"path": "$.links", "issue": "rendered page has no internal link; every page needs at least one (the CTA)"}]


def find_block_violations(page, cartridge_name, block_slots=None):
    """page.json's optional top-level "blocks" map records the writer's
    layout-block variant choice per section slot ({slot: block-id}) so scores
    attach to blocks (docs/KIMI-LONG-RUN.md phase 3). Every slot must be one
    the cartridge's schema declares in "block_slots", and every chosen id a
    registered block that slot allows. Absent "blocks" is fine -- the
    cartridge's defaults render."""
    blocks_map = page.get("blocks")
    if blocks_map is None:
        return []
    if not isinstance(blocks_map, dict):
        return [{"path": "$.blocks", "issue": '"blocks" must be an object mapping a section slot to a registered block id'}]
    from . import blocks as blocks_mod

    registered = set(blocks_mod.block_names())
    problems = []
    for slot, block_id in blocks_map.items():
        path = f"$.blocks.{slot}"
        slot_spec = (block_slots or {}).get(slot)
        if slot_spec is None:
            problems.append({"path": path, "issue": f"{cartridge_name} has no block slot {slot!r}; declared slots: {sorted(block_slots or {})}"})
            continue
        if block_id not in registered:
            problems.append({"path": path, "issue": f"unknown block id {block_id!r}; registered: {sorted(registered)}"})
            continue
        allowed = slot_spec.get("blocks") or []
        if block_id not in allowed:
            problems.append({"path": path, "issue": f"block {block_id!r} is not allowed for slot {slot!r}; choose one of {allowed}"})
    return problems


def find_forbidden_term_urls(facts_pack, terms=None):
    """Every URL this run uses whose own path contains one of the tenant's
    banned terms. A storefront handle is outside this harness's control, so a
    page still links to the product URL as-is -- but each such URL is logged
    per run and listed in REVIEW.md so it stays visible."""
    if terms is None:
        terms = vocab.VISIBLE_TEXT_FORBIDDEN_TERMS
    terms = [t.lower() for t in terms]
    if not terms:
        return []
    urls = []
    product_url = facts_pack.get("product", {}).get("url")
    if product_url and any(t in product_url.lower() for t in terms):
        urls.append(product_url)
    for claim in facts_pack.get("verified_claims", []):
        source = claim.get("source", "")
        if source.startswith("http") and any(t in source.lower() for t in terms) and source not in urls:
            urls.append(source)
    return urls
