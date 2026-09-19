"""Cycle 42: harness/asset_describe.py -- vision-drafted alt text/tags for a
product's uncapped asset pool, plus `harness images describe`/`pool`'s own
budget-cap and CLI-flag behavior (harness/cli.py's cmd_images_describe/
cmd_images_pool).
"""
import argparse
import datetime
import json

import pytest
from PIL import Image

from harness import asset_describe, cli, pricing
from harness.budget import Budget, record_spend
from tests.conftest import FakeClient, json_response
from tests.support import TENANT


# ---------------------------------------------------------------------------
# Pricing normalization -- pricing.py only knows bare model-family ids.
# ---------------------------------------------------------------------------

def test_pricing_model_id_normalizes_a_dated_snapshot_to_its_family():
    assert asset_describe._pricing_model_id("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert asset_describe._pricing_model_id("claude-haiku-4-5") == "claude-haiku-4-5"


def test_pricing_model_id_leaves_an_unknown_model_unchanged():
    # so pricing.calculate_cost still raises UnknownModel for it, rather
    # than being silently priced at $0 or some other model's rate.
    assert asset_describe._pricing_model_id("some-other-model-2099") == "some-other-model-2099"
    with pytest.raises(pricing.UnknownModel):
        pricing.calculate_cost(asset_describe._pricing_model_id("some-other-model-2099"), 10, 10)


def test_default_vision_model_prices_cleanly():
    # DEFAULT_VISION_MODEL is a dated snapshot id -- confirm it normalizes to
    # a real pricing.py entry rather than crashing cost logging.
    cost = pricing.calculate_cost(asset_describe._pricing_model_id(asset_describe.DEFAULT_VISION_MODEL), 1000, 200)
    assert cost > 0


# ---------------------------------------------------------------------------
# Model resolution / tenant-neutral prompt
# ---------------------------------------------------------------------------

class _FakeTenant:
    def __init__(self, *, models_vision=None, vocab=None):
        self._models_vision = models_vision
        self.vocab = vocab or {}

    def get(self, dotted_key, default=None):
        if dotted_key == "models.vision":
            return self._models_vision
        return default


def test_vision_model_for_defaults_when_tenant_sets_nothing():
    assert asset_describe.vision_model_for(_FakeTenant()) == asset_describe.DEFAULT_VISION_MODEL


def test_vision_model_for_respects_tenant_override():
    assert asset_describe.vision_model_for(_FakeTenant(models_vision="claude-sonnet-5")) == "claude-sonnet-5"


def test_forbidden_terms_for_reads_emf_terms_from_tenant_vocab():
    tenant = _FakeTenant(vocab={"emf_terms": ["emf", "electromagnetic"]})
    assert asset_describe.forbidden_terms_for(tenant) == ("emf", "electromagnetic")


def test_strip_forbidden_terms_removes_whole_word_case_insensitively():
    text = "Close-up of the EMF shielding panel on the wall."
    cleaned = asset_describe.strip_forbidden_terms(text, ["emf"])
    assert "emf" not in cleaned.lower()
    assert "shielding panel" in cleaned.lower()


def test_strip_forbidden_terms_with_no_forbidden_terms_is_unchanged():
    assert asset_describe.strip_forbidden_terms("a plain sentence", []) == "a plain sentence"


def test_build_vision_system_prompt_is_tenant_neutral_and_names_the_product():
    product = {"name": "Fuji", "title": "Peak Saunas Fuji 2-Person Infrared Sauna"}
    prompt = asset_describe.build_vision_system_prompt(product, ["emf", "electromagnetic"])
    assert "Fuji" in prompt
    assert "describe only what is visible" in prompt.lower()
    assert "no health claims" in prompt.lower()
    assert "never mention emf" in prompt.lower()
    assert "electromagnetic" in prompt  # tenant's own forbidden term list, not hardcoded
    for tag in asset_describe.ALLOWED_TAGS:
        assert tag in prompt


# ---------------------------------------------------------------------------
# candidates_for -- excluded / already-has-alt filtering
# ---------------------------------------------------------------------------

def test_candidates_for_skips_excluded_and_already_reviewed_unless_forced(tmp_path, monkeypatch):
    pool = [
        {"id": "a1", "source": "shopify"},
        {"id": "a2", "source": "drive"},
        {"id": "a3", "source": "drive"},
    ]
    monkeypatch.setattr(asset_describe.ground_mod, "full_asset_pool", lambda *a, **k: pool)
    review = {
        "version": 1,
        "assets": {
            "a1": {"excluded": True, "alt": "", "note": ""},
            "a2": {"excluded": False, "alt": "already has alt text", "note": ""},
        },
    }
    (tmp_path / "asset-review.json").write_text(json.dumps(review))

    class T:
        claims_dir = tmp_path
        brand_dir = tmp_path
        claims_config = {}

    ids = [a["id"] for a in asset_describe.candidates_for(T(), {"slug": "x"})]
    assert ids == ["a3"]  # a1 excluded, a2 already has alt

    forced_ids = [a["id"] for a in asset_describe.candidates_for(T(), {"slug": "x"}, force=True)]
    assert forced_ids == ["a2", "a3"]  # a1 still excluded even with --force; a2 now included


# ---------------------------------------------------------------------------
# Vision response parsing
# ---------------------------------------------------------------------------

def test_parse_vision_response_valid_json():
    text = json_response({"name": "sauna-exterior-wood-side-view", "alt": "Wooden sauna exterior, side view.", "tags": ["exterior", "side-view"]})
    parsed = asset_describe._parse_vision_response(text)
    assert parsed == {"name": "sauna-exterior-wood-side-view", "alt": "Wooden sauna exterior, side view.", "tags": ["exterior", "side-view"]}


def test_parse_vision_response_drops_tags_outside_the_fixed_list():
    text = json_response({"name": "x-y-z", "alt": "an alt sentence", "tags": ["exterior", "made-up-tag"]})
    parsed = asset_describe._parse_vision_response(text)
    assert parsed["tags"] == ["exterior"]


def test_parse_vision_response_raises_on_missing_alt():
    text = json_response({"name": "x-y-z", "tags": []})
    with pytest.raises(asset_describe.VisionResponseInvalid):
        asset_describe._parse_vision_response(text)


def test_parse_vision_response_raises_on_non_json():
    with pytest.raises(json.JSONDecodeError):
        asset_describe._parse_vision_response("not json at all")


# ---------------------------------------------------------------------------
# describe_one / describe_assets with a fake client and a fake download
# ---------------------------------------------------------------------------

def _write_fake_jpeg(path, size=(50, 50)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 20, 30)).save(path, format="JPEG")
    return path


def _fake_download_asset_factory(tmp_path):
    def fake_download_asset(asset, dest_dir, *, log=None, **kwargs):
        path = _write_fake_jpeg(tmp_path / f"{asset['id']}.jpg")
        return {"path": path, "width": 50, "height": 50, "variants": [], "aspect": "1x1"}
    return fake_download_asset


class FakeLog:
    def __init__(self):
        self.events = []
        self.calls = []

    def event(self, stage, message):
        self.events.append((stage, message))

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_describe_one_parses_json_and_strips_a_forbidden_word(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_describe, "download_asset", _fake_download_asset_factory(tmp_path))
    response = json_response({
        "name": "sauna-emf-panel-close-up",
        "alt": "Close-up of the EMF shielding panel mounted on the wall.",
        "tags": ["interior", "panel"],
    })
    client = FakeClient([response])
    budget = Budget()
    log = FakeLog()
    result = asset_describe.describe_one(
        {"id": "a1"}, product={"name": "Fuji", "title": "Peak Fuji"}, forbidden_terms=("emf",),
        client=client, model="claude-haiku-4-5", budget=budget, log=log, cache_dir=tmp_path,
    )
    assert result is not None
    assert "emf" not in result["name"].lower()
    assert "emf" not in result["alt"].lower()
    assert result["tags"] == ["interior", "panel"]
    assert budget.calls_used == 1


def test_describe_one_returns_none_when_download_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_describe, "download_asset", lambda asset, dest_dir, **kwargs: None)
    client = FakeClient([])  # never reached
    budget = Budget()
    log = FakeLog()
    result = asset_describe.describe_one(
        {"id": "a1"}, product={"name": "Fuji", "title": "Fuji"}, forbidden_terms=(),
        client=client, model="claude-haiku-4-5", budget=budget, log=log, cache_dir=tmp_path,
    )
    assert result is None
    assert budget.calls_used == 0


def test_describe_one_skips_and_logs_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_describe, "download_asset", _fake_download_asset_factory(tmp_path))
    client = FakeClient(["not valid json"])
    budget = Budget()
    log = FakeLog()
    result = asset_describe.describe_one(
        {"id": "a1"}, product={"name": "Fuji", "title": "Fuji"}, forbidden_terms=(),
        client=client, model="claude-haiku-4-5", budget=budget, log=log, cache_dir=tmp_path,
    )
    assert result is None
    assert any("invalid vision response" in msg for _, msg in log.events)


def test_describe_assets_writes_review_file_and_counts_described_and_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_describe, "download_asset", _fake_download_asset_factory(tmp_path))
    brand_dir_path = tmp_path / "brand"
    brand_dir_path.mkdir()

    class T:
        claims_dir = tmp_path
        brand_dir = brand_dir_path
        runs_dir = tmp_path / "runs"

    responses = [
        json_response({"name": "a1-name", "alt": "Alt for asset one.", "tags": ["exterior"]}),
        "not valid json",  # a2 skipped
        json_response({"name": "a3-name", "alt": "Alt for asset three.", "tags": ["interior"]}),
    ]
    client = FakeClient(responses)
    budget = Budget()
    log = FakeLog()
    assets = [{"id": "a1", "source": "drive"}, {"id": "a2", "source": "drive"}, {"id": "a3", "source": "drive"}]

    result = asset_describe.describe_assets(
        T(), assets, product={"name": "Fuji", "title": "Fuji"}, client=client,
        model="claude-haiku-4-5", budget=budget, log=log, save_every=2,
    )
    assert result == {"described": 2, "skipped": 1}

    review = json.loads((brand_dir_path / "asset-review.json").read_text())["assets"]
    assert review["a1"]["alt"] == "Alt for asset one."
    assert review["a1"]["by"] == "vision-draft"
    assert review["a1"]["note"] == "alt drafted by vision (claude-haiku-4-5); tags: exterior"
    assert review["a1"]["excluded"] is False
    assert "a2" not in review  # skipped asset never gets an entry
    assert review["a3"]["alt"] == "Alt for asset three."


def test_describe_assets_saves_progress_every_save_every_assets(monkeypatch, tmp_path):
    """A crash after the first save_every batch must not lose that batch's
    progress -- simulate by checking the file exists with the first result
    after only enough responses for one batch, before describe_assets even
    finishes (it can't finish with only 2 canned responses for 3 assets, so
    the FakeClient itself raises on the third call -- describe_assets must
    have written the first 2 to disk before that point, not only at the end)."""
    monkeypatch.setattr(asset_describe, "download_asset", _fake_download_asset_factory(tmp_path))
    brand_dir_path = tmp_path / "brand"
    brand_dir_path.mkdir()

    class T:
        claims_dir = tmp_path
        brand_dir = brand_dir_path
        runs_dir = tmp_path / "runs"

    responses = [
        json_response({"name": "a1-name", "alt": "Alt one.", "tags": []}),
        json_response({"name": "a2-name", "alt": "Alt two.", "tags": []}),
    ]
    client = FakeClient(responses)
    budget = Budget()
    log = FakeLog()
    assets = [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]

    with pytest.raises(AssertionError):  # FakeMessages raises once it runs out of canned responses
        asset_describe.describe_assets(
            T(), assets, product={"name": "Fuji", "title": "Fuji"}, client=client,
            model="claude-haiku-4-5", budget=budget, log=log, save_every=2,
        )

    review = json.loads((brand_dir_path / "asset-review.json").read_text())["assets"]
    assert set(review) == {"a1", "a2"}  # saved after the 2nd (save_every) asset, before the crash on the 3rd


# ---------------------------------------------------------------------------
# pool_report
# ---------------------------------------------------------------------------

def test_pool_report_counts_total_reviewed_excluded_with_alt(monkeypatch, tmp_path):
    class FakeSource:
        def active_products(self):
            return [{"name": "Fuji", "slug": "fuji-slug"}]

    monkeypatch.setattr(asset_describe.ground_mod, "LocalFactsSource", lambda claims_dir: FakeSource())
    pool = [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]
    monkeypatch.setattr(asset_describe.ground_mod, "full_asset_pool", lambda *a, **k: pool)
    brand_dir_path = tmp_path / "brand"
    brand_dir_path.mkdir()
    review = {
        "version": 1,
        "assets": {
            "a1": {"alt": "has alt", "excluded": False},
            "a2": {"alt": "", "excluded": True},
        },
    }
    (brand_dir_path / "asset-review.json").write_text(json.dumps(review))

    class T:
        claims_dir = tmp_path
        brand_dir = brand_dir_path
        claims_config = {}

    rows = asset_describe.pool_report(T())
    assert rows == [{"model": "fuji", "total": 3, "reviewed": 2, "excluded": 1, "with_alt": 1}]


# ---------------------------------------------------------------------------
# CLI: harness images describe -- --dry-run / --limit / --force / stops at
# the daily cap (fake ledger) / skips excluded.
# ---------------------------------------------------------------------------

def _images_args(**overrides):
    defaults = dict(tenant=None, model="fuji", limit=None, force=False, dry_run=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _patch_candidates(monkeypatch, assets):
    monkeypatch.setattr(asset_describe, "product_for_model", lambda tenant, model: {"name": "Fuji", "title": "Fuji", "slug": "fuji"})
    monkeypatch.setattr(asset_describe, "candidates_for", lambda tenant, product, force=False: list(assets))


def test_cmd_images_describe_dry_run_makes_no_calls_and_writes_nothing(monkeypatch, capsys):
    assets = [{"id": f"a{i}"} for i in range(5)]
    _patch_candidates(monkeypatch, assets)

    def boom(*a, **k):
        raise AssertionError("dry-run must not call make_client")

    monkeypatch.setattr(cli, "make_client", boom)
    exit_code = cli.cmd_images_describe(_images_args(dry_run=True))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Would describe 5 image(s)" in out


def test_cmd_images_describe_honors_limit(monkeypatch):
    assets = [{"id": f"a{i}"} for i in range(5)]
    _patch_candidates(monkeypatch, assets)
    captured = {}

    def fake_describe_assets(tenant, candidates, **kwargs):
        captured["candidates"] = candidates
        return {"described": len(candidates), "skipped": 0}

    monkeypatch.setattr(asset_describe, "describe_assets", fake_describe_assets)
    monkeypatch.setattr(cli, "make_client", lambda: object())
    exit_code = cli.cmd_images_describe(_images_args(limit=2))
    assert exit_code == 0
    assert len(captured["candidates"]) == 2


def test_cmd_images_describe_honors_force(monkeypatch):
    assets = [{"id": "a1"}]
    seen = {}
    monkeypatch.setattr(asset_describe, "product_for_model", lambda tenant, model: {"name": "Fuji", "title": "Fuji", "slug": "fuji"})

    def fake_candidates_for(tenant, product, force=False):
        seen["force"] = force
        return list(assets)

    monkeypatch.setattr(asset_describe, "candidates_for", fake_candidates_for)
    monkeypatch.setattr(asset_describe, "describe_assets", lambda tenant, candidates, **kwargs: {"described": len(candidates), "skipped": 0})
    monkeypatch.setattr(cli, "make_client", lambda: object())
    cli.cmd_images_describe(_images_args(force=True))
    assert seen["force"] is True


def test_cmd_images_describe_unknown_model_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(asset_describe, "product_for_model", lambda tenant, model: None)
    monkeypatch.setattr(asset_describe, "available_model_slugs", lambda tenant: ["fuji", "mini"])
    exit_code = cli.cmd_images_describe(_images_args(model="not-a-model"))
    assert exit_code == 1
    assert "unknown --model" in capsys.readouterr().err


def test_cmd_images_describe_stops_at_the_daily_cap(monkeypatch, capsys):
    """Fake-ledger cap-stop: the tenant's daily cap is already fully spent
    before this command runs, so reserve_spend must refuse before any vision
    call is made -- exit 3, nothing described, no client ever constructed."""
    assets = [{"id": "a1"}]
    _patch_candidates(monkeypatch, assets)

    today_iso = datetime.date.today().isoformat()
    record_spend(TENANT, run_id="prior-run", cost=5.0, today_iso=today_iso)
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: {"budget": {"daily_usd": 5.0}}))

    def boom(*a, **k):
        raise AssertionError("must not call make_client once the daily cap is already spent")

    monkeypatch.setattr(cli, "make_client", boom)
    exit_code = cli.cmd_images_describe(_images_args())
    assert exit_code == 3
    assert "budget exceeded" in capsys.readouterr().err


def test_cmd_images_pool_prints_a_row_per_active_product(monkeypatch, capsys):
    monkeypatch.setattr(
        asset_describe, "pool_report",
        lambda tenant: [{"model": "fuji", "total": 10, "reviewed": 3, "excluded": 1, "with_alt": 2}],
    )
    exit_code = cli.cmd_images_pool(argparse.Namespace(tenant=None))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "fuji" in out and "10" in out
