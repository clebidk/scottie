"""Cycle 99: the Library shows one card per structure (a cartridge x look);
every first screen / listicle style of that look is a variant inside the
card. `harness library build` prunes version folders whose version no longer
exists.

No network and no model call: built versions are written by hand (meta.json,
review.html, thumb.png) except where the build itself is under test.
"""
import json

import pytest

from harness import cli, heroes, library, pipeline
from tests.support import TENANT
from tests.test_listicle_site_cycle69 import _auth, _csrf, client  # noqa: F401 (client is a fixture)

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    monkeypatch.setattr(library, "find_browser", lambda: None)


def _built(key, *, thumb=False, **extra):
    """A version folder as the build leaves it."""
    version = library.find(TENANT, key)
    folder = TENANT.library_dir / version.key
    folder.mkdir(parents=True, exist_ok=True)
    meta = dict(library.describe(TENANT, version), sha=library.version_sha(TENANT, version), **extra)
    (folder / "meta.json").write_text(json.dumps(meta))
    if not extra.get("unavailable") and not extra.get("failed"):
        (folder / "index.html").write_text("<html><body>page</body></html>")
        (folder / "review.html").write_text(f"<html><body>review {key}</body></html>")
    if thumb:
        (folder / "thumb.png").write_bytes(PNG)
    return folder


def _card(body, look_key):
    card = body[body.index(f'id="lib-{look_key.replace("/", "-")}"'):]
    return card[:card.index('id="lib-', 10)] if 'id="lib-' in card[10:] else card


def _stale(key):
    folder = TENANT.library_dir / key
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"key": key}))
    (folder / "review.html").write_text("old")
    return folder


# ---------------------------------------------------------------------------
# structures
# ---------------------------------------------------------------------------

def test_versions_group_into_one_structure_per_look():
    cards = library.structures(TENANT)
    keys = [c["key"] for c in cards]
    assert len(keys) == len(set(keys))
    # every version is a variant of exactly one structure, the one for its look
    variant_keys = [v["key"] for c in cards for v in c["variants"]]
    assert sorted(variant_keys) == sorted(v.key for v in library.versions(TENANT))
    for card in cards:
        assert all(v["key"] == card["key"] or v["key"].startswith(card["key"] + "/") for v in card["variants"])
    # landing first, listicle second, the rest in discover order
    cartridges = list(dict.fromkeys(c["cartridge"] for c in cards))
    assert cartridges[:2] == ["landing", "listicle"]
    assert cartridges[2:] == [c for c in pipeline.discover_cartridges() if c not in ("landing", "listicle")]
    by_key = {c["key"]: c for c in cards}
    listicle_open = by_key["listicle/open"]
    assert len(listicle_open["variants"]) == len(heroes.look_heroes("listicle", "open", TENANT)) * 5
    assert listicle_open["title"] == "Listicle, open look" and listicle_open["origin"] == "shipped"
    assert {"Story · myths", "Display · reasons"} <= {v["label"] for v in listicle_open["variants"]}
    landing_open = by_key["landing/open"]
    spec = heroes.look_spec("landing", "open", TENANT)
    assert len(landing_open["variants"]) == 1 + len(set(spec["heroes"]) - set(spec["default_heroes"]))
    assert landing_open["variants"][0]["label"] == "Ad first"   # the look's own first screen
    assert by_key["article/default"]["variants"][0]["label"] == "Default"


def test_origin_tags_and_the_best_scores(monkeypatch, tmp_path):
    root = tmp_path / "looks"
    for slug, source in (("hand-made", {"kind": "url", "origin": "x.co", "converted": {"by": "hand"}}),
                         ("from-pack", {"kind": "pack", "origin": "06.html"}),
                         ("fetched", {"kind": "url", "origin": "y.co"})):
        (root / "landing" / slug).mkdir(parents=True)
        (root / "landing" / slug / "template.html").write_text("x")
        (root / "landing" / slug / "library.json").write_text(json.dumps(
            {"title": f"{slug} title", "summary": f"{slug} summary.", "source": source, "fidelity": 0.7}))
    monkeypatch.setattr(type(TENANT), "looks_dir", property(lambda self: root))
    by_key = {c["key"]: c for c in library.structures(TENANT)}
    assert by_key["landing/hand-made"]["origin"] == "hand-converted"
    assert by_key["landing/from-pack"]["origin"] == "hand-converted"
    assert by_key["landing/fetched"]["origin"] == "imported"
    assert by_key["landing/fetched"]["title"] == "fetched title"
    assert by_key["landing/fetched"]["summary"] == "fetched summary." and by_key["landing/fetched"]["fidelity"] == 0.7
    # the best design score among a look's variants
    _built("landing/open", design_score=61, design_status="fail")
    _built("landing/open/split", design_score=83, design_status="pass")
    card = library.structures(TENANT)[0]
    assert card["key"] == "landing/open" and card["design"]["key"] == "landing/open/split"


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_the_page_shows_one_card_per_look_with_its_variants_inside(client):  # noqa: F811
    _built("landing/open", thumb=True, design_score=70, design_status="pass")
    _built("landing/open/split", thumb=True, design_score=82, design_status="pass")
    _built("landing/open/dark_statement")
    _built("landing/open/caps_over_photo",
           unavailable="not renderable offline: the run rendered the dark_statement first screen instead -- "
                       "the caps_over_photo first screen needs a photo")
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    cards = library.structures(TENANT)
    assert body.count('class="gen lib-card"') == len(cards)
    assert body.count('id="lib-landing-open"') == 1
    card = _card(body, "landing/open")
    # the default variant large, with its links
    assert 'class="shot lib-shot" href="/library/landing/open/review"' in card
    assert '<img class="poster lib-poster" src="/library/landing/open/thumb"' in card
    assert 'href="/upload?cartridge=landing&amp;look=open" data-lib-generate>' in card
    assert ">Shipped</span>" in card and "Landing page, open look" in card
    assert "Design <b>82</b>" in card   # the best of the variants
    # the strip: one small thumbnail per built variant, each swapping the card to itself
    assert 'Showing <b data-lib-current>Ad first</b>' in card
    split = card[card.index('id="libv-landing-open-split"'):]
    split = split[:split.index("</a>")]
    assert 'data-lib-variant' in split and 'data-thumb="/library/landing/open/split/thumb"' in split
    assert 'data-review="/library/landing/open/split/review"' in split
    assert 'data-generate="/upload?cartridge=landing&amp;look=open&amp;hero_style=split"' in split
    assert '<img loading="lazy" src="/library/landing/open/split/thumb"' in split and ">Split</span>" in split
    dark = card[card.index('id="libv-landing-open-dark_statement"'):]
    assert 'data-thumb=""' in dark[:dark.index("</a>")]   # built, no thumbnail
    # not renderable offline: a text chip with the reason, never a variant to swap to
    chip = card[card.index('id="libv-landing-open-caps_over_photo"'):]
    chip = chip[:chip.index("</span>")]
    assert chip.startswith('id="libv-landing-open-caps_over_photo" title="not renderable offline: the run')
    assert "needs a photo" in chip and chip.endswith(">Caps over photo: not renderable offline")
    assert card.count("data-lib-variant") == 3
    # no other card carries a landing/open variant
    for other in cards:
        if other["key"] != "landing/open":
            assert "landing-open-" not in _card(body, other["key"])


def test_the_section_count_lines(client):  # noqa: F811
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    for cartridge in ("landing", "listicle", "article"):
        group = [c for c in library.structures(TENANT) if c["cartridge"] == cartridge]
        n, m = len(group), sum(len(c["variants"]) for c in group)
        line = (f'{n} structure{"s" if n != 1 else ""} &middot; {m} variant{"s" if m != 1 else ""}</span>')
        section = body[body.index(f'id="lib-group-{cartridge}"'):]
        assert line in section[:600], (cartridge, line)
    assert "1 structure &middot; 1 variant</span>" in body   # article


def test_listicle_styles_of_one_first_screen_share_a_chip(client):  # noqa: F811
    why = "not renderable offline: the run rendered the story first screen instead -- the face first screen needs a still"
    for style in ("reasons", "myths"):
        _built(f"listicle/open/face/{style}", unavailable=why)
    _built("listicle/open/story/reasons", thumb=True)
    card = _card(client.get("/library", headers=_auth()).get_data(as_text=True), "listicle/open")
    face = [c for c in card.split('class="chip lib-off"')[1:]
            if c.startswith(' id="libv-listicle-open-face-') and "not renderable" in c[:300]]
    assert len(face) == 1   # the other three face styles are not built here: their own shared chip
    assert f'title="{why} (reasons, myths)">Face \u00b7 2 styles: not renderable offline</span>' in face[0]
    assert "Story \u00b7 reasons" in card and 'data-thumb="/library/listicle/open/story/reasons/thumb"' in card


def test_a_failed_variant_is_a_chip_with_its_reason(monkeypatch, client):  # noqa: F811
    monkeypatch.setattr(library, "_render_one", lambda *a, **k: (None, "fake run exit 2: gate STOP"))
    summary = library.build(TENANT, only=["quiz"], log=lambda _m: None)
    assert summary["failed"] == [["quiz/default", "fake run exit 2: gate STOP"]]
    meta = json.loads((TENANT.library_dir / "quiz/default/meta.json").read_text())
    assert meta["failed"] == "fake run exit 2: gate STOP"
    assert not library._up_to_date(TENANT.library_dir / "quiz/default", meta["sha"])   # retried next build
    card = _card(client.get("/library", headers=_auth()).get_data(as_text=True), "quiz/default")
    assert 'title="failed: fake run exit 2: gate STOP">Default: failed</span>' in card
    assert "Open review" not in card and "lib-shot\" href" not in card


def test_routes_still_answer_per_variant(client):  # noqa: F811
    _built("landing/open/split", thumb=True)
    review = client.get("/library/landing/open/split/review", headers=_auth())
    assert review.status_code == 200 and b"review landing/open/split" in review.get_data()
    thumb = client.get("/library/landing/open/split/thumb", headers=_auth())
    assert thumb.status_code == 200 and thumb.mimetype == "image/png"
    assert client.get("/library/landing/open/nope/review", headers=_auth()).status_code == 404
    assert client.get("/library/landing/open/thumb", headers=_auth()).status_code == 404
    assert client.post("/library/rebuild", data={"csrf": _csrf()}, headers=_auth()).status_code == 303
    page = client.get("/looks/import", headers=_auth())
    assert page.status_code == 200 and "Import a page" in page.get_data(as_text=True)


# ---------------------------------------------------------------------------
# pruning
# ---------------------------------------------------------------------------

def test_prune_removes_folders_of_versions_that_no_longer_exist():
    live = _built("landing/open")
    live_nested = _built("landing/open/split")
    gone_look = _stale("landing/block-cinematic")
    _stale("landing/block-cinematic/split")
    gone_hero = _stale("landing/open/old_hero")
    gone_listicle = _stale("listicle/gone-look/story/reasons")
    scratch = _stale("harness-library-abc/staging/landing/x")
    printed = []
    removed = library.prune(TENANT, log=printed.append)
    assert removed == ["landing/block-cinematic", "landing/open/old_hero", "listicle/gone-look/story/reasons"]
    assert printed == [f"pruned {k}: no such version any more" for k in removed]
    assert not gone_look.exists() and not gone_hero.exists() and not gone_listicle.exists()
    assert not (TENANT.library_dir / "listicle").exists()   # the empty folders above it went too
    assert (live / "review.html").exists() and (live_nested / "review.html").exists()
    assert scratch.exists()   # a build's scratch folder is never pruned
    assert library.prune(TENANT, log=printed.append) == []


def test_the_build_prunes_and_prints_it(capsys):
    stale = _stale("landing/block-buybox")
    gone_quiz = _stale("quiz/old-look")
    assert cli.main(["library", "build", "--tenant", TENANT.name, "--only", "quiz"]) == 0
    out = capsys.readouterr().out
    assert "pruned quiz/old-look: no such version any more" in out and "1 pruned" in out
    assert not gone_quiz.exists()
    assert stale.exists()   # --only quiz leaves the landing folders alone
