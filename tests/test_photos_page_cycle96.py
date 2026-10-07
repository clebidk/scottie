"""Cycle 96: the Photos page -- add, tag, curate and use library photos from
the review site (harness/site_photos.py, harness/photo_uploads.py).

Offline: the model is a FakeClient, the reference strips are stubbed, the
photos are made with Pillow. The real tenant's brand folder (with its
photo-library.json) is copied into tmp_path, so the committed manifest is
never written.
"""
import base64
import hashlib
import io
import json
import shutil

import pytest
from PIL import Image

from harness import brand_import, images, jobs, serve, site, site_photos
from harness import photo_library as pl
from harness import photo_uploads as pu
from tests.conftest import FakeClient, FakeResponse, json_response
from tests.support import TENANT

REVIEWER = "caleb@peaksaunas.com"
PASSWORD = "pw-cycle-96"
FUJI = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
REAL_MANIFEST = pl.manifest_path(TENANT)
REAL_SHA = hashlib.sha256(REAL_MANIFEST.read_bytes()).hexdigest()


def _jpeg(size=(1800, 1000), color=(150, 110, 70), seed=0):
    im = Image.new("RGB", size, color)
    px = im.load()
    for x in range(0, size[0], 7):
        for y in range(0, size[1], 7):
            if (x * 7 + y * 3 + seed) % 11 < 5:
                px[x, y] = (240, 235, 225)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _tag_answer(product="fuji", shot="exterior"):
    return json_response({"product": product, "product_confidence": 0.9, "candidates": [product],
                          "model_agnostic": False, "setting": "indoor", "shot": shot,
                          "features_visible": ["glass-door", "wood-grain"], "has_text_overlay": False, "people": 0,
                          "description": "A cedar cabin in a living room.", "blurry": False, "cluttered": False,
                          "product_reason": "door and bench match"})


def _look_answer(shot="hero-wide", quality=5):
    return json_response({"name": "fuji-living-room", "alt": "A cedar cabin in a living room.", "tags": [],
                          "shot": shot, "quality": quality, "focal": [0.4, 0.55]})


def _auth(email=REVIEWER):
    return {"Authorization": "Basic " + base64.b64encode(f"{email}:{PASSWORD}".encode()).decode()}


@pytest.fixture
def library(tmp_path, monkeypatch):
    """The tenant's brand folder copied (the real manifest is never
    written), an empty derivatives folder, no reference downloads."""
    brand = tmp_path / "brand"
    shutil.copytree(TENANT.brand_dir, brand)
    monkeypatch.setattr(type(TENANT), "brand_dir", property(lambda self: brand))
    files = tmp_path / "files"
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(files))
    (files / TENANT.name).mkdir(parents=True)
    monkeypatch.setattr(pl, "build_reference_images", lambda tenant, **kw: [])
    yield files / TENANT.name
    assert hashlib.sha256(REAL_MANIFEST.read_bytes()).hexdigest() == REAL_SHA


@pytest.fixture
def client(library, monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


def _upload(client, files, *, csrf=None, model=""):
    data = {"csrf": site.csrf_token(REVIEWER) if csrf is None else csrf, "model": model,
            "photos": [(io.BytesIO(b), name) for name, b in files]}
    return client.post("/photos/upload", data=data, headers=_auth(), content_type="multipart/form-data")


def _post_edit(client, pid, **form):
    data = {"csrf": site.csrf_token(REVIEWER), **form}
    return client.post(f"/photos/{pid}/edit", data=data, headers=_auth())


def _run_job(monkeypatch, *answers):
    fake = FakeClient([FakeResponse(a, input_tokens=2000, output_tokens=300) for a in answers])
    monkeypatch.setattr(pu, "make_client", lambda: fake)
    seen = []
    real = jobs.set_progress
    monkeypatch.setattr(jobs, "set_progress", lambda t, job_id, step: (seen.append(step), real(t, job_id, step)))
    job = jobs.run_one(TENANT, log=lambda *_: None)
    return job, seen, fake


def _added(client, monkeypatch, *, name="living-room.jpg", data=None, model="fuji", answers=None):
    data = data or _jpeg()
    assert _upload(client, [(name, data)], model=model).status_code == 303
    job, seen, _fake = _run_job(monkeypatch, *(answers or (_tag_answer(), _look_answer())))
    assert job["state"] == "done", job["reason"]
    return pl.photo_id(data), job, seen


def _row(pid):
    return pl.photos_by_id(pl.load_manifest(TENANT))[pid]


# ---------------------------------------------------------------------------
# Upload -> job -> library row
# ---------------------------------------------------------------------------

def test_the_page_and_the_upload_need_the_login_and_the_form_token(client):
    assert client.get("/photos").status_code == 401
    assert client.get("/photos", headers=_auth("nobody@example.com")).status_code == 401
    assert _upload(client, [("a.jpg", _jpeg())], csrf="nope").status_code == 403
    assert client.post(f"/photos/{pl.ID_PREFIX}000000000001/edit", data={"action": "excluded", "value": "1"},
                       headers=_auth()).status_code == 403
    assert jobs.list_jobs(TENANT) == []
    page = client.get("/photos", headers=_auth()).get_data(as_text=True)
    assert f'name="csrf" value="{site.csrf_token(REVIEWER)}"' in page
    assert 'name="photos" multiple' in page and 'class="drop"' in page
    for endpoint in ("photos_home", "photos_upload", "photo_edit", "photo_thumb"):
        assert endpoint in site.ENDPOINTS
    for endpoint in ("photos_upload", "photo_edit", "photos_pull_drive"):
        assert endpoint in site.CSRF_ENDPOINTS


def test_an_upload_becomes_a_job_that_imports_measures_and_tags(client, monkeypatch, library):
    data = _jpeg()
    resp = _upload(client, [("IMG_2041.jpg", data)], model="fuji")
    assert resp.status_code == 303 and "/photos?job=" in resp.headers["Location"]
    job = jobs.list_jobs(TENANT)[0]
    assert (job["type"], job["state"], job["target"]) == ("photos_import", "queued", pu.JOB_TARGET)
    pending = pu.pending_dir(TENANT, job["payload"]["batch"])
    assert sorted(p.name for p in pending.iterdir()) == ["001-IMG_2041.jpg", "batch.json"]
    page = client.get(f"/photos?job={job['id']}", headers=_auth())
    assert 'http-equiv="refresh"' in page.get_data(as_text=True)          # progress refreshes the page
    assert "waiting for the worker" in page.get_data(as_text=True)
    assert any(r["action"] == "photos_upload" and r["detail"]["files"] == ["IMG_2041.jpg"]
               for r in jobs.audit_rows(TENANT))

    done, seen, fake = _run_job(monkeypatch, _tag_answer(), _look_answer())
    assert done["state"] == "done", done["reason"]
    assert seen == ["importing 1 photo", "measuring", "0 of 1 tagged", "1 of 1 tagged"]
    assert not pending.exists()
    assert len(fake.messages.calls) == 2                       # pass 1 agreed with the label: no verify call
    pid = pl.photo_id(data)
    row = _row(pid)
    assert (row["source_name"], row["origin"], row["added_by"], row["label_model"]) == \
        ("IMG_2041.jpg", "upload", REVIEWER, "fuji")
    assert (library / f"{pid}.jpg").exists()
    assert row["width"] == 1600 and row["height"] == 889        # the 1600 px derivative
    it = row["image_tags"]
    assert it["measured"] == images.MEASURE_VERSION and it["orientation"] == "landscape"
    assert it["fullbleed_ok"] is True and it["mood"] in images.MOODS
    assert (it["shot"], it["quality"], it["focal"], it["model"]) == ("hero-wide", 5, {"x": 0.4, "y": 0.55}, "fuji")
    assert row["tags"]["product"] == "fuji" and row["decision"]["folder_model"] == "fuji"
    result = done["result"]
    assert (result["added"], result["tagged"], result["untagged"]) == (1, 1, 0)
    assert result["cost_usd"] > 0                                # costed like every model call
    job_page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "Add photos" in job_page and "1 of 1 tagged" in job_page and "/photos" in job_page
    grid = client.get("/photos", headers=_auth()).get_data(as_text=True)
    assert f'id="p-{pid}"' in grid and "hero-wide" in grid and "quality 5" in grid and "Fuji" in grid
    thumb = client.get(f"/photos/thumb/{pid}", headers=_auth())
    assert thumb.status_code == 200 and thumb.mimetype == "image/jpeg"


def test_the_resolver_picks_a_freshly_uploaded_photo(client, monkeypatch):
    pid, _job, _seen = _added(client, monkeypatch)
    facts = {"product": {"name": "Fuji", "slug": FUJI}, "assets": []}
    slot = {"name": "hero", "want": {"shot": ["hero-wide"], "fullbleed_ok": True}, "n": 1, "last_resort": None}
    result = images.fill_slots([slot], facts, TENANT)[0]
    assert [p["id"] for p in result["picks"]] == [pid] and result["picks"][0]["via"] == "want"
    # the cycle 75 pool sees it too (ground.facts_for reads the same manifest)
    own, _agnostic = pl.photos_for_product(pl.load_manifest(TENANT), "fuji", tenant=TENANT)
    assert [p["id"] for p in own] == [pid]
    # excluded on the page -> the resolver drops it at once, no restart
    assert _post_edit(client, pid, action="excluded", value="1").status_code == 303
    assert images.fill_slots([slot], facts, TENANT)[0]["picks"] == []


def test_a_second_upload_of_the_same_photo_is_not_added_twice(client, monkeypatch):
    data = _jpeg(seed=3)
    pid, _job, _seen = _added(client, monkeypatch, data=data)
    assert _upload(client, [("again.jpg", data)]).status_code == 303
    job, seen, fake = _run_job(monkeypatch)
    assert job["state"] == "done" and job["result"]["existing"] == 1 and job["result"]["added"] == 0
    assert fake.messages.calls == [] and seen[-1] == "1 of 1 tagged"      # already tagged: no model call
    assert [p["id"] for p in pl.load_manifest(TENANT)["photos"]].count(pid) == 1


def test_the_daily_cap_stops_tagging_but_keeps_the_photos(client, monkeypatch):
    from harness import budget as budget_mod

    def refuse(*a, **k):
        raise budget_mod.BudgetExceeded("daily cap $5.00 reached", "daily_cap")

    monkeypatch.setattr(budget_mod, "reserve_spend", refuse)
    data = _jpeg(seed=5)
    assert _upload(client, [("a.jpg", data)]).status_code == 303
    job, _seen, fake = _run_job(monkeypatch)
    assert job["state"] == "failed" and "daily cap" in job["reason"] and "0 of 1 tagged" in job["reason"]
    assert fake.messages.calls == []
    row = _row(pl.photo_id(data))
    assert row["tags"] is None and row["image_tags"]["measured"] == images.MEASURE_VERSION


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def test_uploads_are_rate_limited_like_ad_uploads(client):
    for i in range(site.UPLOADS_PER_WINDOW):
        assert _upload(client, [(f"{i}.jpg", _jpeg((40, 30), seed=i))]).status_code == 303
    resp = _upload(client, [("late.jpg", _jpeg((40, 30), seed=99))])
    assert resp.status_code == 429 and "Wait a few minutes" in resp.get_data(as_text=True)
    assert len(jobs.list_jobs(TENANT)) == site.UPLOADS_PER_WINDOW


@pytest.mark.parametrize("files,status,needle", [
    ([("notes.txt", b"hello")], 400, "not a photo this page takes"),
    ([("fake.jpg", b"\x89PNG\r\n\x1a\n" + b"0" * 64)], 400, "does not match its extension"),
    ([("ok.jpg", None), ("big.jpg", "big")], 413, "40 MB limit"),
])
def test_bad_files_are_refused_and_nothing_is_kept(client, monkeypatch, files, status, needle):
    monkeypatch.setattr(pu, "MAX_FILE_BYTES", 40 * pu.MB)
    real = [(n, _jpeg((60, 40)) if b is None else (b"\xff\xd8\xff" + b"0" * (40 * pu.MB + 10)) if b == "big" else b)
            for n, b in files]
    resp = _upload(client, real)
    assert resp.status_code == status and needle in resp.get_data(as_text=True)
    assert jobs.list_jobs(TENANT) == []
    pending = TENANT.jobs_dir / pu.PENDING_DIRNAME
    assert not pending.exists() or not any(pending.iterdir())


def test_the_batch_limits(client, monkeypatch):
    monkeypatch.setattr(pu, "MAX_BATCH_FILES", 2)
    resp = _upload(client, [(f"{i}.jpg", _jpeg((30, 20), seed=i)) for i in range(3)])
    assert resp.status_code == 413 and "At most 2 photos at once" in resp.get_data(as_text=True)
    monkeypatch.setattr(pu, "MAX_BATCH_FILES", 30)
    monkeypatch.setattr(pu, "MAX_BATCH_BYTES", 10_000)
    resp = _upload(client, [(f"{i}.jpg", _jpeg((300, 200), seed=i)) for i in range(4)])
    assert resp.status_code == 413 and "together" in resp.get_data(as_text=True)
    assert jobs.list_jobs(TENANT) == []


def test_heic_is_refused_with_a_message_when_pillow_cannot_read_it(client, monkeypatch):
    monkeypatch.setattr(pu, "heic_supported", lambda: False)
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"0" * 100
    resp = _upload(client, [("IMG_1.HEIC", heic)])
    assert resp.status_code == 400 and "cannot read HEIC" in resp.get_data(as_text=True)
    assert pu.sniff_photo(heic) == ".heic"


# ---------------------------------------------------------------------------
# The grid and the edits
# ---------------------------------------------------------------------------

def test_the_grid_shows_every_photo_with_its_tags_flags_and_use(client, monkeypatch):
    pid, _job, _seen = _added(client, monkeypatch)
    run = TENANT.out_dir / "20261007-120000-fuji"
    run.mkdir(parents=True)
    (run / ".image-selection.json").write_text(json.dumps(
        {"listicle": [pid], "slots": {"landing": [{"slot": "hero", "picks": [{"id": pid}]}]}}))
    body = client.get("/photos", headers=_auth()).get_data(as_text=True)
    total = len(pl.load_manifest(TENANT)["photos"])
    assert total == 67                                              # the 66 committed photos + the new one
    assert body.count('class="ph-card') == site_photos.PER_PAGE and "Showing <b>67</b>" in body
    assert client.get("/photos?page=2", headers=_auth()).get_data(as_text=True).count('class="ph-card') == 7
    assert "Used in" in body and "/gen/20261007-120000-fuji/listicle" in body
    assert "glass-door" in body and "full-bleed ok" in body and "Coverage" in body
    assert "What to shoot next" in body and "1600 px wide" in body and "old logo" in body
    assert 'aria-current="page">Library photos' in body and 'href="/images"' in body
    # filters: shot, model, flag
    only = client.get("/photos?shot=hero-wide&model=fuji", headers=_auth()).get_data(as_text=True)
    assert only.count('class="ph-card') == 1 and f'id="p-{pid}"' in only
    excluded = client.get("/photos?flag=excluded", headers=_auth()).get_data(as_text=True)
    assert f'id="p-{pid}"' not in excluded
    ai = client.get("/photos?flag=ai", headers=_auth()).get_data(as_text=True)
    assert ai.count('class="ph-card') == sum(1 for p in pl.load_manifest(TENANT)["photos"] if p["ai_generated"])
    newest = client.get("/photos?sort=newest", headers=_auth()).get_data(as_text=True)
    assert newest.index(f'id="p-{pid}"') < newest.index('class="ph-card', newest.index(f'id="p-{pid}"') + 10)
    assert client.get("/images", headers=_auth()).status_code == 200      # the second tab still works


def test_each_edit_updates_the_manifest_and_writes_one_audit_line(client, monkeypatch):
    pid, _job, _seen = _added(client, monkeypatch)
    back = "model=fuji"
    resp = _post_edit(client, pid, action="save", features="bench, red light panel", shot="room", mood="dark",
                      model="everest", back=back)
    assert resp.status_code == 303 and resp.headers["Location"].endswith(f"/photos?model=fuji#p-{pid}")
    row = _row(pid)
    assert row["tags"]["features_visible"] == ["red-light-panel", "bench"]
    assert (row["image_tags"]["shot"], row["image_tags"]["shot_source"]) == ("room", "person")
    assert (row["image_tags"]["mood"], row["image_tags"]["mood_source"]) == ("dark", "person")
    assert row["tags"]["product"] == "everest" and row["image_tags"]["model"] == "everest"
    actions = [r["action"] for r in jobs.audit_rows(TENANT) if r["target"] == f"photo:{pid}"]
    assert sorted(actions) == ["photo_model", "photo_mood", "photo_shot", "photo_tags"]
    # an unchanged save writes nothing
    _post_edit(client, pid, action="save", features="red-light-panel, bench", shot="room", mood="dark",
               model="everest")
    assert len([r for r in jobs.audit_rows(TENANT) if r["target"] == f"photo:{pid}"]) == 4
    for action, value, check in (("excluded", "1", lambda r: r["excluded"] is True),
                                 ("old_mark", "1", lambda r: r["tags"]["old_logo_visible"] is True),
                                 ("excluded", "0", lambda r: r["excluded"] is False)):
        assert _post_edit(client, pid, action=action, value=value).status_code == 303
        assert check(_row(pid))
    actions = [r["action"] for r in jobs.audit_rows(TENANT) if r["target"] == f"photo:{pid}"]
    assert actions[:3] == ["photo_excluded", "photo_old_mark", "photo_excluded"]
    # the model and the mark are kept when the photo is tagged again (cycle 75 force re-tag)
    from tests.test_first_screen_cycle79 import _Log
    from harness.budget import Budget
    pl.tag_photos(TENANT, client=FakeClient([_tag_answer(product="fuji")]), model="claude-sonnet-5",
                  budget=Budget(), log=_Log(), refs=[], ids={pid})
    row = _row(pid)
    assert row["tags"]["product"] == "everest" and row["tags"]["old_logo_visible"] is True
    assert row["tags"]["features_visible"] == ["red-light-panel", "bench"]


def test_a_bad_edit_is_refused_and_writes_nothing(client, monkeypatch):
    pid, _job, _seen = _added(client, monkeypatch)
    before = pl.manifest_path(TENANT).read_bytes()
    resp = _post_edit(client, pid, action="save", features="bench, hot tub")
    assert resp.status_code == 400 and "Not a tag this library uses: hot tub" in resp.get_data(as_text=True)
    for form in ({"action": "save", "model": "jacuzzi"}, {"action": "save", "shot": "selfie"},
                 {"action": "focal", "x": "2", "y": "0.5"}, {"action": "nope"}):
        assert _post_edit(client, pid, **form).status_code == 400
    assert pl.manifest_path(TENANT).read_bytes() == before
    assert _post_edit(client, f"{pl.ID_PREFIX}ffffffffffff", action="excluded", value="1").status_code == 404


def test_a_click_on_the_photo_sets_the_focal_point_and_measuring_again_keeps_it(client, monkeypatch, library):
    pid, _job, _seen = _added(client, monkeypatch)
    body = client.get("/photos", headers=_auth()).get_data(as_text=True)
    assert f'data-focal="f-{pid}"' in body and f'id="f-{pid}"' in body and "closest('.ph-img[data-focal]')" in body
    assert _post_edit(client, pid, action="focal", x="0.731", y="0.2").status_code == 303
    it = _row(pid)["image_tags"]
    assert (it["focal"], it["focal_source"]) == ({"x": 0.73, "y": 0.2}, "person")
    assert any(r["action"] == "photo_focal" and r["detail"] == {"x": 0.73, "y": 0.2} for r in jobs.audit_rows(TENANT))
    new, _counts = images.measure_manifest(pl.load_manifest(TENANT), library)
    assert pl.photos_by_id(new)[pid]["image_tags"]["focal"] == {"x": 0.73, "y": 0.2}
    assert images.crop_window(1600, 889, "1:1", {"x": 0.73, "y": 0.2})[0] > 600   # the crop follows it


def test_delete_moves_the_file_to_the_trash_and_restore_brings_it_back(client, monkeypatch, library):
    pid, _job, _seen = _added(client, monkeypatch)
    assert _post_edit(client, pid, action="delete").status_code == 303
    row = _row(pid)
    assert row["excluded"] is True and row["removed"]["by"] == REVIEWER
    assert not (library / f"{pid}.jpg").exists() and (library / ".trash" / f"{pid}.jpg").exists()
    assert f'id="p-{pid}"' not in client.get("/photos", headers=_auth()).get_data(as_text=True)
    trash = client.get("/photos?flag=removed", headers=_auth()).get_data(as_text=True)
    assert f'id="p-{pid}"' in trash and "Restore" in trash
    assert client.get(f"/photos/thumb/{pid}", headers=_auth()).mimetype == "image/jpeg"
    assert _post_edit(client, pid, action="excluded", value="0").status_code == 400     # restore first
    assert _post_edit(client, pid, action="restore").status_code == 303
    row = _row(pid)
    assert "removed" not in row and row["excluded"] is False and (library / f"{pid}.jpg").exists()
    actions = [r["action"] for r in jobs.audit_rows(TENANT) if r["target"] == f"photo:{pid}"]
    assert actions[:2] == ["photo_restore", "photo_delete"]


def test_a_web_edit_during_a_tag_job_is_not_lost(client, monkeypatch):
    """The job saves only the fields it sets, under the manifest lock."""
    data = _jpeg(seed=9)
    pid = pl.photo_id(data)
    assert _upload(client, [("a.jpg", data)]).status_code == 303

    class EditingMessages:
        def __init__(self):
            self.answers = [_tag_answer(), _look_answer()]
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 2:      # between the product tag and the look tag
                pu.edit_photo(TENANT, pid, "excluded", {"value": "1"}, by="someone@peaksaunas.com")
            return FakeResponse(self.answers.pop(0))

    fake = type("C", (), {})()
    fake.messages = EditingMessages()
    monkeypatch.setattr(pu, "make_client", lambda: fake)
    job = jobs.run_one(TENANT, log=lambda *_: None)
    assert job["state"] == "done", job["reason"]
    row = _row(pid)
    assert row["excluded"] is True and row["tags"]["product"] == "fuji"


def test_pull_from_drive_imports_new_files_into_the_same_job(client, monkeypatch, tmp_path):
    body = client.get("/photos", headers=_auth()).get_data(as_text=True)
    assert "Pull from Drive" not in body                                  # no folder configured
    monkeypatch.setattr(pu, "drive_folder", lambda tenant: "1FolderIdFolderIdFolderId0")
    assert "Pull from Drive" in client.get("/photos", headers=_auth()).get_data(as_text=True)
    data = _jpeg(seed=11)
    drive_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123"
    monkeypatch.setattr(brand_import, "enumerate_source", lambda drive_folder: [
        {"id": drive_id, "name": "porch dusk.jpg", "local_path": None, "folder_path": ("Fuji",)},
        {"id": "x" * 25, "name": "notes.pdf", "local_path": None, "folder_path": ()}])

    def download(entries, directory, log=None):
        for entry in entries:
            path = directory / entry["name"]
            path.write_bytes(data)
            yield dict(entry, path=path)

    monkeypatch.setattr(brand_import, "download_incoming", download)
    resp = client.post("/photos/pull-drive", data={"csrf": site.csrf_token(REVIEWER)}, headers=_auth())
    assert resp.status_code == 303
    job, _seen, _fake = _run_job(monkeypatch, _tag_answer(), _look_answer())
    assert job["state"] == "done", job["reason"]
    assert job["result"]["added"] == 1 and job["result"]["origin"] == "drive"
    row = _row(pl.photo_id(data))
    assert (row["drive_id"], row["source_name"], row["origin"]) == (drive_id, "porch dusk.jpg", "drive")
    assert any(r["action"] == "photos_pull_drive" for r in jobs.audit_rows(TENANT))


def test_the_sidebar_names_one_photos_page():
    from harness import site_ui

    photos = [item for _section, items in site_ui.NAV for item in items if item[1] == "Photos"]
    assert len(photos) == 1 and photos[0][0] == "photos_home"
    assert {"photos_home", "image_library", "image_library_product"} <= set(photos[0][3])
    assert not [item for _section, items in site_ui.NAV for item in items if item[1] == "Images"]
