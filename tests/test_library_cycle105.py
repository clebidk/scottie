"""Cycle 105: a Library card shows one picture and one variants line; the
variant strip is hidden until that line or the picture is clicked. The
picture is the newest real run's phone screen when `harness library build`
cached one (landing / listicle), else the offline reference render.

No network and no model call: versions and runs are written by hand.
"""
import json

import pytest

from harness import library, site_ui
from tests.support import TENANT
from tests.test_library_cycle99 import PNG, _built, _card
from tests.test_listicle_site_cycle69 import _auth, client  # noqa: F401 (client is a fixture)


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    monkeypatch.setattr(library, "find_browser", lambda: None)


def _run(run_id, *, look="open", cartridge="landing", at="2026-10-10T12:00:00", chosen=2, shot=b"",
         dry_run=False):
    """A run folder as `harness run` leaves it, with the design gate's
    chosen phone screenshot when `shot` is given."""
    run_dir = TENANT.out_dir / run_id
    page = run_dir / cartridge
    page.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"run_id": run_id, "state": "generated", "dry_run": dry_run, "history": [{"state": "generated", "at": at}]}))
    (page / "page.json").write_text(json.dumps({"look": look, "headline": "Real headline"}))
    (page / "index.html").write_text("<html><body>real page</body></html>")
    if shot:
        (page / "design").mkdir()
        (page / "design" / "scores.json").write_text(json.dumps({"chosen": chosen, "score": 74}))
        (page / "design" / f"variant-{chosen}.png").write_bytes(shot)
    return run_dir


def _build_pictures():
    """library.build with every version up to date: only the prune and the
    real-run pictures happen. Returns (summary, log lines)."""
    lines = []
    summary = library.build(TENANT, only=["landing", "listicle"], log=lines.append)
    return summary, lines


@pytest.fixture
def _all_up_to_date(monkeypatch):
    monkeypatch.setattr(library, "_up_to_date", lambda *_a: True)


def test_the_strip_is_collapsed_behind_one_variants_line(client):  # noqa: F811
    _built("landing/open", thumb=True)
    _built("landing/open/split", thumb=True)
    _built("listicle/open/story/reasons", thumb=True)
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    by_key = {c["key"]: c for c in library.structures(TENANT)}
    card = _card(body, "landing/open")
    n = len(by_key["landing/open"]["variants"])
    # the line: a toggle that falls back to the review link without script
    assert (f'<a class="lib-toggle" href="/library/landing/open/review" target="_blank" rel="noopener" '
            f'data-lib-toggle aria-expanded="false" aria-controls="libs-landing-open">{n} variants</a>') in card
    # the strip and the chips are inside a hidden block after it
    more = card.index('<div class="lib-more" id="libs-landing-open" hidden>')
    assert more < card.index('class="lib-strip"') and more < card.index('id="libv-landing-open-split"')
    assert more < card.index('class="lib-offs"')
    # the picture opens it too; one picture, the buttons stay
    assert 'aria-hidden="true" data-lib-toggle aria-controls="libs-landing-open">' in card
    assert card.count('class="poster lib-poster"') == 1
    assert "Open review" in card and "Generate with this" in card
    # a listicle look counts first screens and styles
    listicle = by_key["listicle/open"]["variants"]
    screens = len({v["first_screen"] for v in listicle})
    assert f'>{screens} first screens · 5 styles</a>' in _card(body, "listicle/open")
    # a structure with one variant has no line and no hidden block
    article = _card(body, "article/default")
    assert "data-lib-toggle" not in article and "lib-more" not in article
    # the click handler opens the block inside the card
    assert "closest('[data-lib-toggle]')" in site_ui.JS and "querySelector('.lib-more')" in site_ui.JS


def test_a_card_with_a_real_run_uses_its_picture(client, _all_up_to_date):  # noqa: F811
    _built("landing/open", thumb=True)
    _built("landing/cinematic", thumb=True)
    _run("20261008-older", at="2026-10-08T09:00:00", shot=PNG + b"older")
    _run("20261010-newest", at="2026-10-10T12:00:00", shot=PNG + b"newest")
    _run("20261011-dryrun", at="2026-10-11T12:00:00", shot=PNG + b"fake", dry_run=True)   # a FakeClient run
    summary, lines = _build_pictures()
    folder = TENANT.library_dir / "landing" / "open"
    assert (folder / library.REAL_THUMB).read_bytes() == PNG + b"newest"
    assert json.loads((folder / library.REAL_META).read_text())["run_id"] == "20261010-newest"
    assert "landing/open: picture from real run 20261010-newest (design variant-2.png)" in lines
    assert "landing/cinematic: picture from the offline render (no real run of this look)" in lines
    assert summary["real"] == 1
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    card = _card(body, "landing/open")
    assert '<img class="poster lib-poster" src="/library/landing/open/real-thumb"' in card
    assert ">from a real run · 2026-10-10</p>" in card
    assert '<p class="small muted lib-current" hidden>' in card   # the offline label waits for a variant pick
    # the variant strip keeps the offline thumbnails
    assert 'data-thumb="/library/landing/open/thumb"' in card
    # no real run: the offline render and its label
    other = _card(body, "landing/cinematic")
    assert '<img class="poster lib-poster" src="/library/landing/cinematic/thumb"' in other
    assert "from a real run" not in other and "real-thumb" not in other
    # the route answers for the cached picture only
    shot = client.get("/library/landing/open/real-thumb", headers=_auth())
    assert shot.status_code == 200 and shot.mimetype == "image/png" and shot.get_data() == PNG + b"newest"
    assert client.get("/library/landing/cinematic/real-thumb", headers=_auth()).status_code == 404
    assert client.get("/library/landing/no-such-look/real-thumb", headers=_auth()).status_code == 404


def test_a_newer_run_refreshes_the_picture(_all_up_to_date):
    _run("20261010-first", at="2026-10-10T12:00:00", shot=PNG + b"first")
    _build_pictures()
    _summary, lines = _build_pictures()
    assert "landing/open: picture from real run 20261010-first (up to date)" in lines
    _run("20261012-second", at="2026-10-12T08:00:00", chosen=1, shot=PNG + b"second")
    _summary, lines = _build_pictures()
    assert "landing/open: picture from real run 20261012-second (design variant-1.png)" in lines
    assert (TENANT.library_dir / "landing/open" / library.REAL_THUMB).read_bytes() == PNG + b"second"


def test_a_run_without_a_design_screenshot_is_captured(monkeypatch, _all_up_to_date):
    run_dir = _run("20261010-nogate", cartridge="listicle")   # no design gate record
    monkeypatch.setattr(library, "find_browser", lambda: "/bin/chromium")
    calls = []

    def fake_capture(browser, html_path, png_path, **_kw):
        calls.append((browser, html_path))
        png_path.write_bytes(PNG + b"captured")
        return True

    monkeypatch.setattr(library, "capture_thumb", fake_capture)
    summary, lines = _build_pictures()
    assert calls == [("/bin/chromium", run_dir / "listicle" / "index.html")]
    assert "listicle/open: picture from real run 20261010-nogate (captured index.html at 390)" in lines
    assert library.real_picture(TENANT, "listicle/open")["source"] == "captured index.html at 390"
    # without a browser and without a design screenshot the card keeps the offline render
    monkeypatch.setattr(library, "find_browser", lambda: None)
    (TENANT.library_dir / "listicle/open" / library.REAL_THUMB).unlink()
    _summary, lines = _build_pictures()
    assert any(line.startswith("listicle/open: picture from the offline render (real run 20261010-nogate")
               for line in lines)
    assert library.real_picture(TENANT, "listicle/open") is None


def test_a_tenant_look_with_declared_heroes_has_one_variant_per_hero(monkeypatch, tmp_path, client):  # noqa: F811
    folder = tmp_path / "looks" / "landing" / "rivian-letters"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text("x")
    (folder / "library.json").write_text(json.dumps({"title": "Rivian letters", "summary": "Big letters."}))
    (folder / "look.json").write_text(json.dumps({"heroes": ["ad_first", "letters_pill"],
                                                  "default_heroes": ["ad_first"]}))
    monkeypatch.setattr(type(TENANT), "looks_dir", property(lambda self: tmp_path / "looks"))
    keys = [v.key for v in library.versions(TENANT) if v.look == "rivian-letters"]
    assert keys == ["landing/rivian-letters", "landing/rivian-letters/letters_pill"]
    card = {c["key"]: c for c in library.structures(TENANT)}["landing/rivian-letters"]
    assert [v["label"] for v in card["variants"]] == ["Ad first", "Letters and pill"]
    # the card picture is the default first screen's render (the look's own version)
    _built("landing/rivian-letters", thumb=True)
    _built("landing/rivian-letters/letters_pill", thumb=True)
    card = {c["key"]: c for c in library.structures(TENANT)}["landing/rivian-letters"]
    assert card["default"]["key"] == "landing/rivian-letters"
    body = _card(client.get("/library", headers=_auth()).get_data(as_text=True), "landing/rivian-letters")
    assert '<img class="poster lib-poster" src="/library/landing/rivian-letters/thumb"' in body
    assert ">2 variants</a>" in body and 'id="libv-landing-rivian-letters-letters_pill"' in body
