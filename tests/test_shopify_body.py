"""`adv shopify-body`: transforms an already-rendered out/<run>/<cartridge>/
index.html into shopify-body.html + shopify-body.assets.json. No Shopify API
call anywhere in this test file or the module it tests -- these tests only
check the HTML/JSON the transform writes to disk."""
import argparse
import json
from pathlib import Path

from harness import tenant as tenant_mod
from harness.cli import cmd_shopify_body
from harness.render import render_page
from harness.page_body import (
    build_shopify_body,
    full_bleed_css,
    strip_document_chrome,
    write_shopify_body,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

FACTS_PACK = {
    "product": {
        "name": "Fuji",
        "short_name": "Peak Fuji 2-Person Infrared Sauna",
        "slug": "fuji",
        "url": "https://peaksaunas.com/products/fuji",
        "price": "8250.00",
        "compare_at_price": None,
        "financing": {"available": True, "lender": None, "monthly": None},
        "image_urls": ["https://cdn.shopify.com/fuji-1.png"],
    },
    "specs": [{"label": "Capacity", "value": "2-Person"}],
    "warranty": "warranty text",
    "shipping": "shipping text",
    "returns": "returns text",
    "reviews_summary": None,
    "verified_claims": [
        {"id": "warranty-terms", "text": "warranty text", "category": "trust", "source": "https://peaksaunas.com/pages/warranty"},
    ],
    # Cycle 31: distinct assets -- pagechecks.find_duplicate_asset_
    # violations requires every image slot on a page to be a distinct asset
    # id (docs/IMAGES-AUDIT-2026-09-14.md problem 3). Cycle 41: six of them,
    # for listicle v0.2's own hero slot plus one image per item.
    "assets": [
        {"id": f"asset-{i}", "url": f"https://cdn.shopify.com/fuji-{i}.png", "kind": "lifestyle", "alt": "Fuji sauna"}
        for i in range(1, 7)
    ],
}

AD_BRIEF = {
    "hook": "hook", "promise": "promise", "angle": "angle", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See the models", "tone": "candid",
    "speaker_pov": "third_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

# Cycle 41: listicle v0.2 -- a style, a hero slot of its own, a proof line
# per item, the fit block, an FAQ, and a 3-bullet closing recap. The sticky
# bar and the alternating bands are what these tests care about carrying
# into shopify-body.html.
LISTICLE_PAGE = {
    "style": "reasons",
    "headline": "5 Reasons Busy Parents Are Switching to Home Saunas",
    "dek": "A quick look at what makes the switch worth it.",
    "hero": {"asset_id": "asset-1"},
    "reasons": [
        {
            "number": i,
            "heading": f"Reason number {i}",
            "text": "A plain, specific reason a buyer can check for themselves.",
            "image": {"asset_id": f"asset-{i + 1}"},
            "proof": {"text": "One customer told us the room was warm within minutes.",
                      "attributed_to_customer": True},
        }
        for i in range(1, 6)
    ],
    "audience_fit": {
        "for_you": [{"text": "You have a dry, level corner to give up."},
                    {"text": "You would rather read the specification than book a call."}],
        "not_for_you": [{"text": "You rent and cannot leave a cabin behind."},
                        {"text": "Your only free wall is in a garage that freezes."}],
    },
    "faq": {
        "questions": [
            {"question": f"A question a buyer asks, number {i}?",
             "answer": "A short, plain answer with nothing to cite in it."}
            for i in range(1, 6)
        ]
    },
    "cta_text": "See the models",
    "cta_url": "https://peaksaunas.com/collections/all",
    "closing": {
        "headline": "Ready to feel the difference?",
        "recap": [
            {"text": "The cabin goes where you have room."},
            {"text": "Everything a seller would say on a call is published instead."},
            {"text": "Assembly is a weekend job, not a trade job."},
        ],
        "warranty_line": {
            "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
            "claim_ids": ["warranty-terms"],
        },
        "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
    },
}


def _fake_fetch_url(url):
    # Cycle 31: download_asset now decodes every download with Pillow to
    # generate srcset variants (harness.render.generate_image_variants) and
    # drops anything it can't decode -- a real (if tiny and identical
    # across urls) PNG here, not the old un-decodable
    # "\xff\xd8\xff\xe0fake-jpeg-bytes" placeholder.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color=(200, 180, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _render_listicle(out_dir):
    # download_assets defaults to True -- a real `adv run` always downloads
    # the images a page references before shopify-body ever runs, so the
    # asset-manifest tests below need the same real assets/<id>.<ext>
    # relative src the renderer actually produces, not the original CDN url.
    return render_page(
        cartridge_name="listicle",
        page=LISTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=out_dir / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-10",
        updated="2026-09-10",
        fetch_url=_fake_fetch_url,
    )


def test_shopify_body_starts_with_the_aurora_full_bleed_has_rules(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert html.startswith("<style>\n" + full_bleed_css())
    assert ".section:has(.pk-lp) .container{" in html
    assert ".section:has(.pk-lp) .page__title{display:none}" in html
    assert ".section:has(.pk-lp) .page__content{" in html


def test_shopify_body_has_no_document_or_chrome_tags(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    lowered = html.lower()
    for tag in ("<!doctype", "<html", "</html>", "<head", "</head>", "<body", "</body>",
                "<header", "</header>", "<footer", "</footer>", "<nav", "</nav>"):
        assert tag not in lowered, f"{tag!r} should not appear in shopify-body.html"


# ---------------------------------------------------------------------------
# strip_document_chrome -- Cycle 40: a classed header/footer/nav (a
# cartridge's own styled band) becomes a <div>, not stripped; a bare one
# (real document chrome) is still unwrapped, same as before.
# ---------------------------------------------------------------------------

def test_strip_document_chrome_unwraps_a_bare_header():
    html = "<body><header><h1>Ad</h1></header><p>body</p></body>"
    assert strip_document_chrome(html) == "<h1>Ad</h1><p>body</p>"


def test_strip_document_chrome_converts_a_classed_header_to_a_div():
    html = '<body><header class="lst-header" data-x="1"><h1>Ad</h1></header></body>'
    assert strip_document_chrome(html) == (
        '<div class="lst-header" data-x="1"><h1>Ad</h1></div>'
    )


def test_strip_document_chrome_converts_a_classed_footer_to_a_div():
    html = '<body><footer class="adv-footer"><p>disclosure</p></footer></body>'
    assert strip_document_chrome(html) == '<div class="adv-footer"><p>disclosure</p></div>'


def test_strip_document_chrome_handles_nested_classed_inside_bare():
    html = (
        "<body><header><header class=\"lst-header\">"
        "<h1>Ad</h1></header></header></body>"
    )
    assert strip_document_chrome(html) == '<div class="lst-header"><h1>Ad</h1></div>'


def test_strip_document_chrome_still_unwraps_bare_nav_and_footer(tmp_path):
    html = "<body><nav>links</nav><footer>plain</footer></body>"
    assert strip_document_chrome(html) == "linksplain"


def test_shopify_body_preserves_ad_label_byline_disclosure_and_sources(tmp_path, monkeypatch):
    # cycle 55: the label renders only when the tenant sets disclosure_label
    monkeypatch.setitem(tenant_mod.active().config, "disclosure_label", "Advertisement")
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert '<span class="adv-badge">Advertisement</span>' in html
    assert "This page is published by PEAK" in html
    assert "Sources" in html


def test_shopify_body_moves_the_motion_script_to_the_very_end(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert "IntersectionObserver" in html
    assert html.rstrip().endswith("</script>")
    # the disclosure/Sources content (originally rendered before the script,
    # inside base.html's <footer>) now comes before it in the reordered output.
    assert html.index("Sources") < html.rindex("<script")


def test_shopify_body_relativizes_internal_links(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert 'href="/collections/all"' in html
    assert "peaksaunas.com" not in html


def test_shopify_body_has_exactly_one_pk_lp_root(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert html.count('class="pk-lp') == 1


def test_write_shopify_body_writes_two_files_next_to_index(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    body_path, manifest_path = write_shopify_body(out_dir)
    assert body_path == out_dir / "shopify-body.html"
    assert manifest_path == out_dir / "shopify-body.assets.json"
    assert body_path.exists()
    assert manifest_path.exists()


def test_assets_manifest_lists_each_image_once_with_a_cdn_filename(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    _, manifest = build_shopify_body(out_dir)
    # Cycle 31: a page may not reuse the same asset id in two slots
    # (docs/IMAGES-AUDIT-2026-09-14.md problem 3). Cycle 41: six distinct
    # images -- the header hero plus one per item. Each render_image_slot
    # <picture> carries one WebP and one JPEG variant here (a 300x300 source
    # is narrower than every configured srcset width, so
    # generate_image_variants emits just one size) -- 6 images x 2 formats =
    # 12 manifest entries, one per distinct local_path, each listed once.
    assert len(manifest) == 12
    by_path = {e["local_path"]: e for e in manifest}
    assert set(by_path) == {f"assets/asset-{i}-300.{ext}" for i in range(1, 7) for ext in ("jpg", "webp")}
    entry = by_path["assets/asset-1-300.jpg"]
    assert entry["alt"] == "Peak Fuji 2-Person Infrared Sauna – lifestyle photo"
    assert entry["cdn_filename"].startswith("pk-listicle-01-")
    assert entry["cdn_filename"].endswith(".jpg")


def test_assets_manifest_is_valid_json_on_disk(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    _, manifest_path = write_shopify_body(out_dir)
    on_disk = json.loads(manifest_path.read_text())
    expected_paths = {f"assets/asset-{i}-300.{ext}" for i in range(1, 7) for ext in ("jpg", "webp")}
    assert {e["local_path"] for e in on_disk} == expected_paths
    assert all(e["alt"] == "Peak Fuji 2-Person Infrared Sauna – lifestyle photo" for e in on_disk)
    # every cdn_filename is distinct and carries the format's own extension
    cdn_filenames = [e["cdn_filename"] for e in on_disk]
    assert len(set(cdn_filenames)) == len(cdn_filenames)
    for e in on_disk:
        assert e["cdn_filename"].endswith("." + e["local_path"].rsplit(".", 1)[1])


def test_build_asset_manifest_lists_every_picture_variant():
    """Cycle 31: a render_image_slot <picture> (WebP <source srcset=...> +
    JPEG <img srcset=...>) must list every distinct width/format variant,
    not just the single inlined fallback src -- each with its own,
    distinct cdn_filename (docs/IMAGES.md's publish contract: every
    variant needs a real upload name)."""
    from harness.page_body import build_asset_manifest

    html = (
        '<picture>'
        '<source type="image/webp" srcset="assets/hero-480.webp 480w, assets/hero-800.webp 800w">'
        '<img src="assets/hero-480.jpg" alt="Peak Fuji – lifestyle photo" '
        'srcset="assets/hero-480.jpg 480w, assets/hero-800.jpg 800w">'
        '</picture>'
    )
    manifest = build_asset_manifest(html, "longform")
    paths = {e["local_path"] for e in manifest}
    assert paths == {
        "assets/hero-480.webp", "assets/hero-800.webp",
        "assets/hero-480.jpg", "assets/hero-800.jpg",
    }
    cdn_filenames = [e["cdn_filename"] for e in manifest]
    assert len(set(cdn_filenames)) == len(cdn_filenames), "every variant needs a distinct cdn filename"
    by_path = {e["local_path"]: e["cdn_filename"] for e in manifest}
    assert by_path["assets/hero-480.jpg"].endswith("-480w.jpg")
    assert by_path["assets/hero-800.webp"].endswith("-800w.webp")


def test_build_asset_manifest_dedupes_by_local_path(tmp_path):
    """shopify.build_asset_manifest's own dedupe (distinct from cycle 31's
    page.json-level "no duplicate asset id" policy, which is enforced
    earlier, at render time) -- if two <img> tags in already-rendered HTML
    ever do reference the same local file, the manifest still lists it once."""
    from harness.page_body import build_asset_manifest

    html = (
        '<img src="assets/x.jpg" alt="Peak Fuji – lifestyle photo">'
        '<img src="assets/x.jpg" alt="Peak Fuji – lifestyle photo">'
        '<img src="assets/y.jpg" alt="Peak Fuji – installation photo">'
    )
    manifest = build_asset_manifest(html, "listicle")
    assert [e["local_path"] for e in manifest] == ["assets/x.jpg", "assets/y.jpg"]


# ---------------------------------------------------------------------------
# The transform itself is cartridge-agnostic (`adv shopify-body <run-dir>/
# <cartridge>` per its own --help, not a listicle-only command) -- proven
# against article's output too, which has no .pk-lp wrapper and no motion
# script at all.
# ---------------------------------------------------------------------------

ARTICLE_PAGE = {
    "headline": "Why the checkout page decides more than the price",
    "dek": "A look at what makes people trust a purchase enough to finish it.",
    "open": [{"text": "Shopping used to mean waiting for a callback."}],
    "body_sections": [{"heading": "Why hidden pricing kills trust", "paragraphs": [{"text": "Buyers move on when the price is hidden."}]}],
    "turn_section": {"heading": "What to look for", "intro": "A few signs.", "criteria": [{"text": "Price shown before any form."}]},
    "close": {"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    "cta": {"text": "See the models", "url": "https://peaksaunas.com/collections/all"},
    "images": [],
}


def test_build_shopify_body_works_on_a_non_listicle_cartridge(tmp_path):
    out_dir = tmp_path / "article"
    render_page(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=out_dir / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-10",
        updated="2026-09-10",
        download_assets=False,
    )
    html, manifest = build_shopify_body(out_dir)
    assert "<header" not in html.lower()
    assert "This page is published by PEAK" in html
    assert html.startswith("<style>\n" + full_bleed_css())
    assert manifest == []


def test_cmd_shopify_body_cli_wiring(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    args = argparse.Namespace(cartridge_dir=str(out_dir), tenant=None)
    assert cmd_shopify_body(args) == 0
    assert (out_dir / "shopify-body.html").exists()
    assert (out_dir / "shopify-body.assets.json").exists()


def test_cmd_shopify_body_errors_without_an_index_html(tmp_path, capsys):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    args = argparse.Namespace(cartridge_dir=str(empty_dir), tenant=None)
    assert cmd_shopify_body(args) == 1
    assert "no index.html" in capsys.readouterr().err


def test_shopify_body_carries_head_tokens_rescoped_to_the_wrapper(tmp_path):
    """Cycle 48: storefronts keep only body <style>; the head's :root tokens
    (harness defaults + brand/base.css) must be re-emitted on .adv-wrap or
    every var(--ps-*, var(--adv-*)) in a cartridge resolves to nothing."""
    from harness import page_body

    class _T:
        brand_dir = tmp_path / "brand"
        def get(self, key, default=None):
            return default
    _T.brand_dir.mkdir()
    (_T.brand_dir / "base.css").write_text(":root{\n  --ps-accent:#123456; /* brand */\n  --ps-text:#111;\n}\n.other{color:red}\n")
    css = page_body.token_css(_T())
    assert ".adv-wrap{--ps-accent:#123456; /* brand */ --ps-text:#111;}" in css
    assert "--adv-accent" in css  # harness/structure.css defaults travel too
    assert css.index("--adv-accent") < css.index("--ps-accent")  # brand overrides defaults
    assert ".other" not in css


def test_a_head_stylesheet_that_mentions_a_body_tag_does_not_become_the_body():
    """Cycle 52, found while rendering the rebranded pages: a tenant's
    brand/base.css is inlined into the document HEAD, and one whose own
    comment mentioned a body tag was matched as the start of the document
    body -- the rest of that stylesheet was then emitted into the export as
    raw text, outside any <style>."""
    html = (
        "<!doctype html><html><head>"
        "<style>/* keeps only what is inside <body> */ .x{color:red}</style>"
        "</head><body><p>real body</p></body></html>"
    )
    body = strip_document_chrome(html)
    assert body.strip() == "<p>real body</p>"
    assert "color:red" not in body

