"""Cycle 102d: "Revise this look" (harness/look_revise.py, harness/site.py).

Offline: FakeClient, the fake screenshot browser of the cycle 102 tests.
  - a revise renders the look at 390 and 1280, sends the note as a named miss
    with the source screenshots and the before shots (streamed, Opus), keeps
    the previous template, adds one history line, stores before/after shots;
  - a refused revision (after its repair round) leaves the look unchanged;
  - the site: the box on the import job page and on an imported look's
    Library card (not on a shipped look's), the job, the before/after page,
    the shot route, the form checks, a Library rebuild for an added look.
"""
import json

import pytest

from harness import jobs, look_import, look_revise, site
from tests.conftest import FakeClient
from tests.support import TENANT
from tests.test_look_import_cycle102 import _gen, _png, browser  # noqa: F401 (browser is a fixture)
from tests.test_look_import_cycle87 import BAD_TEMPLATE, REVIEWER, _auth, _valid_template, client  # noqa: F401

NOTE = ("On desktop the body sections sit in a narrow left column while the key facts are centred; give the "
        "body the same centred measure.")
REVISED = _valid_template().replace('{% extends "base.html" %}', '{% extends "base.html" %}{# revised #}', 1)


def _look(slug="rev-look", library=None):
    folder = TENANT.looks_dir / "landing" / slug
    folder.mkdir(parents=True)
    (folder / "template.html").write_text(_valid_template())
    (folder / "source-1.jpg").write_bytes(_png(390, 844))
    (folder / "library.json").write_text(json.dumps({
        "name": slug, "title": "Rev look", "source": {"kind": "images", "origin": "a.png", "images": ["source-1.jpg"]},
        **(library or {})}))
    return folder


def test_a_revise_sends_the_note_as_a_named_miss_and_keeps_the_previous_template(browser):  # noqa: F811
    folder = _look()
    fake = FakeClient([_gen(REVISED)])
    seen = []
    result = look_revise.revise_look(TENANT, cartridge="landing", slug="rev-look", note=f"  {NOTE}\n", by=REVIEWER,
                                     client=fake, progress=seen.append)
    assert seen == ["rendering", "revising", "validating", "storing"]
    assert fake.messages.streamed == [0] and fake.messages.calls[0]["model"] == "claude-opus-5-5"
    call = fake.messages.calls[0]
    assert "## Reference look" not in call["system"] and "Boxless rules" in call["system"]
    content = call["messages"][0]["content"]
    text = content[-1]["text"]
    assert f"- The owner's note: {NOTE}" in text and "named ONE miss" in text and _valid_template() in text
    labels = " ".join(b["text"] for b in content if b["type"] == "text")
    assert "The source page" in labels and "The current look as it renders now" in labels
    assert "390 px wide" in labels and "1280 px wide" in labels
    assert sum(1 for b in content if b["type"] == "image") >= 3       # the source sheet + the before shots
    assert (folder / "template.prev.html").read_text() == _valid_template()
    assert (folder / "template.html").read_text() == REVISED
    for name in look_revise.BEFORE_SHOTS + look_revise.AFTER_SHOTS:
        assert (folder / name).is_file(), name
    lib = json.loads((folder / "library.json").read_text())
    assert len(lib["history"]) == 1 and REVIEWER in lib["history"][0] and "the same centred measure" in lib["history"][0]
    assert lib["revised_at"] and result["history_line"] == lib["history"][0]
    assert result["cost_usd"] == pytest.approx((20000 * 4 + 9000 * 20) / 1e6) and result["attempts"] == 1
    assert result["before"] == list(look_revise.BEFORE_SHOTS) and result["after"] == list(look_revise.AFTER_SHOTS)


def test_a_refused_revision_leaves_the_look_unchanged():
    folder = _look()
    fake = FakeClient([_gen(BAD_TEMPLATE), _gen(BAD_TEMPLATE)])
    with pytest.raises(look_import.LookImportError, match="after 1 repair round.*the look is unchanged") as info:
        look_revise.revise_look(TENANT, cartridge="landing", slug="rev-look", note=NOTE, by=REVIEWER, client=fake)
    assert len(fake.messages.calls) == 2 and "The harness refused this template" in \
        fake.messages.calls[1]["messages"][2]["content"]
    assert info.value.cost_usd > 0
    assert (folder / "template.html").read_text() == _valid_template()
    assert not (folder / "template.prev.html").exists()
    assert "history" not in json.loads((folder / "library.json").read_text())
    with pytest.raises(look_import.LookImportError, match="write what to change"):
        look_revise.revise_look(TENANT, cartridge="landing", slug="rev-look", note="  ", by=REVIEWER, client=fake)


def test_the_site_revises_a_look_and_shows_before_and_after(client, monkeypatch, browser):  # noqa: F811
    _look(library={"library": "added"})
    monkeypatch.setattr(look_revise, "make_client", lambda: FakeClient([_gen(REVISED)]))
    form = {"csrf": site.csrf_token(REVIEWER), "note": NOTE}
    assert client.post("/looks/landing/rev-look/revise", headers=_auth(), data={"note": NOTE}).status_code == 403
    assert client.post("/looks/landing/rev-look/revise", headers=_auth(),
                       data={**form, "note": " "}).status_code == 400
    assert client.post("/looks/landing/no-look/revise", headers=_auth(), data=form).status_code == 404
    resp = client.post("/looks/landing/rev-look/revise", headers=_auth(), data=form)
    job = jobs.list_jobs(TENANT)[0]
    assert resp.status_code == 303 and resp.headers["Location"].endswith(f"/job/{job['id']}")
    assert job["type"] == "look_revise" and job["payload"]["note"] == NOTE
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "done", done["reason"]
    build = jobs.list_jobs(TENANT)[0]
    assert build["type"] == "library_build" and build["payload"]["only"] == ["landing"]   # the card is rebuilt
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert f"Note: {NOTE}" in page and "<figcaption>Before, 390 px</figcaption>" in page
    assert "<figcaption>After, 1280 px</figcaption>" in page
    assert '<img src="/looks/landing/rev-look/shot/revise-before-1280.png"' in page
    assert "template.prev.html" in page and "Revise this look" in page
    shot = client.get("/looks/landing/rev-look/shot/render-1280.png", headers=_auth())
    assert shot.status_code == 200 and shot.mimetype == "image/png"
    assert client.get("/looks/landing/rev-look/shot/template.html", headers=_auth()).status_code == 404
    assert any(r["action"] == "look_revise_done" for r in jobs.audit_rows(TENANT))


def test_a_failed_revise_job_lists_the_errors(client, monkeypatch):  # noqa: F811
    _look()
    monkeypatch.setattr(look_revise, "make_client", lambda: FakeClient([_gen(BAD_TEMPLATE), _gen(BAD_TEMPLATE)]))
    form = {"csrf": site.csrf_token(REVIEWER), "note": NOTE}
    assert client.post("/looks/landing/rev-look/revise", headers=_auth(), data=form).status_code == 303
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "failed" and "tagline" in done["reason"]
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "<li>" in page and "The look is unchanged." in page


def test_the_box_is_on_imported_cards_and_the_import_result_not_on_shipped_cards(client):  # noqa: F811
    _look(library={"library": "added"})
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    card = body[body.index('id="lib-landing-rev-look"'):][:8000]
    assert 'action="/looks/landing/rev-look/revise"' in card and "About $0.40 a revise" in card
    shipped = body[body.index('id="lib-landing-open"'):]
    shipped = shipped[:shipped.index('id="lib-', 10)]
    assert "Revise this look" not in shipped
    job_id = jobs.enqueue(TENANT, "look_import", {"kind": "images", "slug": "rev-look", "cartridge": "landing",
                                                  "by": "t"}, by="t", target="look:landing/rev-look")
    jobs.mark_done(TENANT, job_id, {"slug": "rev-look", "cartridge": "landing", "title": "Rev look"})
    page = client.get(f"/job/{job_id}", headers=_auth()).get_data(as_text=True)
    assert 'action="/looks/landing/rev-look/revise"' in page
