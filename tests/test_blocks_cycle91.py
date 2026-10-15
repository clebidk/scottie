"""Cycle 91: the block catalog (harness/blocks/catalog/) and the two looks
built from it (cartridges/landing/looks/cinematic, .../buybox).

Asserted here, offline:
  - the catalog: nine blocks, each with a partial that carries
    data-look-pattern="<id>" on its root, `bk-<id>-` classes, tokens only
    and no fixed width over a 390px phone;
  - renderer-only blocks render from a fixture facts pack with no writer
    data (stat_strip, hotspot_image, model_card, model_grid, trust_bar, faq)
    and show only verified facts; photo_essay picks library photos by tag;
  - writer-backed blocks (photo_essay, feature_grid, statement_band) pass and
    fail their gates; the catalog gate refuses unknown, undeclared and
    missing blocks; the degenerate-repair guard counts a dropped block;
  - a look declaring blocks pulls only those blocks' fields into the prompt;
  - the default landing looks render byte for byte as before (render hash);
  - the two new looks render in the offline fake run, keep the Shopify
    export invariants, pass the pagechecks and the brand lint, and the
    Library lists them; the look importer accepts a block include.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from evals import fake_run
from harness import landing, library, looks, pagechecks, photo_library, repair, write
from harness.blocks import catalog
from harness.blocks.catalog import feature_grid, hotspot_image, photo_essay, stat_strip
from harness.page_body import build_shopify_body
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_landing_cycle82 import AD_BRIEF, SRC
from tests.test_landing_cycle82 import _facts_pack as _landing_facts_pack
from tests.test_landing_proof_cycle85 import _facts_pack as _proof_facts_pack

LOOKS_DIR = REPO_ROOT / "cartridges" / "landing" / "looks"
FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"
ALL_IDS = ("stat_strip", "photo_essay", "hotspot_image", "feature_grid", "model_card", "model_grid",
           "statement_band", "faq", "trust_bar")

# sha256 of the canned landing page rendered in the `open` and `proof` looks
# (brand folder absent, assets not downloaded). First pinned on master bbc3ac4
# before the catalog existed and before the FAQ markup moved into
# blocks/faq.html; re-pinned at the merge of origin/master 3e70b2e (cycles
# 93-95 changed both looks) after checking that a render of 3e70b2e itself
# gives the same values. Change only with a deliberate change to those looks.
# Cycle 97 re-pin (owner grading pass): the sticky bar now shows the model name
# with the price beneath and its button reads "Shop now"; nothing else in the
# render changed (diffed against master ee2bd55, which gave the old values
# 0a748b00... and bea34247...).
# Cycle 110 re-pin: the canned page is a reasons-model page now (the "Why it
# works" section with a mid-page CTA replaces "The ad, checked"), every CTA
# carries data-el, "Text us" moved from the hero to the closing band and the
# closing band no longer repeats the warranty sentence (it stays in the trust
# line).
DEFAULT_LOOK_HASHES = {
    "open": "aad8a7785b4310483d759370c1fa6c5379d3d82bc66c81cf3a500010efbc4903",
    "proof": "792c8f267f1f70919b1c6897138aa26bba8688d3f5418e6f3249838b2ef9d666",
}


def _facts_pack():
    """The cycle 85 landing facts pack (rating 4.79 / 47 reviews, specs, the
    Fuji claims, Bread Pay) -- what the renderer-only blocks read."""
    return _proof_facts_pack()


def _ctx(page=None, facts_pack=None, options=None, assets=None, frame_asset=None, tenant=TENANT):
    fp = facts_pack or _facts_pack()
    return catalog.BlockContext(
        page=page or {}, facts_pack=fp, tenant=tenant, cartridge="landing", look="cinematic",
        options=options or {}, assets={} if assets is None else assets, frame_asset=frame_asset,
        asset_alt=lambda a, name=None: f"{name or 'Peak Fuji'} -- photo",
    )


def _render(tmp_path, page, *, look, facts_pack=None, brand_dir=None):
    out = tmp_path / "landing"
    render_page(
        cartridge_name="landing", page=dict(copy.deepcopy(page), look=look), ad_brief=AD_BRIEF,
        facts_pack=copy.deepcopy(facts_pack or _facts_pack()), cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=brand_dir or TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out,
        published="2026-10-07", updated="2026-10-07", tenant=TENANT, download_assets=False,
    )
    return out


def _block_page(look):
    return fake_run._canned_page("landing", tenant="peak-saunas", landing_look=look)


def _keys(problems):
    return {p.get("key") or p.get("path") for p in problems}


# ---------------------------------------------------------------------------
# The catalog and its partials
# ---------------------------------------------------------------------------

def test_the_catalog_registers_the_nine_blocks_with_a_partial_each():
    assert catalog.ids() == ALL_IDS
    for block in catalog.all_blocks():
        assert catalog.ID_RE.match(block.id) and block.title and block.needs
        assert (catalog.CATALOG_DIR / f"{block.id}.html").is_file()
        assert block.partial == f"blocks/{block.id}.html"
    assert {b.id for b in catalog.all_blocks() if b.writer_backed} == {"photo_essay", "feature_grid", "statement_band"}


@pytest.mark.parametrize("block_id", ALL_IDS)
def test_each_partial_is_scoped_tokened_marked_and_mobile_safe(block_id):
    from harness.look_import import NAMED_COLOURS, _COLOUR_FN_RE, _HEX_RE

    text = (catalog.CATALOG_DIR / f"{block_id}.html").read_text()
    markup = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    # the marker cycle 90's fidelity scoring reads, on the root element
    # the cycle 91 id first; a cycle 90 pattern name (look_design.PATTERNS) may follow it
    assert re.search(f'data-look-pattern="{block_id}(?: [a-z-]+)?"', markup)
    css = "\n".join(re.findall(r"<style>(.*?)</style>", markup, re.S))
    assert css, "the block's CSS lives in its partial"
    assert not _HEX_RE.search(css) and not _COLOUR_FN_RE.search(css)
    for _prop, value in re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", css):
        assert not any(w in NAMED_COLOURS for w in re.findall(r"[a-z]+", re.sub(r"--[\w-]+", "", value.lower()))
                       if w not in ("transparent", "inherit", "none")), value
    for sel, _body in _rules(css):
        assert f".bk-{block_id}" in sel, sel
    no_media = re.sub(r"@media[^{]*", "", css)
    # a fixed width (not a max-width cap) over a phone breaks the 390px layout
    for m in re.finditer(r"(?<![\w-])(?:min-width|width|flex-basis)\s*:\s*(\d+)px", no_media):
        assert int(m.group(1)) <= 390, m.group(0)
    # every class the block's own markup sets is its own (the CTA pill and
    # the faq legacy branch are the look's)
    allowed_foreign = {"ld-btn", "ld-btn--light", "op-btn", "ld-sec", "ld-narrow", "ld-h2", "ld-qa"}
    for attr in re.findall(r'class="([^"]*)"', re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", markup)):
        for cls in attr.split():
            assert cls.startswith(f"bk-{block_id}") or cls in allowed_foreign, cls


def test_a_look_json_naming_an_unknown_block_is_refused():
    with pytest.raises(ValueError, match="unknown blocks"):
        catalog.parse_look_spec({"blocks": ["stat_strip", "carousel"]})
    with pytest.raises(ValueError, match="options for blocks it does not use"):
        catalog.parse_look_spec({"blocks": ["faq"], "options": {"stat_strip": {"layout": "inline"}}})
    spec = catalog.parse_look_spec({"blocks": ["stat_strip"], "options": {"stat_strip": {"layout": "inline"}}})
    assert spec == {"blocks": ("stat_strip",), "options": {"stat_strip": {"layout": "inline"}}}


def test_the_two_looks_declare_their_blocks_and_the_default_looks_declare_none():
    assert catalog.look_blocks("landing", "cinematic", tenant=TENANT) == (
        "stat_strip", "photo_essay", "feature_grid", "statement_band", "faq")
    assert catalog.look_blocks("landing", "buybox", tenant=TENANT) == ("trust_bar", "hotspot_image", "faq")
    # cycle 94: the same look.json declares the hero compositions (harness/heroes)
    from harness import heroes

    assert heroes.look_spec("landing", "cinematic", TENANT)["default_heroes"] == ("caps_over_photo",)
    assert heroes.look_spec("landing", "buybox", TENANT)["default_heroes"] == ("buybox",)
    assert catalog.look_spec("landing", "cinematic", tenant=TENANT)["options"]["stat_strip"] == {"layout": "inline"}
    for look in ("open", "proof"):
        assert catalog.look_blocks("landing", look, tenant=TENANT) == ()
    assert looks.cartridge_looks("landing") == ("open", "proof", "cinematic", "buybox", "cinematic-light",
                                                "peak")  # cycle 97, cycle 110
    assert looks.DEFAULT_LOOKS["landing"] == "open"


# ---------------------------------------------------------------------------
# Renderer-only blocks, from facts alone
# ---------------------------------------------------------------------------

def _verified_ids(fp):
    return {c["id"] for c in fp["verified_claims"]}


def test_stat_strip_is_three_or_four_verified_stats():
    fp = _facts_pack()
    data = stat_strip.render(_ctx(facts_pack=fp, options={"layout": "inline"}))
    assert data["layout"] == "inline" and 3 <= len(data["stats"]) <= 4
    assert all(s["claim_ids"] and set(s["claim_ids"]) <= _verified_ids(fp) for s in data["stats"])
    assert data["stats"][0]["value"] == "4.79 ★"  # first_screen.stats leads with the rating
    assert not any("(" in s["value"] for s in data["stats"])
    # too few verified facts: nothing to show
    bare = dict(fp, specs=[], verified_claims=[], reviews_summary=None)
    assert stat_strip.render(_ctx(facts_pack=bare))["stats"] == []


def test_hotspot_labels_come_from_verified_facts_and_positions_from_the_tenant_table():
    fp = _facts_pack()
    assets = {}
    data = hotspot_image.render(_ctx(facts_pack=fp, assets=assets))
    assert data["asset_id"] == "asset-cutout-fuji" and data["asset_id"] in assets and data["show_dots"]
    labels = {d["key"]: d["label"] for d in data["dots"]}
    assert labels["heater"] == "700–25,000nm infrared" and labels["bench"] == "2-person bench"
    assert labels["panel"] == "Red light panel, included"
    assert "glass" not in labels and "control" not in labels   # no claim states them
    assert 3 <= len(data["dots"]) <= 5
    assert all(set(d["claim_ids"]) <= _verified_ids(fp) for d in data["dots"])
    table = json.loads((Path(TENANT.root) / "brand" / "hotspots.json").read_text())["models"]["fuji"]
    assert all([d["x"], d["y"]] == table[d["key"]] for d in data["dots"])


def test_hotspots_are_placed_evenly_without_a_table_and_need_three_labels_for_dots(monkeypatch):
    monkeypatch.setattr(hotspot_image, "load_positions", lambda tenant: {})
    fp = _facts_pack()
    data = hotspot_image.render(_ctx(facts_pack=fp))
    ys = [d["y"] for d in data["dots"]]
    assert ys == sorted(ys) and len(set(d["x"] for d in data["dots"])) == 2
    thin = dict(fp, specs=[s for s in fp["specs"] if "Capacity" in s["label"]],
                verified_claims=[c for c in fp["verified_claims"] if "rlt" not in c["id"] and "red" not in c["id"]])
    few = hotspot_image.render(_ctx(facts_pack=thin))
    assert few["dots"] and not few["show_dots"]


def test_model_card_is_the_runs_product_from_facts_only():
    fp = _facts_pack()
    data = catalog.get("model_card").render(_ctx(facts_pack=fp, options={"variant": "panel"}))
    assert data["name"] == "Peak Fuji" and data["price"]["text"] == "$8,250" and data["price"]["claim_ids"] == ["price-fuji"]
    assert data["rating"]["value"] == "4.79" and len(data["specs"]) == 2 and data["variant"] == "panel"
    assert data["financing"] == "Financing is available through Bread Pay at checkout."
    assert data["asset_id"] == "asset-cutout-fuji"
    assert set(data["claim_ids"]) <= _verified_ids(fp)


def _models_facts_pack():
    fp = _facts_pack()
    rows = []
    for slug, name, price in (("rainier", "Rainier", "$7,250"), ("everest", "Everest", "$7,950"),
                              ("fuji", "Fuji", "$8,250"), ("nocut", "Nocut", "$1,000")):
        cid = f"price-{slug}"
        if cid not in _verified_ids(fp):
            fp["verified_claims"].append({"id": cid, "text": f"The Peak {name} is priced at {price}.",
                                          "category": "price", "source": SRC})
        rows.append({"slug": slug, "name": f"Peak {name}", "model_name": name, "url": SRC, "price_text": price,
                     "price_claim_id": cid, "capacity_text": "2-Person" if slug != "rainier" else "1-Person",
                     "capacity_claim_id": "spec-fuji-capacity" if slug == "fuji" else None, "image": None})
    fp["models"] = rows
    return fp


def test_model_grid_shows_every_priced_model_with_a_cutout_and_one_cta(tmp_path):
    fp = _models_facts_pack()
    data = catalog.get("model_grid").render(_ctx(facts_pack=fp))
    assert [c["slug"] for c in data["cards"]] == ["rainier", "everest", "fuji"]   # no cut-out: left out
    assert [c["price"]["text"] for c in data["cards"]] == ["$7,250", "$7,950", "$8,250"]
    fuji = data["cards"][-1]
    assert fuji["is_run_model"] and fuji["rating"] and fuji["specs"][0]["claim_ids"] == ["spec-fuji-capacity"]
    assert all(c["rating"] is None for c in data["cards"][:2])
    # the CTA rule: one phrase on every card, so it may name no model
    named = catalog.get("model_grid").page_gate({"cta_text": "Shop the Fuji"}, fp, TENANT)
    assert _keys(named) == {"blocks:model_grid:cta_names_model"}
    assert catalog.get("model_grid").page_gate({"cta_text": "See pricing"}, fp, TENANT) == []
    # rendered through a look that declares it: every card links to the page's one CTA
    look_root = tmp_path / "tlooks"
    (look_root / "landing" / "grid-demo").mkdir(parents=True)
    (look_root / "landing" / "grid-demo" / "template.html").write_text(
        (LOOKS_DIR / "open" / "template.html").read_text().replace(
            '  {% include "blocks/faq.html" %}', '  {% include "blocks/model_grid.html" %}'))
    (look_root / "landing" / "grid-demo" / "look.json").write_text(json.dumps({"blocks": ["model_grid"]}))
    page = dict(copy.deepcopy(fake_run.LANDING_PAGE), cta_text="See pricing", look="grid-demo")
    out = tmp_path / "out" / "landing"
    render_page(cartridge_name="landing", page=page, ad_brief=AD_BRIEF, facts_pack=fp,
                cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
                templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out, published="2026-10-07",
                updated="2026-10-07", tenant=TENANT, download_assets=False, looks_dir=look_root)
    html = (out / "index.html").read_text()
    grid = html[html.index('data-look-pattern="model_grid '):]
    grid = grid[:grid.index("</ul>")]
    assert grid.count(f'<a class="ld-btn" data-el="cards" href="{page["cta_url"]}">See pricing</a>') == 3
    assert "$7,950" in grid and "Peak Everest" in grid


def test_trust_bar_shows_only_stated_facts():
    fp = _facts_pack()
    kinds = [f["kind"] for f in catalog.get("trust_bar").render(_ctx(facts_pack=fp))["facts"]]
    assert kinds == ["warranty", "shipping", "financing"]   # no HSA/FSA claim in this pack
    fp["verified_claims"].append({"id": "hsa-fsa", "text": "Peak saunas may be eligible for HSA/FSA purchase through Redo.",
                                  "category": "trust", "source": SRC})
    facts = catalog.get("trust_bar").render(_ctx(facts_pack=fp))["facts"]
    assert facts[-1] == {"text": "Peak saunas may be eligible for HSA/FSA purchase through Redo.",
                         "claim_ids": ["hsa-fsa"], "kind": "hsa"}
    assert catalog.get("trust_bar").render(_ctx(facts_pack={"verified_claims": [], "product": {}}))["facts"] == []


def test_faq_block_reads_the_pages_faq():
    data = catalog.get("faq").render(_ctx(page=copy.deepcopy(fake_run.LANDING_PAGE)))
    assert [q["question"] for q in data["questions"]] == [
        q["question"] for q in fake_run.LANDING_PAGE["faq"]["questions"]]


# ---------------------------------------------------------------------------
# photo_essay: library photos by tag
# ---------------------------------------------------------------------------

_LEGACY_TO_93 = {"exterior": "exterior", "lifestyle": "room", "interior": "room", "detail": "detail-macro"}


def _library(monkeypatch, photos):
    """A photo library of `photos` behind cycle 93's resolver: the manifest
    (for the descriptions the writer is told) and the resolver's candidate
    pool (images.candidates), in the shape images.py builds it."""
    from harness import images

    pool = [{"id": pid, "source": "library", "shot": _LEGACY_TO_93[shot], "shot_source": "legacy",
             "shots": {_LEGACY_TO_93[shot]}, "mood": None, "aspect": 1.25, "orientation": None,
             "fullbleed_ok": False, "dark_ok": False, "focal": {"x": 0.5, "y": 0.5}, "model": "fuji", "quality": None,
             "ai_generated": False, "old_brand": False, "blurry": blurry, "cluttered": False,
             "asset": {"id": pid, "url": f"photo-library/{pid}.jpg", "kind": "lifestyle"}}
            for pid, shot, _desc, blurry in photos]
    monkeypatch.setattr(images, "candidates", lambda *a, **k: list(pool))
    manifest = {"photos": [
        {"id": pid, "file": f"{pid}.jpg", "ai_generated": False, "excluded": False,
         "tags": {"shot": shot, "product": "fuji", "model_agnostic": False, "has_text_overlay": False,
                  "blurry": blurry, "cluttered": False, "description": desc, "features_visible": [],
                  "product_confidence": 0.9}}
        for pid, shot, desc, blurry in photos]}
    monkeypatch.setattr(photo_library, "load_manifest", lambda tenant, log=None: manifest)


PHOTOS = [("asset-photo-000000000001", "exterior", "the cabin on a deck", False),
          ("asset-photo-000000000002", "lifestyle", "a couple inside", False),
          ("asset-photo-000000000003", "detail", "the heater panel", False),
          ("asset-photo-000000000004", "interior", "the bench", False),
          ("asset-photo-000000000005", "detail", "blurry control", True)]


def _photo_pack():
    fp = _facts_pack()
    fp["assets"] = fp["assets"] + [{"id": pid, "url": f"photo-library/{pid}.jpg", "kind": "lifestyle"}
                                   for pid, *_ in PHOTOS]
    return fp


def test_photo_essay_picks_room_detail_exterior_and_pairs_the_writers_lines(monkeypatch):
    _library(monkeypatch, PHOTOS)
    fp = _photo_pack()
    picks = photo_essay.candidates(fp, TENANT)
    assert [p["group"] for p in picks] == ["room", "detail", "exterior", "room"]
    # the resolver ranks the blurry detail below the sharp one, so it is not in the four
    assert "asset-photo-000000000005" not in [p["asset_id"] for p in picks]
    lines = [{"text": f"Line {w}.", "claim_ids": []} for w in ("one", "two", "three", "four")]
    lines[1] = {"text": "Heats to 150°F.", "claim_ids": ["spec-fuji-max-temperature"]}
    page = {"blocks": {"photo_essay": {"lines": lines}}, "room_photo": {"asset_id": picks[3]["asset_id"]}}
    assets = {}
    data = photo_essay.render(_ctx(page=page, facts_pack=fp, assets=assets))
    # the room photo is already on the page: dropped with its own line
    assert [p["asset_id"] for p in data["photos"]] == [p["asset_id"] for p in picks[:3]]
    assert [p["line"] for p in data["photos"]] == ["Line one.", "Heats to 150°F.", "Line three."]
    assert data["claim_ids"] == ["spec-fuji-max-temperature"] and set(data["asset_ids"]) <= set(assets)
    # the writer is told the same photos in the same order
    prompt = " ".join(photo_essay.writer_lines(fp, TENANT))
    assert "exactly 4 entries" in prompt and prompt.index("a couple inside") < prompt.index("the heater panel")


def test_photo_essay_degrades_and_never_prints_an_unchecked_line(monkeypatch):
    _library(monkeypatch, PHOTOS[:1])
    fp = _photo_pack()
    assert photo_essay.candidates(fp, TENANT) == []          # one photo is not an essay
    assert photo_essay.render(_ctx(page={}, facts_pack=fp))["photos"] == []
    assert "omit blocks.photo_essay" in photo_essay.writer_lines(fp, TENANT)[0]
    assert not catalog.get("photo_essay").needs_writer(fp, TENANT)
    _library(monkeypatch, PHOTOS)
    page = {"blocks": {"photo_essay": {"lines": [{"text": "Heats to 150°F.", "claim_ids": []},
                                                  {"text": "Fine.", "claim_ids": ["not-a-claim"]}]}}}
    data = photo_essay.render(_ctx(page=page, facts_pack=fp))
    assert [p["line"] for p in data["photos"][:2]] == ["", ""]
    # the ad still comes from the resolver too (images.fill_slots, shot "ad-still")
    from harness import images

    record = {"frame_path": "/tmp/frame.jpg", "width": 800, "height": 1000}
    with_still = photo_essay.render(_ctx(page={}, facts_pack=fp, options={"ad_still": True}))
    assert len(with_still["photos"]) == 4   # already four photos: no room for the still
    _library(monkeypatch, PHOTOS[:2])
    library_only = images.candidates
    monkeypatch.setattr(images, "candidates", lambda fp_, tenant, ad_frame=None, **k: library_only(fp_, tenant)
                        + images.ad_still_candidates(ad_frame))
    ctx = _ctx(page={}, facts_pack=fp, options={"ad_still": True})
    ctx.frame_record = record
    still = photo_essay.render(ctx)
    assert still["photos"][-1]["asset_id"] == "asset-adframe" and still["photos"][-1]["line"] == ""


# ---------------------------------------------------------------------------
# Writer-backed gates
# ---------------------------------------------------------------------------

def _gate(block_id, fields, fp=None):
    return catalog.get(block_id).gate(fields, fp or _facts_pack(), TENANT)


def test_photo_essay_gate():
    good = copy.deepcopy(fake_run.LANDING_BLOCK_FIELDS["photo_essay"])
    assert _gate("photo_essay", good) == []
    long = copy.deepcopy(good)
    long["lines"][0]["text"] = " ".join(["word"] * 15)
    assert "blocks:photo_essay:line_words:0" in _keys(_gate("photo_essay", long))
    number = copy.deepcopy(good)
    number["lines"][1] = {"text": "It heats to 150°F in the cabin.", "claim_ids": []}
    problems = _gate("photo_essay", number)
    assert "blocks:photo_essay:line:1_claims" in _keys(problems)
    assert any(p["path"] == "$.blocks.photo_essay.lines[1].claim_ids" for p in problems)
    unverified = copy.deepcopy(good)
    unverified["lines"][2]["claim_ids"] = ["made-up"]
    assert "blocks:photo_essay:line:2_unverified" in _keys(_gate("photo_essay", unverified))
    assert _keys(_gate("photo_essay", {"lines": good["lines"][:1]})) == {"blocks:photo_essay:lines_count"}


def test_feature_grid_gate_and_the_icon_follows_the_claim():
    good = copy.deepcopy(fake_run.LANDING_BLOCK_FIELDS["feature_grid"])
    assert _gate("feature_grid", good) == []
    bad = copy.deepcopy(good)
    bad["cells"][0]["label"] = "Medical grade red light"
    bad["cells"][1]["claim_ids"] = ["not-a-claim"]
    bad["cells"][2]["claim_ids"] = []
    bad["cells"][3]["label"] = bad["cells"][1]["label"]
    bad["cells"][3]["line"] = " ".join(["word"] * 15)
    assert {"blocks:feature_grid:label_words:0", "blocks:feature_grid:cell:1_unverified",
            "blocks:feature_grid:cell:2_claims", "blocks:feature_grid:label_duplicate:3",
            "blocks:feature_grid:line_words:3"} <= _keys(_gate("feature_grid", bad))
    assert _keys(_gate("feature_grid", {"cells": good["cells"][:2]})) == {"blocks:feature_grid:cells_count"}
    fp = _facts_pack()
    page = {"blocks": {"feature_grid": good}}
    cells = feature_grid.render(_ctx(page=page, facts_pack=fp))["cells"]
    assert [c["icon"] for c in cells] == ["red_light", "full_spectrum", "wood", "shipping"]
    assert all('stroke="currentColor"' in c["icon_svg"] for c in cells)
    # a warranty claim draws the warranty icon, whatever the label says
    assert feature_grid.icon_for(["warranty-terms"], fp) == "warranty"
    assert feature_grid.icon_for(["spec-fuji-capacity"], fp) is None


def test_feature_grid_icons_are_the_proof_looks_icons():
    text = (LOOKS_DIR / "proof" / "template.html").read_text()
    proof_icons = dict(re.findall(r'^\s+"(\w+)": \'(.*)\',?$', text, re.M))
    assert proof_icons == feature_grid.ICON_PATHS


def test_statement_band_gate():
    assert _gate("statement_band", {"line": "A real sauna session at home, on your own schedule"}) == []
    assert _keys(_gate("statement_band", {"line": "Heat to 150°F in fifteen minutes"})) >= {
        "blocks:statement_band:line_number"}
    assert _keys(_gate("statement_band", {"line": " ".join(["calm"] * 13)})) == {"blocks:statement_band:line_words"}
    assert _keys(_gate("statement_band", {})) == {"blocks:statement_band:line"}


# ---------------------------------------------------------------------------
# The catalog gate inside the repair loop
# ---------------------------------------------------------------------------

def _page_gates(page, look):
    from tests.test_landing_cycle82 import CARTRIDGE_DIR

    md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    schema = json.loads(TENANT.render((CARTRIDGE_DIR / "schema.json").read_text()))
    from tests.test_landing_cycle82 import LANDING_BRIEF

    ctas = landing.allowed_cta_texts(schema, _facts_pack(), LANDING_BRIEF, TENANT)
    return repair.check_page_gates(
        page, _facts_pack(), "landing", financing_lender="Bread Pay", speaker_pov="first_person",
        word_range=write.parse_word_range(md), allowed_cta_texts=ctas, ad_brief=LANDING_BRIEF, tenant=TENANT,
        look=look,
    )


def test_the_canned_block_pages_pass_every_gate_in_their_looks():
    for look in ("cinematic", "buybox"):
        assert _page_gates(_block_page(look), look) == [], look
    assert "blocks" not in _block_page("buybox")   # buybox has no writer block


def test_undeclared_unknown_missing_and_renderer_blocks_fail():
    cinematic = _block_page("cinematic")
    # a look that does not use the block
    assert "blocks:feature_grid:not_in_look" in _keys(_page_gates(cinematic, "open"))
    # an unknown block, renderer-owned fields
    page = copy.deepcopy(cinematic)
    page["blocks"]["carousel"] = {"slides": []}
    page["blocks"]["stat_strip"] = {"stats": [{"value": "9", "label": "x"}]}
    keys = _keys(_page_gates(page, "cinematic"))
    assert {"blocks:unknown:carousel", "blocks:stat_strip:renderer_owned"} <= keys
    # a declared writer block left out (photo_essay has no library photos here: not required)
    page = copy.deepcopy(cinematic)
    del page["blocks"]["feature_grid"]
    del page["blocks"]["photo_essay"]
    keys = _keys(_page_gates(page, "cinematic"))
    assert "blocks:feature_grid:missing" in keys and "blocks:photo_essay:missing" not in keys
    # a registry slot pick (a string) is still the cycle 34 gate's, and catalog fields never trip it
    assert pagechecks.find_block_violations(cinematic, "landing", None) == []


def test_the_look_on_page_json_is_used_when_the_run_look_is_not_given():
    page = dict(_block_page("cinematic"), look="cinematic")
    assert repair._block_catalog_violations(page, _facts_pack(), "landing", None, TENANT) == []
    assert repair._block_catalog_violations(fake_run.LANDING_PAGE, _facts_pack(), "landing", None, TENANT) == []


def test_a_repair_that_drops_a_block_is_degenerate_unless_the_gate_asked():
    prev = _block_page("cinematic")
    page = copy.deepcopy(prev)
    del page["blocks"]["statement_band"]
    assert repair.degenerate_repair(prev, page)
    asked = [{"path": "$.blocks.statement_band", "key": "blocks:statement_band:not_in_look"}]
    assert not repair.degenerate_repair(prev, page, asked)


# ---------------------------------------------------------------------------
# The writer prompt
# ---------------------------------------------------------------------------

def _system(look):
    from tests.test_landing_proof_cycle85 import _system_text
    from tests.test_render import AD_BRIEF as RENDER_BRIEF

    md = (REPO_ROOT / "cartridges" / "landing" / "cartridge.md").read_text()
    _schema, kwargs = write.build_initial_write_request(
        cartridge_name="landing", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=RENDER_BRIEF,
        facts_pack=_facts_pack(), model="claude-test", word_range=write.parse_word_range(md),
        tenant=TENANT, landing_look=look,
    )
    return _system_text(kwargs)


def test_a_look_pulls_only_its_blocks_fields_into_the_prompt():
    cinematic, buybox, open_ = _system("cinematic"), _system("buybox"), _system("open")
    assert "blocks.feature_grid.cells has 3-6 cells" in cinematic
    assert "blocks.statement_band.line: one display line of at most 12 words" in cinematic
    assert "omit blocks.photo_essay" in cinematic   # no library photos in this facts pack
    assert "keys: `feature_grid`, `statement_band`" in cinematic
    for text in (buybox, open_):
        assert "section BLOCKS" not in text and "blocks.feature_grid" not in text and "blocks.statement_band" not in text
    assert "feature_grid" not in open_


def test_a_model_grid_look_tells_the_writer_about_the_cta():
    lines = catalog.writer_lines(("model_grid", "faq"), _facts_pack(), TENANT)
    assert lines and "cta_text must name no single model" in lines[0]


# ---------------------------------------------------------------------------
# Default looks unchanged; the new looks render
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", ["open", "proof"])
def test_the_default_landing_looks_render_byte_for_byte_as_before(tmp_path, look):
    page = fake_run.LANDING_PAGE if look == "open" else fake_run.LANDING_PROOF_PAGE
    fp = _landing_facts_pack() if look == "open" else _proof_facts_pack()
    out = _render(tmp_path, page, look=look, facts_pack=fp, brand_dir=tmp_path / "nobrand")
    assert hashlib.sha256((out / "index.html").read_bytes()).hexdigest() == DEFAULT_LOOK_HASHES[look]
    assert "data-look-pattern" not in (out / "index.html").read_text()


def test_a_declared_faq_gets_the_catalog_markup(tmp_path):
    html = (_render(tmp_path, _block_page("cinematic"), look="cinematic") / "index.html").read_text()
    assert 'data-look-pattern="faq"' in html and 'class="ld-qa"' not in html
    assert html.count('class="bk-faq-qa"') == len(fake_run.LANDING_PAGE["faq"]["questions"])


def test_the_cinematic_look_renders_its_blocks_in_order(tmp_path):
    html = (_render(tmp_path, _block_page("cinematic"), look="cinematic") / "index.html").read_text()
    # no ad still and no library photo here: caps_over_photo falls back to dark_statement (cycle 94)
    hero_at = html.index('data-look-pattern="hero:dark_statement"')
    order = [hero_at] + [html.index(f'data-look-pattern="{b}')
                         for b in ("stat_strip", "feature_grid", "statement_band", "faq")]
    assert order == sorted(order)
    assert html.index('data-look-pattern="stat_strip ') < html.index("Why it works")   # right under the hero
    assert 'class="cn-stats cn-stats--dark"' in html and "bk-stat_strip--inline" in html
    assert "A real sauna session at home, on your own schedule" in html
    url = fake_run.LANDING_PAGE["cta_url"]
    ctas = re.findall(r'<a class="([^"]*)"(?: data-el="\w+")? href="([^"]*)"', html)
    assert {href for cls, href in ctas if "btn" in cls or "cta" in cls} == {url}
    assert len(re.findall(r"<h1[\s>]", html)) == 1 and _lint(html) == []


def test_the_cinematic_stats_sit_on_hairlines_under_a_light_composition(tmp_path):
    page = dict(_block_page("cinematic"), hero_style="split")
    html = (_render(tmp_path, page, look="cinematic") / "index.html").read_text()
    assert 'data-look-pattern="hero:split"' in html and 'class="cn-stats cn-stats--light"' in html


def test_the_buybox_look_renders_trust_bar_the_buybox_hero_and_hotspots(tmp_path):
    html = (_render(tmp_path, _block_page("buybox"), look="buybox") / "index.html").read_text()
    order = [html.index(p) for p in ('data-look-pattern="trust_bar ', 'data-look-pattern="hero:buybox"',
                                     'data-look-pattern="hotspot_image ', 'data-look-pattern="faq"')]
    assert order == sorted(order)
    assert "data-look-pattern=\"model_card\"" not in html   # the buy panel is the hero composition's
    assert "$8,250" in html and html.count('class="bk-hotspot_image-dot ') >= 3 and "2-person bench" in html
    assert "Financing is available through Bread Pay at checkout." in html
    assert len(re.findall(r"<h1[\s>]", html)) == 1 and _lint(html) == []


@pytest.mark.parametrize("look", ["cinematic", "buybox"])
def test_the_new_looks_pass_the_offline_fake_run_export_and_pagechecks(look):
    exit_code, run_dir, pages = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="landing", look=look)
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == look
    assert set(page.get("blocks", {})) == ({"photo_essay", "feature_grid", "statement_band"} if look == "cinematic"
                                           else set())
    out = run_dir / "landing"
    html = (out / "index.html").read_text()
    for block_id in catalog.look_blocks("landing", look, tenant=TENANT):
        if block_id != "photo_essay":   # no photo library on a test machine: it degrades to nothing
            assert re.search(f'data-look-pattern="{block_id}[ "]', html), block_id
    assert pagechecks.find_html_validity_violations(html) == []
    assert pagechecks.find_rendered_json_ld_violations(html, "landing") == []
    assert pagechecks.find_rendered_internal_link_violations(html, tenant=TENANT) == []
    assert pagechecks.find_image_markup_violations(html) == []
    export, _manifest = build_shopify_body(out)
    assert export.startswith("<style>\n") and export.count("</style>") == 1
    assert not re.search(r'href="https?://(?:www\.)?peaksaunas\.com', export)
    unscoped = [sel for sel, _b in _rules(_css(export)) if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    assert unscoped == []
    assert export.count('class="pk-lp') == 1 and _lint(html) == [] and _lint(export) == []


def test_the_library_lists_the_two_looks():
    keys = [v.key for v in library.versions(TENANT) if v.cartridge == "landing"]
    assert "landing/cinematic" in keys and "landing/buybox" in keys
    for look in ("cinematic", "buybox"):
        info = library.describe(TENANT, library.find(TENANT, f"landing/{look}"))
        assert info["notes"].startswith("Built from blocks")
        assert library.arm_for(library.find(TENANT, f"landing/{look}"), TENANT) == f"landing:{look}"


def test_the_library_sha_covers_the_look_json_and_its_blocks(monkeypatch, tmp_path):
    version = library.find(TENANT, "landing/buybox")
    before = library.version_sha(TENANT, version)
    fake = tmp_path / "catalog"
    fake.mkdir()
    for p in catalog.CATALOG_DIR.iterdir():
        if p.is_file():
            (fake / p.name).write_bytes(p.read_bytes())
    (fake / "trust_bar.html").write_text("changed")
    monkeypatch.setattr(catalog, "CATALOG_DIR", fake)
    assert library.version_sha(TENANT, version) != before


# ---------------------------------------------------------------------------
# Imported looks may include blocks
# ---------------------------------------------------------------------------

def test_the_look_importer_accepts_a_block_include_and_refuses_others():
    from harness import look_import

    demo = look_import.load_demo(TENANT, "landing")
    contract = look_import.contract("landing", TENANT, demo)
    schema = look_import.load_schema("landing", TENANT)
    from harness import heroes

    # an imported look is one file: the shipped look with its hero written back in (cycle 94)
    base = heroes.standalone_source((LOOKS_DIR / "open" / "template.html").read_text(), "landing", "open")
    assert "blocks" in contract["names"] and "trust_bar" in contract["data"]["blocks"]
    assert look_import.static_errors(base, "landing", contract_=contract, schema=schema) == []
    assert look_import.look_spec_for(base) == {"blocks": ["faq"]}
    # the trust bar block covers the trust line the look no longer prints
    with_bar = base.replace('      {% if ld.trust %}\n      <ul class="ld-trust">{% for t in ld.trust %}<li>{{ t.text }}</li>'
                            '{% endfor %}</ul>\n      {% endif %}\n', '      {% include "blocks/trust_bar.html" %}\n')
    assert with_bar != base
    assert look_import.static_errors(with_bar, "landing", contract_=contract, schema=schema) == []
    bad = base.replace('{% include "blocks/faq.html" %}', '{% include "blocks/carousel.html" %}')
    assert any("not allowed in a look" in e for e in look_import.static_errors(bad, "landing", contract_=contract,
                                                                                schema=schema))
    errors, html = look_import.validate_template(with_bar, "landing", "bar-demo", TENANT, demo)
    assert errors == [] and 'data-look-pattern="trust_bar ' in html


def test_the_facts_pack_carries_the_model_rows_only_when_a_look_uses_model_grid():
    from types import SimpleNamespace

    from harness import pipeline
    from harness.ground import LocalFactsSource
    from tests.test_quiz import FUJI_SLUG
    from tests.test_render import AD_BRIEF as RENDER_BRIEF

    source = LocalFactsSource(TENANT.claims_dir)
    plain = source.facts_for(FUJI_SLUG, RENDER_BRIEF)
    assert "models" not in plain
    pack = source.facts_for(FUJI_SLUG, RENDER_BRIEF, include_models=True)
    ids = {c["id"] for c in pack["verified_claims"]}
    assert pack["models"] and all(r["price_claim_id"] in ids for r in pack["models"])
    prices = [float(re.sub(r"[^\d.]", "", r["price_text"])) for r in pack["models"]]
    assert prices == sorted(prices)
    state = SimpleNamespace(selected=["landing"], landing_look="cinematic", tenant=TENANT)
    assert pipeline._landing_look_uses(state, "stat_strip") and not pipeline._landing_look_uses(state, "model_grid")
    assert not pipeline._landing_look_uses(SimpleNamespace(selected=["listicle"], landing_look="cinematic",
                                                           tenant=TENANT), "stat_strip")


def test_block_markers_carry_the_cycle_90_pattern_names_where_one_exists():
    from harness.look_design import PATTERN_NAMES

    pairs = {"stat_strip": "stat-strip", "hotspot_image": "hotspots", "model_grid": "product-grid",
             "trust_bar": "trust-bar", "feature_grid": "icon-row"}
    for block_id, name in pairs.items():
        assert name in PATTERN_NAMES
        text = (catalog.CATALOG_DIR / f"{block_id}.html").read_text()
        assert f'data-look-pattern="{block_id} {name}"' in text
