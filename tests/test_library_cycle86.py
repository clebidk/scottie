"""Cycle 86: the Library -- one offline reference render of every page
version (harness/library.py), the /library page on the review site, the
"Generate with this" pre-selection through upload -> inbox -> A/B/C test,
and the library_build job.

No network and no model call: the build itself goes through
evals/fake_run.py's FakeClient, and the A/B/C runner is
tests/test_abtest_cycle67.py's FakeRunner.
"""
import json
import socket
import subprocess
from pathlib import Path

import pytest

from harness import abtest, abtest_inbox, cli, jobs, library, meta_ingest, pipeline
from harness import tenant as tenant_mod
from tests.support import TENANT
from tests.test_abtest_cycle67 import FakeRunner
from tests.test_listicle_site_cycle69 import JPG, _auth, _csrf, _upload, client  # noqa: F401 (client is a fixture)

META_FIELDS = ("cartridge", "look", "first_screen", "style", "title", "summary", "audience_temperature",
               "word_range", "built_at", "sha")


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """No test depends on whether this machine has Chromium."""
    monkeypatch.setattr(library, "find_browser", lambda: None)


def _build(**kw):
    return library.build(TENANT, log=lambda _m: None, **kw)


def _meta(key):
    return json.loads((TENANT.library_dir / key / "meta.json").read_text())


# ---------------------------------------------------------------------------
# 1. what exists
# ---------------------------------------------------------------------------

def test_versions_cover_every_cartridge_look_first_screen_and_style():
    keys = [v.key for v in library.versions(TENANT)]
    assert len(keys) == len(set(keys))
    for cartridge in pipeline.discover_cartridges():
        assert any(k.startswith(f"{cartridge}/") for k in keys), cartridge
    assert {"landing/open", "landing/proof", "longform/open", "longform/classic", "product-page/pdp",
            "product-page/classic", "article/default", "comparison/default", "quiz/default"} <= set(keys)
    listicle_keys = [k for k in keys if k.startswith("listicle/")]
    assert len(listicle_keys) == 3 * 5
    assert "listicle/open/face/myths" in listicle_keys and "listicle/open/display/reasons" in listicle_keys


def test_arm_strings_match_what_the_abtest_library_builds():
    by_key = {v.key: v for v in library.versions(TENANT)}
    assert library.arm_for(by_key["landing/proof"], TENANT) == "landing:proof"
    assert library.arm_for(by_key["article/default"], TENANT) == "article"
    assert library.arm_for(by_key["listicle/open/story/myths"], TENANT) == "listicle:myths:open:story"
    arm = library.arm_from_fields("listicle", "open", "display", "tested", tenant=TENANT)
    assert arm.id == "listicle:tested:open:display"
    for bad in (("landing", "nope", None, None), ("landing", None, "face", None), ("", None, None, None),
                ("listicle", "open", "face", None)):
        with pytest.raises(library.LibraryError):
            library.arm_from_fields(*bad, tenant=TENANT)


# ---------------------------------------------------------------------------
# 2. the build
# ---------------------------------------------------------------------------

def test_build_writes_the_tree_and_meta_for_a_subset(isolated_tenant_paths):
    out_before = Path(TENANT.out_dir)
    summary = _build(only=["landing"])
    assert sorted(summary["built"]) == ["landing/open", "landing/proof"]
    assert summary["failed"] == [] and summary["thumbs"] == 0 and summary["browser"] is None
    assert sorted(p.name for p in TENANT.library_dir.iterdir()) == ["landing"]
    for look in ("open", "proof"):
        folder = TENANT.library_dir / "landing" / look
        assert {"index.html", "review.html", "meta.json"} <= {p.name for p in folder.iterdir()}
        assert not (folder / "thumb.png").exists()
        review = (folder / "review.html").read_text()
        assert 'src="assets/' not in review  # self-contained
        meta = _meta(f"landing/{look}")
        for field in META_FIELDS:
            assert field in meta, field
        assert meta["cartridge"] == "landing" and meta["look"] == look
        assert meta["first_screen"] is None and meta["style"] is None
        assert meta["audience_temperature"] == "warm to hot" and meta["word_range"] == [250, 450]
        assert meta["arm"] == f"landing:{look}" and meta["summary"].startswith("A short, ad-first landing page")
        assert len(meta["sha"]) == 64 and meta["sha"] == library.version_sha(TENANT, library.find(TENANT, f"landing/{look}"))
    # the proof look really rendered the proof template
    assert 'class="ld-benefits' in (TENANT.library_dir / "landing/proof/index.html").read_text()
    assert 'class="ld-benefits' not in (TENANT.library_dir / "landing/open/index.html").read_text()
    # the scratch world is gone: the tenant paths are the test's own again, and no run landed there
    assert Path(TENANT.out_dir) == out_before and not any(out_before.iterdir())


def test_unchanged_versions_are_skipped_and_force_rebuilds():
    _build(only=["landing"])
    first = _meta("landing/proof")["built_at"]
    again = _build(only=["landing"])
    assert again["built"] == [] and sorted(again["skipped"]) == ["landing/open", "landing/proof"]
    # a template change (a different sha) rebuilds only that version
    meta = _meta("landing/open")
    meta["sha"] = "0" * 64
    (TENANT.library_dir / "landing/open/meta.json").write_text(json.dumps(meta))
    changed = _build(only=["landing"])
    assert changed["built"] == ["landing/open"] and changed["skipped"] == ["landing/proof"]
    forced = _build(only=["landing"], force=True)
    assert sorted(forced["built"]) == ["landing/open", "landing/proof"] and forced["skipped"] == []
    assert _meta("landing/proof")["sha"] == library.version_sha(TENANT, library.find(TENANT, "landing/proof"))
    assert first  # built_at was set the first time


def test_face_first_screen_is_marked_not_renderable_offline(monkeypatch):
    wanted = [library.Version("listicle", "open", "face", "myths"), library.Version("listicle", "open", "story", "myths")]
    monkeypatch.setattr(library, "versions", lambda tenant: wanted)
    summary = _build()
    assert summary["built"] == ["listicle/open/story/myths"]
    assert summary["unavailable"] == ["listicle/open/face/myths"]
    face = _meta("listicle/open/face/myths")
    assert "still from the ad" in face["unavailable"] and face["first_screen"] == "face"
    assert not (TENANT.library_dir / "listicle/open/face/myths/review.html").exists()
    story = _meta("listicle/open/story/myths")
    assert story["arm"] == "listicle:myths:open:story" and story["style"] == "myths"
    assert "Myths" in story["headline"]
    # an unavailable version is up to date too (no re-run every build)
    assert _build()["skipped"] == ["listicle/open/face/myths", "listicle/open/story/myths"]


def test_no_browser_means_no_thumbs_and_a_browser_gets_one(monkeypatch):
    _build(only=["landing"])
    assert not list(TENANT.library_dir.rglob("thumb.png"))
    assert _meta("landing/open")["thumb"] is False

    shots = []

    def fake_capture(browser, html_path, png_path):
        shots.append((browser, Path(html_path).name))
        Path(png_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return True

    monkeypatch.setattr(library, "find_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(library, "capture_thumb", fake_capture)
    summary = _build(only=["landing"], force=True)
    assert summary["thumbs"] == 2 and shots == [("/usr/bin/chromium", "review.html")] * 2
    assert (TENANT.library_dir / "landing/proof/thumb.png").exists() and _meta("landing/proof")["thumb"] is True


def test_capture_thumb_stops_a_browser_that_never_exits(tmp_path):
    png = tmp_path / "thumb.png"
    html = tmp_path / "review.html"
    html.write_text("<html></html>")
    seen = {}

    class Hanging:
        def __init__(self, cmd, **_kw):
            seen["cmd"] = cmd
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)  # the file appears, the process stays
            self.done = False

        def poll(self):
            return 0 if self.done else None

        def terminate(self):
            seen["terminated"] = True
            self.done = True

        def wait(self, timeout=None):
            return 0

    assert library.capture_thumb("/usr/bin/chromium", html, png, popen=Hanging, timeout_s=10) is True
    assert seen["terminated"] and "--window-size=390,844" in seen["cmd"]
    assert "--force-device-scale-factor=1" in seen["cmd"] and f"--screenshot={png}" in seen["cmd"]

    def missing(*_a, **_k):
        raise FileNotFoundError("no browser")

    assert library.capture_thumb("/nope", html, tmp_path / "none.png", popen=missing) is False

    class ExitsWithoutWriting(Hanging):
        def __init__(self, cmd, **_kw):
            self.done = True

    stale = tmp_path / "stale.png"
    stale.write_bytes(b"\x89PNG old")  # an older file is never reported as this capture
    assert library.capture_thumb("/usr/bin/chromium", html, stale, popen=ExitsWithoutWriting) is False


def test_the_build_runs_offline_and_puts_everything_back():
    before = (socket.create_connection, tenant_mod.Tenant.__dict__["out_dir"])
    with library.offline_run_env() as scratch:
        assert TENANT.out_dir == scratch / "out" and TENANT.runs_dir == scratch / "runs"
        with pytest.raises(ConnectionError):
            socket.create_connection(("example.com", 443))
    assert (socket.create_connection, tenant_mod.Tenant.__dict__["out_dir"]) == before
    assert not scratch.exists()


def test_cli_library_build(capsys):
    assert cli.main(["library", "build", "--tenant", TENANT.name, "--only", "landing"]) == 0
    assert "2 built" in capsys.readouterr().out
    assert cli.main(["library", "build", "--tenant", TENANT.name, "--only", "landing"]) == 0
    assert "2 up to date" in capsys.readouterr().out
    assert cli.main(["library", "build", "--tenant", TENANT.name, "--only", "no-such-page"]) == 1


# ---------------------------------------------------------------------------
# 3. tenant-level imported looks
# ---------------------------------------------------------------------------

def _tenant_looks(monkeypatch, tmp_path):
    root = tmp_path / "looks"
    bold = root / "landing" / "bold"
    bold.mkdir(parents=True)
    (bold / "template.html").write_text("{% extends 'base.html' %}")
    (bold / "library.json").write_text(json.dumps({
        "name": "bold", "title": "Bold storefront landing", "summary": "Imported from the store theme.",
        "source": "https://example.com/theme", "added_at": "2026-10-07T10:00:00"}))
    clash = root / "landing" / "proof"  # a harness look name: the harness look wins
    clash.mkdir()
    (clash / "template.html").write_text("x")
    (root / "landing" / "notemplate").mkdir()
    monkeypatch.setattr(type(TENANT), "looks_dir", property(lambda self: root))


def test_tenant_look_folders_are_listed_and_described(monkeypatch, tmp_path, client):  # noqa: F811
    _tenant_looks(monkeypatch, tmp_path)
    landing = [v for v in library.versions(TENANT) if v.cartridge == "landing"]
    assert [v.key for v in landing] == ["landing/open", "landing/proof", "landing/bold"]
    assert landing[-1].tenant_look
    summary = _build(only=["landing"])
    assert summary["unavailable"] == ["landing/bold"] and sorted(summary["built"]) == ["landing/open", "landing/proof"]
    meta = _meta("landing/bold")
    assert meta["title"] == "Bold storefront landing" and meta["notes"] == "Imported from the store theme."
    assert meta["summary"].startswith("A short, ad-first landing page") and meta["summary"].endswith(meta["notes"])
    assert meta["source"] == "tenant" and meta["imported_from"] == "https://example.com/theme"
    assert meta["arm"] is None and "cannot render" in meta["unavailable"]
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    card = body[body.index('id="lib-landing-bold"'):]
    card = card[:card.index('id="lib-', 10)] if 'id="lib-' in card[10:] else card
    assert "Bold storefront landing" in card and "Imported look" in card and "Generate with this" not in card
    assert "Imported from the store theme." in card and "Not renderable offline" in card


# ---------------------------------------------------------------------------
# 4. the /library page
# ---------------------------------------------------------------------------

def test_library_page_lists_every_version_grouped_by_cartridge(client):  # noqa: F811
    _build(only=["landing"])
    resp = client.get("/library", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert library.NOTE.replace("'", "&#x27;") in body
    assert 'href="/library"' in body and ">Library<" in body  # the nav entry
    for version in library.versions(TENANT):
        assert f'id="lib-{version.key.replace("/", "-")}"' in body, version.key
    groups = [body.index(f'id="lib-group-{c}"') for c in pipeline.discover_cartridges()]
    assert groups == sorted(groups)
    # each card sits inside its own cartridge's group
    land, nxt = body.index('id="lib-group-landing"'), body.index('id="lib-group-listicle"')
    assert land < body.index('id="lib-landing-proof"') < nxt
    assert "A short, ad-first landing page" in body[land:nxt]  # the cartridge's purpose, once per group
    # built cards open the framed review route; unbuilt ones say so
    assert 'href="/library/landing/proof/review"' in body
    assert 'href="/library/article/default/review"' not in body and "Not built yet" in body
    review = client.get("/library/landing/proof/review", headers=_auth())
    assert review.status_code == 200 and review.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in review.headers["Content-Security-Policy"]
    assert review.get_data() == (TENANT.library_dir / "landing/proof/review.html").read_bytes()
    assert client.get("/library/../../etc/review", headers=_auth()).status_code == 404
    assert client.get("/library/landing/nope/review", headers=_auth()).status_code == 404
    assert client.get("/library/landing/proof/thumb", headers=_auth()).status_code == 404  # no browser, no thumb
    assert client.get("/library").status_code == 401
    # "Generate with this" carries the pre-selection
    assert 'href="/upload?cartridge=landing&amp;look=proof"' in body
    assert 'href="/upload?cartridge=listicle&amp;look=open&amp;hero_style=display&amp;style=myths"' in body


def test_library_cards_show_a_thumb_when_there_is_one(client):  # noqa: F811
    _build(only=["landing"])
    (TENANT.library_dir / "landing/open/thumb.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    assert 'src="/library/landing/open/thumb"' in body and 'src="/library/landing/proof/thumb"' not in body
    resp = client.get("/library/landing/open/thumb", headers=_auth())
    assert resp.status_code == 200 and resp.mimetype == "image/png"


def test_upload_form_takes_the_pre_selection_and_the_test_pins_variant_a(monkeypatch, client):  # noqa: F811
    form = client.get("/upload?cartridge=listicle&look=open&hero_style=display&style=myths", headers=_auth())
    html = form.get_data(as_text=True)
    assert form.status_code == 200 and "listicle:myths:open:display" in html
    for name, value in (("cartridge", "listicle"), ("look", "open"), ("hero_style", "display"), ("style", "myths")):
        assert f'<input type="hidden" name="{name}" value="{value}">' in html
    bad = client.get("/upload?cartridge=landing&look=nope", headers=_auth())
    assert bad.status_code == 400 and "cannot be built" in bad.get_data(as_text=True)

    resp = _upload(client, name="Pinned Ad", extra={"cartridge": "listicle", "look": "open",
                                                    "hero_style": "display", "style": "myths"})
    assert resp.status_code == 303
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).items()[0]
    assert item["pinned_arm"] == "listicle:myths:open:display"
    assert jobs.audit_rows(TENANT)[0]["detail"]["pinned_arm"] == "listicle:myths:open:display"
    refused = _upload(client, name="Bad Pin", extra={"cartridge": "quiz", "look": "proof"})
    assert refused.status_code == 400 and len(meta_ingest.Inbox(TENANT.meta_inbox_dir).items()) == 1

    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    runner = FakeRunner()
    monkeypatch.setattr(abtest, "default_runner", runner)
    done = jobs.run_one(TENANT)
    assert done["type"] == "create_test" and done["state"] == "done", done
    rec = abtest.load_test(TENANT, done["result"]["test_id"])
    assert rec["pinned_arm"] == "listicle:myths:open:display"
    assert rec["variants"][0]["key"] == "A" and rec["variants"][0]["arm"] == "listicle:myths:open:display"
    assert len({v["arm"] for v in rec["variants"]}) == 3
    assert runner.calls[0][0] == "listicle:myths:open:display"


def test_abtest_from_inbox_honours_a_pinned_version(monkeypatch):
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    for ad_id, pin in (("111", "landing:proof"), ("222", "landing:gone")):
        directory = inbox.item_dir(ad_id)
        directory.mkdir(parents=True)
        (directory / f"meta-{ad_id}.jpg").write_bytes(JPG)
        inbox.write({"ad_id": ad_id, "ad_name": f"Ad {ad_id}", "media_type": "image",
                     "media_file": f"meta-{ad_id}.jpg", "source": "meta", "state": "new", "reason": "",
                     "history": [], "pinned_arm": pin})
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    runner = FakeRunner()
    summary = abtest_inbox.from_inbox(TENANT, by="caleb@peaksaunas.com", runner=runner, log=lambda _m: None)
    assert summary["built"] == ["111"] and summary["failed"] == ["222"]
    rec = abtest.load_test(TENANT, inbox.read("111")["test_id"])
    assert rec["variants"][0]["arm"] == "landing:proof"
    assert inbox.read("222")["state"] == "failed" and "pinned page version" in inbox.read("222")["reason"]


# ---------------------------------------------------------------------------
# 5. the job
# ---------------------------------------------------------------------------

def test_rebuild_button_queues_one_library_job(client):  # noqa: F811
    assert client.post("/library/rebuild", data={}, headers=_auth()).status_code == 403
    resp = client.post("/library/rebuild", data={"csrf": _csrf()}, headers=_auth())
    assert resp.status_code == 303
    [job] = jobs.list_jobs(TENANT)
    assert resp.headers["Location"].endswith(f"/job/{job['id']}")
    assert job["type"] == "library_build" and job["payload"]["force"] is True and job["target"] == "library"
    again = client.post("/library/rebuild", data={"csrf": _csrf()}, headers=_auth())
    assert again.headers["Location"].endswith(f"/job/{job['id']}") and len(jobs.list_jobs(TENANT)) == 1
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    assert f'href="/job/{job["id"]}"' in body and "Rebuild queued" in body
    assert jobs.audit_rows(TENANT)[0]["action"] == "library_build"


def test_library_build_job_runs_through_the_worker(client):  # noqa: F811
    job_id = jobs.enqueue(TENANT, "library_build", {"by": "caleb@peaksaunas.com", "only": ["landing"]},
                          by="caleb@peaksaunas.com", target=library.JOB_TARGET)
    done = jobs.run_one(TENANT, log=lambda _m: None)
    assert done["id"] == job_id and done["state"] == "done", done
    assert done["result"]["built"] == 2 and done["result"]["failed"] == [] and done["result"]["browser"] is False
    assert (TENANT.library_dir / "landing/proof/review.html").exists()
    page = client.get(f"/job/{job_id}", headers=_auth()).get_data(as_text=True)
    assert "Rebuild library" in page and "2 built" in page and 'href="/library"' in page


def test_library_build_job_fails_when_a_version_fails(monkeypatch):
    monkeypatch.setattr(library, "_render_one", lambda *a, **k: (None, "fake run exit 2: gate STOP"))
    jobs.enqueue(TENANT, "library_build", {"by": "x@y", "only": ["landing"]}, by="x@y", target=library.JOB_TARGET)
    done = jobs.run_one(TENANT, log=lambda _m: None)
    assert done["state"] == "failed" and "landing/open: fake run exit 2" in done["reason"]


def test_review_site_startup_queues_a_build_only_when_the_library_is_missing():
    job_id = library.queue_if_missing(TENANT)
    assert job_id and jobs.get_job(TENANT, job_id)["type"] == "library_build"
    assert library.queue_if_missing(TENANT) is None  # one is already waiting
    jobs.mark_done(TENANT, job_id, {})
    TENANT.library_dir.mkdir(parents=True)
    assert library.queue_if_missing(TENANT) is None


def test_cmd_serve_queues_without_waiting(monkeypatch):
    calls = []
    monkeypatch.setenv("REVIEW_PASSWORD", "pw")
    monkeypatch.setattr("flask.Flask.run", lambda self, **kw: calls.append(kw))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("startup must not build"))
    assert cli.main(["serve", "--tenant", TENANT.name, "--port", "4999"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 4999}]
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "library_build" and job["state"] == "queued"
    assert not TENANT.library_dir.exists()
