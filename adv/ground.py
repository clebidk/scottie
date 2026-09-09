"""Grounding: turn an ad_brief + the local claims store into a small facts_pack
the writer can cite from. `FactsSource` is a protocol so `write`/`cli` don't
care whether facts came from the local JSON files or (later) g Brain.
"""
import json
from pathlib import Path
from typing import Protocol


class FactsSource(Protocol):
    def facts_for(self, product_slug, ad_brief) -> dict: ...


class LocalFactsSource:
    """Reads claims/products.json and claims/verified.json from disk."""

    def __init__(self, claims_dir):
        self.claims_dir = Path(claims_dir)
        self._products = None
        self._verified = None

    def _load(self):
        if self._products is None:
            self._products = json.loads((self.claims_dir / "products.json").read_text())["products"]
        if self._verified is None:
            self._verified = json.loads((self.claims_dir / "verified.json").read_text())

    def all_verified_claims(self):
        """The full claims/verified.json universe, for gating ad_brief.claims_made
        (which can reference anything approved, not just the eventual product's
        curated facts_pack subset)."""
        self._load()
        return self._verified

    def pick_product(self, product_slug, ad_brief):
        self._load()
        products = self._products

        if product_slug:
            for slug, p in products.items():
                if slug == product_slug or p["name"].lower() == product_slug.lower():
                    return p
            raise ValueError(f"unknown --product: {product_slug!r}")

        haystack = " ".join(
            [
                ad_brief.get("transcript_or_text", ""),
                ad_brief.get("hook", ""),
                ad_brief.get("promise", ""),
                ad_brief.get("angle", ""),
            ]
        ).lower()
        for slug, p in products.items():
            if p["name"].lower() in haystack:
                return p

        for slug, p in products.items():
            if p.get("default"):
                return p

        return next(iter(products.values()))

    def facts_for(self, product_slug, ad_brief):
        self._load()
        product = self.pick_product(product_slug, ad_brief)

        specs = [dict(s) for s in product.get("specs", [])]
        assets = [
            {"id": f"asset-{product['slug']}-{i + 1}", "url": url, "kind": "image", "alt": f"{product['name']} sauna"}
            for i, url in enumerate(product.get("image_urls", []))
        ]

        name_slug = product["name"].lower().replace(" ", "-")
        price_id = f"price-{name_slug}"
        financing_id = f"financing-{name_slug}"
        spec_ids = {s["claim_id"] for s in specs if s.get("claim_id")}
        universal_ids = {
            "founder-ceo", "warranty-terms", "shipping-policy", "returns-policy",
            price_id, financing_id,
        } | spec_ids
        verified_claims = [
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in self._verified
            if c["id"] in universal_ids
        ]
        by_id = {c["id"]: c["text"] for c in self._verified}

        return {
            "product": {
                "name": product["name"],
                "slug": product["slug"],
                "url": product["url"],
                "price": product["price"],
                "compare_at_price": product.get("compare_at_price"),
                "financing_line": product.get("financing_line"),
                "financing_estimated": product.get("financing_estimated", False),
                "image_urls": product.get("image_urls", []),
            },
            "specs": specs,
            "warranty": by_id.get("warranty-terms"),
            "shipping": by_id.get("shipping-policy"),
            "returns": by_id.get("returns-policy"),
            "reviews_summary": None,  # no verified review data yet; see claims/pending.json
            "verified_claims": verified_claims,
            "assets": assets,
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
