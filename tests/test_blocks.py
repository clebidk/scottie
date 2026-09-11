"""Kimi long-run phase 3 (docs/KIMI-LONG-RUN.md): the block registry and its
deterministic gate. The real registry must pass with the suite's tenant word
lists; planted-violation registries under tmp_path must fail, one violation
class per test. Also: blocks.choice (the writer's recorded variant pick) and
its path-traversal guard, the page.json "blocks" gate check, and the
longform proof-row composition.
"""
import json

from harness import blocks
from harness.blocks import gate as block_gate
from harness.cli import check_page_gates
from tests.test_render import AD_BRIEF, FACTS_PACK, LONGFORM_PAGE
from tests.test_tenant import ENGINE_TENANT_WORDS
from tests.support import REPO_ROOT

ALL_TENANT_WORDS = tuple(set(ENGINE_TENANT_WORDS))


# ---------------------------------------------------------------------------
# The real registry passes the gate (the plan's 20-30 block count included).
# ---------------------------------------------------------------------------

def test_real_registry_passes_the_block_gate():
    problems = block_gate.validate_registry(tenant_words=ALL_TENANT_WORDS, min_blocks=20, max_blocks=30)
    assert problems == [], "block gate failures:\n" + "\n".join(problems)


def test_registry_is_shadcn_registry_item_shape():
    data = blocks.load_registry()
    for entry in data["items"]:
        assert entry["type"] == "registry:block"
        assert isinstance(entry["files"], list) and entry["files"]
        for f in entry["files"]:
            assert set(f) >= {"path", "type"}
        for key in ("license", "tenant_neutral", "mobile_rules", "motion", "screenshot"):
            assert key in entry["meta"], f"{entry['name']}: meta.{key} missing"


# ---------------------------------------------------------------------------
# Planted violations, one per test -- the gate must catch each.
# ---------------------------------------------------------------------------

def _write_block(root, name, *, html=None, css=None, meta_overrides=None, files_extra=None):
    block_dir = root / name
    block_dir.mkdir(parents=True, exist_ok=True)
    (block_dir / "block.html").write_text(html if html is not None else "<ul class=\"bk-x\">{% for i in items %}<li>{{ i.text }}</li>{% endfor %}</ul>\n")
    files = [{"path": f"{name}/block.html", "type": "registry:component"}]
    if css is not None:
        (block_dir / "block.css").write_text(css)
        files.append({"path": f"{name}/block.css", "type": "registry:style"})
    shot = block_dir / "screenshot.svg"
    shot.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>\n')
    files.append({"path": f"{name}/screenshot.svg", "type": "registry:asset"})
    files += files_extra or []
    meta = {
        "license": "Apache-2.0",
        "tenant_neutral": True,
        "styling": "self" if css is not None else "structure",
        "mobile_rules": "stacks under 480px",
        "motion": "none",
        "screenshot": f"{name}/screenshot.svg",
        "sections": ["proof"],
        "bindings": {"items": "list of {text}"},
    }
    meta.update(meta_overrides or {})
    entry = {
        "name": name,
        "type": "registry:block",
        "category": "proof",
        "title": "X",
        "description": "A planted fixture block.",
        "files": files,
        "dependencies": [],
        "meta": meta,
    }
    (root / "registry.json").write_text(json.dumps({"items": [entry]}))
    return entry


def _validate(root):
    return block_gate.validate_registry(root / "registry.json", tenant_words=ALL_TENANT_WORDS)


def test_gate_accepts_a_minimal_good_block(tmp_path):
    _write_block(tmp_path, "ok-block")
    assert _validate(tmp_path) == []


def test_gate_catches_literal_copy(tmp_path):
    _write_block(tmp_path, "copy-block", html='<p class="bk-copy-block">Buy our sauna today, it is great</p>\n')
    assert any("literal copy" in p for p in _validate(tmp_path))


def test_gate_catches_a_script_tag(tmp_path):
    _write_block(tmp_path, "js-block", html="<div class=\"bk-js-block\"></div><script>alert(1)</script>\n")
    assert any("<script>" in p for p in _validate(tmp_path))


def test_gate_catches_an_inline_handler(tmp_path):
    _write_block(tmp_path, "handler-block", html='<button class="bk-handler-block" onclick="buy()">{% if x %}{{ x }}{% endif %}</button>\n')
    assert any("inline event handlers" in p for p in _validate(tmp_path))


def test_gate_catches_an_unsized_image(tmp_path):
    _write_block(tmp_path, "img-block", html='<img class="bk-img-block" src="{{ image.url }}" alt="{{ image.alt }}">\n')
    assert any("must be sized" in p for p in _validate(tmp_path))


def test_gate_accepts_a_sized_image(tmp_path):
    _write_block(tmp_path, "img-block", html='<img class="bk-img-block" src="{{ image.url }}" alt="{{ image.alt }}" {% if image.width %}width="{{ image.width }}" height="{{ image.height }}"{% endif %}>\n')
    assert _validate(tmp_path) == []


def test_gate_catches_a_hex_color(tmp_path):
    _write_block(tmp_path, "hex-block", css=".bk-hex-block{color:#1a2b3c}\n")
    assert any("CSS variables for colors" in p for p in _validate(tmp_path))


def test_gate_catches_a_color_keyword(tmp_path):
    _write_block(tmp_path, "kw-block", css=".bk-kw-block{background: white}\n")
    assert any("CSS variables for colors" in p for p in _validate(tmp_path))


def test_gate_catches_a_fixed_width_over_390px(tmp_path):
    _write_block(tmp_path, "wide-block", css=".bk-wide-block{max-width: 720px}\n")
    assert any("over 390px" in p for p in _validate(tmp_path))


def test_gate_accepts_a_fixed_width_at_or_under_390px(tmp_path):
    _write_block(tmp_path, "narrow-block", css=".bk-narrow-block{max-width: 390px}\n")
    assert _validate(tmp_path) == []


def test_gate_catches_an_unscoped_selector(tmp_path):
    _write_block(tmp_path, "leaky-block", css=".someone-else{color:var(--ink)}\n")
    assert any("escapes the block's scope" in p for p in _validate(tmp_path))


def test_gate_catches_a_tenant_word(tmp_path):
    _write_block(tmp_path, "tenant-block", html='<p class="bk-tenant-block">{{ headline }}</p><!-- Peak -->\n')
    assert any("tenant word" in p for p in _validate(tmp_path))


def test_gate_catches_a_missing_screenshot(tmp_path):
    _write_block(tmp_path, "shot-block")
    (tmp_path / "shot-block" / "screenshot.svg").unlink()
    assert any("screenshot" in p for p in _validate(tmp_path))


def test_gate_catches_a_missing_license(tmp_path):
    _write_block(tmp_path, "license-block", meta_overrides={"license": "  "})
    assert any("license" in p for p in _validate(tmp_path))


def test_gate_catches_a_file_outside_the_block_dir(tmp_path):
    _write_block(tmp_path, "escape-block", files_extra=[{"path": "other/block.html", "type": "registry:component"}])
    assert any("not under the block's own directory" in p for p in _validate(tmp_path))


# ---------------------------------------------------------------------------
# blocks.choice -- the writer's recorded pick, with the traversal guard.
# ---------------------------------------------------------------------------

def test_choice_returns_the_writers_registered_pick():
    page = {"blocks": {"proof": "proof-stat-row"}}
    assert blocks.choice(page, "proof", "some-default") == "proof-stat-row"


def test_choice_falls_back_on_unknown_unregistered_or_malformed_picks():
    assert blocks.choice({"blocks": {"proof": "not-a-block"}}, "proof", "some-default") == "some-default"
    assert blocks.choice({"blocks": {"proof": "../../../etc"}}, "proof", "some-default") == "some-default"
    assert blocks.choice({"blocks": "nope"}, "proof", "some-default") == "some-default"
    assert blocks.choice({}, "proof", "some-default") == "some-default"
    assert blocks.choice(None, "proof", "some-default") == "some-default"


# ---------------------------------------------------------------------------
# page.json "blocks" map gate check + longform composition.
# ---------------------------------------------------------------------------

LONGFORM_SLOTS = {"proof": {"blocks": ["proof-stat-row", "proof-stat-row-cards"], "default": "proof-stat-row"}}


def _blocks_problems(page):
    return check_page_gates(
        page, FACTS_PACK, "longform",
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
        block_slots=LONGFORM_SLOTS,
    )


def test_block_violations_absent_or_valid_pick_passes():
    assert check_page_gates(
        LONGFORM_PAGE, FACTS_PACK, "longform",
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
        block_slots=LONGFORM_SLOTS,
    ) == []
    page = dict(LONGFORM_PAGE, blocks={"proof": "proof-stat-row"})
    assert _blocks_problems(page) == []


def test_block_violations_catch_unknown_slot_unknown_id_and_disallowed_id():
    assert any("no block slot" in p["issue"] for p in _blocks_problems(dict(LONGFORM_PAGE, blocks={"nope": "proof-stat-row"})))
    assert any("unknown block id" in p["issue"] for p in _blocks_problems(dict(LONGFORM_PAGE, blocks={"proof": "not-registered"})))
    assert any("not allowed for slot" in p["issue"] for p in _blocks_problems(dict(LONGFORM_PAGE, blocks={"proof": "sticky-cta-bar"})))


def test_longform_proof_row_renders_through_the_block(tmp_path):
    """The extraction proof: the proof row markup comes from
    blocks/proof-stat-row/block.html now -- same output as the old inline
    markup, and the writer's recorded pick renders when present."""
    from harness.render import render_page as _render_page

    page_with_stats = dict(LONGFORM_PAGE, hero=dict(LONGFORM_PAGE["hero"], proof_stats=[
        {"value": "4.8/5", "label": "from 1,200+ reviews", "claim_ids": ["reviews-live"]},
        {"value": "Free", "label": "shipping, always", "claim_ids": ["shipping-policy"]},
    ]))

    def render(page):
        return _render_page(
            cartridge_name="longform",
            page=page,
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            cartridges_dir=REPO_ROOT / "cartridges",
            brand_dir=tmp_path / "brand-does-not-exist",
            templates_dir=REPO_ROOT / "harness" / "templates",
            out_dir=tmp_path / "longform",
            published="2026-09-09",
            updated="2026-09-09",
            download_assets=False,
        ).read_text()

    default_html = render(page_with_stats)
    assert 'class="adv-proof-stats"' in default_html
    assert "4.8/5" in default_html
    picked_html = render(dict(page_with_stats, blocks={"proof": "proof-stat-row"}))
    assert picked_html == default_html
