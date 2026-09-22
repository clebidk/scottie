"""Cycle 56: the comparison cartridge v1.0.0 (harness/comparison.py).

Two axes from verified facts only: a renderer-owned model table (the run's
product plus two more of the tenant's own active models, every cell a
fragment of one verified claim or a dash) and 2-3 category-level
alternatives from the cartridge's allowlist, described with no numbers.

COMPARISON_PAGE is the canned writer output the fake-client driver
(evals/fake_run.py), the end-to-end test here and the disclosure-label
suite share. It is a Fuji page: the Fuji is the featured column and
_model_options' closest-price picks (Everest, Rainier) fill the other two.
"""
import copy
import json

import pytest

from harness import comparison
from harness.claims import ClaimsGateFailure
from harness.ground import LocalFactsSource
from harness.render import render_page
from harness.repair import check_page_gates, count_words
from harness.simplicity import find_above_fold_link_violations
from harness.write import build_initial_write_request
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT, newest_run_dir
from tests.test_render import AD_BRIEF

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
HERO_ID = "asset-drive-1TXFcMw2yIrt6m3kermZK7McHXevDUkIu"
LIFESTYLE_ID = f"asset-{FUJI_SLUG}-3"
MODEL_IDS = ["fuji", "everest", "rainier"]

COMPARISON_PAGE = {
    "axis": "models",
    "headline": "Fuji vs Everest vs Rainier: which home infrared cabin fits couples short on floor space",
    "dek": {
        "text": "Three indoor cabins with published prices, compared line by line so you can see where they match and where they part ways.",
        "claim_ids": [],
    },
    "hero": {"asset_id": HERO_ID},
    "lifestyle": {"asset_id": LIFESTYLE_ID},
    "best_for": [
        {"id": "fuji", "text": "Two people who want a red cedar cabin.", "claim_ids": ["spec-fuji-capacity", "spec-fuji-cabin-material"]},
        {"id": "everest", "text": "Two people who want the same footprint for less.", "claim_ids": ["spec-everest-capacity", "price-everest"]},
        {"id": "rainier", "text": "One person with a standard outlet and a smaller room.", "claim_ids": ["spec-rainier-capacity", "spec-rainier-electrical"]},
    ],
    "numbers_mean": {
        "paragraphs": [
            {
                "text": "Start with the room. The Fuji and the Everest share the same exterior footprint, so a corner that fits one fits the other; the Rainier is narrower and shallower, which matters in a spare bedroom or a home office where every foot of wall counts and a door still has to swing open.",
                "claim_ids": ["gbrain-fuji-dimensions", "gbrain-everest-dimensions", "spec-rainier-dimensions"],
            },
            {
                "text": "Then look at power before anything else. The two larger cabins each need a dedicated circuit of their own, while the Rainier runs on a standard household outlet, so it is the one to look at first if you would rather not book an electrician before the cabin even arrives at your door.",
                "claim_ids": ["gbrain-fuji-power", "gbrain-everest-power", "spec-rainier-electrical"],
            },
            {
                "text": "Price closes the gap between them. Every figure in the table is the published price on each model's own page, and the three sit close together, so the choice usually comes down to seats, wood and wiring rather than a large jump in cost from one cabin to the next.",
                "claim_ids": ["price-fuji", "price-everest", "price-rainier"],
            },
        ]
    },
    "alternatives": [
        {
            "id": "studio membership",
            "summary": "A studio membership suits someone who wants to try the habit before they commit to a cabin at home.",
            "similarities": [
                "Both give you regular, quiet heat sessions as part of a weekly routine.",
                "Both leave the upkeep of the equipment to someone who knows it well.",
            ],
            "differences": [
                {
                    "theirs": "A studio sets the hours, and every visit means a drive there and back.",
                    "ours": {"text": "A home cabin is ready whenever you are, and shipping is free.", "claim_ids": ["shipping-policy"]},
                },
                {
                    "theirs": "A studio fee keeps going for as long as you keep the habit.",
                    "ours": {"text": "A home cabin is a one-time purchase at a published price.", "claim_ids": ["price-fuji"]},
                },
            ],
        },
        {
            "id": "traditional sauna",
            "summary": "A traditional sauna heats the air around you and suits people who love a very hot room and a longer warm-up.",
            "similarities": [
                "Both are built as wooden cabins meant to last for years in a home.",
                "Both reward a steady routine more than an occasional long session.",
            ],
            "differences": [
                {
                    "theirs": "A traditional sauna usually needs a dedicated heater and a longer warm-up before each session.",
                    "ours": {"text": "Each cabin here uses full-spectrum infrared heater panels.", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
                },
                {
                    "theirs": "Most traditional rooms leave light therapy out of the design entirely.",
                    "ours": {"text": "Red light therapy is included standard on all three models.", "claim_ids": ["gbrain-allowlist-red-light"]},
                },
            ],
        },
        {
            "id": "portable blanket",
            "summary": "A portable blanket packs away in a closet and suits someone who wants warmth without giving up any floor space at all.",
            "similarities": [
                "Both use infrared warmth rather than heating the whole room around you.",
                "Both fit a routine at home, with no commute and no booking.",
            ],
            "differences": [
                {
                    "theirs": "A blanket wraps you lying down, so reading or sitting upright is awkward.",
                    "ours": {"text": "A cabin seats you upright on a real bench, with room to move.", "claim_ids": ["gbrain-fuji-dimensions"]},
                },
                {
                    "theirs": "A blanket is set up and packed away again around each session.",
                    "ours": {"text": "A cabin stays assembled and ready once it is in place.", "claim_ids": []},
                },
            ],
        },
    ],
    "who_for": [
        {"id": "fuji", "text": "The Fuji is for two people who want a red cedar cabin inside and out and have a dedicated circuit, or can add one, near the spot they have in mind.", "claim_ids": ["spec-fuji-capacity", "gbrain-fuji-wood", "gbrain-fuji-power"]},
        {"id": "everest", "text": "The Everest is for two people who like the Fuji's size but would rather have a hemlock cabin, and it needs the same kind of dedicated circuit.", "claim_ids": ["spec-everest-capacity", "spec-everest-cabin-material", "gbrain-everest-power"]},
        {"id": "rainier", "text": "The Rainier is for one person, a smaller room, and a standard outlet with no new wiring to arrange before delivery.", "claim_ids": ["spec-rainier-capacity", "spec-rainier-electrical"]},
    ],
    "faq": {
        "questions": [
            {"question": "Do all three models include red light therapy?", "answer": "Yes. Red light therapy is included standard on every model compared here, so it is not an extra you add at checkout.", "claim_ids": ["gbrain-allowlist-red-light"]},
            {"question": "Which one can plug into an ordinary outlet?", "answer": "The Rainier runs on a standard household outlet. The Fuji and the Everest each need a dedicated circuit, which is worth checking with an electrician before you order.", "claim_ids": ["spec-rainier-electrical", "gbrain-fuji-power", "gbrain-everest-power"]},
            {"question": "Are the prices in the table the real prices?", "answer": "They are the prices published on each model's own product page at the time this page was built. Check the product page before you order, since a price can change.", "claim_ids": ["price-fuji", "price-everest", "price-rainier"]},
            {"question": "Is shipping extra?", "answer": "No. Shipping is free on all orders, and in-stock cabins ship after a short handling time.", "claim_ids": ["shipping-policy"]},
            {"question": "Does the cabin wood differ between the three?", "answer": "Yes. The Fuji is built from red cedar inside and out, while the Everest and the Rainier use hemlock, so the look and scent of the room differ even where the size does not.", "claim_ids": ["gbrain-fuji-wood", "spec-everest-cabin-material", "spec-rainier-cabin-material"]},
            {"question": "What happens if I change my mind after delivery?", "answer": "Returns require the cabin to be taken apart and repacked in its original crate, and a restocking fee applies, so measure the room and check the power first.", "claim_ids": ["returns-policy"]},
        ]
    },
    "closing": {
        "headline": "Pick by room, power and seats",
        "recap": [
            {"text": "Two of the three share one footprint; the third fits a smaller room.", "claim_ids": ["gbrain-fuji-dimensions", "spec-rainier-dimensions"]},
            {"text": "Check your outlet first: only one runs on a standard household outlet.", "claim_ids": ["spec-rainier-electrical"]},
            {"text": "Every price shown is the one published on the model's own page.", "claim_ids": ["price-fuji"]},
        ],
    },
    "cta_text": "See the models",
    "cta_url": "/collections/all",
}


def _facts_pack_with_comparison(**kwargs):
    return LocalFactsSource(TENANT.claims_dir).facts_for(FUJI_SLUG, AD_BRIEF, include_comparison=True, **kwargs)


def _page():
    return copy.deepcopy(COMPARISON_PAGE)


def _gate(page, facts_pack=None):
    facts_pack = facts_pack or _facts_pack_with_comparison()
    return check_page_gates(
        page, facts_pack, "comparison",
        financing_lender="Bread Pay", speaker_pov="first_person",
        word_range=(800, 1200), allowed_cta_texts=["See the models", "See pricing"],
        ad_brief=AD_BRIEF, tenant=TENANT,
    )


def _keys(problems):
    return {p.get("key") for p in problems}


# ---------------------------------------------------------------------------
# Grounding: the model table from verified claims
# ---------------------------------------------------------------------------

def test_the_featured_column_is_the_runs_product_and_the_others_are_active_own_models():
    pack = _facts_pack_with_comparison()
    models = pack["comparison"]["models"]
    assert [m["id"] for m in models] == MODEL_IDS
    assert models[0]["featured"] and not any(m["featured"] for m in models[1:])
    names = {m["name"] for m in models}
    assert "Crown" not in names  # retired model: inactive, and a banned name


def test_every_filled_cell_is_a_fragment_of_its_own_verified_claim():
    pack = _facts_pack_with_comparison()
    verified = {c["id"]: c["text"] for c in pack["verified_claims"]}
    filled = 0
    for model in pack["comparison"]["models"]:
        for key, cell in model["cells"].items():
            if not cell["claim_ids"]:
                assert cell["text"] == comparison.MISSING
                continue
            filled += 1
            for cid in cell["claim_ids"]:
                assert cid in verified, cid
                if key != comparison.WARRANTY_ROW:
                    assert cell["text"].lower() in verified[cid].lower(), (key, cell, verified[cid])
    assert filled >= 25


def test_the_price_cell_never_shows_a_compare_at_price():
    pack = _facts_pack_with_comparison()
    fuji = pack["comparison"]["models"][0]
    assert fuji["cells"]["price"] == {"text": "$8,250", "claim_ids": ["price-fuji"]}


def test_the_warranty_cell_is_the_fixed_spec_value():
    pack = _facts_pack_with_comparison()
    for model in pack["comparison"]["models"]:
        assert model["cells"]["warranty"]["text"] == "Limited lifetime warranty (terms by component)"
        assert model["cells"]["warranty"]["claim_ids"] == ["warranty-terms"]


def test_a_page_sourced_claim_fills_the_controls_row_for_every_model():
    pdp = [
        {"id": f"pdp-{m}-app-control", "text": "Adjust temperature and session length in the brand app (iOS & Android).",
         "category": "spec", "source": "https://example.com/x"}
        for m in MODEL_IDS
    ]
    pack = _facts_pack_with_comparison(pdp_claims=pdp)
    for model in pack["comparison"]["models"]:
        assert model["cells"]["controls"]["text"] == "App (iOS & Android)"
    assert {"pdp-everest-app-control", "pdp-rainier-app-control"} <= {c["id"] for c in pack["verified_claims"]}


def test_a_live_price_claim_wins_over_the_static_one():
    live = {"text": "The PEAK Everest is priced at $7,777.", "id": "price-everest", "category": "price",
            "source": "https://example.com/everest"}
    slug = next(s for s, p in json.loads((TENANT.claims_dir / "products.json").read_text())["products"].items()
                if p["name"] == "Everest")
    pack = _facts_pack_with_comparison(live_price_claims={slug: live})
    assert pack["comparison"]["models"][1]["cells"]["price"]["text"] == "$7,777"


def test_no_comparison_block_without_the_flag():
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI_SLUG, AD_BRIEF)
    assert "comparison" not in pack


def test_the_other_models_backing_claims_join_the_citable_universe():
    pack = _facts_pack_with_comparison()
    ids = {c["id"] for c in pack["verified_claims"]}
    assert {"price-everest", "spec-rainier-electrical", "gbrain-everest-dimensions"} <= ids


# ---------------------------------------------------------------------------
# Renderer-owned table rows
# ---------------------------------------------------------------------------

DECISION_ROWS = ["capacity", "dimensions", "power", "red_light", "max_temperature", "cabin_material", "price", "warranty"]


def test_the_table_is_the_eight_decision_rows_in_order():
    # Cycle 60: at most 8 rows, the ones a buyer decides on, in this order.
    # A row no model has a verified value for is still dropped, never drawn
    # as a line of dashes.
    ctx = comparison.render_context(_facts_pack_with_comparison(), _page())
    keys = [r["key"] for r in ctx["rows"]]
    assert len(keys) <= 8
    assert keys == [k for k in DECISION_ROWS if k in keys]
    assert keys[-2:] == ["price", "warranty"]
    assert {"capacity", "power", "price"} <= set(keys)
    assert [row["key"] for row in comparison.row_catalog()["fixed_rows"]] == DECISION_ROWS


def test_a_writer_extra_rows_pick_from_an_old_page_json_never_adds_a_row():
    page = _page()
    page["extra_rows"] = [{"id": "audio"}, {"id": "heaters"}, {"id": "made_up_row"}]
    ctx = comparison.render_context(_facts_pack_with_comparison(), page)
    keys = [r["key"] for r in ctx["rows"]]
    assert not {"audio", "heaters", "made_up_row"} & set(keys)
    assert len(keys) <= 8


def test_row_labels_are_sentence_case():
    for row in comparison.render_context(_facts_pack_with_comparison(), _page())["rows"]:
        label = row["label"]
        assert label[:1].isupper() and label[1:] == label[1:].lower() or "W × D × H" in label, label


def test_each_column_head_carries_its_models_price():
    ctx = comparison.render_context(_facts_pack_with_comparison(), _page())
    assert [m.get("price") for m in ctx["models"]][0] == "$8,250"


def test_the_table_backstop_catches_an_unverified_cell_and_a_banned_column():
    pack = _facts_pack_with_comparison()
    ctx = comparison.render_context(pack, _page())
    assert comparison.find_table_violations(ctx, pack) == []
    ctx["rows"][0]["cells"][1] = {"text": "9-Person", "claim_ids": ["spec-nowhere"]}
    ctx["models"][2] = dict(ctx["models"][2], name="Crown")
    assert _keys(comparison.find_table_violations(ctx, pack)) == {"comparison:table_row:capacity", "comparison:banned_name"}


# ---------------------------------------------------------------------------
# Gates (stable comparison:* keys)
# ---------------------------------------------------------------------------

def test_the_cartridge_states_one_word_range_and_it_is_800_to_1200():
    # parse_word_range takes the FIRST "N-M words" in cartridge.md; an
    # earlier "2-5 words" (the audience slot) once made it 2-5.
    from harness.write import parse_word_range

    text = TENANT.render((REPO_ROOT / "cartridges" / "comparison" / "cartridge.md").read_text())
    assert parse_word_range(text) == (800, 1200)


def test_the_canned_page_passes_every_gate_and_the_word_range():
    assert 800 <= count_words(COMPARISON_PAGE) <= 1200, count_words(COMPARISON_PAGE)
    assert _gate(_page()) == []


def test_a_models_headline_must_name_the_three_columns_in_order():
    page = _page()
    page["headline"] = "Everest vs Fuji vs Rainier: Which Home Infrared Cabin Fits Couples Short on Floor Space"
    assert "comparison:headline_formula" in _keys(_gate(page))


def test_an_alternatives_headline_names_one_of_the_pages_alternatives():
    page = _page()
    page["axis"] = "alternatives"
    page["headline"] = "Home infrared cabins vs studio memberships: what busy commuters should compare"
    assert _gate(page) == []
    page["headline"] = "Home Infrared Cabins vs Far-Infrared-Only Cabins: What Busy Commuters Should Compare"
    assert "comparison:headline_formula" in _keys(_gate(page))


def test_the_headline_audience_is_never_generic_and_the_category_never_a_model():
    page = _page()
    page["headline"] = "Fuji vs Everest vs Rainier: Which Home Infrared Cabin Fits Buyers"
    assert "comparison:headline_slots" in _keys(_gate(page))
    page["axis"] = "alternatives"
    page["headline"] = "Rainier Cabins vs Studio Memberships: What Busy Commuters Should Compare"
    assert "comparison:headline_slots" in _keys(_gate(page))


def test_an_unknown_axis_fails():
    page = _page()
    page["axis"] = "brands"
    assert "comparison:axis" in _keys(_gate(page))


@pytest.mark.parametrize("field,value", [
    ("summary", "A studio membership costs about $200 a month."),
    ("similarity", "Both offer sessions 7 days a week."),
    ("theirs", "A studio fee adds up to 2,400 a year."),
])
def test_any_number_in_an_alternatives_description_fails(field, value):
    page = _page()
    alt = page["alternatives"][0]
    if field == "summary":
        alt["summary"] = value
    elif field == "similarity":
        alt["similarities"][0] = value
    else:
        alt["differences"][0]["theirs"] = value
    problems = [p for p in _gate(page) if (p.get("key") or "").startswith("comparison:alternative_digits")]
    assert len(problems) == 1
    assert "plain words only" in problems[0]["issue"]


def test_a_trigger_word_in_an_alternatives_description_fails():
    page = _page()
    page["alternatives"][1]["similarities"][0] = "Both are proven to help you relax."
    assert any((k or "").startswith("comparison:alternative_claims") for k in _keys(_gate(page)))


def test_alternatives_come_from_the_allowlist_two_to_three_of_them():
    page = _page()
    page["alternatives"][1]["id"] = "a rival brand's cabin"
    assert "comparison:alternatives_allowlist:1" in _keys(_gate(page))
    page = _page()
    page["alternatives"] = page["alternatives"][:1]
    assert "comparison:alternatives_count" in _keys(_gate(page))


def test_the_tenants_side_of_a_difference_is_claims_gated():
    page = _page()
    page["alternatives"][0]["differences"][0]["ours"] = {"text": "A home cabin pays for itself in 28 months.", "claim_ids": []}
    problems = _gate(page)
    assert any(p["path"] == "$.alternatives[0].differences[0].ours" and "claim_id" in p["issue"] for p in problems)


def test_best_for_and_who_for_carry_one_line_per_column_in_order():
    page = _page()
    page["best_for"] = list(reversed(page["best_for"]))
    page["who_for"] = page["who_for"][:2]
    assert {"comparison:best_for", "comparison:who_for"} <= _keys(_gate(page))


def test_a_best_for_number_without_a_claim_id_fails():
    page = _page()
    page["best_for"][0] = {"id": "fuji", "text": "Couples with a 20A circuit.", "claim_ids": []}
    assert any(p["path"] == "$.best_for[0]" for p in _gate(page))


def test_an_faq_number_needs_a_claim_id():
    page = _page()
    page["faq"]["questions"][0] = {"question": "How hot?", "answer": "It reaches 150 degrees.", "claim_ids": []}
    assert "comparison:faq_claims:0" in _keys(_gate(page))


def test_a_competitor_name_fails_the_existing_forbidden_vocab_gate():
    page = _page()
    page["alternatives"][0]["summary"] = "Some buyers look at Sunlighten first."
    assert any("forbidden term" in p["issue"] for p in _gate(page))


def test_renderer_owned_sections_are_never_written():
    page = _page()
    page["comparison_table"] = {"rows": []}
    page["closing"]["warranty_line"] = {"text": "Limited lifetime warranty.", "claim_ids": ["warranty-terms"]}
    keys = _keys(_gate(page))
    assert {"comparison:renderer_owned:comparison_table", "comparison:renderer_owned:warranty_line"} <= keys


def test_images_are_distinct_and_never_a_column_image():
    page = _page()
    column_image = _facts_pack_with_comparison()["comparison"]["models"][0]["image"]["id"]
    page["hero"] = {"asset_id": column_image}
    page["lifestyle"] = {"asset_id": column_image}
    keys = _keys(_gate(page))
    assert {"comparison:images:hero", "comparison:images:lifestyle"} <= keys


def test_a_title_case_headline_fails_and_sentence_case_passes():
    # Cycle 60: sentence case -- the first word and proper names only.
    page = _page()
    page["headline"] = "Fuji vs Everest vs Rainier: Which Home Infrared Cabin Fits Couples Short on Floor Space"
    assert "comparison:headline_case" in _keys(_gate(page))
    page["axis"] = "alternatives"
    page["headline"] = "Infrared Sauna vs Studio Membership: What At-Home Wellness Buyers Should Compare"
    assert "comparison:headline_case" in _keys(_gate(page))
    page["headline"] = "Infrared sauna vs studio membership: what at-home wellness buyers should compare"
    assert "comparison:headline_case" not in _keys(_gate(page))


def test_the_dek_and_best_for_lines_stay_short():
    page = _page()
    page["dek"]["text"] = " ".join(["word"] * 23) + "."
    page["best_for"][1]["text"] = "Two people who want the same footprint for less and do not mind a little extra wait."
    keys = _keys(_gate(page))
    assert "comparison:dek_length" in keys
    assert "comparison:best_for_length:1" in keys
    assert not {"comparison:best_for_length:0", "comparison:best_for_length:2"} & keys


def test_extra_rows_are_no_longer_a_writer_field():
    # the table's rows are fixed; an old page.json that still carries the
    # key is neither required nor rejected
    page = _page()
    assert "extra_rows" not in page
    assert _gate(page) == []
    page["extra_rows"] = [{"id": "audio"}]
    assert not any(k.startswith("comparison:extra_rows") for k in _keys(_gate(page)))


def test_the_display_headline_is_sentence_case_for_a_title_case_page():
    fp = _facts_pack_with_comparison()
    page = _page()
    page["headline"] = "Fuji vs Everest vs Rainier: Which Home Infrared Cabin Fits Couples Short on Floor Space"
    ctx = comparison.render_context(fp, page, tenant_name="PEAK")
    assert ctx["headline"] == "Fuji vs Everest vs Rainier: which home infrared cabin fits couples short on floor space"
    page["headline"] = "Infrared Sauna vs Studio Membership: What At-Home PEAK Buyers Should Compare"
    assert comparison.render_context(fp, page, tenant_name="PEAK")["headline"] == (
        "Infrared sauna vs studio membership: what at-home PEAK buyers should compare"
    )
    # a headline already in sentence case is left exactly as written,
    # proper names included
    page["headline"] = "Red cedar cabins vs studio memberships: what Canadian commuters should compare"
    assert comparison.render_context(fp, page, tenant_name="PEAK")["headline"] == page["headline"]


def test_counts_numbers_mean_faq_and_recap():
    page = _page()
    page["numbers_mean"]["paragraphs"] = page["numbers_mean"]["paragraphs"][:2]
    page["faq"]["questions"] = page["faq"]["questions"][:4]
    page["closing"]["recap"] = page["closing"]["recap"][:2]
    assert {"comparison:numbers_mean", "comparison:faq_count", "comparison:recap"} <= _keys(_gate(page))


def test_the_header_carries_one_link_above_the_fold():
    assert find_above_fold_link_violations(_page(), "comparison") == []
    page = _page()
    page["hero"]["url"] = "/pages/somewhere-else"
    assert find_above_fold_link_violations(page, "comparison")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def test_the_writer_is_told_the_three_models_and_both_formulas():
    _schema, kwargs = build_initial_write_request(
        cartridge_name="comparison", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
        facts_pack=_facts_pack_with_comparison(), model="m", tenant=TENANT,
    )
    tail = kwargs["system"][-1]["text"]
    assert '"Fuji vs Everest vs Rainier: which <category> fits <audience>"' in tail
    assert "<category> vs <alternative>: what <audience> should compare" in tail
    assert "NO digits" in tail
    for alt in comparison.allowed_alternatives():
        assert alt in tail
    # cycle 60: the copy rules that keep the page calm
    assert "sentence case" in tail
    assert "22 words" in tail and "14 words" in tail
    assert "extra_rows" not in tail


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _render(tmp_path, page=None, facts_pack=None, **kwargs):
    return render_page(
        cartridge_name="comparison",
        page=page or _page(),
        ad_brief=AD_BRIEF,
        facts_pack=facts_pack or _facts_pack_with_comparison(),
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "comparison",
        published="2026-09-22",
        updated="2026-09-22",
        tenant=TENANT,
        download_assets=False,
        **kwargs,
    ).read_text()


def test_the_page_renders_the_table_alternatives_and_fixed_lines(tmp_path):
    html = _render(tmp_path)
    assert '"@type": "FAQPage"' in html
    assert "$8,250" in html and "$7,950" in html and "$7,250" in html
    assert "120V/15A, standard outlet" in html
    assert 'class="cmp-rowhead" scope="row"' in html
    assert "Studio membership" in html and "Traditional sauna" in html
    assert "Limited lifetime warranty; full terms by component are published on the warranty page." in html
    assert "Financing is available through Bread Pay at checkout." in html
    assert html.count(">See the models<") == 3  # header, closing, sticky bar -- one offer
    assert 'class="adv-badge"' not in html


def _template_css():
    text = (REPO_ROOT / "cartridges" / "comparison" / "template.html").read_text()
    return text.split("<style>", 1)[1].split("</style>", 1)[0]


def _table_html(html):
    return html.split('<table class="cmp-table"', 1)[1].split("</table>", 1)[0]


def test_the_page_is_sentence_case_with_no_uppercase_micro_labels(tmp_path):
    # Cycle 60: no text-transform:uppercase anywhere in the page's own CSS
    # (headlines, buttons and labels included), and none of v1's micro-label
    # classes are drawn.
    css = _template_css()
    assert "uppercase" not in css
    assert ".adv-case-upper" not in css
    html = _render(tmp_path)
    for cls in ("cmp-h4", "cmp-diff-k", "cmp-flag", "cmp-trust-item", "cmp-diff-side--ours"):
        assert f'class="{cls}' not in html and f" {cls}" not in html, cls
    h1 = html.split('<h1 class="cmp-h1">', 1)[1].split("</h1>", 1)[0]
    assert h1 == COMPARISON_PAGE["headline"]


def test_best_for_sits_outside_the_table(tmp_path):
    html = _render(tmp_path)
    table = _table_html(html)
    assert "Best for" not in table
    for entry in COMPARISON_PAGE["best_for"]:
        assert entry["text"] not in table
        assert entry["text"] in html


def test_each_column_head_has_name_price_and_link_and_the_pick_label(tmp_path):
    html = _render(tmp_path)
    head = _table_html(html).split("</thead>", 1)[0]
    assert head.count('class="cmp-price"') == 3
    assert "$8,250" in head
    assert head.count(">View model<") == 3
    assert head.count(">Our pick<") == 1


def test_the_trust_line_is_one_plain_line_under_the_cta(tmp_path):
    html = _render(tmp_path)
    hero = html.split("</header>", 1)[0]
    assert "<ul" not in hero
    cta_then_line = hero.split('class="cmp-btn"', 1)[1]
    assert 'class="cmp-reassure"' in cta_then_line


def test_alternatives_are_a_two_column_row_list(tmp_path):
    html = _render(tmp_path)
    alts = html.split('class="cmp-alts"', 1)[1]
    assert alts.count('class="cmp-vs"') == len(COMPARISON_PAGE["alternatives"])
    assert ">Studio membership<" in alts and ">PEAK at home<" in alts
    for diff in COMPARISON_PAGE["alternatives"][0]["differences"]:
        assert diff["theirs"] in alts and diff["ours"]["text"] in alts


def test_phones_get_one_stacked_card_per_model(tmp_path):
    html = _render(tmp_path)
    assert html.count('<article class="cmp-card') == 3
    css = _template_css()
    assert "@media (max-width:640px)" in css
    mobile = css.split("@media (max-width:640px)", 1)[1]
    assert ".cmp .cmp-table-wrap{display:none}" in mobile and ".cmp .cmp-cards{display:block}" in mobile


def test_one_content_grid_with_a_left_aligned_text_column():
    css = _template_css()
    assert "--cmp-outer:1200px" in css and "--cmp-text:680px" in css
    text_rule = css.split(".cmp .cmp-text{", 1)[1].split("}", 1)[0]
    assert "margin" not in text_rule  # shares the headings' left edge, never centred


def test_images_never_sit_on_a_white_tile():
    # a studio cut-out's own white ground is multiplied into the panel it
    # sits on, so it never reads as a pasted white rectangle
    css = _template_css()
    rule = css.split(".cmp .adv-img--contain{", 1)[1].split("}", 1)[0]
    assert "mix-blend-mode:multiply" in rule and "background:transparent" in rule
    assert "#fff" not in css.lower() and "white" not in css.lower()


def test_the_sources_list_covers_the_renderer_built_table(tmp_path):
    html = _render(tmp_path)
    sources = html.split("<h3>Sources</h3>", 1)[1]
    # each model's facts cite that model's own page, labelled with its name --
    # an internal-only source never falls back to the featured model's page
    assert "PEAK – Everest product page" in sources
    assert "PEAK – Rainier product page" in sources


def test_the_eyebrow_renders_only_when_the_tenant_sets_a_disclosure_label(tmp_path, monkeypatch):
    assert '<p class="cmp-eyebrow">' not in _render(tmp_path)
    monkeypatch.setitem(TENANT.config, "disclosure_label", "Paid feature")
    assert '<p class="cmp-eyebrow">Paid feature</p>' in _render(tmp_path)


def test_a_page_with_no_hero_is_stopped_at_render(tmp_path):
    page = _page()
    page.pop("hero")
    with pytest.raises(ClaimsGateFailure):
        _render(tmp_path, page=page)


def test_column_images_are_downloaded_with_their_own_model_alt(tmp_path, monkeypatch):
    from harness import render as render_mod

    fetched = []

    def fake_download(asset, dest_dir, **kwargs):
        fetched.append((asset["id"], asset["alt"]))
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{len(fetched)}-800.jpg"
        (dest_dir / name).write_bytes(b"fake")
        return {"path": dest_dir / name, "width": 800, "height": 600,
                "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}], "cutout": None}

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    run_dir = tmp_path / "20260922-000000-cmp-test-aaaa"
    run_dir.mkdir()
    html = render_page(
        cartridge_name="comparison", page=_page(), ad_brief=AD_BRIEF, facts_pack=_facts_pack_with_comparison(),
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "comparison",
        published="2026-09-22", updated="2026-09-22", tenant=TENANT, download_assets=True,
    ).read_text()
    alts = dict(fetched)
    assert any(alt.startswith("Peak Rainier") for alt in alts.values())
    assert len(fetched) == 5  # hero + lifestyle + three columns
    assert html.count('class="cmp-thumb"') == 3


def test_the_page_survives_the_shopify_body_export(tmp_path):
    from harness.page_body import build_shopify_body

    _render(tmp_path)
    body, _manifest = build_shopify_body(tmp_path / "comparison")
    assert body.startswith("<style>")
    assert ".cmp .cmp-rowhead" in body
    for tag in ("<header", "</header>", "<footer", "</footer>"):
        assert tag not in body


def test_the_page_uses_brand_tokens_only():
    import re

    css = (REPO_ROOT / "cartridges" / "comparison" / "template.html").read_text().split("<style>", 1)[1].split("</style>", 1)[0]
    assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= {"#f4f5f6"}
    assert "--pk-accent:var(--ps-accent,var(--adv-accent))" in css
    assert "--pk-radius:var(--ps-radius-card" in css and "--pk-serif:var(--ps-serif" in css


# ---------------------------------------------------------------------------
# End to end: harness run --cartridges comparison with the fake client.
# ---------------------------------------------------------------------------

def test_run_comparison_cartridge_end_to_end(monkeypatch):
    from harness import cli
    from tests.test_cli_run import _base_args, _patch_network

    responses = [
        json_response(dict(AD_BRIEF, source_file="founder-warranty-demo.txt", audience="")),
        json_response(COMPARISON_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args(input=str(TENANT.fixtures_dir / "founder-warranty-demo.txt"), cartridges="comparison"))
    assert exit_code == 0

    run_dir = newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert [m["id"] for m in facts_pack["comparison"]["models"]] == MODEL_IDS
    html = (run_dir / "comparison" / "index.html").read_text()
    assert "Fuji vs Everest vs Rainier" in html
