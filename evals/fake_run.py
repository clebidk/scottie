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
import copy
import contextlib
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path

from harness import cli
from harness import headlines
from harness import heroes
from harness import listicle as listicle_mod
from harness import looks
from harness import pipeline
from harness import skeletons
from harness import tenant as tenant_mod
from harness.prices import build_live_price_claims

# Cycle 97: tests/conftest.py sets these at import time for the suite (the
# photo library off, one draft, no TypeSafe key, no design gate). Importing
# the doubles below must not change them for the process that imports this
# module: `harness library build` on prod lost its 66 library photos that way
# (every caps_over_photo and face version "not renderable offline").
_SUITE_ENV = ("HARNESS_PHOTO_LIBRARY_DIR", "HARNESS_JEV_DRAFTS", "TYPESAFE_API_KEY", "HARNESS_DESIGN_GATE")
_saved_env = {key: os.environ.get(key) for key in _SUITE_ENV}
from tests.conftest import FakeClient, json_response  # noqa: E402
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE, _FILLER_SENTENCES  # noqa: E402

for _key, _value in _saved_env.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value

# Cycle 82: the landing cartridge's canned page -- every number in it
# cites a verified claim the offline facts pack carries; the price, rating,
# specs and links are the renderer's.
LANDING_PAGE = {
    "cta_text": "Shop the Fuji",
    "cta_url": "https://peaksaunas.com/products/peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy",
    "hero": {
        "eyebrow": "Sauna shopping, made plain",
        "headline": "The home sauna with nothing hidden before checkout",
        "accent_phrase": "nothing hidden",
        "subhead": "The price, the power it needs and what arrives at your door, stated up front so you can decide without a sales call.",
        "hero_image": {"asset_id": "asset-peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy-1"},
    },
    "checked_claims": [
        {"ad_said": "Sauna shopping used to be confusing.",
         "fact": "The Fuji page lists its capacity, cabin wood and heat range before you buy: two people, Canadian red cedar, up to 150°F.",
         "claim_ids": ["spec-fuji-capacity", "spec-fuji-cabin-material", "spec-fuji-max-temperature"]},
        {"ad_said": "It plugs into your home.",
         "fact": "The Fuji runs on a 120V / 20A dedicated outlet; a standard 15A outlet is not sufficient.",
         "claim_ids": ["gbrain-fuji-power"]},
        {"ad_said": "It fits a spare corner.",
         "fact": "Assembled, the cabin measures 53 inches wide, 44 inches deep and 75 inches high.",
         "claim_ids": ["gbrain-fuji-dimensions"]},
        {"ad_said": "Real infrared, not a gimmick.",
         "fact": "Infrared heaters cover near, mid and far wavelengths, from 700 to 25,000nm.",
         "claim_ids": ["spec-fuji-infrared-wavelength-range"]},
        {"ad_said": "Red light comes with it.",
         "fact": "Medical-grade red light therapy is included standard, with one panel you sit facing from about seven to eight inches away.",
         "claim_ids": ["gbrain-allowlist-red-light", "gbrain-fuji-rlt"]},
        {"ad_said": "Getting it home is simple.",
         "fact": "Every order ships free. In-stock orders leave the warehouse within a few business days, and heavy freight arrives on a delivery appointment you schedule.",
         "claim_ids": ["shipping-policy"]},
    ],
    "faq": {
        "questions": [
            {"question": "Do I need special wiring?",
             "answer": "You need a 120V / 20A dedicated outlet. A standard 15A outlet is not sufficient, so check your panel before ordering.",
             "claim_ids": ["gbrain-fuji-power"]},
            {"question": "How many people fit inside?",
             "answer": "Two people sit side by side on one bench.",
             "claim_ids": ["spec-fuji-capacity"]},
            {"question": "How hot does the cabin get?",
             "answer": "The Fuji reaches up to 150°F, with heaters placed around you so the warmth reaches your back, sides and legs, not just the front.",
             "claim_ids": ["spec-fuji-max-temperature", "gbrain-allowlist-360-full-spectrum"]},
            {"question": "What happens if I change my mind?",
             "answer": "Returns are accepted once the cabin is taken apart and repacked in its original crate.",
             "claim_ids": ["returns-policy"]},
        ]
    },
    "closing": {"headline": "See the Fuji with every number on the page"},
}

# Cycle 85: the same page in the landing `proof` look -- plus the two blocks
# that look asks the writer for, each citing claims the offline facts pack
# verifies. No proof.quote: the offline facts pack has no reviews_summary.
LANDING_PROOF_PAGE = {
    **LANDING_PAGE,
    "benefits": [
        {"text": "Canadian red cedar, inside and out", "claim_ids": ["gbrain-fuji-wood"]},
        {"text": "Room for two people side by side", "claim_ids": ["spec-fuji-capacity"]},
        {"text": "Near, mid and far infrared heat", "claim_ids": ["spec-fuji-infrared-wavelength-range"]},
        {"text": "Medical-grade red light therapy, included standard",
         "claim_ids": ["gbrain-allowlist-red-light", "gbrain-fuji-rlt"]},
        {"text": "Heaters placed all around you", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
    ],
    "icon_row": [
        {"icon_key": "red_light", "label": "Red light therapy", "claim_ids": ["gbrain-allowlist-red-light"]},
        {"icon_key": "full_spectrum", "label": "Full-spectrum heat", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"icon_key": "wood", "label": "Canadian red cedar", "claim_ids": ["gbrain-fuji-wood"]},
        {"icon_key": "us_owned", "label": "US-owned company", "claim_ids": ["gbrain-allowlist-us-owned"]},
    ],
}

# Cycle 91: the writer fields of the catalog blocks (harness/blocks/catalog/)
# a block-built landing look asks for -- each line citing claims the offline
# facts pack verifies. _canned_page adds the ones the run's look declares.
LANDING_BLOCK_FIELDS = {
    "photo_essay": {"lines": [
        {"text": "Room for two people side by side on one cedar bench.",
         "claim_ids": ["spec-fuji-capacity", "gbrain-fuji-wood"]},
        {"text": "The red light panel faces you while the cabin heats.", "claim_ids": ["gbrain-fuji-rlt"]},
        {"text": "Heaters sit all around you, so the warmth reaches your back and legs.",
         "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"text": "A compact cabin that fits a spare corner of the house.", "claim_ids": ["gbrain-fuji-dimensions"]},
    ]},
    "feature_grid": {"cells": [
        {"label": "Red light", "line": "Medical-grade red light therapy comes standard in the cabin.",
         "claim_ids": ["gbrain-allowlist-red-light", "gbrain-fuji-rlt"]},
        {"label": "Full-spectrum heat", "line": "Near, mid and far infrared heaters placed all around you.",
         "claim_ids": ["spec-fuji-infrared-wavelength-range", "gbrain-allowlist-360-full-spectrum"]},
        {"label": "Canadian red cedar", "line": "The cabin is red cedar on the inside and the outside.",
         "claim_ids": ["gbrain-fuji-wood"]},
        {"label": "Free shipping", "line": "Every order ships free, on a delivery appointment you schedule.",
         "claim_ids": ["shipping-policy"]},
    ]},
    "statement_band": {"line": "A real sauna session at home, on your own schedule"},
}

CANNED_PAGES = {
    "article": ARTICLE_PAGE,
    "product-page": PRODUCT_PAGE_PAGE,
    "longform": LONGFORM_PAGE,
    "landing": LANDING_PAGE,
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
    "mistakes": "5 Mistakes Careful Buyers Make When Buying A Home Infrared Cabin",
    "questions": "5 Questions Careful Buyers Should Ask Before Buying A Home Cabin",
    "myths": "5 Home Infrared Cabin Myths Careful Buyers Still Hear, and What the Evidence Says",
    "tested": "We Checked 5 Home Infrared Cabin Claims Careful Buyers Keep Hearing. Here Is What Held Up",
}


# Cycle 65: the fake-run listicle tests use the founder fixture -- an
# attributed line must be his own words. Cycle 74: one distinct proof line per
# item (the repeated-sentence gate), each citing a policy claim every facts
# pack carries.
_LISTICLE_PROOFS = (
    # cycle 79 review: no evidence of the speaker's pronoun in a text ad -> the neutral frame
    {"text": 'In the ad, the creator says, "I hated how confusing sauna shopping used to be."',
     "attributed_to_customer": True},
    {"text": "Every order ships free.", "claim_ids": ["shipping-policy"]},
    {"text": "Returns are accepted once the cabin is repacked in its original crate.", "claim_ids": ["returns-policy"]},
    {"text": "In-stock orders leave the warehouse within a few business days.", "claim_ids": ["shipping-policy"]},
    {"text": "Shipping is free whatever the model.", "claim_ids": ["shipping-policy"]},
)


_TAG_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
              "kilo", "lima", "mike", "november")


def _filler_words(n, offset=0, tag=None):
    """Cycle 74: `tag` marks every sentence of this field as its own, so the
    overlapping filler windows never repeat a sentence across fields (the
    repeated-sentence gate)."""
    stream = " ".join(_FILLER_SENTENCES * 4).split()
    text = " ".join(stream[offset:offset + n])
    if tag is None:
        return text
    return re.sub(r"([.!?:])(\s|$)", rf" ({_TAG_WORDS[tag]})\1\2", text) + f" ({_TAG_WORDS[tag]})"


def _display_line(brief):
    """Cycle 79: the display line shares a word with the ad's hook (gate)."""
    words = re.findall(r"[A-Za-z]{5,}", (brief or {}).get("hook") or "")
    word = max(words, key=len).lower() if words else "sauna"
    return f"The {word} part, made simple"


def _listicle_page(style, headline=None, hero_quote_id=None, display_line=None):
    headline = headline or _LISTICLE_HEADLINES[style]
    page = {
        "style": style,
        # Cycle 79: the first-screen fields.
        "eyebrow": "For careful buyers",
        "headline": headline,
        "accent_phrase": " ".join(headline.rstrip(".").split()[-2:]),
        "display_headline": display_line or "Check the cabin before it comes home",
        # Cycle 74: the dek carries the fixtures' hooks (message match).
        "dek": "A plain look at the price and what holds up once the box arrives, without a sales call.",
        "lede": "What should you check before a cabin like this comes home? The answers start below.",
        "scroll_cue": "Start with the first check",
        "hero": {"asset_id": _LISTICLE_ASSET_IDS[0]},
        "reasons": [
            {
                "number": i,
                "heading": f"What a careful buyer checks first, part {i}",
                "text": _filler_words(130, offset=i * 17, tag=i),
                "image": {"asset_id": _LISTICLE_ASSET_IDS[i]},
                "proof": _LISTICLE_PROOFS[i - 1],
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
                    "answer": _filler_words(40, offset=i * 29, tag=7 + i),
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
    if hero_quote_id:
        page["hero_quote_id"] = hero_quote_id
    return page


def _canned_page(cartridge, style=None, tenant=None, headline=None, hero_quote_id=None, display_line=None,
                 landing_look=None):
    if cartridge == "quiz":
        # Cycle 57: built from the run tenant's own quiz rubric (question ids
        # and option labels must echo it), lazily like comparison's.
        from harness import quiz as quiz_mod
        from tests.test_quiz import quiz_page

        root = tenant_mod.load_tenant(tenant, require=True).root
        return quiz_page(quiz_mod.load_rubric(quiz_mod.rubric_path(root))[0])
    if cartridge == "comparison":
        # Imported lazily so the driver's startup stays light when the
        # comparison cartridge isn't involved. Cycle 56: the v1.0.0 canned
        # page is a Fuji "models" page (Fuji vs Everest vs Rainier) -- the
        # product a claim-free brief defaults to, and the one the soak pins.
        from tests.test_comparison import COMPARISON_PAGE

        return COMPARISON_PAGE
    if cartridge == "listicle":
        return _listicle_page(style or listicle_mod.STYLES[0], headline, hero_quote_id, display_line)
    if cartridge == "landing" and landing_look == "proof":
        return LANDING_PROOF_PAGE
    if cartridge == "landing" and landing_look:
        # Cycle 91: a block-built look (cinematic, buybox, a tenant look with a
        # look.json) gets the writer fields of the blocks it declares.
        from harness.blocks import catalog

        wanted = [b for b in catalog.look_blocks("landing", landing_look, tenant=tenant_mod.load_tenant(tenant))
                  if b in LANDING_BLOCK_FIELDS]
        if wanted:
            return {**LANDING_PAGE, "blocks": {b: copy.deepcopy(LANDING_BLOCK_FIELDS[b]) for b in wanted}}
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
    # Cycle 92: a fake run has no judge answers; the design gate is off
    # unless the caller set HARNESS_DESIGN_GATE itself (and the judge answers
    # with extra_responses).
    gate_was_set = "HARNESS_DESIGN_GATE" in os.environ
    if not gate_was_set:
        os.environ["HARNESS_DESIGN_GATE"] = "off"
    try:
        yield
    finally:
        if not gate_was_set:
            os.environ.pop("HARNESS_DESIGN_GATE", None)
        for obj, name, old in saved:
            setattr(obj, name, old)


def _newest_run_dir(base_dir, pattern):
    dirs = list(Path(base_dir).glob(pattern))
    if not dirs:
        raise SystemExit(f"no run directory matching {pattern!r} under {base_dir}")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def run_once(input_arg, *, tenant=None, cartridges="article,product-page,longform", seed=42, product=None,
             style=None, headline_template=None, skeleton=None, look=None, hero_style=None, extra_responses=()):
    """One fake-client run, programmatically. Returns (exit_code, run_dir or
    None, [page.json paths]). Shared by main() (the CLI), evals/soak.py
    (the 200-generation dry run) and harness/library.py (cycle 86: one
    reference render per page version; `hero_style` is the listicle's first
    screen, as `harness run --hero-style`)."""
    selected = [c.strip() for c in cartridges.split(",") if c.strip()]
    known = set(CANNED_PAGES) | {"comparison", "listicle", "quiz"}
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
        # Cycle 73: a pinned skeleton sets the style when --style does not.
        resolved_style = listicle_mod.resolve_style(
            skeletons.style_for(skeleton, style) if skeleton else style,
            seed=seed, tenant=tenant_mod.load_tenant(tenant, require=True),
        )
    # Cycle 70: the canned headline has to be the one the run's headline
    # template asks for, so a fake run pins the template -- the style's own
    # formula unless the caller names one. A named one must need no extra
    # evidence: the canned items cite none.
    canned_headline = None
    if "listicle" in selected:
        headline_template = headline_template or headlines.legacy_id(resolved_style)
        if headline_template != headlines.legacy_id(resolved_style):
            plan = headlines.build_plan(
                headline_template, resolved_style, {"verified_claims": []},
                tenant=tenant_mod.load_tenant(tenant, require=True), today=datetime.date.today(),
            )
            canned_headline = headlines.example_headline(plan, 5)

    # Cycle 85: the landing page's look decides which canned page the writer
    # "returns" (the proof look's page carries benefits and icon_row), from
    # the same rule the run uses.
    landing_look = None
    if "landing" in selected:
        # Cycle 91: requested_for with the tenant, so a tenant look (with its
        # look.json blocks) gets the canned page it declares, as the run does.
        tenant_obj_ = tenant_mod.load_tenant(tenant, require=True)
        landing_look = looks.resolve_look("landing", looks.requested_for("landing", look, tenant_obj_),
                                          tenant=tenant_obj_)

    brief = _brief_for(input_arg)
    responses = [json_response(brief)]
    if brief.get("claims_made"):
        responses.append(json_response({}))  # the semantic-match call only happens when claims exist
    # Cycle 79: the first screen names the ad quote it shows (when the ad has one).
    from harness.claims import safe_quote_candidates

    quotes = safe_quote_candidates(brief, None)
    hero_quote_id = quotes[0]["id"] if quotes else None
    responses += [json_response(_canned_page(c, resolved_style, tenant, canned_headline, hero_quote_id,
                                             _display_line(brief), landing_look))
                  for c in selected]
    responses += list(extra_responses)   # cycle 92: the design gate's judge answers, in order
    client = FakeClient(responses)

    args = argparse.Namespace(
        input=input_arg,
        cartridges=cartridges,
        seed=seed,
        style=style,
        headline_template=headline_template,
        skeleton=skeleton,
        look=look,
        hero_style=hero_style,
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
    parser.add_argument("--headline-template", default=None,
                        help="listicle headline template id; default: the style's own formula")
    parser.add_argument("--skeleton", choices=skeletons.skeleton_ids(), default=None,
                        help="listicle winner skeleton id; default: picked from the ad's angle")
    parser.add_argument("--product", default=None)
    parser.add_argument("--look", choices=list(looks.all_looks()), default=None,
                        help="a look of a selected cartridge (e.g. landing: open | proof); default: the cartridge's own")
    parser.add_argument("--hero-style", type=heroes.arg, metavar="HERO", default=None,
                        help="first-screen hero composition (harness/heroes/); default: the look's own "
                             "(the listicle's seeded from --seed)")
    parser.add_argument("--baseline-dir", default=None,
                        help="copy each cartridge's page.json here as <cartridge>.page.json, plus a manifest.json")
    ns = parser.parse_args(argv)

    exit_code, run_dir, pages = run_once(
        ns.input, tenant=ns.tenant, cartridges=ns.cartridges, seed=ns.seed, product=ns.product,
        style=ns.style, headline_template=ns.headline_template, skeleton=ns.skeleton, look=ns.look,
        hero_style=ns.hero_style,
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
