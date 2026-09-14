"""listicle cartridge: registration, rendering, and reuse of the shared
claims gate (no forked copy of any check -- see cartridges/listicle/cartridge.md's
"reuse the shared code; do not fork it" rule)."""
import json

import pytest

from tests.support import REPO_ROOT, TENANT
from harness.cli import discover_cartridges
from harness.repair import (
    check_page_gates,
    find_cta_violation,
    get_cta_text,
)
from harness.claims import ClaimsGateFailure, gate_page_json
from harness.render import render_page
from harness.write import parse_word_range, resolve_allowed_cta_texts

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "listicle"


def _make_image_bytes(width, height, fmt="PNG"):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 60, 200)).save(buf, format=fmt)
    return buf.getvalue()

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
        {"id": "shipping-policy", "text": "shipping text", "category": "trust", "source": "https://peaksaunas.com/policies/shipping-policy"},
        {"id": "gbrain-allowlist-red-light", "text": "Medical-grade red light therapy (included standard).", "category": "trust", "source": "https://peaksaunas.com/products/fuji"},
    ],
    # Cycle 31: seven distinct assets -- pagechecks.find_duplicate_asset_
    # violations now requires every image slot on a page to be a distinct
    # asset id (docs/IMAGES-AUDIT-2026-09-14.md problem 3), and _make_items
    # below can be called with up to 7 items.
    "assets": [
        {"id": f"asset-{i}", "url": f"https://cdn.shopify.com/fuji-{i}.png", "kind": "lifestyle", "alt": "Fuji sauna"}
        for i in range(1, 8)
    ],
}

AD_BRIEF = {
    "hook": "hook", "promise": "promise", "angle": "angle", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See the models", "tone": "candid",
    "speaker_pov": "third_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

_FILLER = (
    "It gives a shopper a specific, concrete reason to trust the switch instead of a vague "
    "promise, the kind of plain detail that actually holds up once the box arrives at the door."
)


def _item_text(n_words=48):
    words = (_FILLER + " ") * ((n_words // len(_FILLER.split())) + 1)
    return " ".join(words.split()[:n_words])


def _make_items(n, with_claim=True):
    # ~85 words per item -- comfortably inside cartridge.md's 40-90 word
    # guideline and enough, across 5-7 items plus the proof row and closing
    # block, to clear the 600-word floor for the word-range gate test below.
    items = []
    for i in range(1, n + 1):
        item = {"number": i, "heading": f"Reason number {i}", "text": _item_text(85), "image": {"asset_id": f"asset-{i}"}}
        if with_claim and i == n:
            item["text"] = "Every unit comes with medical-grade red light therapy included standard. " + _item_text(75)
            item["claim_ids"] = ["gbrain-allowlist-red-light"]
        items.append(item)
    return items


def _listicle_page(n_items=7):
    return {
        "headline": "7 Reasons Busy Parents Are Switching to Peak Saunas",
        "dek": "A quick look at what makes the switch worth it.",
        "proof_row": [
            {"label": "Free shipping", "text": "on every order", "claim_ids": ["shipping-policy"]},
        ],
        "reasons": _make_items(n_items),
        "cta_text": "See the models",
        "cta_url": "https://peaksaunas.com/collections/all",
        "closing": {
            "headline": "Ready to feel the difference?",
            "paragraphs": [{"text": "Peak Saunas is one brand that makes switching easy for a busy household."}],
            "warranty_line": {
                "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
                "claim_ids": ["warranty-terms"],
            },
            "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
        },
    }


# ---------------------------------------------------------------------------
# Registration: discoverable, but opt-in only (not in the default random-3
# pool).
# ---------------------------------------------------------------------------

def test_listicle_is_discovered():
    assert "listicle" in discover_cartridges()


def test_listicle_is_not_in_the_default_cartridge_pool():
    pool = TENANT.get("default_cartridge_pool")
    assert "listicle" not in pool
    assert set(pool) == {"article", "product-page", "longform"}


def test_word_range_parses_600_to_1100():
    cartridge_md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    assert parse_word_range(cartridge_md) == (600, 1100)


def test_allowed_cta_texts_resolve_model_name_placeholder():
    schema = json.loads((CARTRIDGE_DIR / "schema.json").read_text())
    resolved = resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna", model_name="Fuji")
    assert resolved == ["See the models", "Shop the Fuji", "Book a consult"]


# ---------------------------------------------------------------------------
# CTA gate: listicle uses a flat top-level cta_text/cta_url, like longform
# and product-page (cli.get_cta_text's non-"article" branch) -- not
# article's nested page.cta.text.
# ---------------------------------------------------------------------------

def test_get_cta_text_reads_flat_field():
    page = _listicle_page()
    assert get_cta_text(page, "listicle") == "See the models"


def test_find_cta_violation_rejects_text_outside_the_allowed_list():
    page = _listicle_page()
    page["cta_text"] = "Buy now and save"
    problems = find_cta_violation(page, "listicle", ["See the models", "Shop the Fuji", "Book a consult"])
    assert len(problems) == 1
    assert "cta_text" in problems[0]["path"]


# ---------------------------------------------------------------------------
# check_page_gates: the full gate (claims.gate_page_json + word range + CTA)
# passes on a well-formed page and reuses every shared check unmodified.
# ---------------------------------------------------------------------------

def test_check_page_gates_passes_for_a_well_formed_page():
    page = _listicle_page(n_items=7)
    problems = check_page_gates(
        page, FACTS_PACK, "listicle",
        financing_lender=None, speaker_pov="third_person",
        word_range=(600, 1100), allowed_cta_texts=["See the models", "Shop the Fuji", "Book a consult"],
        ad_brief=AD_BRIEF,
    )
    assert problems == []


def test_gate_page_json_rejects_a_hype_word_in_an_item():
    page = _listicle_page()
    page["reasons"][0]["text"] = "This is a real game-changer for a busy household. " + _item_text(30)
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="third_person", ad_brief=AD_BRIEF)


def test_gate_page_json_rejects_bad_warranty_wording():
    page = _listicle_page()
    page["closing"]["warranty_line"]["text"] = "Backed by a limited lifetime warranty on everything."
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="third_person", ad_brief=AD_BRIEF)
    assert any("warranty wording" in p["issue"] for p in exc_info.value.items)


def test_gate_page_json_rejects_an_invented_financing_figure_with_no_lender_configured():
    page = _listicle_page()
    page["closing"]["financing_line"]["text"] = "Financing available from $199/mo with Affirm."
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="third_person", ad_brief=AD_BRIEF)
    assert any("financing_line" in p["path"] for p in exc_info.value.items)


def test_gate_page_json_enforces_first_person_attribution():
    page = _listicle_page()
    page["reasons"][0]["text"] = "I ran into this myself last winter and it changed my whole routine. " + _item_text(30)
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="first_person", ad_brief={**AD_BRIEF, "speaker_pov": "first_person"})


# ---------------------------------------------------------------------------
# render_page: header/byline/disclosure/CTA-twice/motion assets, brand tokens.
# ---------------------------------------------------------------------------

def test_render_listicle_page(tmp_path):
    page = _listicle_page(n_items=6)
    index_path = render_page(
        cartridge_name="listicle",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle",
        published="2026-09-10",
        updated="2026-09-10",
        download_assets=False,
    )
    html = index_path.read_text()

    assert "Advertisement" in html
    assert "is an advertisement published by Peak Saunas" in html
    assert '"@type": "ItemList"' in html
    assert 'class="pk-lp' in html
    assert "IntersectionObserver" in html
    assert html.count(">See the models<") == 2
    assert html.count('class="pk-h2"') == 6
    assert "Limited lifetime warranty; full terms by component are published on the warranty page." in html
    # no lender configured -- the renderer always swaps closing.financing_line
    # to the generic "Financing available" (same belt-and-suspenders pattern
    # as every other cartridge's hero/final_cta financing slot), regardless
    # of what the already-gated model text says.
    assert "Financing available" in html
    assert "Financing is available at checkout." not in html
    assert "Bread Pay" not in html

    page_json = json.loads((tmp_path / "listicle" / "page.json").read_text())
    assert page_json == page


def test_render_listicle_page_downloads_used_item_images(tmp_path):
    downloaded = {}
    image_bytes = _make_image_bytes(600, 600)

    def fake_fetch_url(url):
        downloaded[url] = downloaded.get(url, 0) + 1
        return image_bytes

    out_dir = tmp_path / "listicle"
    index_path = render_page(
        cartridge_name="listicle",
        page=_listicle_page(n_items=5),
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-10",
        updated="2026-09-10",
        fetch_url=fake_fetch_url,
    )
    html = index_path.read_text()
    # Cycle 31: the five reasons now use five distinct asset ids
    # (docs/IMAGES-AUDIT-2026-09-14.md problem 3 -- a page may not reuse the
    # same asset id in two slots), so five distinct urls are fetched, each
    # exactly once -- download_asset's own iteration over assets_by_id
    # (keyed by asset id) already guarantees no id is ever re-fetched.
    assert downloaded == {f"https://cdn.shopify.com/fuji-{i}.png": 1 for i in range(1, 6)}
    assert 'src="assets/asset-1-480.jpg"' in html
