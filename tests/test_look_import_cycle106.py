"""Cycle 106: the look import tuned on real pages (harness/look_design.py
fidelity, harness/look_import.py prompts).

What is asserted here, offline (FakeClient, no model call):
  - the fidelity score reads the render as a reader sees it (an article
    column of items is opened up) and scores the RHYTHM: where photos and
    buttons sit down the page;
  - a marked element must hold what its pattern is (an author card its
    avatar, a CTA band its button), and a pattern the demo page has no
    data for is not scored;
  - the drafting prompt holds the source section by section and the map
    from source elements to renderer data;
  - the revise round names where the photos and buttons belong;
  - a story page imported as a landing look learns from a hand-made
    listicle look, with the name map between the two contracts.
"""
import json

from harness import look_design, look_import, look_vision
from tests.conftest import FakeClient
from tests.support import TENANT
from tests.test_look_import_cycle102 import _gen, _image_files, _section, _vision, VISION
from tests.test_look_import_cycle87 import _valid_template


def _story(photos=(2, 3, 4, 5), ctas=(4,)):
    """A list article: strip, hero, six content items (some with a photo,
    one with a button band), FAQ, closing band."""
    answer = json.loads(json.dumps(VISION))
    sections = [_section("nav", heading="none"), _section("hero", heading="h1", patterns=["author-card"])]
    for i in range(2, 8):
        sections.append(_section("content", numbered=True, images=1 if i in photos else 0,
                                 cta=1 if i in ctas else 0, patterns=["cta-band"] if i in ctas else []))
    sections += [_section("faq"), _section("cta", bg="dark", cta=1)]
    answer["sections"] = sections
    return look_vision.to_observation(answer, origin="story.example")


def _page(items, extra=""):
    """A rendered look: the wrapper holds a hero, an article column of
    items, a closing band."""
    return ('<html><body><div class="pk-lp adv-listicle"><header><h1>Title</h1>' + extra + '</header>'
            '<div class="col">' + "".join(items) + '</div>'
            '<section><h2>Questions</h2><p>One two three</p></section>'
            '<section style="background:#111;color:#fff"><h2>Close</h2>'
            '<a class="op-btn" href="#x">Go</a></section></div></body></html>')


def _item(photo=False, band=False):
    return ('<section><h2>Item heading</h2><p>Some words in the item body here.</p>'
            + ('<img src="a.jpg" alt="">' if photo else '') + '</section>'
            + ('<section data-look-pattern="cta-band"><a class="op-btn" href="#x">Go</a></section>' if band else ''))


def test_the_score_opens_the_article_column_and_scores_where_the_photos_sit():
    obs = _story()
    with_photos = [_item(photo=i in (2, 3, 4, 5), band=i == 4) for i in range(2, 8)]
    without = [_item(band=i == 4) for i in range(2, 8)]
    good = look_design.fidelity(obs, _page(with_photos), TENANT)
    bare = look_design.fidelity(obs, _page(without), TENANT)
    assert len(good["sections"]["render"]) >= 8            # the items count, not the one column
    assert good["rhythm"] > bare["rhythm"] and good["score"] > bare["score"]
    assert bare["cadence"]["render_photos"].count("p") == 0 and good["cadence"]["source_photos"].count("p") == 4


def test_cadence_match_lines_up_positions_not_dashes():
    assert look_design.cadence_match("-p--p---", "-p--p---") == 1.0
    assert look_design.cadence_match("-p--p---", "--------") == 0.0      # all-dash pages do not look alike
    assert look_design.cadence_match("--------", "------") == 1.0
    assert 0 < look_design.cadence_match("p------p", "p-------") < 1


def test_a_marked_element_must_hold_what_its_pattern_is():
    obs = _story()
    empty_card = '<div data-look-pattern="author-card"><p>By someone</p></div>'
    real_card = '<div data-look-pattern="author-card"><img class="adv-brand-logo" src="a.png" alt=""><p>By someone</p></div>'
    items = [_item(photo=True) for _ in range(6)]
    assert "author-card" in look_design.fidelity(obs, _page(items, empty_card), TENANT)["missing"]
    assert "author-card" in look_design.fidelity(obs, _page(items, real_card), TENANT)["present"]
    no_button = items[:2] + ['<section data-look-pattern="cta-band"><h2>Band</h2></section>'] + items[2:]
    assert "cta-band" in look_design.fidelity(obs, _page(no_button, real_card), TENANT)["missing"]


def test_a_pattern_the_demo_page_cannot_show_is_not_scored():
    assert {"author-card", "photo-per-item", "rating-line", "pull-quote"} <= look_design.unscored_patterns(
        "landing", {"rating": None, "review_quote": None})
    assert look_design.unscored_patterns("listicle", {"rating_line": {"text": "4.9"}, "pull_quote": {"text": "q"}}) \
        == set()
    obs = _story()
    fid = look_design.fidelity(obs, _page([_item(photo=True) for _ in range(6)]), TENANT, unscored={"author-card"})
    assert "author-card" not in fid["missing"] and fid["unscored"] == ["author-card"]


def test_the_drafting_prompt_holds_the_source_section_by_section_and_the_data_map():
    text = look_import.build_user_message("listicle", "x", _story(), images=True)
    assert "## The source, section by section" in text and "S1 | hero | light | [author-card]" in text
    assert "S4 | content | light | 1 photo | button | numbered | [cta-band]" in text
    assert "## Which renderer data fills which source element (listicle)" in text
    assert "first_screen.byline" in text and "section-map:" in text


def test_the_revise_round_names_where_the_photos_and_buttons_belong():
    obs = _story()
    fid = look_design.fidelity(obs, _page([_item() for _ in range(6)]), TENANT, unscored={"rating-line"})
    text = look_import.fidelity_feedback(fid, obs)
    assert "Photos: the source has a photo in 4 of its" in text and "(S2, S3, S4, S5)" in text
    assert "Buttons: the source has a button in sections S4, S9" in text
    assert "- author-card:" in text and "the source shows it in section S1" in text


def test_a_story_page_imported_as_landing_learns_from_a_hand_made_listicle_look(tmp_path, monkeypatch):
    monkeypatch.setattr(look_design, "PACK_DIR", tmp_path / "no-pack")
    folder = TENANT.looks_dir / "listicle" / "hand-story"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text('{% extends "base.html" %}<section data-look-pattern="photo-per-item">'
                                          '{{ first_screen.byline.name }}</section>')
    (folder / "library.json").write_text(json.dumps({
        "name": "hand-story", "summary": "Kept: a photo per item. Dropped: the offer.",
        "source": {"kind": "url", "origin": "x.example", "converted": {"by": "hand", "cycle": "98b"}}}))
    answer = json.loads(json.dumps(VISION))
    answer["sections"] = ([_section("hero", heading="h1")] + [_section("content", images=1) for _ in range(7)]
                          + [_section("cta", bg="dark", cta=1)])
    fake = FakeClient([_vision(answer), _gen(_valid_template()), _gen(_valid_template())])
    result = look_import.import_look(TENANT, cartridge="landing", name="story-look", client=fake,
                                     source={"kind": "images", "paths": _image_files(tmp_path, 1)})
    assert result["exemplar"] == "hand-story"
    system = fake.messages.calls[1]["system"]
    assert "## Exemplar: the hand-made listicle look `hand-story`" in system
    assert "`page.reasons[]` {heading, text, image} -> `page.reasons[]` {headline, body}" in system
    # a landing page that is not a story keeps the landing candidates
    assert not look_design.is_story(look_vision.to_observation(VISION, origin="x"))


def test_an_important_base_rule_beats_a_plain_caps_headline():
    """The tenant's base.css forces `.adv-wrap h1{text-transform:none!important}`: a plain `uppercase` on
    the look's h1 renders in sentence case, so caps-headline is missing until it is `!important` too."""
    obs = look_vision.to_observation({**VISION, "sections": [_section("hero", heading="h1",
                                                                      patterns=["caps-headline"])]}, origin="x")

    def page(rule):
        return ('<html><head><style>.adv-wrap h1{text-transform:none!important}'
                f'.adv-landing .ld-h1.ld-h1{{{rule}}}</style></head><body><main class="adv-wrap">'
                '<div class="pk-lp adv-landing"><section><h1 class="ld-h1" data-look-pattern="caps-headline">'
                'Title</h1></section></div></main></body></html>')

    assert "caps-headline" in look_design.fidelity(obs, page("text-transform:uppercase"), TENANT)["missing"]
    assert "caps-headline" in look_design.fidelity(obs, page("text-transform:uppercase!important"),
                                                   TENANT)["present"]
