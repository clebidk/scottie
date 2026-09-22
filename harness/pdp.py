"""Cycle 54: the product-page cartridge's `pdp` look -- renderer-owned
sections and the writer-owned structural gates.

The `pdp` look lays a product page out like the brand's own storefront
product detail page (see the tenant's docs/PDP-STRUCTURE-*.md for what was
measured), but its headline, proof tiles and FAQ answer the ad the visitor
clicked. Two halves, the same split harness/listicle.py uses:

  - render_context: everything the look shows that did NOT come from the
    writer -- the gallery, the price, the fixed financing and warranty
    sentences, the rating line, the HSA/FSA line, the short spec list in the
    buy panel, the lead-time line, the spec table, the model compare table.
    Each one is built from facts_pack alone, so "omitted when unverified" is
    structural: there is no page.json field to invent one in, and
    find_renderer_owned_violations rejects a page that adds one.
  - find_product_page_violations: the writer-owned checks the repair loop
    runs (harness/repair.py's check_page_gates) -- ad-proof tiles with claim
    ids, a 5-7 question FAQ whose factual answers carry claim ids, a promise
    line with no uncited number, no urgency vocabulary.

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
from .textutil import product_name_slug, walk_page

AD_PROOF_RANGE = (3, 4)
FAQ_COUNT_RANGE = listicle.FAQ_COUNT_RANGE
BENEFIT_BLOCK_COUNT = 3
GALLERY_MAX = 6
GALLERY_MIN_UNIQUE = 4
BUY_PANEL_SPEC_LINES = 3
SPEC_TABLE_MAX_ROWS = 10

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
         `asset-<slug>-<n>` manifest ids) that the page does not already
         show in another slot;
      4. only if that leaves fewer than GALLERY_MIN_UNIQUE images: storefront
         images the page already shows elsewhere (the benefit blocks' and
         the paragraph-image matcher's picks), in page order.

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
    shown = set(_page_asset_ids_in_order(page))
    storefront = [a["id"] for a in facts_pack.get("assets", []) if slug and a["id"].startswith(prefix)]
    for asset_id in storefront:
        if asset_id not in shown:
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


def eyebrow(facts_pack, tenant=None):
    """The small line over the product name: the model's own fit (its
    capacity/placement spec values, as the model picker builds it) and the
    tenant's product category (tenant.yaml `product_category`), whichever
    exist."""
    tenant = tenant or tenant_mod.active()
    product = facts_pack.get("product") or {}
    fit = ""
    for option in facts_pack.get("model_options") or []:
        if option.get("url") == product.get("url"):
            fit = option.get("fit") or ""
            break
    parts = [p for p in (fit, tenant.get("product_category") or "") if p]
    return " · ".join(parts)


def benefit_asset_ids(page, facts_pack, gallery_ids, *, allow_ai_renders=False):
    """One image id (or None) for each of the first BENEFIT_BLOCK_COUNT
    proof bullets: the bullet's own `image` when it has one; else the next
    unused angle_section.image / detail_images pick; else an eligible
    facts_pack asset that is neither in the gallery nor anywhere else on the
    page (a lifestyle or installation photo first). Called before assets are
    downloaded, with gallery_asset_ids' result, so render_page downloads
    exactly these."""
    assets = {a["id"]: a for a in facts_pack.get("assets", [])}
    eligible = _eligible(assets, allow_ai_renders)
    bullets = [b for b in (page.get("proof_bullets") or []) if isinstance(b, dict)][:BENEFIT_BLOCK_COUNT]
    own = [(b.get("image") or {}).get("asset_id") for b in bullets]
    taken = {i for i in own if i} | set(gallery_ids)
    spare = []
    angle = (page.get("angle_section") or {}).get("image") or {}
    for node in [angle, *(page.get("detail_images") or [])]:
        if isinstance(node, dict) and node.get("asset_id") and node["asset_id"] not in taken:
            spare.append(node["asset_id"])
    on_page = set(_page_asset_ids_in_order(page))
    pool = [a for a in facts_pack.get("assets", []) if a["id"] not in taken and a["id"] not in on_page]
    pool.sort(key=lambda a: 0 if a.get("kind") in ("lifestyle", "installation") else 1)
    spare += [a["id"] for a in pool]
    picks = []
    for asset_id in own:
        if not (asset_id and eligible(asset_id)):
            asset_id = next((s for s in spare if eligible(s) and s not in picks), None)
        if asset_id:
            spare = [s for s in spare if s != asset_id]
        picks.append(asset_id)
    return picks


def benefit_blocks(page, assets, benefit_ids):
    """The "why this model" blocks: the first BENEFIT_BLOCK_COUNT proof
    bullets, each with the image benefit_asset_ids chose for it (absent when
    its download failed -- the block then renders text only)."""
    bullets = [b for b in (page.get("proof_bullets") or []) if isinstance(b, dict)][:BENEFIT_BLOCK_COUNT]
    blocks = []
    for i, bullet in enumerate(bullets):
        asset_id = benefit_ids[i] if i < len(benefit_ids) else None
        blocks.append({"label": bullet.get("label") or "", "text": bullet.get("text") or "",
                       "asset": assets.get(asset_id) if asset_id else None})
    return blocks


def ghost_cta(page, tenant=None):
    """The buy panel's secondary action. "Book a consult" style text (the
    tenant's own cta_variants.consult, first entry) only when the tenant's
    cta_mode allows consult CTAs; otherwise the page's one cta_text as a
    text link to the same cta_url -- one destination offered twice, never a
    second phrase or a second destination."""
    tenant = tenant or tenant_mod.active()
    url = page.get("cta_url")
    text = page.get("cta_text") or ""
    if (tenant.get("cta_mode") or "buy") in ("consult", "auto"):
        variants = tenant.get("cta_variants.consult") or []
        if variants:
            consult = str(variants[0]).format(
                tenant_short_name=tenant.get("tenant_short_name") or tenant.display_name,
                short_name="", model_name="",
            ).strip()
            if consult and consult != text:
                return {"text": consult, "url": url, "kind": "button"}
    return {"text": text, "url": url, "kind": "link"}


def render_context(page, facts_pack, assets, gallery_ids, benefit_ids=(), *, tenant=None):
    """Everything the `pdp` look renders that came from facts_pack or
    tenant.yaml rather than from the writer. `assets` is render_page's
    assets_by_id after downloading (a failed download is simply absent);
    `gallery_ids`/`benefit_ids` are gallery_asset_ids'/benefit_asset_ids'
    results from before the download."""
    tenant = tenant or tenant_mod.active()
    product = facts_pack.get("product") or {}
    min_reviews = tenant.get("reviews.min_count")
    ctx = {
        "product_name": tenant.display_product_name(product.get("short_name") or product.get("name") or ""),
        "eyebrow": eyebrow(facts_pack, tenant),
        "gallery": [assets[i] for i in gallery_ids if i in assets],
        "price": price_panel(page, facts_pack),
        "financing": financing_sentence(facts_pack),
        "rating": listicle.rating_line(facts_pack, min_reviews=int(min_reviews) if min_reviews else None),
        "hsa": listicle.hsa_claim(facts_pack),
        "buy_specs": spec_rows(facts_pack, limit=BUY_PANEL_SPEC_LINES),
        "shipping": shipping_line(facts_pack, tenant),
        "warranty": warranty_line(facts_pack),
        "specs": spec_rows(facts_pack),
        "compare": facts_pack.get("model_compare"),
        "review_quote": listicle.pull_quote(facts_pack),
        "benefits": benefit_blocks(page, assets, list(benefit_ids)),
        "ghost_cta": ghost_cta(page, tenant),
        "disclosure_label": tenant.get("disclosure_label") or "",
    }
    return ctx


def context_claim_ids(ctx):
    """Every claim id the renderer-owned sections cite, for the page's
    Sources list."""
    ids = set()
    for key in ("price", "rating", "shipping", "warranty"):
        if ctx.get(key):
            ids.update(ctx[key].get("claim_ids") or [])
    if ctx.get("hsa"):
        ids.add(ctx["hsa"]["id"])
    for row in (ctx.get("buy_specs") or []) + (ctx.get("specs") or []):
        ids.update(row.get("claim_ids") or [])
    for row in (ctx.get("compare") or {}).get("rows", []):
        for cell in row.get("cells") or []:
            if cell:
                ids.update(cell.get("claim_ids") or [])
    return ids


# ---------------------------------------------------------------------------
# Writer prompt lines -- stated from the same constants the gate measures
# ---------------------------------------------------------------------------

def writer_rules_lines():
    plo, phi = AD_PROOF_RANGE
    flo, fhi = FAQ_COUNT_RANGE
    return [
        "This is a product page whose headline, proof tiles and FAQ answer the ad the visitor "
        "clicked. hero.promise is the headline: the ad's main claim or angle, rewritten for this "
        "product -- a number, price or percentage in it needs its claim_id in hero.claim_ids, so "
        "prefer a promise with no number at all.",
        f"ad_proof has {plo}-{phi} tiles. Each tile answers one point the ad made, and each "
        "carries at least one claim_id from facts_pack.verified_claims -- a tile you cannot cite "
        "is replaced by one you can.",
        f'faq.questions has {flo}-{fhi} entries: the questions a visitor from this ad asks before '
        "buying (its objections, its hidden worries), each answered in 1-3 sentences. Any answer "
        "stating a number, a price, a spec or a trigger word carries claim_ids.",
        "Never write the gallery, the price panel, the rating, the spec table, the model compare "
        "table, a lead-time line or an HSA/FSA line -- the renderer builds them from facts_pack. "
        "No urgency: no countdown, no \"limited time\", no discount push.",
    ]


# ---------------------------------------------------------------------------
# Gate: writer-owned structural checks
# ---------------------------------------------------------------------------

def find_ad_proof_violations(page):
    """3-4 proof tiles that answer the ad's angle, each one a verified claim
    (at least one claim_id; that each id exists is the shared claims gate's
    job)."""
    tiles = page.get("ad_proof")
    tiles = tiles if isinstance(tiles, list) else []
    lo, hi = AD_PROOF_RANGE
    problems = []
    if not lo <= len(tiles) <= hi:
        problems.append(_problem(
            "$.ad_proof", "product-page:ad_proof_count",
            f"ad_proof has {len(tiles)} tiles; it needs {lo}-{hi}",
        ))
    for i, tile in enumerate(tiles):
        path = f"$.ad_proof[{i}]"
        if not isinstance(tile, dict) or not (tile.get("text") or "").strip():
            problems.append(_problem(path, f"product-page:ad_proof_shape:{i}",
                                     "proof tile needs a label and a text"))
            continue
        if not tile.get("claim_ids"):
            problems.append(_problem(
                path, f"product-page:ad_proof_claims:{i}",
                "every proof tile is one verified claim -- give it at least one claim_id from "
                "facts_pack.verified_claims, or pick a different claim to show",
                text=tile.get("text"),
            ))
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
    problems += find_ad_proof_violations(page)
    problems += listicle.find_faq_violations(page, prefix="product-page")
    problems += find_promise_violations(page, digit_exempt_terms)
    problems += listicle.find_urgency_violations(page, prefix="product-page")
    problems += find_renderer_owned_violations(page)
    return problems
