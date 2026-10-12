"""Cycle 90: import fidelity (harness/look_design.py, harness/look_import.py).

What is asserted here, offline (FakeClient, no network):
  - the design-level observation of the owner's ten pack pages
    (tests/fixtures/look_import/pack/, copies of peak-lp-pack/*.html):
    the distinctive patterns per page, the per-section CSS summary, the
    12 KB cap, none of the pages' words;
  - the small CSS cascade (specificity, :not(), var() through the tenant
    tokens) and the sanitised CSS for the prompt;
  - the prompt holds the design brief, the CSS and the closest exemplar
    (or the one named with --exemplar);
  - the fidelity score of a template that emits the pattern markers vs one
    that does not; the repair round below 0.7 and its stop after two;
  - the byline's own links are accepted, any other extra href refused with
    the list of allowed hrefs; dark bands as many as the observed page;
  - library.json, the Library card, the job page and the CLI show it.
"""
import json
import shutil

import pytest

from harness import cli, heroes, jobs, library, look_design, look_import
from tests.conftest import FakeClient, FakeResponse
from tests.support import REPO_ROOT, TENANT
from tests.test_look_import_cycle87 import _auth, _post, client  # noqa: F401 (client is a fixture)

PACK = REPO_ROOT / "tests" / "fixtures" / "look_import" / "pack"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "look_import" / "sample-landing.html"
# Cycle 94: the shipped look includes the shared hero partial (harness/heroes/);
# an imported look is one file, so the reference is the look with it inline.
OPEN_LOOK = heroes.standalone_source(
    (REPO_ROOT / "cartridges" / "landing" / "looks" / "open" / "template.html").read_text(), "landing", "open")


def _src(name):
    path = PACK / name
    return {"kind": "upload", "data": path.read_bytes(), "filename": path.name}


def _obs(name):
    return look_import.observe(look_import.load_source(_src(name)), tenant=TENANT)


def _names(obs):
    return {p["name"] for p in obs["patterns"]}


def _client(*texts):
    return FakeClient([FakeResponse(t, input_tokens=20000, output_tokens=8000) for t in texts])


def _marked(template=OPEN_LOOK):
    """The open look plus the 09 (Rivian-style) patterns, each marked:
    display letters around a pill-shaped hero image, check rows, the stat
    strip, the sticky bar."""
    t = template.replace(
        '<header class="ld-hero">',
        '<div class="ld-mark" data-look-pattern="display-letters" aria-hidden="true"><span class="ld-l">PE</span>'
        '<div class="ld-pill" data-look-pattern="pill-image">{% if hero_asset %}{{ render_image_slot(hero_asset) }}'
        '{% endif %}</div><span class="ld-l">AK</span></div>\n  <header class="ld-hero">', 1)
    t = t.replace('<ol class="ld-checks">', '<ol class="ld-checks" data-look-pattern="check-rows">', 1)
    t = t.replace('<ul class="ld-specs">', '<ul class="ld-specs" data-look-pattern="stat-strip">', 1)
    t = t.replace('<div class="ld-bar">', '<div class="ld-bar" data-look-pattern="sticky-bar">', 1)
    t = t.replace(
        ".adv-case-upper .adv-landing .ld-h2{text-transform:uppercase}",
        ".adv-case-upper .adv-landing .ld-h2{text-transform:uppercase}\n"
        ".adv-landing .ld-mark{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;"
        "gap:16px;padding:0 16px}\n"
        ".adv-landing .ld-l{font-family:var(--pk-display-wide);font-size:clamp(60px,17vw,270px);line-height:.8;"
        "color:var(--pk-ink)}\n"
        ".adv-landing .ld-pill{border-radius:999px;overflow:hidden;aspect-ratio:2.2/1}", 1)
    assert t.count("data-look-pattern") == 5
    return t


# ---------------------------------------------------------------------------
# Observation: patterns per pack page
# ---------------------------------------------------------------------------

EXPECTED = {
    "01-ridge.html": {"caps-headline", "dark-hero", "big-numerals"},
    "02-hexclad.html": {"dark-page"},
    "06-eight-sleep.html": {"trust-bar", "hotspots", "sticky-bar"},
    "09-rivian.html": {"display-letters", "pill-image"},
    "10-plunge.html": {"product-grid", "trust-bar", "faq-accordion"},
}


@pytest.mark.parametrize("page", sorted(EXPECTED))
def test_the_observation_detects_each_pack_pages_distinctive_patterns(page):
    obs = _obs(page)
    assert EXPECTED[page] <= _names(obs), _names(obs)
    for p in obs["patterns"]:
        assert p["name"] in look_design.PATTERNS and p["evidence"]


def test_patterns_are_not_found_where_the_page_has_none():
    found = {p.name: _names(_obs(p.name)) for p in sorted(PACK.glob("*.html"))}
    assert len(found) == 10
    assert [n for n, s in found.items() if "display-letters" in s or "pill-image" in s] == ["09-rivian.html"]
    assert [n for n, s in found.items() if "hotspots" in s or "model-picker" in s] == ["06-eight-sleep.html"]
    assert [n for n, s in found.items() if "product-grid" in s] == ["10-plunge.html"]
    assert [n for n, s in found.items() if "dark-page" in s] == ["02-hexclad.html", "07-oura.html"]
    assert "caps-headline" not in found["09-rivian.html"] and "dark-hero" not in found["09-rivian.html"]


def test_every_pack_observation_stays_under_12_kb_and_keeps_no_words():
    for path in sorted(PACK.glob("*.html")):
        obs = _obs(path.name)
        text = json.dumps(obs, separators=(",", ":"))
        assert len(text) <= look_design.MAX_OBSERVATION_BYTES, path.name
        assert obs["version"] == 2 and obs["brief"]
        for s in obs["sections"]:
            assert len(s["skeleton"]) <= 41 and "style" in s
    text = json.dumps(_obs("09-rivian.html")) + json.dumps(_obs("02-hexclad.html"))
    for phrase in ("Infrared saunas, indoors and out", "Heat from every angle", "Hemlock and red cedar",
                   "4,950", "Text us", "peaksaunas.com"):
        assert phrase.lower() not in text.lower(), phrase


def test_the_css_summary_of_a_section():
    ridge = _obs("01-ridge.html")
    hero = ridge["sections"][0]
    assert hero["role"] == "hero" and hero["background"] == "dark"
    st = hero["style"]
    assert st["bg"] == "dark" and st["text"] == "light" and st["contrast"] == "high"
    assert st["max_width"] == 1180 and st["image"] == "overlay" and st["scrim"] is True
    assert st["type"] == {"el": "h1", "size": "clamp(31px,8.4vw,78px)", "transform": "uppercase", "weight": "400",
                          "font": "display-wide"}
    reasons = ridge["sections"][2]["style"]
    assert reasons["layout"] == {"display": "grid", "cols": 1, "cols_desktop": 2}
    assert reasons["radius"] == "4px" and reasons["pad"] == "roomy" and reasons["image"] == "contained"
    rivian = _obs("09-rivian.html")["sections"]
    assert rivian[1]["style"]["radius"] == "pill" and rivian[1]["style"]["image"] == "masked"
    assert rivian[6]["style"]["layout"] == {"display": "grid", "cols": 2, "cols_desktop": 4}
    assert rivian[1]["skeleton"][:6] == ["section.hero 136c", " div.wrap 136c", "  div.mark 5c", "   span.l.l1 2c",
                                         "   div.pill", "    img 1.94:1"]
    plunge = _obs("10-plunge.html")["sections"]
    assert plunge[1]["style"]["scrim"] is True and plunge[2]["style"]["layout"]["cols_desktop"] == 4
    hex_hero = _obs("02-hexclad.html")["sections"][1]["style"]["type"]
    assert hex_hero["size"] == "clamp(34px,9.6vw,84px)" and hex_hero["size_desktop"] == "clamp(44px,5vw,72px)"


def test_the_design_brief_describes_the_composition():
    brief = _obs("09-rivian.html")["brief"]
    assert brief.startswith("A light page in 7 content sections, content up to 1280px wide.")
    assert "giant display letters flank a pill-shaped image window" in brief
    assert "Distinctive:" in brief and "check-mark rows" in brief
    assert _obs("02-hexclad.html")["brief"].startswith("A dark page")
    assert "Above the first screen: benefits (4 columns) on dark [trust-bar]" in _obs("06-eight-sleep.html")["brief"]


def test_the_cascade_follows_specificity_not_and_tenant_tokens():
    html = ('<html><body><div class="a"><p class="b x" id="p">t</p><img class="c" src="i.jpg"></div></body></html>')
    tree = look_import.parse_html(html)
    css = look_design.Css([
        ".a .b{color:#000;font-size:20px}",
        ".b{color:#fff}",                     # lower specificity: loses
        "#p{font-size:30px}",                 # id wins
        ".a > *:not(.c){position:relative}",  # never the img
        ".c{position:absolute;inset:0}",
        ".a{background:var(--ps-band)}",
        ":root{--local:var(--ps-ink-dark)}",
        ".a:hover{color:red}",                # state: skipped
    ], look_design.tenant_tokens(TENANT)).bind(tree)
    p = next(el for el in tree.iter() if el.get("id") == "p")
    img = next(el for el in tree.iter() if el.get("class") == "c")
    assert css.declared(p)["color"] == "#000" and css.declared(p)["font-size"] == "30px"
    assert css.declared(img)["position"] == "absolute"
    assert css.background(p) == (22, 24, 23)            # --ps-band from the tenant's base.css
    assert css.resolve("var(--local)") == "#161817"
    assert look_design.font_token("var(--ps-display-wide)") == "display-wide"
    assert look_design.font_token('"PK Acid Grotesk",sans-serif') == "display-acid"


def test_the_sanitised_css_has_no_fonts_urls_or_colour_literals():
    obs, css = look_import.observe_with_css(look_import.load_source(_src("01-ridge.html")), tenant=TENANT)
    assert css and len(css) <= look_design.MAX_CSS_CHARS + 60
    assert "@font-face" not in css and "url(" not in css
    assert not look_import._HEX_RE.search(css) and "rgba(" not in css
    assert "var(--ps-" in css and "<dark .58>" in css     # the hero scrim rgba(22,24,23,.58)
    assert ".r-num{" in css and "text-transform:uppercase" in css


# ---------------------------------------------------------------------------
# Prompt and exemplars
# ---------------------------------------------------------------------------

@pytest.fixture
def pack_dir(tmp_path, monkeypatch):
    """A pack folder like peak-lp-pack: two pages and their hand-made looks."""
    root = tmp_path / "pack"
    root.mkdir()
    for page in ("09-rivian.html", "10-plunge.html"):
        shutil.copy(PACK / page, root / page)
    for slug, page, template in (("rivian-letters", "09-rivian.html", _marked()),
                                 ("plunge-category", "10-plunge.html",
                                  OPEN_LOOK.replace('{% extends "base.html" %}',
                                                    '{% extends "base.html" %}{# PLUNGE-EXEMPLAR #}', 1))):
        folder = root / "looks" / "landing" / slug
        folder.mkdir(parents=True)
        (folder / "template.html").write_text(template)
        (folder / "library.json").write_text(json.dumps({"name": slug, "cartridge": "landing",
                                                         "source": {"kind": "pack", "origin": page}}))
    monkeypatch.setattr(look_design, "PACK_DIR", root)
    return root


def _import(page, name, client, **kw):
    return look_import.import_look(TENANT, cartridge="landing", name=name, source=_src(page), client=client, **kw)


def test_the_prompt_holds_the_brief_the_css_and_the_closest_exemplar(pack_dir):
    client = _client(_marked())
    result = _import("09-rivian.html", "rivian-test", client)
    assert result["exemplar"] == "rivian-letters"
    call = client.messages.calls[0]
    system, user = call["system"], call["messages"][0]["content"]
    assert "## Exemplar: the hand-made look `rivian-letters`" in system and 'data-look-pattern="pill-image"' in system
    assert "## Reference look" not in system and "Design fidelity (cycle 90)" in system
    assert "Design brief" in user and result["brief"] in user
    assert ".mark{display:grid" in user and "Build observation" in user
    assert "display-letters, pill-image" in user
    assert "`{{ page.cta_url }}`" in user and "`{{ cartridge_data.secondary.href }}`" in user
    assert "byline_html is the ONLY way to mention" in user
    # the Plunge-style page gets the Plunge-style exemplar
    client = _client(_marked(), _marked(), _marked())
    assert _import("10-plunge.html", "plunge-test", client)["exemplar"] == "plunge-category"
    assert "PLUNGE-EXEMPLAR" in client.messages.calls[0]["system"]


def test_the_exemplar_can_be_named_and_an_unknown_one_is_refused_before_any_call(pack_dir):
    client = _client(_marked(), _marked(), _marked())
    assert _import("10-plunge.html", "plunge-test", client, exemplar="rivian-letters")["exemplar"] == "rivian-letters"
    empty = _client()
    with pytest.raises(look_import.LookImportError, match="no exemplar 'nope'.*plunge-category"):
        _import("10-plunge.html", "plunge-two", empty, exemplar="nope")
    assert empty.messages.calls == []


def test_without_an_exemplar_the_default_look_is_the_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(look_design, "PACK_DIR", tmp_path / "no-pack")
    client = _client(_marked())
    result = _import("09-rivian.html", "rivian-test", client)
    assert result["exemplar"] is None
    assert "## Reference look: cartridges/landing/looks/open/template.html" in client.messages.calls[0]["system"]


def test_a_tenant_look_made_by_hand_is_an_exemplar_with_its_stored_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(look_design, "PACK_DIR", tmp_path / "no-pack")
    folder = TENANT.looks_dir / "landing" / "rivian-letters"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text(_marked())
    (folder / "library.json").write_text(json.dumps({"name": "rivian-letters", "source": {"kind": "pack",
                                                                                           "origin": "09-rivian.html"}}))
    (folder / "observation.json").write_text(json.dumps(_obs("09-rivian.html")))
    found = look_design.exemplars(TENANT, "landing")
    assert [e["slug"] for e in found] == ["rivian-letters"]
    assert found[0]["observation"]["patterns"][0]["name"] == "display-letters"
    # an imported (not hand-made) look is never an exemplar
    other = TENANT.looks_dir / "landing" / "auto-look"
    other.mkdir()
    (other / "template.html").write_text(OPEN_LOOK)
    (other / "library.json").write_text(json.dumps({"source": {"kind": "url", "origin": "x"}}))
    assert [e["slug"] for e in look_design.exemplars(TENANT, "landing")] == ["rivian-letters"]


# ---------------------------------------------------------------------------
# Fidelity
# ---------------------------------------------------------------------------

def _render(template, obs):
    demo = look_import.load_demo(TENANT, "landing")
    errors, html = look_import.validate_template(template, "landing", "fid-look", TENANT, demo,
                                                 max_dark=look_import.dark_allowance(obs))
    assert errors == []
    return html


def test_the_fidelity_score_rewards_the_marked_patterns():
    obs = _obs("09-rivian.html")
    marked = look_design.fidelity(obs, _render(_marked(), obs), TENANT)
    plain = look_design.fidelity(obs, _render(OPEN_LOOK, obs), TENANT)
    assert marked["score"] >= look_design.FIDELITY_THRESHOLD > plain["score"]
    assert marked["missing"] == [] and set(marked["present"]) == _names(obs)
    assert set(plain["missing"]) == _names(obs) and plain["present"] == []
    assert marked["order"] == plain["order"] and marked["bands"] == plain["bands"]
    assert plain["sections"]["source"][0] == "hero"


def test_a_marker_on_the_wrong_element_does_not_count():
    obs = _obs("01-ridge.html")
    assert {"dark-hero", "caps-headline"} <= _names(obs)
    # dark-hero on the light first screen, caps-headline on a sentence-case h1
    t = OPEN_LOOK.replace('<header class="ld-hero">', '<header class="ld-hero" data-look-pattern="dark-hero">', 1) \
        .replace('<h1 class="ld-h1 {{ h1_cls }}">', '<h1 class="ld-h1 ld-h1--long" data-look-pattern="caps-headline">',
                 1)
    fid = look_design.fidelity(obs, _render(t, obs), TENANT)
    assert {"dark-hero", "caps-headline"} <= set(fid["missing"])
    # on a dark hero with the upper-case display h1 both count
    t2 = OPEN_LOOK.replace('<header class="ld-hero">', '<header class="ld-hero" data-look-pattern="dark-hero">', 1) \
        .replace('<h1 class="ld-h1 {{ h1_cls }}">', '<h1 class="ld-h1 ld-h1--wide" data-look-pattern="caps-headline">',
                 1).replace(".adv-landing .ld-hero{padding:20px 0 0}",
                            ".adv-landing .ld-hero{padding:20px 0 0;background:var(--pk-dark)}", 1)
    fid = look_design.fidelity(obs, _render(t2, obs), TENANT)
    assert {"dark-hero", "caps-headline"} <= set(fid["present"])


def test_low_fidelity_gets_a_repair_round_naming_the_missing_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr(look_design, "PACK_DIR", tmp_path / "no-pack")
    client = _client(OPEN_LOOK, _marked())
    result = _import("09-rivian.html", "rivian-test", client)
    assert len(client.messages.calls) == 2 and result["attempts"] == 2
    repair = client.messages.calls[1]["messages"]
    assert repair[1]["role"] == "assistant"
    assert "fidelity to the observed page is" in repair[2]["content"]
    assert "- display-letters:" in repair[2]["content"] and "- pill-image:" in repair[2]["content"]
    assert result["fidelity"] >= 0.7 and result["missing_patterns"] == []
    folder = TENANT.looks_dir / "landing" / "rivian-test"
    assert 'data-look-pattern="pill-image"' in (folder / "template.html").read_text()
    library_json = json.loads((folder / "library.json").read_text())
    assert library_json["fidelity"] == result["fidelity"] and library_json["missing_patterns"] == []
    assert library_json["patterns"] == ["display-letters", "pill-image", "sticky-bar", "check-rows", "stat-strip"]


def test_the_repair_rounds_stop_after_two_and_keep_the_best_valid_template(tmp_path, monkeypatch):
    # cycle 102: a draft and ONE revise round (was two repairs)
    monkeypatch.setattr(look_design, "PACK_DIR", tmp_path / "no-pack")
    half = _marked().replace(' data-look-pattern="check-rows"', "").replace(' data-look-pattern="stat-strip"', "")
    client = _client(OPEN_LOOK, half)
    seen = []
    result = _import("09-rivian.html", "rivian-test", client, progress=seen.append)
    assert len(client.messages.calls) == look_import.MAX_CALLS == 2
    assert seen.count("revising") == 1 and seen.count("validating") == 2
    assert result["attempts"] == 2 and result["fidelity"] < 0.75
    assert result["missing_patterns"] == ["check-rows", "stat-strip"]    # the best of the two was stored
    library_json = json.loads((TENANT.looks_dir / "landing" / "rivian-test" / "library.json").read_text())
    assert library_json["fidelity"] == result["fidelity"] and library_json["missing_patterns"] == ["check-rows",
                                                                                                  "stat-strip"]


# ---------------------------------------------------------------------------
# Validator: the byline's links, extra hrefs, dark bands
# ---------------------------------------------------------------------------

def test_the_bylines_own_link_is_accepted_inside_the_page():
    links = look_import.byline_hrefs(TENANT, "landing")
    assert "/pages/austin-laudenslager" in links
    # page 03's refusal: the byline printed inside the wrapper
    t = OPEN_LOOK.replace('  {% if page.checked_claims %}',
                          '  <div class="ld-c">{{ byline_html | safe }}</div>\n  {% if page.checked_claims %}', 1)
    demo = look_import.load_demo(TENANT, "landing")
    errors, html = look_import.validate_template(t, "landing", "by-look", TENANT, demo)
    assert errors == [] and 'href="/pages/austin-laudenslager"' in html


def test_any_other_extra_href_is_refused_with_the_allowed_list():
    demo = look_import.load_demo(TENANT, "landing")
    page = demo["page"]
    from harness import landing
    ctx = landing.render_context(page, demo["facts_pack"], ad_brief=demo["ad_brief"], tenant=TENANT)
    html = (f'<html><body><div class="pk-lp adv-landing"><a class="ld-btn" href="{page["cta_url"]}">'
            f'{page["cta_text"]}</a><a class="ld-text" href="{ctx["secondary"]["href"]}">x</a>'
            '<a href="/pages/austin-laudenslager">Austin</a><a href="/pages/someone-else">Other</a></div></body></html>')
    errors = look_import.cta_errors(html, "landing", page, ctx, look_import.byline_hrefs(TENANT, "landing"))
    assert len(errors) == 1
    assert "'/pages/someone-else'" in errors[0] and "the only hrefs allowed are page.cta_url" in errors[0]
    assert "cartridge_data.secondary.href" in errors[0] and "byline_html (/pages/austin-laudenslager" in errors[0]
    # without the byline's links the founder link is a second target too (the cycle 87 behaviour)
    assert len(look_import.cta_errors(html, "landing", page, ctx)) == 2


def test_dark_bands_are_allowed_as_many_as_the_observed_page_has():
    assert look_import.dark_allowance(_obs("01-ridge.html")) == 2      # the dark hero + the closing band
    assert look_import.dark_allowance(_obs("02-hexclad.html")) is None   # a dark page
    assert look_import.dark_allowance(_obs("09-rivian.html")) == 2      # the full-bleed band + the close
    two = OPEN_LOOK.replace(".adv-landing .ld-hero{padding:20px 0 0}",
                            ".adv-landing .ld-hero{padding:20px 0 0;background:var(--pk-dark)}", 1)
    demo = look_import.load_demo(TENANT, "landing")
    contract_ = look_import.contract("landing", TENANT, demo)
    schema = look_import.load_schema("landing", TENANT)

    def errs(max_dark):
        return look_import.static_errors(two, "landing", contract_=contract_, schema=schema, max_dark=max_dark)
    assert any("ONE dark closing band" in e for e in errs(1))
    assert not any("dark band" in e for e in errs(2)) and not any("dark band" in e for e in errs(None))


def test_the_rules_file_states_the_allowed_hrefs_and_the_patterns():
    rules = look_import.RULES_PATH.read_text()
    assert "byline_html` -- byline_html is the ONLY" not in rules   # one wording, below
    assert "byline_html | safe }}` -- byline_html is the ONLY" in rules
    assert "`{{ cartridge_data.secondary.href }}`" in rules
    for name in look_design.PATTERN_NAMES:
        assert f"`{name}`" in rules, name


# ---------------------------------------------------------------------------
# Library card, job page, CLI
# ---------------------------------------------------------------------------

def test_the_library_card_shows_the_fidelity(client):  # noqa: F811
    folder = TENANT.looks_dir / "landing" / "fid-look"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text(OPEN_LOOK)
    (folder / "library.json").write_text(json.dumps({"name": "fid-look", "title": "Fidelity look",
                                                     "summary": "Imported.", "fidelity": 0.8,
                                                     "source": {"kind": "upload", "origin": "x.html"}}))
    entry = next(e for e in library.entries(TENANT) if e["key"] == "landing/fid-look")
    assert entry["fidelity"] == 0.8
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    card = body[body.index('id="lib-landing-fid-look"'):]
    assert '<p class="small muted lib-fidelity">fidelity 0.8</p>' in card[:4000]
    other = body[body.index('id="lib-landing-open"'):]
    other = other[:other.index('id="lib-', 10)]
    assert "lib-fidelity" not in other


def test_the_job_page_shows_the_brief_the_fidelity_and_the_missing_patterns(client, monkeypatch):  # noqa: F811
    fake = _client(OPEN_LOOK, OPEN_LOOK, OPEN_LOOK)   # valid, but never marks the fixture's FAQ accordion
    monkeypatch.setattr(look_import, "make_client", lambda: fake)
    assert _post(client, {"name": "split-hero", "html": FIXTURE.read_text()}).status_code == 303
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "done", done["reason"]
    result = done["result"]
    assert result["fidelity"] < 0.7 and result["missing_patterns"] == ["faq-accordion"]
    assert result["brief"].startswith("A light page in 5 content sections")
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "<b>Design brief.</b> A light page" in page
    assert f"Fidelity <b>{result['fidelity']:.2f}</b>" in page
    assert "Missing patterns: <code>faq-accordion</code>" in page


def test_the_import_form_takes_an_exemplar(client):  # noqa: F811
    assert 'name="exemplar"' in client.get("/looks/import", headers=_auth()).get_data(as_text=True)
    resp = _post(client, {"name": "split-hero", "url": "https://x.example/", "exemplar": "Not A Slug!"})
    assert resp.status_code == 400 and "is not a look name" in resp.get_data(as_text=True)
    assert _post(client, {"name": "split-hero", "url": "https://x.example/", "exemplar": "rivian-letters"}
                 ).status_code == 303
    assert jobs.list_jobs(TENANT)[0]["payload"]["exemplar"] == "rivian-letters"


def test_the_cli_takes_an_exemplar_and_the_dry_run_prints_the_brief(capsys, monkeypatch, pack_dir):
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("a dry run makes no model call"))
    args = ["look", "import", "--tenant", TENANT.name, "--file", str(PACK / "10-plunge.html"), "--name", "plunge-x",
            "--dry-run"]
    assert cli.main(args) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["brief"] in out.err and "Exemplar: plunge-category" in out.err
    assert cli.main(args + ["--exemplar", "rivian-letters"]) == 0
    assert "Exemplar: rivian-letters" in capsys.readouterr().err
    monkeypatch.setattr(cli, "make_client", lambda: _client(_marked(), _marked(), _marked()))
    assert cli.main(args[:-1]) == 0
    printed = capsys.readouterr().out
    assert "design brief: A light page" in printed and "fidelity " in printed and "exemplar: plunge-category" in printed


def test_an_icon_row_whose_images_have_a_percent_width_is_observed_not_crashed():
    """Cycle 97b: swarva.com's icon row had <img width="100%">; the observer
    raised ValueError on float("100%") and the import died."""
    row = "".join(f'<div><img src="i{i}.svg" width="100%" height="auto"><p>Label {i}</p></div>' for i in range(3))
    html = f"<html><body><section><div class=\"icons\">{row}</div><p>{'word ' * 60}</p></section></body></html>"
    obs = look_import.observe(look_import.load_source(
        {"kind": "upload", "data": html.encode(), "filename": "percent_width_icon.html"}), tenant=TENANT)
    assert "patterns" in obs
