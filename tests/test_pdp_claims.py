"""Fix cycle 9 item 1: PDP claim seeding from a product page's body_html.
tests/fixtures/mini-body-html-sample.html is a real body_html snippet
(fetched live from the Mini's product page) -- the actual sentences that
had no matching verified claim and STOPped fixtures/product-features-v2.mov
(app control, outlet/electrical, Bluetooth speakers) live in this fixture."""
import json
from pathlib import Path


from harness.pdp_claims import extract_pdp_claims, save_pdp_claims_cache, seed_pdp_claims

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_BODY_HTML = (REPO_ROOT / "tests" / "fixtures" / "mini-body-html-sample.html").read_text()
MINI_URL = "https://peaksaunas.com/products/peak-saunas-mini-1-person-indoor-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"


def _mini_product():
    return {"name": "Mini", "slug": "peak-saunas-mini", "url": MINI_URL, "active": True}


def test_extract_pdp_claims_finds_every_fact_stated_on_the_page():
    claims = extract_pdp_claims(_mini_product(), {"body_html": MINI_BODY_HTML}, "2026-09-09")
    ids = {c["id"] for c in claims}
    assert ids == {
        "pdp-mini-app-control",
        "pdp-mini-electrical",
        "pdp-mini-speakers",
        "pdp-mini-wood",
        "pdp-mini-red-light",
        "pdp-mini-crate-shipping",
        "pdp-mini-capacity",
        "pdp-mini-assembly",
    }


def test_extract_pdp_claims_text_is_the_pages_own_sentence():
    claims = {c["id"]: c for c in extract_pdp_claims(_mini_product(), {"body_html": MINI_BODY_HTML}, "2026-09-09")}
    assert "peak saunas app" in claims["pdp-mini-app-control"]["text"].lower()
    assert "120v" in claims["pdp-mini-electrical"]["text"].lower()
    assert "no electrician" in claims["pdp-mini-electrical"]["text"].lower()
    assert "bluetooth speaker" in claims["pdp-mini-speakers"]["text"].lower()
    assert "hemlock" in claims["pdp-mini-wood"]["text"].lower()
    assert "medical-grade red light" in claims["pdp-mini-red-light"]["text"].lower()
    assert "protective crate" in claims["pdp-mini-crate-shipping"]["text"].lower()
    assert "1-person" in claims["pdp-mini-capacity"]["text"].lower()
    assert "clasp-together" in claims["pdp-mini-assembly"]["text"].lower()
    # each is trimmed to one sentence, not the whole paragraph
    for c in claims.values():
        assert c["text"].endswith((".", "!", "?"))
        assert len(c["text"]) < len(MINI_BODY_HTML) / 4


def test_extract_pdp_claims_shape_and_provenance():
    claims = extract_pdp_claims(_mini_product(), {"body_html": MINI_BODY_HTML}, "2026-09-09")
    for c in claims:
        assert c["category"] == "spec"
        assert c["source"] == MINI_URL
        assert c["approved_by"] == "site"
        assert c["date"] == "2026-09-09"
        assert c["id"].startswith("pdp-mini-")


def test_extract_pdp_claims_never_produces_a_claim_mentioning_emf():
    body_with_emf = MINI_BODY_HTML.replace(
        "Plugs into a standard 120V household outlet",
        "Near-zero EMF. Plugs into a standard 120V household outlet",
    )
    claims = extract_pdp_claims(_mini_product(), {"body_html": body_with_emf}, "2026-09-09")
    for c in claims:
        assert "emf" not in c["text"].lower()
        assert "electromagnetic" not in c["text"].lower()


def test_extract_pdp_claims_only_creates_a_fact_the_page_actually_states():
    body = "<p>The Mini is a 1-person sauna, hand-finished in Canadian hemlock.</p>"
    claims = extract_pdp_claims(_mini_product(), {"body_html": body}, "2026-09-09")
    ids = {c["id"] for c in claims}
    assert ids == {"pdp-mini-capacity", "pdp-mini-wood"}


def test_extract_pdp_claims_empty_without_body_html():
    assert extract_pdp_claims(_mini_product(), {}, "2026-09-09") == []
    assert extract_pdp_claims(_mini_product(), None, "2026-09-09") == []


def test_seed_pdp_claims_skips_inactive_products_and_products_with_no_raw_match():
    products = {
        "peak-saunas-mini": _mini_product(),
        "peak-saunas-crown": {"name": "Crown", "slug": "peak-saunas-crown", "url": "https://peaksaunas.com/products/peak-saunas-crown", "active": False},
        "peak-saunas-fuji": {"name": "Fuji", "slug": "peak-saunas-fuji", "url": "https://peaksaunas.com/products/peak-saunas-fuji", "active": True},
    }
    live_by_handle = {
        "peak-saunas-mini": {"body_html": MINI_BODY_HTML},
        "peak-saunas-crown": {"body_html": "<p>1-person, hemlock.</p>"},  # inactive -- must be skipped
        # no raw entry for fuji at all -- must be skipped without error
    }
    claims = seed_pdp_claims(products, live_by_handle, "2026-09-09")
    ids = {c["id"] for c in claims}
    assert all(i.startswith("pdp-mini-") for i in ids)
    assert ids, "expected Mini's claims to be seeded"


def test_save_pdp_claims_cache_writes_json(tmp_path):
    cache_path = tmp_path / "runs" / "pdp-claims-cache.json"
    claims = [{"id": "pdp-mini-wood", "text": "hemlock", "category": "spec", "source": MINI_URL, "approved_by": "site", "date": "2026-09-09"}]
    save_pdp_claims_cache(cache_path, claims)
    data = json.loads(cache_path.read_text())
    assert data["claims"] == claims
    assert "generated_at" in data
