"""End-to-end dry run of `adv run` against the real cartridges/ and claims/
files in this repo, with a fake Anthropic client standing in for every model
call. No network, no whisper, no ffmpeg (the fixture is a .txt -> passthrough
ingest path). The live-price fetch (fix 2), reviews fetch (fix 3), and asset
download (fix 8) are also faked -- see `_patch_network` -- so this stays a
network-free dry run."""
import argparse
import json
from pathlib import Path

from harness import cli, pipeline
from harness.prices import build_live_price_claims
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE
from tests.support import TENANT, newest_run_dir

FIXTURE = TENANT.fixtures_dir / "hidden-costs-v2.transcript.txt"


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
        # No real Shopify body_html here -- pdp_claims.seed_pdp_claims (fix
        # cycle 9 item 1) sees no raw products and produces no claims, which
        # is fine: this dry run doesn't exercise PDP claim seeding.
        return products, by_slug, []

    monkeypatch.setattr(pipeline, "refresh_price_data", fake_refresh_price_data)
    monkeypatch.setattr(pipeline, "fetch_reviews_claim", lambda url, today_iso, log=None: None)
    monkeypatch.setattr(pipeline, "http_fetch_bytes", lambda url: b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    def fake_download_drive_file(file_id, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{file_id}.jpg"
        dest.write_bytes(b"\xff\xd8\xff\xe0fake-drive-bytes")
        return dest

    monkeypatch.setattr(pipeline, "download_drive_file", fake_download_drive_file)


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
    "audience": "",
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
        tenant=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_run_dry_run_produces_three_pages(monkeypatch):
    responses = [
        json_response(AD_BRIEF_RESPONSE),
        json_response({}),  # fix cycle 12 item 4: semantic-match call, no mappings
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
    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")

    assert (run_dir / "ad_brief.json").exists()
    assert (run_dir / "facts_pack.json").exists()
    assert (run_dir / "REVIEW.md").exists()

    for cartridge in ("article", "product-page", "longform"):
        index_html = run_dir / cartridge / "index.html"
        assert index_html.exists(), f"missing {index_html}"
        assert "Advertisement" in index_html.read_text()

    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert facts_pack["product"]["slug"] == FUJI_SLUG


# ---------------------------------------------------------------------------
# Fix cycle 10 item 1: product-picking now runs BEFORE the ad-claims gate in
# cmd_run's real order -- before this fix, gate_ad_brief_claims always ran
# first and STOPped on a price ad claim before pick_product_with_warning's
# price-based inference (fix cycle 9 item 2) ever got a chance to run for
# real (it was only ever verified in isolation, per docs/FIXLOG.md Cycle 9's
# "Not fixed" section). No model name is named anywhere in this ad_brief --
# only the quoted $5,450 price, which is the Mini's and no other active
# product's -- so a PASS here, grounded on the Mini, is proof the real
# pipeline order reaches price inference now.
# ---------------------------------------------------------------------------

MINI_SLUG = "peak-saunas-mini-1-person-indoor-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"

PRICE_INFERENCE_AD_BRIEF = {
    "hook": "I keep hearing all the benefits of infrared saunas.",
    "promise": "See if it pencils out compared to a membership.",
    "angle": "The math works out cheaper than a sauna membership.",
    "claims_made": ["Infrared sauna is on sale right now for $5,450."],
    "speaker_experience": ["I ran the math myself before deciding."],
    "features_shown": [],
    "objections_raised": [],
    "cta": "Buy now",
    "tone": "candid",
    "speaker_pov": "first_person",
    "source_file": "price-comparison-v2.transcript.txt",
    "input_type": "text",
    "transcript_or_text": "I think I'm going to buy the Peak sauna.",
    "audience": "",
}


def test_run_reaches_price_based_product_inference_in_the_real_pipeline_order(monkeypatch):
    responses = [
        json_response(PRICE_INFERENCE_AD_BRIEF),
        json_response({}),  # fix cycle 12 item 4: semantic-match call, no mappings
        # images=[]: this run grounds on the Mini, whose real asset manifest
        # does not contain the Fuji id the shared ARTICLE_PAGE carries, and
        # the phase-2 image-allowlist gate check would (correctly) fail it.
        # This test is about price-based product inference, not assets.
        json_response(dict(ARTICLE_PAGE, images=[])),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args(cartridges="article", product=None))
    assert exit_code == 0

    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert facts_pack["product"]["slug"] == MINI_SLUG

    review_md = (run_dir / "REVIEW.md").read_text()
    assert "product inferred from quoted price $5,450" in review_md


def test_run_stops_on_unmatched_claim(monkeypatch):
    # Not an EMF claim -- fix 7 (below) drops EMF-mentioning claims instead of
    # stopping, so this uses a different, still-unsourced claim to test the
    # generic unmatched-claim STOP path. Also deliberately not phrased with
    # "competitor"/"competing" as its own grammatical subject -- fix cycle 12
    # item 3's classify_ad_claim_about would otherwise classify it "about:
    # alternative" instead (never stops the run under any policy), which is
    # not what this test is checking.
    # This test exercises ad_overclaim_policy "stop" specifically -- pin it
    # regardless of the tenant's committed default (Cycle 19: the default is
    # now "warn"), so the test stays hermetic against claims/config.json's
    # real on-disk value rather than depending on it.
    stop_claims_config = dict(TENANT.claims_config, ad_overclaim_policy="stop")
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: stop_claims_config))

    bad_ad_brief = dict(AD_BRIEF_RESPONSE)
    bad_ad_brief["claims_made"] = ["Peak Saunas ships every order within two business days."]
    responses = [
        json_response(bad_ad_brief),
        json_response({}),  # fix cycle 12 item 4: semantic-match call, no mappings
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args())
    assert exit_code == 2

    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")
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

    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")

    ad_brief = json.loads((run_dir / "ad_brief.json").read_text())
    assert ad_brief["claims_made"] == []
    assert ad_brief["features_shown"] == []
    assert ad_brief["_dropped_emf_claims"] == [
        "Competitor saunas leak dangerous levels of EMF radiation.",
        "near-zero EMF design",
    ]

    review_md = (run_dir / "REVIEW.md").read_text()
    assert "dropped claim: Competitor saunas leak dangerous levels of EMF radiation." in review_md
