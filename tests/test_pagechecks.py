"""Kimi long-run phase 2 (docs/KIMI-LONG-RUN.md): the four deterministic
page checks in harness/pagechecks.py, each with a discrimination smoke test
-- a known-good page passes, a planted bad page fails.

Image allowlist and internal links are page.json-level checks wired into
repair.check_page_gates, so the writer repair loop can fix them (proven here
end to end through write_and_gate_page). HTML validity and JSON-LD are
post-render backstops in render_page: writer prose is autoescaped, so a
structural failure is a template/renderer/tenant-file bug that must STOP
the run, not burn repair calls.
"""
from pathlib import Path

import pytest

from harness import pagechecks
from harness.claims import ClaimsGateFailure
from harness.repair import check_page_gates, write_and_gate_page
from harness.budget import Budget
from harness.log import RunLog
from harness.render import render_page
from tests.conftest import FakeClient, json_response
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, LONGFORM_PAGE, PRODUCT_PAGE_PAGE

REPO_ROOT = Path(__file__).resolve().parent.parent

ALL_PAGES = [("article", ARTICLE_PAGE), ("product-page", PRODUCT_PAGE_PAGE), ("longform", LONGFORM_PAGE)]


def _check(page, cartridge_name="article"):
    return check_page_gates(
        page, FACTS_PACK, cartridge_name,
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
    )


def _render(tmp_path, cartridge_name="article", page=ARTICLE_PAGE, brand_dir=None):
    return render_page(
        cartridge_name=cartridge_name,
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=brand_dir or (tmp_path / "brand-does-not-exist"),
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / cartridge_name,
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )


# ---------------------------------------------------------------------------
# Image allowlist (gate level)
# ---------------------------------------------------------------------------

def test_image_allowlist_known_good_pages_pass():
    for cartridge_name, page in ALL_PAGES:
        assert pagechecks.find_image_allowlist_violations(page, FACTS_PACK) == [], cartridge_name


def test_image_allowlist_catches_an_unknown_asset_id():
    page = dict(ARTICLE_PAGE, images=[{"asset_id": "asset-not-in-the-manifest"}])
    problems = pagechecks.find_image_allowlist_violations(page, FACTS_PACK)
    assert len(problems) == 1
    assert problems[0]["path"] == "$.images[0].asset_id"
    assert "asset-not-in-the-manifest" in problems[0]["issue"]


def test_image_allowlist_failure_flows_through_check_page_gates():
    page = dict(ARTICLE_PAGE, images=[{"asset_id": "bogus"}])
    assert any("bogus" in p["issue"] for p in _check(page))


def test_repair_loop_fixes_an_unknown_asset_id(tmp_path):
    """The bait page fails attempt 1; the fake client's second (good) page is
    the repair -- proving this check is wired where the repair loop can
    handle it, per the phase-2 registration."""
    bad = dict(ARTICLE_PAGE, images=[{"asset_id": "bogus"}])
    client = FakeClient([json_response(bad), json_response(ARTICLE_PAGE)])
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, _fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=Budget(),
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2
    assert any("bogus" in item["issue"] for item in attempts[0])


# ---------------------------------------------------------------------------
# Internal links (gate level)
# ---------------------------------------------------------------------------

def test_internal_links_known_good_pages_pass():
    for cartridge_name, page in ALL_PAGES:
        assert pagechecks.find_internal_link_violations(page) == [], cartridge_name


def test_internal_links_catch_an_external_cta_url():
    page = dict(ARTICLE_PAGE, cta={"text": "See the models", "url": "https://competitor.example.com/steal"})
    problems = pagechecks.find_internal_link_violations(page)
    assert len(problems) == 1
    assert problems[0]["path"] == "$.cta.url"
    assert "not an internal link" in problems[0]["issue"]


def test_internal_links_catch_a_non_http_scheme():
    page = dict(ARTICLE_PAGE, cta={"text": "See the models", "url": "javascript:alert(1)"})
    problems = pagechecks.find_internal_link_violations(page)
    assert any("not an http(s) url" in p["issue"] for p in problems)


def test_internal_links_require_at_least_one():
    page = {k: v for k, v in ARTICLE_PAGE.items() if k != "cta"}
    problems = pagechecks.find_internal_link_violations(page)
    assert any("no internal link" in p["issue"] for p in problems)


def test_internal_links_allow_relative_paths_and_anchors():
    page = dict(ARTICLE_PAGE, cta={"text": "See the models", "url": "/collections/all"})
    assert pagechecks.find_internal_link_violations(page) == []


# ---------------------------------------------------------------------------
# HTML validity (post-render backstop)
# ---------------------------------------------------------------------------

def test_html_validity_known_good_rendered_pages_pass(tmp_path):
    for cartridge_name, page in ALL_PAGES:
        html = _render(tmp_path, cartridge_name, page).read_text()
        assert pagechecks.find_html_validity_violations(html) == [], cartridge_name


def test_html_validity_catches_an_unclosed_tag():
    problems = pagechecks.find_html_validity_violations("<html><body><div><p>never closed</p></html>")
    assert len(problems) == 1
    assert "does not parse" in problems[0]["issue"]


def test_html_validity_catches_a_double_hyphen_comment():
    # The exact shape the peak-saunas byline.html comment had before phase 2.
    problems = pagechecks.find_html_validity_violations("<html><body><!-- a -- b --></body></html>")
    assert len(problems) == 1


def test_render_page_stops_on_a_structurally_broken_tenant_block(tmp_path):
    """A tenant-injected block (the one raw-HTML injection a page has) that
    breaks the document STOPs the render before index.html is written."""
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "byline.html").write_text('<div class="byline"><span></div>')
    out_dir = tmp_path / "article"
    with pytest.raises(ClaimsGateFailure) as exc_info:
        _render(tmp_path, brand_dir=brand_dir)
    assert exc_info.value.stage == "html_structure:article"
    assert not (out_dir / "index.html").exists()


# ---------------------------------------------------------------------------
# JSON-LD (post-render backstop)
# ---------------------------------------------------------------------------

def test_json_ld_known_good_rendered_pages_pass(tmp_path):
    for cartridge_name, page in ALL_PAGES:
        html = _render(tmp_path, cartridge_name, page).read_text()
        assert pagechecks.find_rendered_json_ld_violations(html, cartridge_name) == [], cartridge_name


def test_json_ld_catches_a_wrong_type():
    html = '<html><head><script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product"}</script></head></html>'
    problems = pagechecks.find_rendered_json_ld_violations(html, "article")
    assert any("must be 'Article'" in p["issue"] for p in problems)


def test_json_ld_catches_an_unparseable_block():
    html = '<html><head><script type="application/ld+json">{not json</script></head></html>'
    problems = pagechecks.find_rendered_json_ld_violations(html, "article")
    assert any("does not parse" in p["issue"] for p in problems)


def test_json_ld_catches_a_missing_block():
    problems = pagechecks.find_rendered_json_ld_violations("<html><body></body></html>", "article")
    assert any("no JSON-LD block" in p["issue"] for p in problems)


def test_json_ld_catches_an_unregistered_cartridge():
    html = '<html><head><script type="application/ld+json">{"@context": "https://schema.org", "@type": "Article"}</script></head></html>'
    problems = pagechecks.find_rendered_json_ld_violations(html, "no-such-cartridge")
    assert any("no registered JSON-LD type" in p["issue"] for p in problems)


# ---------------------------------------------------------------------------
# Rendered internal-link count (post-render backstop)
# ---------------------------------------------------------------------------

def test_rendered_internal_link_count_known_good_pages_pass(tmp_path):
    for cartridge_name, page in ALL_PAGES:
        html = _render(tmp_path, cartridge_name, page).read_text()
        assert pagechecks.find_rendered_internal_link_violations(html) == [], cartridge_name


def test_rendered_internal_link_count_catches_a_page_with_none():
    problems = pagechecks.find_rendered_internal_link_violations(
        '<html><body><a href="https://elsewhere.example.com/x">out</a></body></html>'
    )
    assert any("no internal link" in p["issue"] for p in problems)
