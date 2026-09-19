"""Run `harness run` end-to-end offline: the test suite's FakeClient stands in
for every model call and every network fetch is faked, exactly as
tests/test_cli_run.py does -- as a standalone command, so a run's page.json
files can be captured under evals/baseline/ as the regression reference
without a model key (docs/KIMI-LONG-RUN.md phases 0, 1 and 4).

Usage:
    python -m evals.fake_run tenants/peak-saunas/fixtures/founder-warranty-demo.txt \
        --tenant peak-saunas [--cartridges article,product-page,longform] \
        [--seed 42] [--product SLUG] [--baseline-dir evals/baseline/NAME]

The exit code is the run's own: 0 pass, 2 gate STOP, 3 budget.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import shutil
import sys
from pathlib import Path

from harness import cli
from harness import listicle as listicle_mod
from harness import pipeline
from harness import tenant as tenant_mod
from harness.prices import build_live_price_claims
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE, _FILLER_SENTENCES

CANNED_PAGES = {
    "article": ARTICLE_PAGE,
    "product-page": PRODUCT_PAGE_PAGE,
    "longform": LONGFORM_PAGE,
}

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"

# Cycle 41: listicle v0.2 is style-driven, so its canned page is built per
# style rather than stored as one literal -- one offline page per style,
# which is what lets `python -m evals.fake_run ... --cartridges listicle
# --style <s>` render all five without an API key. Asset ids are this
# product's own Shopify manifest ids (the same shape every other canned page
# uses) so pagechecks.find_image_allowlist_violations sees real ids.
_LISTICLE_ASSET_IDS = [f"asset-{FUJI_SLUG}-{i}" for i in range(1, 7)]

_LISTICLE_HEADLINES = {
    "reasons": "5 Reasons Careful Buyers Are Choosing A Home Infrared Cabin",
    "mistakes": "5 Mistakes People Make Buying A Home Infrared Cabin",
    "questions": "5 Questions to Ask Before You Buy A Home Cabin",
    "myths": "5 Home Infrared Cabin Myths, and What the Evidence Says",
    "tested": "We Tested Home Infrared Cabins for 8 Weeks. Here Is What Held Up",
}


def _filler_words(n, offset=0):
    stream = " ".join(_FILLER_SENTENCES * 4).split()
    return " ".join(stream[offset:offset + n])


def _listicle_page(style):
    return {
        "style": style,
        "headline": _LISTICLE_HEADLINES[style],
        "dek": "A plain look at what actually holds up once the box arrives.",
        "hero": {"asset_id": _LISTICLE_ASSET_IDS[0]},
        "reasons": [
            {
                "number": i,
                "heading": f"What a careful buyer checks first, part {i}",
                "text": _filler_words(130, offset=i * 17),
                "image": {"asset_id": _LISTICLE_ASSET_IDS[i]},
                "proof": {
                    "text": "One customer told us the room was warm before the kettle had boiled.",
                    "attributed_to_customer": True,
                },
            }
            for i in range(1, 6)
        ],
        "audience_fit": {
            "for_you": [
                {"text": "You have a dry, level corner of a room you can give up for good."},
                {"text": "You would rather read the published specification than book a sales call."},
                {"text": "You want a session you can take without leaving the house."},
            ],
            "not_for_you": [
                {"text": "You rent and cannot leave a cabin behind when the lease ends."},
                {"text": "Your only free wall is in an unheated garage that freezes in winter."},
                {"text": "You want something that folds away between sessions."},
            ],
        },
        "faq": {
            "questions": [
                {
                    "question": f"A question a careful buyer asks before ordering, part {i}?",
                    "answer": _filler_words(40, offset=i * 29),
                }
                for i in range(1, 6)
            ]
        },
        "cta_text": "See the models",
        "cta_url": "/collections/all",
        "closing": {
            "headline": "Ready to see which one fits",
            "recap": [
                {"text": "The cabin goes where you have room, not where a spa happens to have room."},
                {"text": "Everything a seller would tell you on a call is published on the page instead."},
                {"text": "Free shipping is included on every order.", "claim_ids": ["shipping-policy"]},
            ],
            "warranty_line": {
                "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
                "claim_ids": ["warranty-terms"],
            },
            "financing_line": {"text": "Financing is available through Bread Pay at checkout.", "claim_ids": []},
        },
    }


def _canned_page(cartridge, style=None):
    if cartridge == "comparison":
        # Imported lazily so the driver's startup stays light when the
        # comparison cartridge isn't involved.
        from tests.test_comparison import COMPARISON_PAGE

        return COMPARISON_PAGE
    if cartridge == "listicle":
        return _listicle_page(style or listicle_mod.STYLES[0])
    return CANNED_PAGES[cartridge]



def _brief_for(input_arg):
    """The canned ad_brief the FakeClient returns for this fixture's one
    ingest call. hidden-costs reuses the suite's own canned brief; the
    founder fixture gets a brief faithful to its transcript -- the price
    claim matches the live Fuji price claim, and the warranty claim is a
    deliberate locked-topic overclaim, dropped under the tenant's committed
    `ad_overclaim_policy: warn` (the fixture exists to exercise that gate).
    Any other text fixture gets a claim-free brief, which passes the gate
    trivially."""
    text = Path(input_arg).read_text()
    name = Path(input_arg).name
    if name == "hidden-costs-v2.transcript.txt":
        from tests.test_cli_run import AD_BRIEF_RESPONSE

        return dict(AD_BRIEF_RESPONSE)
    if name == "founder-warranty-demo.txt":
        return {
            "hook": "I built this company because I hated how confusing sauna shopping used to be.",
            "promise": "The price and the warranty, stated plainly by the founder.",
            "angle": "A founder stating the price and the warranty directly beats a sales call.",
            "claims_made": [
                "The Peak Saunas Fuji is priced at $8250.",
                "Peak Saunas warranty covers heating elements for 7 years and cabinetry and structure for 7 years.",
            ],
            "speaker_experience": [
                "I built this company because I hated how confusing sauna shopping used to be.",
            ],
            "features_shown": [],
            "objections_raised": ["sauna shopping used to be confusing"],
            "cta": "See pricing",
            "tone": "candid",
            "speaker_pov": "first_person",
            "source_file": name,
            "input_type": "text",
            "transcript_or_text": text,
            "audience": "",
        }
    return {
        "hook": "A plain statement about the product.",
        "promise": "See the details without talking to anyone.",
        "angle": "Plain information beats a sales call.",
        "claims_made": [],
        "speaker_experience": [],
        "features_shown": [],
        "objections_raised": [],
        "cta": "Learn more",
        "tone": "candid",
        "speaker_pov": "first_person",
        "source_file": name,
        "input_type": "text",
        "transcript_or_text": text,
        "audience": "",
    }


@contextlib.contextmanager
def fake_environment(client):
    """Swap cli.make_client and every network-touching pipeline global for
    fakes, then restore them -- importing this module from a test must not
    leak doubles into the rest of the suite (the same discipline conftest's
    _restore_active_tenant applies to the tenant globals)."""

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
        return products, by_slug, []

    def fake_download_drive_file(file_id, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{file_id}.jpg"
        dest.write_bytes(b"\xff\xd8\xff\xe0fake-drive-bytes")
        return dest

    patches = [
        (cli, "make_client", lambda: client),
        (pipeline, "refresh_price_data", fake_refresh_price_data),
        (pipeline, "fetch_reviews_claim", lambda url, today_iso, log=None: None),
        (pipeline, "http_fetch_bytes", lambda url: b"\xff\xd8\xff\xe0fake-jpeg-bytes"),
        (pipeline, "download_drive_file", fake_download_drive_file),
    ]
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    for obj, name, value in patches:
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, old in saved:
            setattr(obj, name, old)


def _newest_run_dir(base_dir, pattern):
    dirs = list(Path(base_dir).glob(pattern))
    if not dirs:
        raise SystemExit(f"no run directory matching {pattern!r} under {base_dir}")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def run_once(input_arg, *, tenant=None, cartridges="article,product-page,longform", seed=42, product=None,
             style=None):
    """One fake-client run, programmatically. Returns (exit_code, run_dir or
    None, [page.json paths]). Shared by main() (the CLI) and evals/soak.py
    (the 200-generation dry run)."""
    selected = [c.strip() for c in cartridges.split(",") if c.strip()]
    known = set(CANNED_PAGES) | {"comparison", "listicle"}
    unknown = [c for c in selected if c not in known]
    if unknown:
        print(f"no canned page for cartridge(s): {unknown}; have: {sorted(known)}", file=sys.stderr)
        return 1, None, []

    # Cycle 41: the canned listicle page has to be written in the SAME style
    # the run itself resolves (pipeline.prepare_run), or the style gate
    # rejects it -- so resolve it here the same way, from the same flag and
    # the same seed.
    resolved_style = None
    if "listicle" in selected:
        resolved_style = listicle_mod.resolve_style(
            style, seed=seed, tenant=tenant_mod.load_tenant(tenant, require=True)
        )

    brief = _brief_for(input_arg)
    responses = [json_response(brief)]
    if brief.get("claims_made"):
        responses.append(json_response({}))  # the semantic-match call only happens when claims exist
    responses += [json_response(_canned_page(c, resolved_style)) for c in selected]
    client = FakeClient(responses)

    args = argparse.Namespace(
        input=input_arg,
        cartridges=cartridges,
        seed=seed,
        style=style,
        product=product,
        ffmpeg_bin="/usr/bin/ffmpeg",
        whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin",
        tenant=tenant,
        batch=False,
    )
    with fake_environment(client):
        exit_code = cli.cmd_run(args)
    if exit_code != 0:
        return exit_code, None, []

    tenant_obj = tenant_mod.load_tenant(tenant, require=True)
    run_dir = _newest_run_dir(tenant_obj.out_dir, f"*-{pipeline.slugify(input_arg)}-*")
    pages = sorted(run_dir.glob("*/page.json"))
    return 0, run_dir, pages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--cartridges", default="article,product-page,longform")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--style", choices=list(listicle_mod.STYLES), default=None,
                        help="listicle style; default: deterministic from --seed")
    parser.add_argument("--product", default=None)
    parser.add_argument("--baseline-dir", default=None,
                        help="copy each cartridge's page.json here as <cartridge>.page.json, plus a manifest.json")
    ns = parser.parse_args(argv)

    exit_code, run_dir, pages = run_once(
        ns.input, tenant=ns.tenant, cartridges=ns.cartridges, seed=ns.seed, product=ns.product,
        style=ns.style,
    )
    if exit_code != 0:
        return exit_code

    print(f"fake run: {len(pages)} page(s) under {run_dir}")
    for p in pages:
        print(f" - {p}")

    if ns.baseline_dir:
        dest = Path(ns.baseline_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for p in pages:
            shutil.copy2(p, dest / f"{p.parent.name}.page.json")
        tenant = tenant_mod.load_tenant(ns.tenant, require=True)
        manifest = {
            "run_id": run_dir.name,
            "tenant": tenant.name,
            "fixture": Path(ns.input).name,
            "seed": ns.seed,
            "cartridges": [c.strip() for c in ns.cartridges.split(",") if c.strip()],
            "captured_at": datetime.date.today().isoformat(),
            "note": "page.json files are the byte-stable regression reference; manifest dates and run ids are not.",
        }
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"baseline: {len(pages)} page.json file(s) -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
