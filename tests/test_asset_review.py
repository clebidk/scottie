"""Cycle 36: harness/asset_review.py -- the tenant asset-review.json loader/
applier/writer used by ground.LocalFactsSource.facts_for and harness/serve.py's
/images human-review site.
"""
import json

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
