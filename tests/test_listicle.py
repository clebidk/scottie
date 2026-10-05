"""listicle cartridge v0.2: the five-style system, the structural gates, and
the rendered page's sections (including the ones the renderer -- never the
writer -- builds from facts_pack).

Registration and reuse of the shared claims gate (no forked copy of any
check -- see cartridges/listicle/cartridge.md's "reuse the shared code; do
not fork it" rule) are asserted here too.
"""
import json
import re

import pytest

from tests.support import REPO_ROOT, TENANT
from harness import listicle
from harness.cli import discover_cartridges
from harness.repair import (
    check_page_gates,
    count_words,
    find_cta_violation,
    get_cta_text,
)
from harness.claims import ClaimsGateFailure, gate_page_json
from harness.page_body import build_shopify_body
from harness.render import render_page
from harness.write import parse_word_range, resolve_allowed_cta_texts, validate_schema

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "listicle"
SCHEMA = json.loads((CARTRIDGE_DIR / "schema.json").read_text())


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
        {"id": "shipping-policy", "text": "Free shipping on all orders.", "category": "trust", "source": "https://peaksaunas.com/policies/shipping-policy"},
        {"id": "gbrain-allowlist-red-light", "text": "Medical-grade red light therapy (included standard).", "category": "trust", "source": "https://peaksaunas.com/products/fuji"},
    ],
    # One hero plus up to seven item images, all distinct
    # (pagechecks.find_duplicate_asset_violations).
    "assets": [
        {"id": f"asset-{i}", "url": f"https://cdn.shopify.com/fuji-{i}.png", "kind": "lifestyle", "alt": "Fuji sauna"}
        for i in range(1, 10)
    ],
}

# Cycle 74: a hook with real words -- listicle_quality's message-match gate
# needs the headline or dek to carry one of them ("switch" in the dek).
AD_BRIEF = {
    "hook": "Thinking about the switch to a home sauna?", "promise": "promise", "angle": "angle",
    "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See the models", "tone": "candid",
    "speaker_pov": "third_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

ALLOWED_CTA_TEXTS = ["See the models", "Shop the Fuji", "Book a consult"]

_FILLER = (
    "It gives a shopper a specific, concrete reason to trust the switch instead of a vague "
    "promise, the kind of plain detail that actually holds up once the box arrives at the door "
    "and the cabin is standing in a real room, measured against a real wall, in a real home."
)


_TAGS = {i: w for i, w in enumerate((
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima",
    "mike", "november", "oscar", "papa"), 1)}


def _words(n, tag=None):
    """n filler words. Cycle 74: `tag` makes every sentence of this field its
    own (the repeated-sentence gate fails one sentence used in two fields)."""
    filler = _FILLER.replace("a shopper", f"a shopper ({_TAGS[tag]})") if tag else _FILLER
    words = (filler + " ") * ((n // len(filler.split())) + 1)
    return " ".join(words.split()[:n])


# Cycle 74: one distinct proof line per item -- the same line on every item
# is what the repeated-sentence gate exists to stop.
_PROOF_LINES = (
    "Every unit ships with medical-grade red light therapy included as standard.",
    "Red light therapy comes in the box on this model, medical-grade, at no extra charge.",
    "Shipping is free on every order, whatever the model.",
    "The medical-grade red light panel is part of the standard build.",
    "Every order ships free, with no freight line at checkout.",
    "Medical-grade red light therapy is standard here, not an upgrade.",
    "There is no shipping charge on any order.",
)


HEADLINES = {
    "reasons": "5 Reasons Busy Parents Are Choosing Home Infrared Saunas",
    "mistakes": "5 Mistakes Busy Parents Make When Buying A Home Infrared Sauna",
    "questions": "5 Questions Busy Parents Should Ask Before Buying A Home Sauna",
    "myths": "5 Home Infrared Sauna Myths Busy Parents Still Hear, and What the Evidence Says",
    "tested": "We Checked 5 Home Infrared Sauna Claims Busy Parents Keep Hearing. Here Is What Held Up",
}


def _items(n=5, with_proof=True):
    items = []
    for i in range(1, n + 1):
        item = {
            "number": i,
            "heading": f"A plain heading number {i}",
            "text": _words(120, tag=i),
            "image": {"asset_id": f"asset-{i + 1}"},
        }
        if with_proof:
            proof = _PROOF_LINES[(i - 1) % len(_PROOF_LINES)]
            item["proof"] = {
                "text": proof,
                "claim_ids": ["gbrain-allowlist-red-light" if "red light" in proof else "shipping-policy"],
            }
        items.append(item)
    return items


def _listicle_page(style="reasons", n_items=5):
    return {
        "style": style,
        # cycle 79 first-screen fields
        "eyebrow": "For busy parents",
        "headline": HEADLINES[style],
        "accent_phrase": " ".join(HEADLINES[style].split()[-2:]),
        "dek": "A quick look at what makes the switch worth it.",
        "lede": "What do you check before a sauna comes home? The answers start below.",
        "scroll_cue": "Start with the first one",
        "hero": {"asset_id": "asset-1"},
        "reasons": _items(n_items),
        "audience_fit": {
            "for_you": [
                {"text": "You have a spare corner of a room that stays dry and level."},
                {"text": "You want a session you can take without leaving the house."},
                {"text": "You would rather read the specification than book a sales call."},
            ],
            "not_for_you": [
                {"text": "You rent and cannot leave a cabin behind when you move."},
                {"text": "Your only free wall is in an unheated garage that freezes."},
                {"text": "You want something that folds away between sessions."},
            ],
        },
        "faq": {
            "questions": [
                {
                    "question": f"A question a buyer actually asks, number {i}?",
                    "answer": _words(35, tag=8 + i),
                }
                for i in range(1, 6)
            ]
        },
        "cta_text": "See the models",
        "cta_url": "https://peaksaunas.com/collections/all",
        "closing": {
            "headline": "Ready to feel the difference?",
            "recap": [
                {"text": "The cabin goes where you have room, not where a spa has room."},
                {"text": "Everything a seller would tell you on a call is published instead."},
                {"text": "Free shipping is included on every order.", "claim_ids": ["shipping-policy"]},
            ],
            "warranty_line": {
                "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
                "claim_ids": ["warranty-terms"],
            },
            "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
        },
    }


def _gate(page, **kwargs):
    kwargs.setdefault("financing_lender", None)
    kwargs.setdefault("speaker_pov", "third_person")
    # Cycle 72: listicle has no page-level word range -- this is what
    # cartridge_write_constraints passes for it.
    kwargs.setdefault("word_range", None)
    kwargs.setdefault("allowed_cta_texts", ALLOWED_CTA_TEXTS)
    kwargs.setdefault("ad_brief", AD_BRIEF)
    return check_page_gates(page, FACTS_PACK, "listicle", **kwargs)


# ---------------------------------------------------------------------------
# Registration and cartridge constants
# ---------------------------------------------------------------------------

def test_listicle_is_discovered():
    assert "listicle" in discover_cartridges()


def test_listicle_is_not_in_the_default_cartridge_pool():
    pool = TENANT.get("default_cartridge_pool")
    assert "listicle" not in pool
    assert set(pool) == {"article", "product-page", "longform"}


# ---------------------------------------------------------------------------
# Cycle 72 (merge of cursor/listicle-winner-rules): listicle adapts a proven
# winner and has no page-level word range and no item word range. Every
# other cartridge keeps its own "N-M words" gate.
# ---------------------------------------------------------------------------

OTHER_RANGED_CARTRIDGES = ("article", "comparison", "longform", "product-page", "quiz")


def _listicle_constraints():
    from harness.repair import cartridge_write_constraints

    return cartridge_write_constraints("listicle", REPO_ROOT / "cartridges", FACTS_PACK, AD_BRIEF, TENANT)


def _short_winner_page():
    """A page at the live winner's density: about 350 words in all."""
    page = _listicle_page(n_items=5)
    for item in page["reasons"]:
        item["text"] = _words(10, tag=item["number"])
    for i, q in enumerate(page["faq"]["questions"], 1):
        q["answer"] = _words(8, tag=8 + i)
    return page


def test_listicle_has_no_page_level_word_range():
    cartridge_md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    assert parse_word_range(cartridge_md) is None
    assert "No page-level word minimum" in cartridge_md
    # The tenant-rendered prompt the harness actually parses, too.
    _schema, word_range, _ctas = _listicle_constraints()
    assert word_range is None


def test_a_350_word_listicle_passes_the_length_gate():
    from harness.repair import find_word_range_violation

    page = _short_winner_page()
    assert 300 <= count_words(page) <= 400
    _schema, word_range, _ctas = _listicle_constraints()
    assert find_word_range_violation(page, word_range) == []
    assert _gate(page, word_range=word_range, listicle_style="reasons") == []


@pytest.mark.parametrize("name", OTHER_RANGED_CARTRIDGES)
def test_other_cartridges_still_enforce_their_word_range(name):
    from harness.repair import find_word_range_violation
    from harness.write import load_cartridge_prompt

    cartridge_md, _ = load_cartridge_prompt(REPO_ROOT / "cartridges" / name, TENANT)
    word_range = parse_word_range(cartridge_md)
    assert word_range is not None, f"{name} lost its 'N-M words' rule"
    lo, hi = word_range
    for n in (lo - 1, hi + 1):
        page = {"body": {"text": _words(n)}}
        assert count_words(page) == n
        problems = find_word_range_violation(page, word_range)
        assert [p["path"] for p in problems] == ["$.word_count"], (name, n)
    assert find_word_range_violation({"body": {"text": _words(lo)}}, word_range) == []


def test_headline_length_phrase_never_parses_as_a_word_range():
    cartridge_md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    headline_rule = next(line for line in cartridge_md.splitlines() if line.startswith("- Headline:"))
    assert "6 to 13 words" in headline_rule  # cycle 79: open-loop headlines
    assert parse_word_range(headline_rule) is None
    # Why the wording matters: the old "8-14 words" is exactly the pattern
    # the page-level gate reads, and would have become a (8, 14) body gate.
    assert parse_word_range("Headline: 8-14 words") == (8, 14)
    assert "8-14 words" not in cartridge_md


def test_cartridge_md_frames_the_listicle_as_adapting_a_winner():
    cartridge_md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    assert "ADAPTING a proven listicle" in cartridge_md
    assert "component map" in cartridge_md
    assert "never a claims source" in cartridge_md
    assert "Do not pad toward article length" in cartridge_md
    assert "hero / item / lifestyle" in cartridge_md
    # Stale text from the pre-cycle-60 branch never comes back.
    for stale in ("Advertisement", "One customer told us", '"a customer"', "40-90", "600-1,100", "900-1,400", "50-150"):
        assert stale not in cartridge_md, stale


def test_cartridge_md_declares_v0_2_0():
    assert "(v0.2.0)" in (CARTRIDGE_DIR / "cartridge.md").read_text()


def test_allowed_cta_texts_resolve_model_name_placeholder():
    resolved = resolve_allowed_cta_texts(SCHEMA, "Peak Fuji 2-Person Infrared Sauna", model_name="Fuji")
    assert resolved == ALLOWED_CTA_TEXTS


# ---------------------------------------------------------------------------
# Style system
# ---------------------------------------------------------------------------

def test_five_styles_each_have_a_headline_formula_and_an_item_pattern():
    assert listicle.STYLES == ("reasons", "mistakes", "questions", "myths", "tested")
    for style in listicle.STYLES:
        assert listicle.HEADLINE_FORMULAS[style]
        assert listicle.ITEM_PATTERNS[style]


def test_style_from_the_seed_rotates_through_every_allowed_style():
    # The default tenant may pin a subset (tenant.yaml cartridges.listicle.styles),
    # so the rotation is over the ALLOWED set, whatever its size.
    allowed = listicle.tenant_styles()
    picked = {listicle.resolve_style(seed=s) for s in range(len(allowed))}
    assert picked == set(allowed)


class _UnpinnedTenant:
    def get(self, key, default=None):
        return default


def test_style_rotation_covers_all_five_when_nothing_is_pinned():
    tenant = _UnpinnedTenant()
    picked = {listicle.resolve_style(seed=s, tenant=tenant) for s in range(len(listicle.STYLES))}
    assert picked == set(listicle.STYLES)


def test_style_from_the_seed_is_deterministic():
    assert listicle.resolve_style(seed=7) == listicle.resolve_style(seed=7)


def test_an_explicit_style_always_wins():
    assert listicle.resolve_style("myths", seed=0) == "myths"


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError):
        listicle.resolve_style("listicle")


def test_a_tenant_may_pin_a_subset_of_styles(monkeypatch):
    class _Pinned:
        def get(self, key, default=None):
            return ["myths", "tested", "not-a-style"] if key == "cartridges.listicle.styles" else default

    assert listicle.tenant_styles(_Pinned()) == ("myths", "tested")
    assert listicle.resolve_style(seed=0, tenant=_Pinned()) == "myths"
    assert listicle.resolve_style(seed=1, tenant=_Pinned()) == "tested"


def test_writer_style_lines_state_the_formula_the_gate_measures():
    for style in listicle.STYLES:
        lines = " ".join(listicle.writer_style_lines(style))
        assert listicle.HEADLINE_FORMULAS[style] in lines
        assert listicle.ITEM_PATTERNS[style] in lines


def test_writer_style_lines_warn_against_real_estate_terms_and_brand_names():
    # Cycle 43: observed on a real run -- "6 Reasons Home Buyers Are Choosing
    # Peak Saunas Infrared Saunas" put a real-estate term in <audience> and
    # the tenant's own name in <category>. Cycle 49: every style now names an
    # <audience> (mistakes/questions/myths/tested joined reasons), so this
    # guidance applies to all five.
    for style in listicle.STYLES:
        lines = " ".join(listicle.writer_style_lines(style))
        assert "real-estate term" in lines
        assert "never the tenant's name" in lines
        assert '"people", "buyers"' in lines


def test_headline_slot_gate_catches_the_tenant_name():
    page = _listicle_page("reasons")
    page["headline"] = "6 Reasons Home Buyers Are Choosing Peak Saunas Infrared Saunas"
    problems = listicle.find_listicle_violations(
        page, style="reasons", tenant_name="Peak Saunas", product_names=["Fuji"],
    )
    keys = {p["key"] for p in problems}
    assert "listicle:headline_slots" in keys


def test_headline_slot_gate_catches_a_product_name():
    page = _listicle_page("reasons")
    page["headline"] = "5 Reasons Busy Parents Are Choosing Peak Fuji 2-Person Infrared Sauna"
    problems = listicle.find_listicle_violations(
        page, style="reasons", tenant_name="Peak Saunas",
        product_names=["Peak Fuji 2-Person Infrared Sauna"],
    )
    keys = {p["key"] for p in problems}
    assert "listicle:headline_slots" in keys


def test_headline_slot_gate_passes_a_category_only_headline():
    page = _listicle_page("reasons")
    page["headline"] = "5 Reasons Busy Parents Are Choosing Home Infrared Saunas"
    problems = listicle.find_listicle_violations(
        page, style="reasons", tenant_name="Peak Saunas", product_names=["Fuji"],
    )
    assert not any(p["key"] == "listicle:headline_slots" for p in problems)


def test_headline_slot_gate_is_a_noop_with_no_names_given():
    # Every existing caller of find_listicle_violations outside
    # check_page_gates doesn't have tenant_name/product_names -- this check
    # must not fire for them.
    page = _listicle_page("reasons")
    page["headline"] = "5 Reasons Busy Parents Are Choosing Home Infrared Saunas"
    assert listicle.find_listicle_violations(page, style="reasons") == []


def test_check_page_gates_flags_a_headline_naming_the_tenant():
    page = _listicle_page("reasons")
    page["headline"] = "5 Reasons Busy Parents Are Choosing Peak Saunas Infrared Saunas"
    problems = _gate(page, listicle_style="reasons")
    assert any(p["key"] == "listicle:headline_slots" for p in problems)


@pytest.mark.parametrize("style", listicle.STYLES)
def test_a_page_in_every_style_validates_against_the_schema_and_passes_the_gate(style):
    page = _listicle_page(style)
    assert validate_schema(page, SCHEMA) == []
    assert _gate(page, listicle_style=style) == []


@pytest.mark.parametrize("style", listicle.STYLES)
def test_a_headline_from_another_style_fails_the_formula_check(style):
    other = next(s for s in listicle.STYLES if s != style)
    page = _listicle_page(style)
    page["headline"] = HEADLINES[other]
    keys = {p["key"] for p in listicle.find_listicle_violations(page, style=style)}
    assert "listicle:headline_formula" in keys


def test_the_headline_number_must_match_the_item_count():
    page = _listicle_page("reasons", n_items=6)
    page["headline"] = HEADLINES["reasons"]  # says 5
    problems = listicle.find_listicle_violations(page, style="reasons")
    assert any(p["key"] == "listicle:headline_formula" and "6 items" in p["issue"] for p in problems)


def test_the_tested_style_headline_number_is_the_item_count():
    # Cycle 49: "tested" used to lead with a number of weeks, exempt from the
    # item-count check; it no longer does -- its headline's N is the item
    # count exactly like the other four styles.
    page = _listicle_page("tested", n_items=7)
    page["headline"] = HEADLINES["tested"]  # says 5
    problems = listicle.find_listicle_violations(page, style="tested")
    assert any(p["key"] == "listicle:headline_formula" and "7 items" in p["issue"] for p in problems)

    page["headline"] = "We Checked 7 Home Infrared Sauna Claims Busy Parents Keep Hearing. Here Is What Held Up"
    assert listicle.find_listicle_violations(page, style="tested") == []


@pytest.mark.parametrize("style", listicle.STYLES)
def test_each_style_accepts_its_own_formula_with_a_filled_audience_slot(style):
    page = _listicle_page(style)
    assert listicle.find_listicle_violations(page, style=style) == []


@pytest.mark.parametrize("style,bad_headline", [
    ("reasons", "5 Reasons Are Choosing Home Infrared Saunas"),
    ("mistakes", "5 Mistakes Make When Buying A Home Infrared Sauna"),
    ("questions", "5 Questions Should Ask Before Buying A Home Sauna"),
    ("myths", "5 Home Infrared Sauna Myths Still Hear, and What the Evidence Says"),
    ("tested", "We Checked 5 Home Infrared Sauna Claims Keep Hearing. Here Is What Held Up"),
])
def test_each_style_rejects_an_empty_audience_slot(style, bad_headline):
    page = _listicle_page(style)
    page["headline"] = bad_headline
    problems = listicle.find_listicle_violations(page, style=style)
    assert any(p["key"] == "listicle:headline_slots" for p in problems)


@pytest.mark.parametrize("style,generic_word", [
    ("reasons", "People"),
    ("mistakes", "Buyers"),
    ("questions", "Shoppers"),
    ("myths", "Customers"),
    ("tested", "Everyone"),
])
def test_each_style_rejects_a_bare_generic_audience_word(style, generic_word):
    page = _listicle_page(style)
    page["headline"] = HEADLINES[style].replace("Busy Parents", generic_word)
    problems = listicle.find_listicle_violations(page, style=style)
    assert any(p["key"] == "listicle:headline_slots" for p in problems)


def test_a_page_written_in_a_different_style_than_the_run_is_flagged():
    page = _listicle_page("myths")
    problems = listicle.find_listicle_violations(page, style="tested")
    assert any(p["key"] == "listicle:style" for p in problems)


def test_harness_run_takes_a_style_flag_and_refuses_an_unknown_one():
    from harness import cli

    parser = cli.build_parser()
    args = parser.parse_args(["run", "ad.txt", "--cartridges", "listicle", "--style", "myths"])
    assert args.style == "myths"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "ad.txt", "--style", "not-a-style"])


# ---------------------------------------------------------------------------
# Structural gates
# ---------------------------------------------------------------------------

def test_item_count_outside_five_to_seven_fails():
    for n in (4, 8):
        page = _listicle_page()
        page["reasons"] = _items(n)
        page["headline"] = f"{n} Reasons Busy Parents Are Choosing Home Infrared Saunas"
        keys = {p["key"] for p in listicle.find_listicle_violations(page)}
        assert "listicle:item_count" in keys


def test_item_numbering_must_match_position():
    page = _listicle_page()
    page["reasons"][2]["number"] = 9
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:item_numbering:2" in keys


@pytest.mark.parametrize("n_words", [8, 40, 49, 200])
def test_item_body_has_no_word_range(n_words):
    # Cycle 72: no floor (cycle 43's 50) and no ceiling (150) -- one idea,
    # a few sentences, at the winner's density.
    page = _listicle_page()
    page["reasons"][0]["text"] = _words(n_words)
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:item_words:0" not in keys


@pytest.mark.parametrize("text", ["", "   ", None])
def test_an_empty_item_body_still_fails(text):
    page = _listicle_page()
    page["reasons"][0]["text"] = text
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:item_words:0" in keys


def test_an_item_with_no_proof_line_fails():
    page = _listicle_page()
    del page["reasons"][1]["proof"]
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:item_proof:1" in keys


def test_a_proof_line_with_neither_a_claim_id_nor_attribution_fails():
    page = _listicle_page()
    page["reasons"][0]["proof"] = {"text": "It is simply better."}
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:item_proof:0" in keys


def test_an_attributed_customer_proof_line_is_allowed():
    page = _listicle_page()
    page["reasons"][0]["proof"] = {
        "text": 'In the ad, she says: "The room was warm before the kettle had boiled."',
        "attributed_to_customer": True,
    }
    brief = {**AD_BRIEF, "transcript_or_text": "The room was warm before the kettle had boiled."}
    page["hero_quote_id"] = "q1"  # cycle 79: the first screen names the quote it shows
    assert _gate(page, listicle_style="reasons", ad_brief=brief) == []


def test_a_missing_hero_fails():
    page = _listicle_page()
    del page["hero"]
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:hero" in keys


def test_a_missing_audience_fit_block_fails():
    page = _listicle_page()
    del page["audience_fit"]
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:audience_fit" in keys


def test_an_faq_outside_five_to_seven_questions_fails():
    page = _listicle_page()
    page["faq"]["questions"] = page["faq"]["questions"][:3]
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:faq_count" in keys


def test_an_faq_answer_stating_a_number_needs_a_claim_id():
    page = _listicle_page()
    page["faq"]["questions"][0]["answer"] = "It ships in 4 business days once the order clears."
    problems = listicle.find_listicle_violations(page)
    assert any(p["key"] == "listicle:faq_claims:0" for p in problems)
    page["faq"]["questions"][0]["claim_ids"] = ["shipping-policy"]
    assert not any(p["key"].startswith("listicle:faq_claims") for p in listicle.find_listicle_violations(page))


def test_the_closing_recap_must_be_exactly_three_bullets():
    page = _listicle_page()
    page["closing"]["recap"] = page["closing"]["recap"][:2]
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:recap" in keys


def test_urgency_vocabulary_anywhere_fails():
    page = _listicle_page()
    page["dek"] = "Limited time only, so act now before the last chance passes."
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert any(k.startswith("listicle:urgency") for k in keys)


# Cycle 49: no style may assert a physical test that never happened -- this
# harness only checks claims against verified specs.

def test_fake_test_phrase_in_the_headline_fails():
    page = _listicle_page("tested")
    page["headline"] = "We Tested 5 Home Infrared Saunas for 8 Weeks. Here Is What Held Up"
    keys = {p["key"] for p in listicle.find_listicle_violations(page, style="tested")}
    assert "listicle:tested_no_fake_test" in keys


def test_fake_test_phrase_in_the_dek_fails_even_in_a_non_tested_style():
    # The gate applies to every style, not only "tested".
    page = _listicle_page("reasons")
    page["dek"] = "We measured every model ourselves before writing this."
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:tested_no_fake_test" in keys


def test_fake_test_phrase_in_an_item_body_fails():
    page = _listicle_page("reasons")
    page["reasons"][0]["text"] = (
        "In our testing, session by session, this held up better than the studio "
        "membership it replaced, and the specification sheet backs that up on its own."
    )
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:tested_no_fake_test" in keys


def test_fake_test_phrase_in_an_faq_answer_fails():
    page = _listicle_page("reasons")
    page["faq"]["questions"][0]["answer"] = "We ran it for weeks of use before we were satisfied."
    keys = {p["key"] for p in listicle.find_listicle_violations(page)}
    assert "listicle:tested_no_fake_test" in keys


def test_a_claims_check_framed_tested_page_passes_the_fake_test_gate():
    page = _listicle_page("tested")
    assert not any(
        p["key"] == "listicle:tested_no_fake_test"
        for p in listicle.find_listicle_violations(page, style="tested")
    )


def test_a_writer_supplied_renderer_owned_section_fails():
    for key in ("trust_line", "pull_quote", "model_picker", "proof_row", "hsa_line"):
        page = _listicle_page()
        page[key] = [{"text": "4.8 stars from thousands of reviews"}]
        keys = {p["key"] for p in listicle.find_listicle_violations(page)}
        assert f"listicle:renderer_owned:{key}" in keys, key


def test_every_gate_problem_carries_a_stable_repair_key():
    page = _listicle_page()
    del page["hero"]
    del page["audience_fit"]
    page["reasons"][0]["number"] = 9
    problems = listicle.find_listicle_violations(page)
    assert problems
    assert all(p.get("key") for p in problems)
    assert len({p["key"] for p in problems}) == len(problems)


# ---------------------------------------------------------------------------
# Deterministic repair of the headline count (no model call)
# ---------------------------------------------------------------------------

def test_a_spelled_out_headline_count_is_fixed_deterministically():
    page = _listicle_page("mistakes")
    page["headline"] = "Five Mistakes Busy Parents Make When Buying A Home Infrared Sauna"
    assert listicle.fix_headline_number(page) == HEADLINES["mistakes"]


def test_a_stale_headline_count_is_fixed_deterministically():
    page = _listicle_page("reasons", n_items=6)
    page["headline"] = HEADLINES["reasons"]  # still says 5
    assert listicle.fix_headline_number(page).startswith("6 Reasons")


def test_a_stale_tested_headline_count_is_fixed_deterministically():
    # Cycle 49: "tested"'s count sits after "We Checked ", not at the
    # headline's own start -- the deterministic repair has to find it there.
    page = _listicle_page("tested", n_items=7)
    page["headline"] = HEADLINES["tested"]  # still says 5
    fixed = listicle.fix_headline_number(page)
    assert fixed == "We Checked 7 Home Infrared Sauna Claims Busy Parents Keep Hearing. Here Is What Held Up"


def test_the_headline_fix_declines_a_headline_it_cannot_repair():
    page = _listicle_page("reasons")
    page["headline"] = "Why Careful Buyers Are Choosing A Home Infrared Cabin"
    assert listicle.fix_headline_number(page) is None
    # Nothing wrong with the count -> nothing to fix.
    tested = _listicle_page("tested")
    assert listicle.fix_headline_number(tested) is None


def test_the_repair_loop_applies_the_headline_fix_without_a_model_call():
    from harness.repair import apply_deterministic_fixes

    page = _listicle_page("questions", n_items=7)
    page["reasons"] = _items(7)
    page["headline"] = HEADLINES["questions"]  # says 5
    problems = listicle.find_listicle_violations(page, style="questions")
    assert apply_deterministic_fixes(page, problems, set(), cartridge_name="listicle") == 1
    assert listicle.find_listicle_violations(page, style="questions") == []


def test_the_repair_loop_removes_a_redundant_nested_cta_url_without_a_model_call():
    # Cycle 43: a writer that echoes the page's own cta_url into a nested
    # object (closing.cta_url observed on a real run) used to stop the run
    # even though it names the same, single destination.
    from harness.repair import apply_deterministic_fixes

    page = _listicle_page("reasons")
    page["closing"]["cta_url"] = page["cta_url"]
    problems = _gate(page, listicle_style="reasons")
    assert any("second CTA url" in p["issue"] for p in problems)
    assert apply_deterministic_fixes(page, problems, set(), cartridge_name="listicle") == 1
    assert "cta_url" not in page["closing"]
    assert _gate(page, listicle_style="reasons") == []


def test_the_repair_loop_leaves_a_genuinely_different_nested_cta_url_for_the_model():
    from harness.repair import apply_deterministic_fixes

    page = _listicle_page("reasons")
    page["closing"]["cta_url"] = "https://peaksaunas.com/pages/other"
    problems = _gate(page, listicle_style="reasons")
    assert any("second CTA url" in p["issue"] for p in problems)
    assert apply_deterministic_fixes(page, problems, set(), cartridge_name="listicle") == 0
    assert page["closing"]["cta_url"] == "https://peaksaunas.com/pages/other"
    assert any("second CTA url" in p["issue"] for p in _gate(page, listicle_style="reasons"))


def test_the_writer_is_told_the_rules_the_gate_measures():
    rules = " ".join(listicle.writer_rules_lines())
    assert "asset_id" in rules
    assert "no word minimum" in rules
    assert "never pad" in rules
    style_lines = " ".join(line for style in listicle.STYLES for line in listicle.writer_style_lines(style))
    for text in (rules, style_lines):
        assert not re.search(r"\d+\s*[-\u2013]\s*\d+ word body|body is \d+", text)
    assert "exactly one \"cta_url\"" in rules
    assert "claim_ids" in rules


# ---------------------------------------------------------------------------
# Shared claims gate: reused, never forked
# ---------------------------------------------------------------------------

def test_get_cta_text_reads_flat_field():
    assert get_cta_text(_listicle_page(), "listicle") == "See the models"


def test_find_cta_violation_rejects_text_outside_the_allowed_list():
    page = _listicle_page()
    page["cta_text"] = "Buy now and save"
    problems = find_cta_violation(page, "listicle", ALLOWED_CTA_TEXTS)
    assert len(problems) == 1
    assert "cta_text" in problems[0]["path"]


def test_gate_page_json_rejects_a_hype_word_in_an_item():
    page = _listicle_page()
    page["reasons"][0]["text"] = "This is a real game-changer for a busy household. " + _words(100)
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
    page["reasons"][0]["text"] = "I ran into this myself last winter and it changed my whole routine. " + _words(100)
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="first_person",
                       ad_brief={**AD_BRIEF, "speaker_pov": "first_person"})


def test_a_second_cta_url_anywhere_still_fails_the_shared_gate():
    page = _listicle_page()
    page["closing"]["cta_url"] = "https://peaksaunas.com/pages/other"
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, FACTS_PACK, "listicle", financing_lender=None, speaker_pov="third_person", ad_brief=AD_BRIEF)


# ---------------------------------------------------------------------------
# Renderer-owned sections, built from facts_pack alone
# ---------------------------------------------------------------------------

RICH_FACTS_PACK = {
    **FACTS_PACK,
    "reviews_summary": {"text": "Rated 4.8 out of 5 across 1,200 reviews.", "claim_ids": ["reviews-live"]},
    "review_quotes": [{"text": "It was warm before the kettle boiled.", "attribution": "A verified customer"}],
    "model_options": [
        {"name": "Model A", "url": "https://peaksaunas.com/products/a", "price_text": "$7,950",
         "fit": "2-Person · Indoor", "claim_ids": ["price-a"]},
        {"name": "Model B", "url": "https://peaksaunas.com/products/b", "price_text": "$9,750",
         "fit": "3-Person · Indoor", "claim_ids": ["price-b"]},
    ],
    "verified_claims": FACTS_PACK["verified_claims"] + [
        {"id": "reviews-live", "text": "Rated 4.8 out of 5 across 1,200 reviews.", "category": "trust",
         "source": "https://judge.me/reviews/stores/peaksaunas.com"},
        {"id": "hsa-fsa", "text": "These saunas may be eligible for HSA/FSA purchase.", "category": "trust",
         "source": "https://peaksaunas.com/pages/hsa"},
    ],
}


def _render(page, tmp_path, facts_pack=FACTS_PACK, **kwargs):
    kwargs.setdefault("download_assets", False)
    return render_page(
        cartridge_name="listicle",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=facts_pack,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle",
        published="2026-09-18",
        updated="2026-09-18",
        **kwargs,
    )


def test_render_context_is_empty_when_nothing_is_verified():
    context = listicle.render_context(FACTS_PACK)
    assert context["rating_line"] is None
    assert context["pull_quote"] is None
    assert context["hsa_claim"] is None
    assert context["model_options"] == []
    # free shipping IS verified in this pack, so the trust line still renders
    assert [i["text"] for i in context["trust_items"]] == ["Free shipping"]


def test_render_context_reads_only_facts_pack():
    context = listicle.render_context(RICH_FACTS_PACK)
    assert context["rating_line"]["text"] == "Rated 4.8 out of 5 across 1,200 reviews."
    assert context["pull_quote"]["text"] == "It was warm before the kettle boiled."
    assert context["hsa_claim"]["id"] == "hsa-fsa"
    assert len(context["model_options"]) == 2


def test_rating_line_drops_the_fetched_date():
    # Cycle 43: reviews.claim_template (tenants/peak-saunas/tenant.yaml) ends
    # in "(fetched YYYY-MM-DD)" -- useful provenance for REVIEW.md, not
    # something a reader of the trust line or sticky bar needs.
    facts = {
        **FACTS_PACK,
        "reviews_summary": {
            "text": "Rated 4.6 out of 5 across 8,200 reviews on Judge.me (fetched 2026-09-09).",
            "claim_ids": ["reviews-live"],
        },
    }
    line = listicle.rating_line(facts)
    assert line["text"] == "Rated 4.6 out of 5 across 8,200 reviews on Judge.me."
    assert line["claim_ids"] == ["reviews-live"]  # citation is unaffected


def test_rating_line_leaves_a_summary_with_no_fetched_date_alone():
    line = listicle.rating_line(RICH_FACTS_PACK)
    assert line["text"] == "Rated 4.8 out of 5 across 1,200 reviews."


def test_render_omits_the_fetched_date_from_trust_line_and_sticky_bar(tmp_path):
    facts = {
        **RICH_FACTS_PACK,
        "reviews_summary": {
            "text": "Rated 4.6 out of 5 across 8,200 reviews on Judge.me (fetched 2026-09-09).",
            "claim_ids": ["reviews-live"],
        },
    }
    page = _listicle_page()
    page["hero_style"] = "display"   # the stats strip shows the rating
    html = _render(page, tmp_path, facts_pack=facts).read_text()
    assert "fetched" not in html.lower()
    assert "4.6 \u2605" in html and "8,200 reviews" in html


def test_render_listicle_page_has_every_section_when_the_data_is_there(tmp_path):
    # Cycle 79: one look ("open"); the cycle 41-55 band/sticky/micro-CTA
    # assertions went with the five retired looks.
    page = _listicle_page("reasons")
    page["hero_style"] = "story"
    html = _render(page, tmp_path, facts_pack=RICH_FACTS_PACK).read_text()

    assert "Advertisement" not in html          # cycle 55: no label for Peak
    assert "This page is published by PEAK" in html
    assert '"@type": "ItemList"' in html
    assert 'class="pk-lp adv-listicle look-open' in html
    # header: H1 and the one-line byline
    assert 'class="op-h1' in html and 'class="op-by"' in html
    # five items, each with its own proof line
    assert html.count('class="op-num"') == 5
    assert html.count('class="op-proof"') == 5
    # renderer-owned sections
    assert "It was warm before the kettle boiled." in html
    assert "Model A" in html and "$7,950" in html
    assert "may be eligible for HSA/FSA purchase" in html
    # the fit block, FAQ and closing recap
    assert "Made for you if" in html and "Not for you if" in html
    assert html.count('class="op-qa"') == 5
    # one CTA text: after item 3 and in the closing band (plus the value
    # stack when the facts pack verifies one)
    assert html.count("See the models &rarr;<") >= 2

    page_json = json.loads((tmp_path / "listicle" / "page.json").read_text())
    assert page_json == page


def test_render_omits_pull_quote_and_hsa_when_the_facts_pack_has_none(tmp_path):
    html = _render(_listicle_page(), tmp_path).read_text()
    assert "HSA" not in html
    assert 'class="op-pull"' not in html
    assert 'class="op-models"' not in html
    assert html.count("See the models &rarr;<") >= 2


def test_about_author_stays_out_of_the_header_and_moves_to_the_footer(tmp_path):
    # Cycle 43 / 79: the "About the author" paragraph renders above the
    # disclosure/sources footer; the header carries only the one-line byline.
    page = _listicle_page()
    page["hero_style"] = "story"
    html = render_page(
        cartridge_name="listicle",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle",
        published="2026-09-18",
        updated="2026-09-18",
        download_assets=False,
        tenant=TENANT,
    ).read_text()

    header_end = html.index('class="op-c op-items"')
    footer_start = html.index('class="adv-footer"')
    header_html, footer_html = html[:header_end], html[footer_start:]

    assert "About the author." in html
    assert "About the author." not in header_html
    assert "About the author." in footer_html
    assert footer_html.index("About the author.") < footer_html.index("adv-disclosure")
    assert "Austin Laudenslager" in header_html and "min read" in header_html


def test_about_author_is_absent_when_the_tenant_byline_carries_no_about_section(tmp_path):
    html = _render(_listicle_page(), tmp_path).read_text()
    assert "About the author" not in html


def test_render_listicle_page_downloads_the_hero_and_every_item_image(tmp_path):
    """Cycle 79: the hero is the product's cut-out (a local tenant file); the
    five item images are downloaded."""
    downloaded = {}
    image_bytes = _make_image_bytes(600, 600)

    def fake_fetch_url(url):
        downloaded[url] = downloaded.get(url, 0) + 1
        return image_bytes

    html = _render(_listicle_page(), tmp_path, download_assets=True, fetch_url=fake_fetch_url).read_text()
    assert downloaded == {f"https://cdn.shopify.com/fuji-{i}.png": 1 for i in range(2, 7)}
    page_json = json.loads((tmp_path / "listicle" / "page.json").read_text())
    assert page_json["hero"]["asset_id"] == "asset-cutout-fuji"
    assert html.count('loading="eager"') <= 1


def test_rendered_images_carry_their_own_ratio_inline(tmp_path):
    """render_image_slot writes a ratio inline, which survives a storefront
    theme's own `img` rules (item photos sit in a 4:3 frame in the open look)."""
    html = _render(
        _listicle_page(), tmp_path, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 900),
    ).read_text()
    assert html.count('style="aspect-ratio:4 / 3"') == 5
    tags = re.findall(r"<img [^>]*>", html)
    assert tags and not any("adv-img--1x1" in tag for tag in tags)


def test_open_look_css_and_inline_ratios_survive_shopify_export(tmp_path):
    _render(
        _listicle_page(), tmp_path, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 900),
    )
    body, _manifest = build_shopify_body(tmp_path / "listicle")
    css = re.sub(r"\s+", "", body[: body.index("</style>")])
    assert ".op-item{display:grid;" in css
    assert ".op-proof{border-left:3pxsolidvar(--pk-red);" in css
    assert body.count('style="aspect-ratio:4 / 3"') == 5
    assert "<header" not in body and "</header>" not in body


def test_rating_line_is_omitted_below_the_minimum_review_count():
    fp = {"reviews_summary": {"text": "Rated 5 out of 5 across 1 reviews on Judge.me (fetched 2026-09-19).", "claim_ids": ["reviews-live"]}}
    assert listicle.rating_line(fp) is None
    assert listicle.trust_line_items(fp) == []
    fp_ok = {"reviews_summary": {"text": "Rated 4.76 out of 5 across 3,958 reviews on Judge.me (fetched 2026-09-19).", "claim_ids": ["reviews-live"]}}
    line = listicle.rating_line(fp_ok)
    assert line == {"text": "Rated 4.76 out of 5 across 3,958 reviews on Judge.me.", "claim_ids": ["reviews-live"]}
    assert listicle.rating_line(fp, min_reviews=1) is not None
