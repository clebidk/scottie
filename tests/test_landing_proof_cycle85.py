"""Cycle 85: the landing cartridge's `proof` look (cartridges/landing/looks/
proof/) -- reviews first, checkmark benefits, trust facts under the CTA, an
icon row -- and its two optional writer fields (benefits, icon_row).

The open look must render exactly as before; the proof look must show only
verified facts; the look must resolve from `--look`, be stamped on page.json
and recorded on an A/B/C variant.
"""
import copy
import json
import re

from evals import fake_run
from harness import abtest, landing, looks, repair, write
from harness.page_body import build_shopify_body
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_landing_cycle82 import PHONE, SRC, WARRANTY_TERM, _keys, _render
from tests.test_landing_cycle82 import _gates as _landing_gates
from tests.test_landing_cycle82 import _facts_pack as _landing_facts_pack

LOOKS_DIR = REPO_ROOT / "cartridges" / "landing" / "looks"
QUOTE = "Assembly took us under an hour and the heat is even."


def _facts_pack():
    """The cycle 82 landing facts pack plus the wood claim the canned proof
    page cites (the offline Peak facts pack has it)."""
    fp = _landing_facts_pack()
    fp["verified_claims"].append({"id": "gbrain-fuji-wood", "category": "spec", "source": SRC,
                                  "text": "Fuji wood: crafted entirely from Canadian Red Cedar — interior AND exterior."})
    return fp


def _proof_page(**changes):
    page = copy.deepcopy(fake_run.LANDING_PROOF_PAGE)
    page["look"] = "proof"
    page.update(changes)
    return page


def _html(tmp_path, page, facts_pack=None):
    return (_render(tmp_path, page=page, facts_pack=facts_pack or _facts_pack()) / "index.html").read_text()


def _gates(page, facts_pack=None):
    return _landing_gates(page, facts_pack or _facts_pack())


def _with_quote_source(fp, text=QUOTE):
    fp = copy.deepcopy(fp)
    fp["review_quotes"] = [{"text": text, "author": "Dana R."}]
    return fp


# ---------------------------------------------------------------------------
# The open look is unchanged
# ---------------------------------------------------------------------------

def test_open_is_the_default_and_renders_the_same_with_or_without_proof_fields(tmp_path):
    base = fake_run.LANDING_PAGE
    default = _html(tmp_path / "a", copy.deepcopy(base))
    explicit = _html(tmp_path / "b", dict(copy.deepcopy(base), look="open"))
    assert default == explicit
    # the open look draws none of the proof look's blocks, even when page.json carries them
    with_fields = _html(tmp_path / "c", dict(copy.deepcopy(fake_run.LANDING_PROOF_PAGE), look="open"))
    for html in (default, with_fields):
        for cls in ("ld-stars", "ld-benefits", "ld-icons", "ld-hq", "ld-act ld-act--proof"):
            assert f'class="{cls}"' not in html
    assert '<p class="ld-label">Reviews</p>' in default


def test_the_dispatcher_picks_the_look_like_longform():
    text = (REPO_ROOT / "cartridges" / "landing" / "template.html").read_text()
    assert '{% extends "looks/" ~ (look or "open") ~ "/template.html" %}' in text
    assert {p.parent.name for p in LOOKS_DIR.glob("*/template.html")} == set(looks.cartridge_looks("landing"))


# ---------------------------------------------------------------------------
# The proof look's render
# ---------------------------------------------------------------------------

def test_the_rating_row_is_the_first_row_of_the_first_screen(tmp_path):
    html = _html(tmp_path, _proof_page())
    stars = html.index('class="ld-stars"')
    assert stars < html.index('class="ld-eyebrow"') < html.index("<h1")
    assert re.search(r'<p class="ld-stars"><span aria-hidden="true">★</span><b>4\.79</b> out of 5, 47 reviews</p>', html)
    # the lower Reviews section moved up: the rating is shown once
    assert '<p class="ld-label">Reviews</p>' not in html and 'class="ld-rating"' not in html


def test_no_rating_claim_means_no_rating_row(tmp_path):
    fp = _facts_pack()
    fp["reviews_summary"] = None
    assert 'class="ld-stars"' not in _html(tmp_path, _proof_page(), facts_pack=fp)


def test_the_benefits_are_check_rows_with_verified_claim_ids(tmp_path):
    page = _proof_page()
    html = _html(tmp_path, page)
    rows = re.findall(r'<li class="ld-benefit"><span class="ld-tick" aria-hidden="true">✓</span><span>([^<]*)</span></li>', html)
    assert rows == [b["text"] for b in page["benefits"]]
    ctx = landing.render_context(page, _facts_pack(), tenant=TENANT, look="proof")
    assert all(b["claim_ids"] for b in ctx["benefits"])
    # right after the first screen, before the reasons (cycle 110)
    assert html.index("</header>") < html.index('class="ld-benefits"') < html.index("Why it works")


def test_a_benefit_row_whose_ids_do_not_verify_is_not_rendered(tmp_path):
    page = _proof_page()
    page["benefits"][0]["claim_ids"] = ["not-a-claim"]
    html = _html(tmp_path, page)
    assert page["benefits"][0]["text"] not in html
    assert html.count('class="ld-benefit"') == len(page["benefits"]) - 1


def test_the_room_photo_follows_the_benefits(tmp_path):
    html = _html(tmp_path, _proof_page(room_photo={"asset_id": "asset-drive-lifestyle-1"}))
    assert html.index('class="ld-benefits"') < html.index('class="ld-room') < html.index("Why it works")
    assert "ld-split" not in html


def test_the_trust_line_sits_under_the_cta_and_the_text_link_is_in_the_closing_band(tmp_path):
    html = _html(tmp_path, _proof_page())
    act = re.search(r'<div class="ld-act ld-act--proof">(.*?)</div>', html, re.S).group(1)
    assert act.index('class="ld-btn"') < act.index('class="ld-trust"') and 'class="ld-text"' not in act
    # cycle 110 (teardown D9): "Text us" moved from the hero to the closing band
    assert html.index('class="ld-close"') < html.index('class="ld-text"')
    trust = re.findall(r"<li>([^<]*)</li>", re.search(r'<ul class="ld-trust">(.*?)</ul>', act).group(1))
    assert trust[:2] == ["Free shipping", WARRANTY_TERM] and len(trust) <= landing.PROOF_TRUST_MAX
    assert "secure" not in act.lower() and "guarantee" not in act.lower()
    # the financing sentence stays verbatim in the price block above the pill
    assert html.index("Financing is available through Bread Pay at checkout.") < html.index('class="ld-act ld-act--proof"')


def test_the_returns_term_only_from_a_short_verified_returns_clause():
    fp = _facts_pack()
    by_id = {c["id"]: c for c in fp["verified_claims"]}
    by_id["returns-policy"]["text"] = "30-day returns; a 25% restocking fee applies."
    assert landing.returns_term(fp, TENANT) == {"text": "30-day returns", "claim_ids": ["returns-policy"]}
    by_id["returns-policy"]["text"] = ("Peak Saunas returns require the sauna to be disassembled and repacked "
                                       "in its original crate.")
    assert landing.returns_term(fp, TENANT) is None
    fp["verified_claims"] = [c for c in fp["verified_claims"] if c["id"] != "returns-policy"]
    assert landing.returns_term(fp, TENANT) is None
    assert [t["text"] for t in landing.proof_trust_items(fp, TENANT)] == ["Free shipping", WARRANTY_TERM]


def test_the_icon_row_draws_only_known_icon_keys(tmp_path):
    page = _proof_page()
    page["icon_row"].append({"icon_key": "rocket", "label": "Fast", "claim_ids": ["spec-fuji-capacity"]})
    html = _html(tmp_path, page)
    cells = re.findall(r'<li class="ld-icon"><svg [^>]*>.*?</svg><span>([^<]*)</span></li>', html)
    assert cells == [c["label"] for c in page["icon_row"][:4]]
    assert "Fast" not in cells
    for svg in re.findall(r"<svg [^>]*>", html):
        assert 'stroke="currentColor"' in svg and 'fill="none"' in svg and 'width="24"' in svg
    # before the closing band
    assert html.index('class="ld-icons"') < html.index('class="ld-close"')


def test_the_template_icon_set_is_exactly_the_gate_icon_set():
    text = (LOOKS_DIR / "proof" / "template.html").read_text()
    block = re.search(r"{% set icons = {(.*?)} %}", text, re.S).group(1)
    assert set(re.findall(r'^\s*"([a-z_]+)":', block, re.M)) == set(landing.ICONS)


def test_the_quote_shows_only_when_it_is_verbatim_in_reviews_summary(tmp_path):
    fp = _with_quote_source(_facts_pack())
    quote = {"text": QUOTE, "author": "Dana R.", "claim_ids": ["reviews-live"]}
    html = _html(tmp_path / "a", _proof_page(proof={"quote": quote}), facts_pack=fp)
    hq = re.search(r'<blockquote class="ld-hq">(.*?)</blockquote>', html, re.S).group(1)
    assert QUOTE in hq and "Dana R." in hq
    assert html.index('class="ld-dek"') < html.index('class="ld-hq"') < html.index('class="ld-price"')

    made_up = dict(quote, text="Best sauna I have ever owned, hands down.")
    html = _html(tmp_path / "b", _proof_page(proof={"quote": made_up}), facts_pack=fp)
    assert 'class="ld-hq"' not in html and "hands down" not in html
    assert 'class="ld-hq"' not in _html(tmp_path / "c", _proof_page())


def test_every_product_link_is_a_counted_cta_and_the_look_has_no_colour_literal():
    text = (LOOKS_DIR / "proof" / "template.html").read_text()
    for a in re.findall(r'<a\b[^>]*href="\{\{\s*page\.cta_url[^>]*>', text):
        assert set(re.search(r'class="([^"]*)"', a).group(1).split()) & set(abtest.CTA_CLASSES)
    css = re.search(r"<style>(.*?)</style>", text, re.S).group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css)


def test_mobile_rules_tap_targets_and_no_box(tmp_path):
    css = re.search(r"<style>(.*?)</style>", (LOOKS_DIR / "proof" / "template.html").read_text(), re.S).group(1)
    proof_css = css.split("/* cycle 85, proof look", 1)[1].split("@media", 1)[0]
    # hairlines and glyphs, never a filled box or a shadow
    assert "background" not in proof_css and "box-shadow" not in proof_css
    # the text link keeps its 44px target; no fixed width wider than a phone
    assert re.search(r"\.ld-text\{[^}]*min-height:44px", css)
    assert not re.search(r"(?<![-\w])width:\s*(?:[4-9]\d\d|\d{4,})px", css)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def test_the_canned_proof_page_passes_every_gate_inside_the_word_range():
    page = _proof_page()
    assert _gates(page) == []
    lo, hi = write.parse_word_range((REPO_ROOT / "cartridges" / "landing" / "cartridge.md").read_text())
    assert lo <= repair.count_words(page) <= hi
    # icon keys are not words
    assert repair.count_words(page) == repair.count_words(
        dict(page, icon_row=[{k: v for k, v in c.items() if k != "icon_key"} for c in page["icon_row"]]))


def test_an_unknown_icon_key_fails():
    page = _proof_page()
    page["icon_row"][0]["icon_key"] = "rocket"
    assert "landing:icon_key:0" in _keys(_gates(page))


def test_an_icon_label_whose_claim_ids_do_not_verify_fails():
    page = _proof_page()
    page["icon_row"][1]["claim_ids"] = ["made-up-claim"]
    assert "landing:icon_claims_unverified:1" in _keys(_gates(page))
    page["icon_row"][1]["claim_ids"] = []
    assert "landing:icon_claims:1" in _keys(_gates(page))


def test_icon_row_count_label_length_and_duplicates():
    page = _proof_page()
    page["icon_row"][1]["icon_key"] = page["icon_row"][0]["icon_key"]
    page["icon_row"][2]["label"] = "Canadian red cedar cabin"
    keys = _keys(_gates(page))
    assert "landing:icon_duplicate:1" in keys and "landing:icon_label_words:2" in keys
    assert "landing:icon_count" in _keys(_gates(_proof_page(icon_row=page["icon_row"][:2])))


def test_benefit_rows_are_short_cited_product_facts():
    page = _proof_page()
    page["benefits"][0]["text"] = "Canadian red cedar used inside and out on every single panel"
    page["benefits"][1]["claim_ids"] = ["shipping-policy"]
    page["benefits"][2]["claim_ids"] = ["nope"]
    keys = _keys(_gates(page))
    assert {"landing:benefit_words:0", "landing:benefit_not_product:1", "landing:benefit_claims_unverified:2"} <= keys
    assert "landing:benefits_count" in _keys(_gates(_proof_page(benefits=page["benefits"][:3])))
    # the tenant's benefit allowlist counts (a trust-category claim)
    ok = _proof_page()
    ok["benefits"][0] = {"text": "A US-owned company", "claim_ids": ["gbrain-allowlist-us-owned"]}
    fp = _facts_pack()
    if "gbrain-allowlist-us-owned" not in {c["id"] for c in fp["verified_claims"]}:
        fp["verified_claims"].append({"id": "gbrain-allowlist-us-owned", "text": "US-owned company.",
                                      "category": "trust", "source": SRC})
    assert _gates(ok, fp) == []


def test_the_open_page_needs_neither_field():
    assert _gates(copy.deepcopy(fake_run.LANDING_PAGE)) == []
    schema = json.loads((REPO_ROOT / "cartridges" / "landing" / "schema.json").read_text())
    assert "benefits" in schema["properties"] and "icon_row" in schema["properties"]
    assert "benefits" not in schema["required"] and "icon_row" not in schema["required"]


# ---------------------------------------------------------------------------
# The look: --look, the writer prompt, page.json, A/B/C
# ---------------------------------------------------------------------------

def test_look_proof_resolves_and_open_stays_the_default():
    assert looks.resolve_look("landing", None, tenant=TENANT) == "open"
    assert looks.resolve_look("landing", "proof", tenant=TENANT) == "proof"
    assert looks.requested_for("landing", "proof") == "proof"
    assert looks.requested_for("listicle", "proof") is None
    assert "proof" in looks.all_looks()


def _system_text(kwargs):
    system = kwargs["system"]
    return system if isinstance(system, str) else " ".join(b.get("text", "") for b in system)


def test_the_writer_is_asked_for_the_proof_blocks_only_in_the_proof_look():
    from tests.test_render import AD_BRIEF

    def request(look):
        md = (REPO_ROOT / "cartridges" / "landing" / "cartridge.md").read_text()
        _schema, kwargs = write.build_initial_write_request(
            cartridge_name="landing", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
            facts_pack=_facts_pack(), model="claude-test", word_range=write.parse_word_range(md),
            tenant=TENANT, landing_look=look,
        )
        return _system_text(kwargs)

    proof, open_ = request("proof"), request("open")
    assert "PROOF look" in proof and '"red_light"' in proof and "icon_row has 3-4 cells" in proof
    assert "PROOF look" not in open_ and "benefits and icon_row are for the proof look only: omit both." in open_


def test_fake_run_with_look_proof_stamps_and_records_the_look():
    fixture = TENANT.fixtures_dir / "founder-warranty-demo.txt"
    exit_code, run_dir, pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="landing", look="proof")
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == "proof" and len(page["benefits"]) == 5 and len(page["icon_row"]) == 4
    assert json.loads((run_dir / "state.json").read_text())["landing"]["look"] == "proof"
    html = (run_dir / "landing" / "index.html").read_text()
    assert 'class="ld-act ld-act--proof"' in html and html.count('class="ld-icon"') == 4


def test_the_proof_export_keeps_its_invariants(tmp_path):
    out = _render(tmp_path, page=_proof_page(), facts_pack=_facts_pack())
    export, _manifest = build_shopify_body(out)
    assert export.startswith("<style>\n") and export.count("</style>") == 1
    assert not re.search(r'href="https?://(?:www\.)?peaksaunas\.com', export)
    unscoped = [sel for sel, _b in _rules(_css(export)) if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    assert unscoped == []
    assert f'href="sms:{PHONE}?&amp;body=' in export
    assert export.count('class="pk-lp') == 1 and export.count("<svg ") == 4
    html = (out / "index.html").read_text()
    assert _lint(html) == [] and _lint(export) == []
    assert len(re.findall(r"<h1[\s>]", html)) == 1


def test_a_landing_ab_test_can_pit_open_against_proof_and_records_the_look(monkeypatch, tmp_path):
    assert abtest.parse_arm("landing:proof").look == "proof"
    assert abtest.parse_arm("landing:open").id == "landing:open"

    def runner(tenant, input_path, arm, seed):
        run_dir = tmp_path / f"run-{seed}"
        (run_dir / arm.cartridge).mkdir(parents=True)
        (run_dir / arm.cartridge / "index.html").write_text("<html></html>")
        (run_dir / arm.cartridge / "page.json").write_text(json.dumps({"look": arm.look}))
        return 0, run_dir

    monkeypatch.setattr(abtest, "save_test", lambda tenant, rec: None)
    rec = {"seed": 7, "input": "fixture.mov", "variants": []}
    for key, arm in (("A", "landing:open"), ("B", "landing:proof")):
        variant = {"key": key, "arm": arm, "attempts": []}
        rec["variants"].append(variant)
        assert abtest._build_variant(TENANT, rec, variant, runner) == "built"
    assert [(v["cartridge"], v["look"]) for v in rec["variants"]] == [("landing", "open"), ("landing", "proof")]


def test_the_proof_look_drops_the_rating_sentence_quote_and_keeps_the_rating_row(tmp_path):
    """Cycle 82e: run 20261007-195303-...-ybme showed the reviews claim text
    under the subhead, attributed to "Judge.me reviews"."""
    fp = _with_quote_source(_facts_pack())
    rating = fp["reviews_summary"]["text"]
    proof = {"quote": {"text": rating, "author": "Judge.me reviews", "claim_ids": ["reviews-live"]}}
    html = _html(tmp_path / "a", _proof_page(proof=proof), facts_pack=fp)
    assert 'class="ld-hq"' not in html and "Judge.me reviews" not in html
    assert 'class="ld-stars"' in html
    real = {"quote": {"text": QUOTE, "author": "Dana R.", "claim_ids": ["reviews-live"]}}
    assert landing.find_quote_violations(_proof_page(proof=real), fp) == []
    assert QUOTE in _html(tmp_path / "b", _proof_page(proof=real), facts_pack=fp)
