"""Winner skeleton library: 10 page maps + 17 headline swipes + selection."""
import json

import pytest

from tests.support import REPO_ROOT
from harness import skeletons
from harness.budget import Budget
from harness.log import RunLog
from harness.write import write_page
from tests.conftest import FakeClient, json_response
from tests.test_render import AD_BRIEF, FACTS_PACK


SKELETONS_DIR = REPO_ROOT / "cartridges" / "listicle" / "skeletons"
HEADLINES_DIR = SKELETONS_DIR / "headlines"


def test_index_lists_exactly_ten_skeletons():
    index = json.loads((SKELETONS_DIR / "index.json").read_text())
    assert index["count"] == 10
    assert len(index["skeletons"]) == 10
    assert index["default_id"] == "classic-n-reasons"
    assert skeletons.list_skeleton_ids() == [row["id"] for row in index["skeletons"]]


def test_headline_index_lists_exactly_seventeen():
    index = json.loads((HEADLINES_DIR / "index.json").read_text())
    assert index["count"] == 17
    assert len(index["headlines"]) == 17
    assert skeletons.list_headline_ids() == [row["id"] for row in index["headlines"]]


def test_every_skeleton_loads_and_passes_shape_check():
    for sid in skeletons.list_skeleton_ids():
        sk = skeletons.load_skeleton(sid)
        assert sk["id"] == sid
        assert skeletons.validate_skeleton_shape(sk) == []


def test_every_headline_loads_and_passes_shape_check():
    for hid in skeletons.list_headline_ids():
        hl = skeletons.load_headline(hid)
        assert hl["id"] == hid
        assert "[" in hl["template"] and "]" in hl["template"]
        assert skeletons.validate_headline_shape(hl) == []


def test_library_groups_match_the_brief():
    ids = set(skeletons.list_skeleton_ids())
    assert {"hormozi-value-stack", "hormozi-mistakes"} <= ids
    assert "native-article-comments" in ids
    assert "simplified-pdp" in ids
    classic = {
        "classic-n-reasons", "hidden-costs", "switcher-reasons",
        "myth-bust", "buyers-checklist", "day-in-the-life",
    }
    assert classic <= ids
    assert skeletons.load_skeleton("simplified-pdp")["target_cartridge"] == "product-page"
    assert skeletons.load_skeleton("native-article-comments")["compliance"]["risk"] == "high"


def test_headline_swipe_templates_match_source_formulas():
    expected = {
        "most-dont-work": "5 Reasons Why Most [Product] don't work for [PROBLEM] (and how [BRAND] is different)",
        "game-changer-for-avatar": "6 Reasons Why [PRODUCT] is a Game-Changer for [AVATAR]",
        "still-problem-after-trying": "5 Reasons Why You're Still [PROBLEM], even after trying [common solutions]",
        "social-proof-switched": "8 Reasons Why 100,000+ [AVATAR] Switched to This [YOUR PRODUCT]",
    }
    for hid, tmpl in expected.items():
        assert skeletons.load_headline(hid)["template"] == tmpl


def test_select_skeleton_respects_explicit_id():
    sk = skeletons.select_skeleton({"angle": "features"}, skeleton_id="hormozi-value-stack")
    assert sk["id"] == "hormozi-value-stack"


def test_select_headline_respects_explicit_id():
    hl = skeletons.select_headline({"angle": "features"}, headline_id="most-dont-work")
    assert hl["id"] == "most-dont-work"


def test_select_skeleton_matches_hidden_costs_angle():
    sk = skeletons.select_skeleton(
        {"angle": "hidden costs of spa memberships", "hook": "stop paying for unused visits"},
        for_cartridge="listicle",
    )
    assert sk["id"] == "hidden-costs"


def test_select_headline_nudges_switch_angle():
    hl = skeletons.select_headline(
        {"angle": "why people switch from spa heat", "hook": "switching"},
        skeleton_id="switcher-reasons",
    )
    assert hl["id"] in {
        "everyones-switching", "avatar-started-switching", "social-proof-switched",
    }


def test_select_skeleton_defaults_to_classic_n_reasons_for_listicle():
    sk = skeletons.select_skeleton({"angle": "something unrelated xyz"}, for_cartridge="listicle")
    assert sk["id"] == "classic-n-reasons"


def test_select_skeleton_defaults_to_simplified_pdp_for_product_page():
    sk = skeletons.select_skeleton({"angle": "retargeting"}, for_cartridge="product-page")
    assert sk["id"] == "simplified-pdp"


def test_select_skeleton_rejects_cross_cartridge_explicit_id():
    with pytest.raises(ValueError, match="targets"):
        skeletons.select_skeleton(
            {"angle": "features"},
            skeleton_id="simplified-pdp",
            for_cartridge="listicle",
        )


def test_authority_headline_skipped_unless_ad_names_authority():
    hl = skeletons.select_headline({"angle": "features of the mini"}, skeleton_id="classic-n-reasons")
    assert hl["id"] != "authority-loves"


def test_skeleton_and_headline_for_writer_are_compact():
    sk = skeletons.skeleton_for_writer(skeletons.load_skeleton("classic-n-reasons"))
    hl = skeletons.headline_for_writer(skeletons.load_headline("everyones-switching"))
    assert "sources" not in sk
    assert sk["peak_saunas"]["is_default"] is True
    assert hl["template"].startswith("7 Reasons")
    assert "peak_saunas" in hl


def test_write_page_includes_skeleton_and_headline_and_keeps_them_on_repair(tmp_path):
    listicle_page = {
        "headline": "5 Reasons Busy Parents Prefer Home Heat",
        "dek": "A short dek.",
        "reasons": [
            {
                "number": i,
                "heading": f"Reason {i}",
                "text": "Short body for this reason with enough plain words here.",
                "image": {"asset_id": "asset-1"},
                "claim_ids": [],
            }
            for i in range(1, 6)
        ],
        "cta_text": "See the models",
        "cta_url": "https://peaksaunas.com/collections/all",
        "closing": {
            "paragraphs": [{"text": "Peak Saunas makes the switch simple for a busy household."}],
            "warranty_line": {
                "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
                "claim_ids": [],
            },
            "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
        },
    }
    client = FakeClient([json_response(listicle_page), json_response(listicle_page)])
    budget = Budget()
    log = RunLog("test-skel", tmp_path / "run.log")

    write_page(
        cartridge_name="listicle",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="fake",
        budget=budget,
        log=log,
        skeleton_id="hormozi-mistakes",
        headline_id="still-problem-after-trying",
    )
    first_user = client.messages.calls[0]["messages"][0]["content"][1]["text"]
    assert '"skeleton"' in first_user
    assert "hormozi-mistakes" in first_user
    assert "headline_skeleton" in first_user
    assert "still-problem-after-trying" in first_user

    write_page(
        cartridge_name="listicle",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="fake",
        budget=budget,
        log=log,
        skeleton_id="hormozi-mistakes",
        headline_id="still-problem-after-trying",
        revision_note="REVISION REQUIRED: fix something",
    )
    repair_user = client.messages.calls[1]["messages"][0]["content"][1]["text"]
    assert '"skeleton"' in repair_user
    assert "headline_skeleton" in repair_user
    assert '"exemplars"' not in repair_user
