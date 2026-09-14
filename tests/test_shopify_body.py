"""`adv shopify-body`: transforms an already-rendered out/<run>/<cartridge>/
index.html into shopify-body.html + shopify-body.assets.json. No Shopify API
call anywhere in this test file or the module it tests -- these tests only
check the HTML/JSON the transform writes to disk."""
import argparse
import json
from pathlib import Path

from harness.cli import cmd_shopify_body
from harness.render import render_page
from harness.shopify import build_shopify_body, full_bleed_css, write_shopify_body

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
    # Cycle 31: five distinct assets -- pagechecks.find_duplicate_asset_
    # violations now requires every image slot on a page to be a distinct
    # asset id (docs/IMAGES-AUDIT-2026-09-14.md problem 3), so LISTICLE_PAGE
    # below can no longer reuse "asset-1" for all five reasons.
    "assets": [
        {"id": f"asset-{i}", "url": f"https://cdn.shopify.com/fuji-{i}.png", "kind": "lifestyle", "alt": "Fuji sauna"}
        for i in range(1, 6)
    ],
}

AD_BRIEF = {
    "hook": "hook", "promise": "promise", "angle": "angle", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See the models", "tone": "candid",
    "speaker_pov": "third_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

LISTICLE_PAGE = {
    "headline": "5 Reasons Busy Parents Are Switching to Peak Saunas",
    "dek": "A quick look at what makes the switch worth it.",
    "reasons": [
        {"number": i, "heading": f"Reason number {i}", "text": "A plain, specific reason a buyer can check for themselves.", "image": {"asset_id": f"asset-{i}"}}
        for i in range(1, 6)
    ],
    "cta_text": "See the models",
    "cta_url": "https://peaksaunas.com/collections/all",
    "closing": {
        "headline": "Ready to feel the difference?",
        "paragraphs": [{"text": "Peak Saunas is one brand that makes switching easy."}],
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


def test_shopify_body_preserves_ad_label_byline_disclosure_and_sources(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    html, _ = build_shopify_body(out_dir)
    assert "Advertisement" in html
    assert "is an advertisement published by Peak Saunas" in html
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
    # Cycle 31: LISTICLE_PAGE's five reasons now use five distinct asset ids
    # (docs/IMAGES-AUDIT-2026-09-14.md problem 3 -- a page may not reuse the
    # same asset id in two slots), so five distinct local_paths are listed;
    # download_asset's variants are always re-encoded as JPEG (see
    # generate_image_variants), so every cdn_filename ends .jpg regardless
    # of the source format.
    assert len(manifest) == 5
    entry = manifest[0]
    assert entry["local_path"] == "assets/asset-1-300.jpg"
    assert entry["alt"] == "Peak Fuji 2-Person Infrared Sauna – lifestyle photo"
    assert entry["cdn_filename"].startswith("pk-listicle-01-")
    assert entry["cdn_filename"].endswith(".jpg")
    assert {e["local_path"] for e in manifest} == {f"assets/asset-{i}-300.jpg" for i in range(1, 6)}


def test_assets_manifest_is_valid_json_on_disk(tmp_path):
    out_dir = tmp_path / "listicle"
    _render_listicle(out_dir)
    _, manifest_path = write_shopify_body(out_dir)
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk == [
        {
            "local_path": f"assets/asset-{i}-300.jpg",
            "alt": "Peak Fuji 2-Person Infrared Sauna – lifestyle photo",
            "cdn_filename": f"pk-listicle-{i:02d}-peak-fuji-2-person-infrared-sauna-lifestyle-photo.jpg",
        }
        for i in range(1, 6)
    ]


def test_build_asset_manifest_dedupes_by_local_path(tmp_path):
    """shopify.build_asset_manifest's own dedupe (distinct from cycle 31's
    page.json-level "no duplicate asset id" policy, which is enforced
    earlier, at render time) -- if two <img> tags in already-rendered HTML
    ever do reference the same local file, the manifest still lists it once."""
    from harness.shopify import build_asset_manifest

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
    assert "Advertisement" in html
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
