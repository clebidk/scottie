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
from harness import pipeline
from harness import tenant as tenant_mod
from harness.prices import build_live_price_claims
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE

CANNED_PAGES = {
    "article": ARTICLE_PAGE,
    "product-page": PRODUCT_PAGE_PAGE,
    "longform": LONGFORM_PAGE,
}

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--cartridges", default="article,product-page,longform")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--product", default=None)
    parser.add_argument("--baseline-dir", default=None,
                        help="copy each cartridge's page.json here as <cartridge>.page.json, plus a manifest.json")
    ns = parser.parse_args(argv)

    selected = [c.strip() for c in ns.cartridges.split(",") if c.strip()]
    unknown = [c for c in selected if c not in CANNED_PAGES]
    if unknown:
        print(f"no canned page for cartridge(s): {unknown}; have: {sorted(CANNED_PAGES)}", file=sys.stderr)
        return 1

    brief = _brief_for(ns.input)
    responses = [json_response(brief), json_response({})] + [json_response(CANNED_PAGES[c]) for c in selected]
    client = FakeClient(responses)

    args = argparse.Namespace(
        input=ns.input,
        cartridges=ns.cartridges,
        seed=ns.seed,
        product=ns.product,
        ffmpeg_bin="/usr/bin/ffmpeg",
        whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin",
        tenant=ns.tenant,
        batch=False,
    )
    with fake_environment(client):
        exit_code = cli.cmd_run(args)
    if exit_code != 0:
        return exit_code

    tenant = tenant_mod.load_tenant(ns.tenant, require=True)
    run_dir = _newest_run_dir(tenant.out_dir, f"*-{pipeline.slugify(ns.input)}-*")
    pages = sorted(run_dir.glob("*/page.json"))
    print(f"fake run: {len(pages)} page(s) under {run_dir}")
    for p in pages:
        print(f" - {p}")

    if ns.baseline_dir:
        dest = Path(ns.baseline_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for p in pages:
            shutil.copy2(p, dest / f"{p.parent.name}.page.json")
        manifest = {
            "run_id": run_dir.name,
            "tenant": tenant.name,
            "fixture": Path(ns.input).name,
            "seed": ns.seed,
            "cartridges": selected,
            "captured_at": datetime.date.today().isoformat(),
            "note": "page.json files are the byte-stable regression reference; manifest dates and run ids are not.",
        }
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"baseline: {len(pages)} page.json file(s) -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
