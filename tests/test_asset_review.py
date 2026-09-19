"""Cycle 36: harness/asset_review.py -- the tenant asset-review.json loader/
applier/writer used by ground.LocalFactsSource.facts_for and harness/serve.py's
/images human-review site.
"""
import json

from harness import asset_review as asset_review_mod
from harness.asset_review import apply_asset_review, load_asset_review, save_asset_review


class FakeLog:
    def __init__(self):
        self.events = []

    def event(self, stage, message):
        self.events.append((stage, message))


def test_load_asset_review_missing_file_returns_empty(tmp_path):
    assert load_asset_review(tmp_path) == {}


def test_load_asset_review_malformed_json_returns_empty_and_warns(tmp_path):
    (tmp_path / "asset-review.json").write_text("not json")
    log = FakeLog()
    assert load_asset_review(tmp_path, log=log) == {}
    assert log.events and "asset-review.json" in log.events[0][1]


def test_load_asset_review_wrong_shape_returns_empty(tmp_path):
    (tmp_path / "asset-review.json").write_text(json.dumps({"not": "the right shape"}))
    assert load_asset_review(tmp_path) == {}


def test_load_asset_review_reads_valid_file(tmp_path):
    data = {"version": 1, "assets": {"asset-x-1": {"alt": "hi", "excluded": False}}}
    (tmp_path / "asset-review.json").write_text(json.dumps(data))
    assert load_asset_review(tmp_path) == data


def test_apply_asset_review_drops_excluded():
    assets = [{"id": "a1", "alt": "default"}, {"id": "a2", "alt": "default"}]
    review = {"assets": {"a1": {"excluded": True}}}
    kept = apply_asset_review(assets, review)
    assert [a["id"] for a in kept] == ["a2"]


def test_apply_asset_review_replaces_alt_without_mutating_input():
    assets = [{"id": "a1", "alt": "default"}]
    review = {"assets": {"a1": {"alt": "reviewer alt"}}}
    kept = apply_asset_review(assets, review)
    assert kept[0]["alt"] == "reviewer alt"
    assert assets[0]["alt"] == "default"


def test_apply_asset_review_empty_alt_override_keeps_default():
    assets = [{"id": "a1", "alt": "default"}]
    review = {"assets": {"a1": {"alt": ""}}}
    kept = apply_asset_review(assets, review)
    assert kept[0]["alt"] == "default"


def test_apply_asset_review_missing_file_is_unchanged():
    assets = [{"id": "a1", "alt": "default"}]
    kept = apply_asset_review(assets, {})
    assert kept == assets


def test_apply_asset_review_no_override_for_this_id_is_unchanged():
    assets = [{"id": "a1", "alt": "default"}]
    review = {"assets": {"a2": {"excluded": True}}}
    kept = apply_asset_review(assets, review)
    assert kept == assets


def test_apply_asset_review_url_mismatch_ignores_override_and_warns():
    assets = [{"id": "a1", "alt": "default", "url": "https://cdn/new.jpg"}]
    review = {"assets": {"a1": {"alt": "stale alt", "excluded": True, "url": "https://cdn/old.jpg"}}}
    log = FakeLog()
    kept = apply_asset_review(assets, review, log=log)
    assert len(kept) == 1
    assert kept[0]["alt"] == "default"
    assert log.events


def test_apply_asset_review_matching_url_still_applies():
    assets = [{"id": "a1", "alt": "default", "url": "https://cdn/same.jpg"}]
    review = {"assets": {"a1": {"alt": "reviewer alt", "url": "https://cdn/same.jpg"}}}
    kept = apply_asset_review(assets, review)
    assert kept[0]["alt"] == "reviewer alt"


def test_save_asset_review_writes_atomically_no_tmp_file_left(tmp_path):
    review = {"version": 1, "assets": {"a1": {"alt": "x"}}}
    save_asset_review(tmp_path, review)
    assert json.loads((tmp_path / "asset-review.json").read_text()) == review
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Cycle 44: apply_asset_review caps the alt it hands to facts_for()'s
# facts_pack (harness/asset_review.py's _cap_alt/ALT_MAX_CHARS) -- a
# reviewed alt can run to a full vision-drafted sentence, and with
# hundreds of reviewed assets that blows the writer prompt's token cap
# (tests/test_ground.py::test_facts_pack_stays_small).
# ---------------------------------------------------------------------------

def test_cap_alt_leaves_an_alt_under_the_cap_unchanged():
    assert asset_review_mod._cap_alt("Fuji sauna") == "Fuji sauna"


def test_cap_alt_truncates_to_at_most_the_cap():
    long_alt = (
        "Close-up of red light therapy panel mounted on a wooden wall "
        "inside the sauna interior."
    )
    capped = asset_review_mod._cap_alt(long_alt)
    assert len(capped) <= asset_review_mod.ALT_MAX_CHARS


def test_cap_alt_never_cuts_a_word_in_half():
    long_alt = (
        "Close-up of red light therapy panel mounted on a wooden wall "
        "inside the sauna interior."
    )
    capped = asset_review_mod._cap_alt(long_alt)
    # capped is a verbatim prefix of the original, and the very next
    # original character (if there is one) starts a new word rather than
    # continuing the last one -- never a mid-word cut.
    assert long_alt.startswith(capped)
    next_char = long_alt[len(capped):len(capped) + 1]
    assert next_char in ("", " ")


def test_cap_alt_strips_trailing_punctuation_left_by_the_cut():
    # "A red light panel," is exactly 18 chars -- with the cap set there,
    # the cut lands right on a word boundary (the next original char is a
    # space, so nothing gets backed up) but keeps the comma, which the
    # final rstrip must still drop.
    original_cap = asset_review_mod.ALT_MAX_CHARS
    asset_review_mod.ALT_MAX_CHARS = 18
    try:
        capped = asset_review_mod._cap_alt("A red light panel, mounted on the wall.")
    finally:
        asset_review_mod.ALT_MAX_CHARS = original_cap
    assert capped == "A red light panel"
    assert not capped.endswith((",", ".", ";", ":", "-"))


def test_apply_asset_review_caps_a_long_override_alt():
    long_alt = (
        "Close-up of red light therapy panel mounted on a wooden wall "
        "inside the sauna interior."
    )
    assets = [{"id": "a1", "alt": "default"}]
    review = {"assets": {"a1": {"alt": long_alt}}}
    kept = apply_asset_review(assets, review)
    assert kept[0]["alt"] == asset_review_mod._cap_alt(long_alt)
    assert len(kept[0]["alt"]) <= asset_review_mod.ALT_MAX_CHARS
