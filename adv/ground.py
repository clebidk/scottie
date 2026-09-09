"""Grounding: turn an ad_brief + the local claims store into a small facts_pack
the writer can cite from. `FactsSource` is a protocol so `write`/`cli` don't
care whether facts came from the local JSON files or (later) g Brain.
"""
import json
import re
from pathlib import Path
from typing import Protocol

DEFAULT_CONFIG = {
    "financing_lender": None,
    "show_compare_at_price": False,
    "reviews_source": "judgeme-live",
    "speaker_name": None,
}

# Fix 8: for the chosen product's Drive assets, prefer lifestyle/interior,
# then render, then installation shots. Never video/logo/ugc in V1.
DRIVE_ASSET_TIERS = (("lifestyle", "interior"), ("render",), ("installation",))
DRIVE_ASSET_NEVER_KINDS = {"video", "logo", "ugc"}
DRIVE_ASSET_MAX = 6

# Fix cycle 3 item 3: these are cleared, product-wide (not per-model) claims
# from claims/seed-from-gbrain.json's allowlist ("Only state verified claims:
# medical-grade red light, free shipping, 360 full spectrum, US-owned,
# Lifetime warranty"). They're already merged into claims/verified.json with
# sources -- the gap fixed here is that facts_for()'s universal_ids never
# surfaced them into facts_pack, so the writer had nothing to cite when
# describing what the sauna actually does (only price/shipping/warranty/
# returns made it through). Every product gets all five.
BENEFIT_ALLOWLIST_IDS = {
    "gbrain-allowlist-red-light",
    "gbrain-allowlist-free-shipping",
    "gbrain-allowlist-360-full-spectrum",
    "gbrain-allowlist-us-owned",
    "gbrain-allowlist-lifetime-warranty",
}


class FactsSource(Protocol):
    def facts_for(self, product_slug, ad_brief) -> dict: ...


def load_claims_config(claims_dir):
    """claims/config.json, with defaults filled in for any missing key."""
    config_path = Path(claims_dir) / "config.json"
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        config.update(json.loads(config_path.read_text()))
    return config


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


class LocalFactsSource:
    """Reads claims/products.json and claims/verified.json from disk."""

    def __init__(self, claims_dir):
        self.claims_dir = Path(claims_dir)
        self.brand_dir = self.claims_dir.parent / "brand"
        self._products = None
        self._verified = None
        self._assets_index = None

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

    def all_verified_claims(self, live_price_claims=None):
        """The full claims/verified.json universe, for gating ad_brief.claims_made
        (which can reference anything approved, not just the eventual product's
        curated facts_pack subset). live_price_claims (fix 2), if given, is a
        slug -> claim map of this run's freshly-fetched price claims, which
        take priority over the static price-* entries they replace."""
        self._load()
        if not live_price_claims:
            return self._verified
        live_by_id = {c["id"]: c for c in live_price_claims.values()}
        merged = [c for c in self._verified if c["id"] not in live_by_id]
        merged.extend(live_by_id.values())
        return merged

    def pick_product(self, product_slug, ad_brief):
        product, _warning = self.pick_product_with_warning(product_slug, ad_brief)
        return product

    def pick_product_with_warning(self, product_slug, ad_brief):
        """Cycle 8 problem 1b: an explicit --product still wins outright. Otherwise
        match each active model's real name (never an alias -- "mini", "peak
        mini", "sauna mini", "el cap", "1-person" etc. are not model names, only
        the product's own `name` field is matched) against the ad's transcript/
        brief, case-insensitively and on a whole word/phrase boundary so "Fuji"
        doesn't match inside some unrelated longer word. If exactly one model is
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
            raise ValueError(f"unknown --product: {product_slug!r}")

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

        for p in active_products:
            if p.get("default"):
                return p, f"product not named in ad; defaulted to {p['name']}"

        default_product = active_products[0] if active_products else next(iter(products.values()))
        return default_product, f"product not named in ad; defaulted to {default_product['name']}"

    def facts_for(self, product_slug, ad_brief, *, config=None, live_price_claim=None, reviews_claim=None):
        self._load()
        product = self.pick_product(product_slug, ad_brief)
        config = config or load_claims_config(self.claims_dir)

        specs = [dict(s) for s in product.get("specs", [])]
        shopify_assets = [
            {"id": f"asset-{product['slug']}-{i + 1}", "url": url, "kind": "image", "alt": f"{product['name']} sauna"}
            for i, url in enumerate(product.get("image_urls", []))
        ]
        name_slug = product["name"].lower().replace(" ", "-")
        drive_assets = select_drive_assets(self._load_assets_index(), name_slug)
        assets = shopify_assets + drive_assets  # Shopify images stay first, as hero (fix 8)

        price_id = f"price-{name_slug}"
        spec_ids = {s["claim_id"] for s in specs if s.get("claim_id")}
        # Fix cycle 8 problem 2: claims/products.json's own `specs` list only
        # ever carried the handful of fields Shopify already exposed (capacity,
        # cabin material, max temp, ...) -- per-model facts seeded from g Brain
        # (dimensions, electrical, red light, heater, wood: `spec-<model>-*`,
        # and the older `gbrain-<model>-*` Fuji/Everest set) were never wired
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
            {"founder-ceo", "warranty-terms", "shipping-policy", "returns-policy", price_id}
            | spec_ids
            | BENEFIT_ALLOWLIST_IDS
        )
        verified_claims = [
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in self._verified
            if c["id"] in universal_ids
        ]
        by_id = {c["id"]: c["text"] for c in self._verified}

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
        # claims/config.json's financing_lender has been set by Caleb, and a
        # compare-at price is never even offered to the writer unless
        # show_compare_at_price is true.
        financing = {"available": True, "lender": config.get("financing_lender"), "monthly": None}
        compare_at_price = product.get("compare_at_price") if config.get("show_compare_at_price") else None

        # Fix cycle 6 item 2: every product's short_name/title/model name
        # across the whole catalog (not just the one this run is about) --
        # claims.py strips these out of a text field before checking it for
        # a bare digit, so writing "Peak Fuji 2-Person Infrared Sauna" never
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

        return {
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
            # attributed to "a customer" unless Caleb has put a real,
            # consented name in claims/config.json.
            "speaker_name": config.get("speaker_name"),
            "digit_exempt_terms": digit_exempt_terms,
        }


class GBrainSource:
    """FactsSource backed by g Brain, through the retrieval allowlist in
    SPEC.md section 3 (page types product/concept/campaign/spec/policy/kb/
    reference/book-analysis; never customer/order/email/support_ticket/
    conversation/slack_log/person).

    Not implemented in V1: docs/knowledge-map.md is being written concurrently
    by another agent and g Brain retrieval isn't wired up yet. Wire this in
    once that document exists and defines how to query g Brain for a given
    product/ad_brief.
    """

    def facts_for(self, product_slug, ad_brief):
        raise NotImplementedError(
            "GBrainSource is a stub; wire it in after docs/knowledge-map.md lands "
            "(see class docstring). Use LocalFactsSource until then."
        )
