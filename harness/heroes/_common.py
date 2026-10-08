"""The fields every new composition prints, built once from what the
cartridge's renderer already computed (`base`: the listicle's first_screen
context, or the landing page's render context). Nothing here is new copy:
the headline is the writer's with its accent span, the numbers are verified
claims the base context already cites, the CTA is page.cta_text/cta_url."""
from .. import first_screen, landing

ACCENT_CLASS = "hr-accent"
CTA_CLASS = {"listicle": "op-btn", "landing": "ld-btn"}
TEXT_LINK_CLASS = {"landing": "ld-text"}


def letters(tenant):
    """The two display letter pairs for letters_pill: tenant.yaml
    `hero.letters` (two strings) when set, else the first two and the last
    two letters of the tenant's display name ("ACME" -> AC / ME)."""
    pinned = tenant.get("hero.letters") if tenant else None
    if isinstance(pinned, (list, tuple)) and len(pinned) == 2 and all(str(x).strip() for x in pinned):
        return [str(pinned[0]).strip().upper(), str(pinned[1]).strip().upper()]
    name = "".join(ch for ch in str(getattr(tenant, "display_name", "") or "") if ch.isalnum()).upper()
    if len(name) >= 4:
        return [name[:2], name[-2:]]
    half = max(1, len(name) // 2)
    return [name[:half], name[half:]]


def fields(page, ad_brief, facts_pack, tenant, *, cartridge, base, look):
    """eyebrow, headline_html, display_html, display_fits, dek, byline,
    rating, price, financing, trust, cta, secondary, quote, made_for,
    scroll_cue, specs, product_name, claim_ids -- for any new composition."""
    base = base or {}
    page = page or {}
    product = (facts_pack or {}).get("product") or {}
    names = tenant.product_names(product) if tenant else {}
    out = {
        "cartridge": cartridge,
        "cta": {"text": page.get("cta_text") or "", "url": page.get("cta_url") or "",
                "cls": CTA_CLASS.get(cartridge, "")},
        "product_name": (names or {}).get("full_name") or product.get("name") or "",
        "quote": base.get("quote"),
        "made_for": base.get("made_for") or "",
        "claim_ids": set(),
    }
    if cartridge == "listicle":
        line = first_screen.display_line(page)
        out.update({
            "eyebrow": base.get("eyebrow") or "",
            "headline_html": first_screen.headline_markup(page, accent_class=ACCENT_CLASS),
            "display_fits": line is not None,
            "display_html": (first_screen.headline_markup(page, line, accent_class=ACCENT_CLASS) if line
                             else first_screen.headline_markup(page, accent_class=ACCENT_CLASS)),
            "dek": str(page.get("dek") or "").strip(),
            "byline": base.get("byline"),
            "trust": [{"text": f"{s['value']} {s['label']}"} for s in (base.get("stats") or [])],
            "price": None,
            "financing": "",
            "secondary": None,
            "scroll_cue": base.get("scroll_cue") or "",
            "specs": [],
            "rating": first_screen.rating_stat(facts_pack),
        })
    else:
        view = landing.headline_view(page)
        markup = first_screen.headline_markup(view, accent_class=ACCENT_CLASS)
        secondary = base.get("secondary")
        out.update({
            "eyebrow": base.get("eyebrow") or "",
            "headline_html": markup,
            "display_fits": bool(base.get("headline_fits")),
            "display_html": markup,
            "dek": str(((page.get("hero") or {}).get("subhead")) or "").strip(),
            "byline": None,
            "trust": list(base.get("trust") or []),
            "price": base.get("price"),
            "financing": base.get("financing") or "",
            "secondary": dict(secondary, cls=TEXT_LINK_CLASS["landing"]) if secondary else None,
            "scroll_cue": "",
            "specs": list(base.get("specs") or []),
            "rating": base.get("rating"),
        })
    if out["rating"]:
        out["claim_ids"].update(out["rating"].get("claim_ids") or [])
    return out
