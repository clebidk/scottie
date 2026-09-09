"""End-to-end dry run of `adv run` against the real cartridges/ and claims/
files in this repo, with a fake Anthropic client standing in for every model
call. No network, no whisper, no ffmpeg (the fixture is a .txt -> passthrough
ingest path). The live-price fetch (fix 2), reviews fetch (fix 3), and asset
download (fix 8) are also faked -- see `_patch_network` -- so this stays a
network-free dry run."""
import argparse
import json
from pathlib import Path

from adv import cli
from adv.claims import ClaimsGateFailure
from adv.prices import build_live_price_claims
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "hidden-costs-v2.transcript.txt"


def _patch_network(monkeypatch):
    """No real HTTP anywhere in this test: fake the fix-2 live price refresh
    (reuse the real, pure build_live_price_claims on the existing
    claims/products.json -- no network, no cache write), the fix-3 reviews
    fetch (no review data found), and the fix-8 asset download (a tiny fake
    image/file written locally instead of a real HTTP/Drive fetch)."""

    def fake_refresh_price_data(*, products_path, cache_path, show_compare_at_price, today_iso, log=None):
        products = json.loads(Path(products_path).read_text())["products"]
        claims = build_live_price_claims(products, today_iso, show_compare_at_price)
        by_slug = {}
        for slug, p in products.items():
            name_slug = p["name"].lower().replace(" ", "-")
            for c in claims:
                if c["id"] == f"price-{name_slug}":
                    by_slug[slug] = c
                    break
        return products, by_slug

    monkeypatch.setattr(cli, "refresh_price_data", fake_refresh_price_data)
    monkeypatch.setattr(cli, "fetch_reviews_claim", lambda url, today_iso, log=None: None)
    monkeypatch.setattr(cli, "http_fetch_bytes", lambda url: b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    def fake_download_drive_file(file_id, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{file_id}.jpg"
        dest.write_bytes(b"\xff\xd8\xff\xe0fake-drive-bytes")
        return dest

    monkeypatch.setattr(cli, "download_drive_file", fake_download_drive_file)


AD_BRIEF_RESPONSE = {
    "hook": "There's nothing worse than not being able to find a price online.",
    "promise": "See the price without talking to anyone.",
    "angle": "Transparent pricing beats a sales call.",
    "claims_made": ["The Peak Saunas Fuji is priced at $8250."],
    "speaker_experience": [
        "I've been trying to find an at-home sauna and so many brands make me talk to someone first.",
        "Peak Saunas laid all the information out right there.",
    ],
    "features_shown": ["price shown on the page"],
    "objections_raised": ["brands make you submit your info before showing a price"],
    "cta": "See pricing",
    "tone": "candid",
    "speaker_pov": "first_person",
    "source_file": "hidden-costs-v2.transcript.txt",
    "input_type": "text",
    "transcript_or_text": FIXTURE.read_text(),
}

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"


def _base_args(**overrides):
    defaults = dict(
        input=str(FIXTURE),
        cartridges="article,product-page,longform",
        seed=42,
        product=FUJI_SLUG,
        ffmpeg_bin="/usr/bin/ffmpeg",
        whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_run_dry_run_produces_three_pages(monkeypatch):
    responses = [
        json_response(AD_BRIEF_RESPONSE),
        json_response(ARTICLE_PAGE),
        json_response(PRODUCT_PAGE_PAGE),
        json_response(LONGFORM_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args())
    assert exit_code == 0

    # find the run dir we just created (newest matching slug under out/)
    out_dirs = sorted((REPO_ROOT / "out").glob("*-hidden-costs-v2-transcript"))
    assert out_dirs, "expected a run dir under out/"
    run_dir = out_dirs[-1]

    assert (run_dir / "ad_brief.json").exists()
    assert (run_dir / "facts_pack.json").exists()
    assert (run_dir / "REVIEW.md").exists()

    for cartridge in ("article", "product-page", "longform"):
        index_html = run_dir / cartridge / "index.html"
        assert index_html.exists(), f"missing {index_html}"
        assert "Advertisement" in index_html.read_text()

    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert facts_pack["product"]["slug"] == FUJI_SLUG


def test_run_stops_on_unmatched_claim(monkeypatch):
    # Not an EMF claim -- fix 7 (below) drops EMF-mentioning claims instead of
    # stopping, so this uses a different, still-unsourced claim to test the
    # generic unmatched-claim STOP path.
    bad_ad_brief = dict(AD_BRIEF_RESPONSE)
    bad_ad_brief["claims_made"] = ["Competitor saunas cost twice as much as Peak."]
    responses = [json_response(bad_ad_brief)]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args())
    assert exit_code == 2

    out_dirs = sorted((REPO_ROOT / "out").glob("*-hidden-costs-v2-transcript"))
    run_dir = out_dirs[-1]
    unmatched = json.loads((run_dir / "unmatched_claims.json").read_text())
    assert unmatched["stage"] == "ad_claims"
    assert len(unmatched["items"]) == 1


# ---------------------------------------------------------------------------
# fix cycle 2 item 7: an EMF-mentioning ad claim/feature is dropped from
# ad_brief during ingest (logged), never a STOP.
# ---------------------------------------------------------------------------

def test_run_drops_emf_claim_instead_of_stopping(monkeypatch):
    emf_ad_brief = dict(AD_BRIEF_RESPONSE)
    emf_ad_brief["claims_made"] = ["Competitor saunas leak dangerous levels of EMF radiation."]
    emf_ad_brief["features_shown"] = ["near-zero EMF design"]
    responses = [
        json_response(emf_ad_brief),
        json_response(ARTICLE_PAGE),
        json_response(PRODUCT_PAGE_PAGE),
        json_response(LONGFORM_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args())
    assert exit_code == 0

    out_dirs = sorted((REPO_ROOT / "out").glob("*-hidden-costs-v2-transcript"))
    run_dir = out_dirs[-1]

    ad_brief = json.loads((run_dir / "ad_brief.json").read_text())
    assert ad_brief["claims_made"] == []
    assert ad_brief["features_shown"] == []
    assert ad_brief["_dropped_emf_claims"] == [
        "Competitor saunas leak dangerous levels of EMF radiation.",
        "near-zero EMF design",
    ]

    review_md = (run_dir / "REVIEW.md").read_text()
    assert "dropped EMF claim: Competitor saunas leak dangerous levels of EMF radiation." in review_md
