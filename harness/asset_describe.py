"""Cycle 42: `harness images describe` -- vision-drafted alt text + tags for
a product's uncapped asset pool (ground.full_asset_pool), written into
tenants/<t>/brand/asset-review.json the same shape a human reviewer's save
writes (harness/asset_review.py, harness/serve.py's /images site).
`by: "vision-draft"` distinguishes an automated entry from a human one, but
nothing downstream (ground.apply_asset_review, ground.match_images_to_text)
treats them differently -- a vision-drafted alt is exactly as usable as a
reviewer-typed one.

Costed and budgeted like every other model-calling stage (see
harness/cli.py's cmd_images_describe): harness/budget.py's daily $ cap
(reserve_spend/record_spend) gates the whole invocation before any call is
made, and every call goes through the per-run Budget object the same way
harness/ingest.py's still_to_text does.

harness/pricing.py only prices bare model-family ids ("claude-haiku-4-5",
no date suffix) -- see _pricing_model_id below for why a dated models.vision
override doesn't crash cost logging.
"""
import base64
import datetime
import io
import re

from PIL import Image

from . import asset_review
from . import ground as ground_mod
from . import pricing
from . import vocab as vocab_mod
from .anthropic_client import thinking_kwargs
from .jsonutil import extract_json
from .render import download_asset
from .textutil import product_name_slug

# The real Anthropic API id for the family harness/pricing.py prices as
# "claude-haiku-4-5" -- used whenever a tenant's tenant.yaml sets no
# models.vision of its own.
DEFAULT_VISION_MODEL = "claude-haiku-4-5-20251001"

# Pillow's Image.thumbnail bounding box -- the long edge never exceeds this,
# and a smaller source is never upscaled.
MAX_IMAGE_DIMENSION = 1200
VISION_JPEG_QUALITY = 85

# --dry-run's printed estimate. Not used for any real accounting decision --
# reserve_spend/record_spend (harness/budget.py) price every actual call at
# its own real token usage; this is only a quick heads-up before spending
# anything.
ESTIMATED_COST_PER_IMAGE_USD = 0.002

# asset_review.save_asset_review is called after every SAVE_EVERY described
# assets (and once more at the end for the remainder), so a crash mid-batch
# keeps most of its progress instead of losing the whole run.
SAVE_EVERY = 10

# Fixed tag vocabulary the vision model may choose from -- a closed list
# (not free text) so tags stay usable for filtering/scoring (see
# ground.match_images_to_text) instead of drifting into synonyms per image.
ALLOWED_TAGS = (
    "exterior", "interior", "front-view", "side-view", "angle-view",
    "door-open", "door-closed", "panel", "control-panel", "bench", "heater",
    "glass", "person", "room-setting", "studio-background", "close-up",
    "cropped", "logo", "text-overlay",
)

VISION_SYSTEM_TEMPLATE = """You are writing an image-library entry for one product photo, for an ecommerce team's internal asset catalog.

Product: {name} ({title})

Describe only what is visible in the image; no health claims; never mention EMF.{forbidden_line}

Output ONLY a single JSON object with exactly these keys, no markdown fences, no commentary:
- name: a short 3-8 word hyphenated slug describing the shot, e.g. "product-exterior-wood-cabin-side-view"
- alt: one sentence, no more than 20 words, describing exactly what is visible
- tags: an array of zero or more tags chosen ONLY from this fixed list: {tag_list}

Output valid JSON only. No prose before or after, no markdown fences."""


class VisionResponseInvalid(Exception):
    """The model's response wasn't the strict JSON shape this stage needs --
    caught by describe_assets, which skips the asset and keeps going rather
    than losing the whole batch over one bad response."""


def _pricing_model_id(model):
    """pricing.PRICES_PER_MILLION only carries bare model-family ids (no
    date suffix) -- every other stage's resolved model id already matches
    one exactly (harness/config.py's DEFAULT_MODELS). models.vision may be
    configured with a dated snapshot id instead (this cycle's own default,
    DEFAULT_VISION_MODEL, is the real Anthropic API id for the family
    pricing.py calls "claude-haiku-4-5") -- match it against the known
    families by prefix so cost logging never raises pricing.UnknownModel.
    The actual API call always uses the exact configured/resolved model id;
    this normalization is for cost-ledger bookkeeping only. A model that
    matches no known family is returned unchanged, so a genuinely unpriced
    model still surfaces UnknownModel rather than being silently priced at
    $0 or some other model's rate."""
    for family in pricing.PRICES_PER_MILLION:
        if model == family or model.startswith(family + "-"):
            return family
    return model


def vision_model_for(tenant):
    """tenant.yaml's models.vision if set, else DEFAULT_VISION_MODEL."""
    return tenant.get("models.vision") or DEFAULT_VISION_MODEL


def forbidden_terms_for(tenant):
    """The tenant's absolute word bans (vocab.yaml's emf_terms), read
    directly off tenant.vocab rather than harness/vocab.py's global
    active-tenant state -- this stage must stay correct for whichever
    tenant it's called with, not whichever tenant a prior activate() call
    left active. Tenant-neutral: no company-specific word is hardcoded
    here, only the vocab.yaml key name."""
    return vocab_mod.Vocabulary(getattr(tenant, "vocab", None) or {}).emf_terms


def strip_forbidden_terms(text, forbidden_terms):
    """`text` with any forbidden term removed (case-insensitive, whole
    word), collapsing the resulting extra whitespace/punctuation. The vision
    model has no brand-voice guardrails of its own the way the writer
    prompt/claims gate do -- this is the backstop that keeps a banned word
    out of a drafted name/alt even if the model's output ignores the system
    prompt's own instruction."""
    if not forbidden_terms or not text:
        return text
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in forbidden_terms) + r")\b", re.IGNORECASE)
    cleaned = pattern.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")


def build_vision_system_prompt(product, forbidden_terms):
    """Tenant-neutral: built only from the product's own name/title (every
    tenant's claims/products.json carries both -- there is no separate
    "category" field in this schema) and the tenant's own vocab.yaml
    forbidden terms, never a hardcoded company or product-category word."""
    forbidden_line = ""
    if forbidden_terms:
        forbidden_line = f" Additionally, never use any of these words: {', '.join(forbidden_terms)}."
    return VISION_SYSTEM_TEMPLATE.format(
        name=product.get("name") or "the product",
        title=product.get("title") or product.get("name") or "the product",
        forbidden_line=forbidden_line,
        tag_list=", ".join(ALLOWED_TAGS),
    )


def candidates_for(tenant, product, *, force=False):
    """The product's uncapped pool (ground.full_asset_pool) minus assets
    already excluded in asset-review.json, minus assets that already carry
    a non-empty reviewed alt unless `force`. Order follows full_asset_pool's
    own (Shopify, then Drive, then listicle-pack)."""
    source = ground_mod.LocalFactsSource(tenant.claims_dir)
    pool = ground_mod.full_asset_pool(source, product, tenant.claims_config)
    overrides = asset_review.load_asset_review(tenant.brand_dir).get("assets") or {}
    result = []
    for asset in pool:
        override = overrides.get(asset["id"]) or {}
        if override.get("excluded"):
            continue
        if override.get("alt") and not force:
            continue
        result.append(asset)
    return result


def _parse_vision_response(text):
    data = extract_json(text)
    if not isinstance(data, dict):
        raise VisionResponseInvalid(f"response is not a JSON object: {text!r}")
    name = data.get("name")
    alt = data.get("alt")
    if not isinstance(name, str) or not name.strip():
        raise VisionResponseInvalid(f"missing/empty 'name': {data!r}")
    if not isinstance(alt, str) or not alt.strip():
        raise VisionResponseInvalid(f"missing/empty 'alt': {data!r}")
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        raise VisionResponseInvalid(f"'tags' is not a list: {data!r}")
    tags = [t for t in tags if isinstance(t, str) and t in ALLOWED_TAGS]
    return {"name": name.strip(), "alt": alt.strip(), "tags": tags}


def _image_bytes_for_vision(asset, cache_dir, *, log=None):
    """Download `asset` (reusing render.download_asset into the same
    runs/asset-cache/ dir harness/serve.py's /images review site uses, so
    the two features share one cache instead of downloading the same file
    twice), then resize it to at most MAX_IMAGE_DIMENSION on its long edge
    and re-encode as JPEG in memory with Pillow -- the exact bytes sent to
    the vision call. None if the download failed."""
    downloaded = download_asset(asset, cache_dir, log=log)
    if downloaded is None:
        return None
    with Image.open(downloaded["path"]) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=VISION_JPEG_QUALITY)
        return buf.getvalue()


def describe_one(asset, *, product, forbidden_terms, client, model, budget, log, cache_dir):
    """Vision-describe one asset. Returns a parsed {"name", "alt", "tags"}
    dict (forbidden terms already stripped from name/alt), or None if the
    image couldn't be downloaded/decoded or the model's response wasn't
    usable JSON -- either way logged, never raised, so one bad asset never
    stops the batch (describe_assets keeps going)."""
    image_bytes = _image_bytes_for_vision(asset, cache_dir, log=log)
    if image_bytes is None:
        log.event("images_describe", f"asset {asset['id']}: could not download/decode image, skipping")
        return None

    system = build_vision_system_prompt(product, forbidden_terms)
    budget.check()
    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                  "data": base64.standard_b64encode(image_bytes).decode("ascii")}},
                    {"type": "text", "text": "Describe this image and return the JSON object described in your instructions."},
                ],
            }
        ],
        **thinking_kwargs(model),
    )
    usage = response.usage
    budget.record_call(usage.input_tokens, usage.output_tokens)
    log.call(
        "images_describe", _pricing_model_id(model), usage.input_tokens, usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
    )

    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    try:
        parsed = _parse_vision_response(text)
    except Exception as e:
        log.event("images_describe", f"asset {asset['id']}: invalid vision response, skipping: {e}")
        return None

    parsed["name"] = strip_forbidden_terms(parsed["name"], forbidden_terms)
    parsed["alt"] = strip_forbidden_terms(parsed["alt"], forbidden_terms)
    return parsed


def describe_assets(tenant, assets, *, product, client, model, budget, log, save_every=SAVE_EVERY):
    """Vision-describes each of `assets` in turn, writing results into
    tenants/<t>/brand/asset-review.json (asset_review.save_asset_review)
    atomically after every `save_every` assets and once more at the end for
    the remainder, so a crash mid-batch keeps most of its progress. Returns
    {"described": N, "skipped": M}."""
    forbidden_terms = forbidden_terms_for(tenant)
    cache_dir = tenant.runs_dir / "asset-cache"
    review = asset_review.load_asset_review(tenant.brand_dir)
    overrides = dict(review.get("assets") or {})
    described, skipped = 0, 0

    for i, asset in enumerate(assets, start=1):
        result = describe_one(
            asset, product=product, forbidden_terms=forbidden_terms,
            client=client, model=model, budget=budget, log=log, cache_dir=cache_dir,
        )
        if result is None:
            skipped += 1
        else:
            entry = dict(overrides.get(asset["id"]) or {})
            entry["alt"] = result["alt"]
            entry.setdefault("excluded", False)
            entry["note"] = f"alt drafted by vision ({model}); tags: {', '.join(result['tags'])}"
            entry["by"] = "vision-draft"
            entry["at"] = asset_review.now_iso()
            if asset.get("source") == "shopify":
                entry["url"] = asset.get("url")
            overrides[asset["id"]] = entry
            described += 1

        if i % save_every == 0:
            asset_review.save_asset_review(tenant.brand_dir, {"version": 1, "assets": overrides})

    asset_review.save_asset_review(tenant.brand_dir, {"version": 1, "assets": overrides})
    return {"described": described, "skipped": skipped}


def pool_report(tenant):
    """Per-model [total, reviewed, excluded, with_alt] counts across every
    active product's uncapped pool -- the same numbers harness/serve.py's
    /images index shows per product, plus a with-alt column."""
    source = ground_mod.LocalFactsSource(tenant.claims_dir)
    overrides = asset_review.load_asset_review(tenant.brand_dir).get("assets") or {}
    rows = []
    for product in source.active_products():
        model_slug = product_name_slug(product["name"])
        pool = ground_mod.full_asset_pool(source, product, tenant.claims_config)
        total = len(pool)
        reviewed = sum(1 for a in pool if a["id"] in overrides)
        excluded = sum(1 for a in pool if overrides.get(a["id"], {}).get("excluded"))
        with_alt = sum(1 for a in pool if (overrides.get(a["id"], {}) or {}).get("alt"))
        rows.append({
            "model": model_slug, "total": total, "reviewed": reviewed,
            "excluded": excluded, "with_alt": with_alt,
        })
    return rows


def product_for_model(tenant, model_slug):
    """The active product whose product_name_slug(name) matches `model_slug`
    (case-insensitive), or None -- the same "model" identifier
    ground.select_drive_assets/select_listicle_pack_assets and
    ground.full_asset_pool already use, so e.g. `--model <slug>` lines up with the
    "model" field on every asset in that product's pool."""
    source = ground_mod.LocalFactsSource(tenant.claims_dir)
    model_slug = (model_slug or "").lower()
    for product in source.active_products():
        if product_name_slug(product["name"]).lower() == model_slug:
            return product
    return None


def available_model_slugs(tenant):
    source = ground_mod.LocalFactsSource(tenant.claims_dir)
    return sorted({product_name_slug(p["name"]) for p in source.active_products()})


def make_run_id(tenant_name, model_slug):
    return f"images-describe-{tenant_name}-{model_slug}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
