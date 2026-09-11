"""Grounding: turn an ad_brief + the local claims store into a small facts_pack
the writer can cite from. `FactsSource` is a protocol so `write`/`cli` don't
care whether facts came from the local JSON files or (later) g Brain.
"""
import json
import re
from pathlib import Path
from typing import Protocol

from . import tenant as tenant_mod
from .errors import UnknownProduct
from .prices import format_price
from .textutil import DOLLAR_AMOUNT_RE, product_name_slug
from .tenant import DEFAULT_CLAIMS_CONFIG as DEFAULT_CONFIG

# Fix 8: for the chosen product's Drive assets, prefer lifestyle/interior,
# then render, then installation shots. Never video/logo/ugc in V1.
DRIVE_ASSET_TIERS = (("lifestyle", "interior"), ("render",), ("installation",))
DRIVE_ASSET_NEVER_KINDS = {"video", "logo", "ugc"}
DRIVE_ASSET_MAX = 6

def listicle_pack_models(tenant=None):
    """The models the tenant's own listicle asset pack actually covers
    (tenant.yaml's listicle_pack_models). Wiring the pack in for a model it has
    no assets for would just add empty lookups."""
    tenant = tenant or tenant_mod.active()
    return {m.lower() for m in (tenant.get("listicle_pack_models") or ())}
# Real photos first (photo_product/photo_install, plus still_video -- brand
# generally, not model-specific); an ai_render only ever becomes eligible via
# select_listicle_pack_assets's own allow_ai_renders gate.
LISTICLE_PACK_TIERS = (("photo_product", "photo_install", "still_video"), ("ai_render",))
LISTICLE_PACK_ASSET_MAX = 6

def benefit_allowlist_ids(tenant=None):
    """Cleared, product-wide (not per-model) claims every product's facts_pack
    always carries -- what the product actually does or is built with, as
    opposed to price/shipping/warranty/returns. From tenant.yaml's
    benefit_allowlist_ids; without them the writer has nothing to cite when
    describing the product itself."""
    tenant = tenant or tenant_mod.active()
    return set(tenant.get("benefit_allowlist_ids") or ())


class FactsSource(Protocol):
    def facts_for(self, product_slug, ad_brief) -> dict: ...


def load_claims_config(claims_dir):
    """The tenant's claims/config.json, with defaults filled in for any missing
    key. Kept as a directory-taking function so a caller with only a claims dir
    (a test, a one-off script) still works; a full run goes through
    tenant.claims_config, which layers tenant.yaml's defaults underneath."""
    config_path = Path(claims_dir) / "config.json"
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        config.update(json.loads(config_path.read_text()))
    return config


# Fix cycle 9 item 2: product inference by price -- only claims_made and the
# ad's own hook/promise/angle count as "the ad brief contains a dollar
# amount"; speaker_experience is deliberately excluded (a hedge like "around
# $200 a month" is the speaker's own estimate, not a quoted price, and
# ingest.py fix cycle 9 item 3 already keeps it out of claims_made).


def _quoted_dollar_amounts(ad_brief):
    fields = [ad_brief.get("hook", ""), ad_brief.get("promise", ""), ad_brief.get("angle", "")]
    fields += [c for c in ad_brief.get("claims_made", []) if isinstance(c, str)]
    text = " ".join(f for f in fields if isinstance(f, str))
    amounts = []
    for m in DOLLAR_AMOUNT_RE.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return amounts


def _pick_by_quoted_price(active_products, ad_brief):
    """(amount, product) for the one active product whose current price is
    within $1 of a dollar amount quoted in the ad brief, or None if no
    amount is quoted, none lines up with any active product's price, or more
    than one distinct product would match (ambiguous -- not a signal)."""
    matches = []
    for amount in _quoted_dollar_amounts(ad_brief):
        for p in active_products:
            price = p.get("price")
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if abs(amount - price) <= 1.0:
                matches.append((amount, p))
    distinct_slugs = {p["slug"] for _, p in matches}
    if len(distinct_slugs) == 1:
        return matches[0]
    return None


def select_drive_assets(assets_index, model_slug, limit=DRIVE_ASSET_MAX):
    """Up to `limit` Drive assets for model_slug, in tier order
    (lifestyle/interior, then render, then installation), never
    video/logo/ugc, skipping any asset marked excluded."""
    candidates = [
        a
        for a in assets_index.get("assets", [])
        if a.get("model") == model_slug and a.get("kind") not in DRIVE_ASSET_NEVER_KINDS and not a.get("excluded")
    ]
    download_pattern = assets_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
    selected, seen = [], set()
    for tier in DRIVE_ASSET_TIERS:
        if len(selected) >= limit:
            break
        for a in candidates:
            if len(selected) >= limit:
                break
            if a["id"] in seen or a.get("kind") not in tier:
                continue
            seen.add(a["id"])
            selected.append(
                {
                    "id": f"asset-drive-{a['id']}",
                    "drive_id": a["id"],
                    "url": download_pattern.format(id=a["id"]),
                    "kind": a.get("kind"),
                    "alt": a.get("title") or f"{model_slug} sauna",
                }
            )
    return selected


def select_listicle_pack_assets(pack_index, model_slug, allow_ai_renders, limit=LISTICLE_PACK_ASSET_MAX):
    """Up to `limit` assets from brand/assets-listicle-pack.json for
    model_slug (whichever models the pack covers), real photos (photo_product,
    photo_install) and brand stills (still_video) first, an ai_render only if
    `allow_ai_renders` is true (claims/config.json's allow_ai_renders, default
    False) -- never selected otherwise, per docs/DRIVE-AUDIT-LISTICLE.md's
    policy-decision-needed flag. An excluded asset is skipped either way."""
    candidates = [
        a
        for a in pack_index.get("assets", [])
        if (a.get("model") == model_slug or a.get("kind") == "still_video") and not a.get("excluded")
    ]
    download_pattern = pack_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
    selected, seen = [], set()
    for tier in LISTICLE_PACK_TIERS:
        if len(selected) >= limit:
            break
        for a in candidates:
            if len(selected) >= limit:
                break
            if a["id"] in seen or a.get("kind") not in tier:
                continue
            if a.get("ai_generated") and not allow_ai_renders:
                continue
            seen.add(a["id"])
            selected.append(
                {
                    "id": f"asset-listicle-{a['id']}",
                    "drive_id": a["id"],
                    "url": download_pattern.format(id=a["id"]),
                    "kind": a.get("kind"),
                    "alt": a.get("title") or f"{model_slug} sauna",
                    "ai_generated": bool(a.get("ai_generated")),
                }
            )
    return selected


class LocalFactsSource:
    """Reads claims/products.json and claims/verified.json from disk."""

    def __init__(self, claims_dir):
        self.claims_dir = Path(claims_dir)
        self.brand_dir = self.claims_dir.parent / "brand"
        self._products = None
        self._verified = None
        self._assets_index = None
        self._listicle_pack_index = None

    def _load(self):
        if self._products is None:
            self._products = json.loads((self.claims_dir / "products.json").read_text())["products"]
        if self._verified is None:
            self._verified = json.loads((self.claims_dir / "verified.json").read_text())

    def _load_assets_index(self):
        if self._assets_index is None:
            assets_path = self.brand_dir / "assets.json"
            self._assets_index = json.loads(assets_path.read_text()) if assets_path.exists() else {"assets": []}
        return self._assets_index

    def _load_listicle_pack_index(self):
        if self._listicle_pack_index is None:
            pack_path = self.brand_dir / "assets-listicle-pack.json"
            self._listicle_pack_index = json.loads(pack_path.read_text()) if pack_path.exists() else {"assets": []}
        return self._listicle_pack_index

    def all_verified_claims(self, live_price_claims=None, extra_claims=None):
        """The full claims/verified.json universe, for gating ad_brief.claims_made
        (which can reference anything approved, not just the eventual product's
        curated facts_pack subset). live_price_claims (fix 2), if given, is a
        slug -> claim map of this run's freshly-fetched price claims, which
        take priority over the static price-* entries they replace.
        extra_claims (fix cycle 9 item 1), if given, is this run's freshly-
        seeded PDP claims (pdp_claims.seed_pdp_claims) -- appended as-is,
        never written to claims/verified.json."""
        self._load()
        claims = self._verified
        if live_price_claims:
            live_by_id = {c["id"]: c for c in live_price_claims.values()}
            claims = [c for c in claims if c["id"] not in live_by_id]
            claims = claims + list(live_by_id.values())
        if extra_claims:
            claims = list(claims) + list(extra_claims)
        return claims

    def pick_product(self, product_slug, ad_brief):
        product, _warning = self.pick_product_with_warning(product_slug, ad_brief)
        return product

    def pick_product_with_warning(self, product_slug, ad_brief):
        """Cycle 8 problem 1b: an explicit --product still wins outright. Otherwise
        match each active model's real name (never an alias, a nickname, or a
        capacity phrase -- only the product's own `name` field is matched)
        against the ad's transcript/brief, case-insensitively and on a whole
        word/phrase boundary so a short model name does not match inside some
        unrelated longer word. If exactly one model is
        named, pick it. If several are named, pick whichever is mentioned first
        in the haystack. If none is named, fall back to the default product (or
        the first active one if none is marked default) and return a warning
        string for REVIEW.md; a `product_slug`/named match never carries a
        warning. Discontinued models (`active: false`) are never picked either
        way.
        """
        self._load()
        products = self._products

        if product_slug:
            for slug, p in products.items():
                if slug == product_slug or p["name"].lower() == product_slug.lower():
                    return p, None
            raise UnknownProduct(
                f"unknown --product: {product_slug!r}; available: "
                + ", ".join(sorted(products))
            )

        active_products = [p for p in products.values() if p.get("active", True)]

        haystack = " ".join(
            [
                ad_brief.get("transcript_or_text", ""),
                ad_brief.get("hook", ""),
                ad_brief.get("promise", ""),
                ad_brief.get("angle", ""),
            ]
        ).lower()

        named = []
        for p in active_products:
            pattern = r"\b" + re.escape(p["name"].lower()) + r"\b"
            m = re.search(pattern, haystack)
            if m:
                named.append((m.start(), p))
        if named:
            named.sort(key=lambda t: t[0])
            return named[0][1], None

        # Fix cycle 9 item 2: no model named -- if the ad quotes a dollar
        # amount that lines up (within $1) with exactly one active product's
        # current price, infer that product rather than falling through to
        # the default. price-comparison-v2.mov never names a model ("I think
        # I'm going to buy the sauna") but does say "$5,450", which is
        # exactly one active model's price and no other's.
        price_pick = _pick_by_quoted_price(active_products, ad_brief)
        if price_pick:
            amount, product = price_pick
            return product, f"product inferred from quoted price {format_price(amount)} = {product['name']}"

        for p in active_products:
            if p.get("default"):
                return p, f"product not named in ad; defaulted to {p['name']}"

        default_product = active_products[0] if active_products else next(iter(products.values()))
        return default_product, f"product not named in ad; defaulted to {default_product['name']}"

    def _comparison_targets(self, product):
        """Kimi long-run phase 6: the subjects a comparison cartridge may put
        in its spec table, and the claims that back their rows.

        Two sources:
        - the tenant's OWN other active products (from claims/products.json;
          discontinued models -- active: false -- are never targets), each
          row citing the model's own spec-/price- claim from
          claims/verified.json;
        - claims/competitors/*.json entries, each one a sourced comparison
          subject -- loaded ONLY once its file carries an approved_by (a
          pending file is skipped, so no run can name a competitor before
          an operator approves the competitor claims; see the directory's
          README).

        Returns (targets, backing_claims): targets is the writer-facing list;
        backing_claims is the claim entries (id/text/category/source) that
        must join facts_pack.verified_claims so the page gate can cite the
        rows."""
        self._load()
        targets = []
        backing = []
        by_id = {c["id"]: c for c in self._verified}
        for p in self._products.values():
            if p["slug"] == product["slug"] or not p.get("active", True):
                continue
            rows = []
            price_claim = by_id.get(f"price-{product_name_slug(p['name'])}")
            if price_claim:
                rows.append({"label": "Price", "text": price_claim["text"], "claim_ids": [price_claim["id"]]})
                backing.append(price_claim)
            for s in p.get("specs", []):
                claim = by_id.get(s.get("claim_id") or "")
                if claim:
                    rows.append({"label": s["label"], "text": s["value"], "claim_ids": [claim["id"]]})
                    backing.append(claim)
            if rows:
                targets.append({"id": product_name_slug(p["name"]), "name": p["name"],
                                "short_name": p.get("short_name", p["name"]),
                                "kind": "own-product", "rows": rows})

        competitors_dir = self.claims_dir / "competitors"
        if competitors_dir.is_dir():
            for path in sorted(competitors_dir.glob("*.json")):
                entry = json.loads(path.read_text())
                if not entry.get("approved_by"):
                    continue  # pending operator approval -- never loaded
                rows = []
                for row in entry.get("rows", []):
                    claim = {"id": row["id"], "text": row["text"], "category": "comparison", "source": row["source"]}
                    rows.append({"label": row["label"], "text": row["text"], "claim_ids": [row["id"]]})
                    backing.append(claim)
                if rows:
                    targets.append({"id": entry["id"], "name": entry["name"],
                                    "kind": entry.get("kind", "competitor"), "rows": rows})
        # dedupe backing claims by id, preserving order
        seen, unique = set(), []
        for c in backing:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique.append(c)
        return targets, unique

    def facts_for(self, product_slug, ad_brief, *, config=None, live_price_claim=None, reviews_claim=None, pdp_claims=None, include_comparison=False):
        self._load()
        product = self.pick_product(product_slug, ad_brief)
        config = config or load_claims_config(self.claims_dir)

        specs = [dict(s) for s in product.get("specs", [])]
        shopify_assets = [
            {"id": f"asset-{product['slug']}-{i + 1}", "url": url, "kind": "image", "alt": f"{product['name']} sauna"}
            for i, url in enumerate(product.get("image_urls", []))
        ]
        name_slug = product_name_slug(product["name"])
        drive_assets = select_drive_assets(self._load_assets_index(), name_slug)
        listicle_pack_assets = []
        if name_slug in listicle_pack_models():
            listicle_pack_assets = select_listicle_pack_assets(
                self._load_listicle_pack_index(), name_slug, bool(config.get("allow_ai_renders"))
            )
        assets = shopify_assets + drive_assets + listicle_pack_assets  # Shopify images stay first, as hero (fix 8)

        price_id = f"price-{name_slug}"
        spec_ids = {s["claim_id"] for s in specs if s.get("claim_id")}
        # Fix cycle 8 problem 2: claims/products.json's own `specs` list only
        # ever carried the handful of fields Shopify already exposed (capacity,
        # cabin material, max temp, ...) -- per-model facts seeded from g Brain
        # (dimensions, electrical, red light, heater, wood: `spec-<model>-*`,
        # and the older `gbrain-<model>-*` set) were never wired
        # into facts_pack at all, so the writer had nothing to cite even once
        # the claim existed in claims/verified.json. Any verified claim whose
        # id is namespaced to this model is citable, not just the ones already
        # listed as an explicit spec-table row.
        spec_ids |= {
            c["id"]
            for c in self._verified
            if c["id"].startswith(f"spec-{name_slug}-") or c["id"].startswith(f"gbrain-{name_slug}-")
        }
        universal_ids = (
            set(tenant_mod.active().get("universal_claim_ids") or ()) | {price_id}
            | spec_ids
            | benefit_allowlist_ids()
        )
        verified_claims = [
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in self._verified
            if c["id"] in universal_ids
        ]

        # Fix cycle 9 item 1: this run's freshly-seeded PDP claims for this
        # product (pdp_claims.seed_pdp_claims) -- never in claims/verified.json,
        # so they're not covered by universal_ids/self._verified above; add
        # directly, scoped to this product's own id namespace.
        pdp_claims_for_product = [c for c in (pdp_claims or []) if c["id"].startswith(f"pdp-{name_slug}-")]
        verified_claims.extend(
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in pdp_claims_for_product
        )

        by_id = {c["id"]: c["text"] for c in self._verified}
        by_id.update({c["id"]: c["text"] for c in pdp_claims_for_product})

        # Fix 2: a live price claim (fetched this run, dated today, sourced
        # to the product URL) replaces the static claims/verified.json entry
        # when one was fetched; otherwise fall back to the static claim above.
        if live_price_claim is not None:
            verified_claims = [c for c in verified_claims if c["id"] != price_id]
            verified_claims.append(
                {
                    "id": live_price_claim["id"],
                    "text": live_price_claim["text"],
                    "category": live_price_claim["category"],
                    "source": live_price_claim["source"],
                }
            )

        reviews_summary = None
        if reviews_claim is not None:
            verified_claims.append(
                {
                    "id": reviews_claim["id"],
                    "text": reviews_claim["text"],
                    "category": reviews_claim["category"],
                    "source": reviews_claim["source"],
                }
            )
            reviews_summary = {"text": reviews_claim["text"], "claim_ids": [reviews_claim["id"]]}

        # Fix 1: financing never carries a lender/monthly figure unless
        # claims/config.json's financing_lender has been set by an operator, and a
        # compare-at price is never even offered to the writer unless
        # show_compare_at_price is true.
        financing = {"available": True, "lender": config.get("financing_lender"), "monthly": None}
        compare_at_price = product.get("compare_at_price") if config.get("show_compare_at_price") else None

        # Fix cycle 6 item 2: every product's short_name/title/model name
        # across the whole catalog (not just the one this run is about) --
        # claims.py strips these out of a text field before checking it for
        # a bare digit, so writing a short_name with a capacity digit never
        # by itself forces a claim_id onto a sentence that has nothing else
        # to cite. A generic "N-Person" capacity token is covered separately
        # by claims.py's own regex, not by this list.
        digit_exempt_terms = sorted(
            {
                t
                for p in self._products.values()
                for t in (p.get("short_name"), p.get("title"), p.get("name"))
                if t
            }
        )

        pack = {
            "product": {
                "name": product["name"],
                "short_name": product.get("short_name", product["name"]),
                "slug": product["slug"],
                "url": product["url"],
                "price": product["price"],
                "compare_at_price": compare_at_price,
                "financing": financing,
                "image_urls": product.get("image_urls", []),
            },
            "specs": specs,
            "warranty": by_id.get("warranty-terms"),
            "shipping": by_id.get("shipping-policy"),
            "returns": by_id.get("returns-policy"),
            "reviews_summary": reviews_summary,
            "verified_claims": verified_claims,
            "assets": assets,
            # Fix 2 (cycle 2): the ad speaker's first-person story is
            # attributed to "a customer" unless an operator has put a real,
            # consented name in claims/config.json.
            "speaker_name": config.get("speaker_name"),
            "digit_exempt_terms": digit_exempt_terms,
        }
        if include_comparison:
            # Kimi long-run phase 6: only a run whose selected cartridges
            # include comparison gets the targets list (and the backing
            # claims joined into the citable universe) -- every other run's
            # facts_pack is byte-identical to before.
            targets, backing_claims = self._comparison_targets(product)
            pack["comparison_targets"] = targets
            existing_ids = {c["id"] for c in pack["verified_claims"]}
            pack["verified_claims"] = pack["verified_claims"] + [
                {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
                for c in backing_claims
                if c["id"] not in existing_ids
            ]
        return pack
