"""Cycle 102: import a look from a screenshot or a URL, hand-conversion grade
(harness/look_vision.py, harness/look_import.py, harness/site.py).

What is asserted here, offline (FakeClient, fake HTTP transport, fake DNS, a
fake screenshot browser -- no model call, no network, no Chromium):
  - an image upload on the review site makes a look_import job; the
    observation comes from the (fake) vision answer, the generation gets the
    screenshots, and the stored look waits for "Add to Library";
  - the vision answer is normalised into the HTML observer's shape;
  - merge rules: the images win on order, rhythm and palette, the HTML on
    exact CSS values and font names;
  - a URL is screenshotted at 390 and 1280 from a sanitised copy, and a page
    over the 2 MB HTML cap is imported from the screenshots alone (refused
    when no screenshot can be taken);
  - the revise round runs on low fidelity, with the render beside the
    source, and is skipped when the import has spent its revise cap;
  - `harness look import --image ...`; the result page (source beside the
    render, fidelity, Add to Library / Discard); the Library chip;
  - HEIC is accepted like the Photos page; the upload limits; the sheets;
  - hand-converted tenant looks are exemplars, by the patterns they mark.
"""
import io
import json

import pytest
from PIL import Image

from harness import capture, cli, jobs, library, look_design, look_import, look_vision, photo_uploads, site
from tests.conftest import FakeClient, FakeResponse
from tests.support import TENANT
from tests.test_look_import_cycle87 import (FIXTURE, OPEN_LOOK, REVIEWER, FakeTransport, _auth, _public,
                                            _valid_template, client)  # noqa: F401 (client is a fixture)


def _png(w=390, h=844, colour=(240, 238, 232)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


def _section(role, bg="light", **kw):
    return {"role": role, "what": f"{role} block", "bg": bg, "heading": kw.pop("heading", "h2"),
            "layout": kw.pop("layout", "stack"), "cols_phone": 1, "cols_desktop": kw.pop("cols_desktop", 1),
            "items": kw.pop("items", 0), "numbered": kw.pop("numbered", False), "images": kw.pop("images", 0),
            "image": kw.pop("image", "none"), "radius": "4px", "cta": kw.pop("cta", 0), "words": 40,
            "pinned": kw.pop("pinned", "none"), "patterns": kw.pop("patterns", [])}


# The fixture landing page as a person sees it: the HTML observer's order.
VISION = {
    "ground": "light", "content_width": "wide",
    "palette": [{"role": "ground", "tone": "light", "hue": "white"}, {"role": "accent", "tone": "mid", "hue": "red"},
                {"role": "band", "tone": "dark", "hue": "navy"}],
    "type": {"headline": {"case": "sentence", "weight": "bold", "face": "sans", "size_phone_px": 38},
             "body": {"face": "sans", "size_phone_px": 16}, "accent": "accent phrase in the accent colour"},
    "photo_cadence": "one wide photo in the first screen",
    "cta_cadence": "in the first screen and the closing band",
    "sections": [
        _section("nav", heading="none"),
        _section("hero", heading="h1", layout="split", cols_desktop=2, images=1, image="contained", cta=1),
        _section("benefits", layout="grid", cols_desktop=3, items=3),
        _section("proof"),
        _section("faq", items=3, patterns=["faq-accordion"]),
        _section("cta", bg="dark", cta=1),
        _section("footer", heading="none"),
    ],
    "special": ["a small logo row"],
}


def _vision(answer=None, **usage):
    usage = {"input_tokens": 9000, "output_tokens": 3000, **usage}
    return FakeResponse(json.dumps(answer or VISION), **usage)


def _gen(text):
    return FakeResponse(text, input_tokens=20000, output_tokens=9000)


def _image_files(tmp_path, n=2):
    paths = []
    for i in range(n):
        path = tmp_path / f"shot-{i + 1}.png"
        path.write_bytes(_png(390, 844, (240 - i * 40, 238, 232)))
        paths.append(path)
    return paths


@pytest.fixture
def browser(monkeypatch):
    """A fake headless browser: every probe says the page is 2000 px tall and
    every screenshot writes a PNG of the asked size. Records the calls."""
    calls = []

    def probe(_browser, html_path, *, width, height=844, tenant=None, **_kw):
        calls.append(("probe", width, html_path.read_text()))
        return {"height": 2000}

    def screenshot(_browser, html_path, png_path, *, width, height, tenant=None, screen_height=None, **_kw):
        calls.append(("shot", width, height))
        png_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), (30, 30, 30)).save(png_path)
        return True

    monkeypatch.setattr(look_vision, "find_browser", lambda: "/fake/chrome")
    monkeypatch.setattr(capture, "probe", probe)
    monkeypatch.setattr(capture, "screenshot", screenshot)
    return calls


def _post_images(client, files, name="Shot look", **extra):  # noqa: F811
    data = {"csrf": site.csrf_token(REVIEWER), "cartridge": "landing", "name": name, **extra,
            "images": [(io.BytesIO(body), filename) for filename, body in files]}
    return client.post("/looks/import", data=data, headers=_auth(), content_type="multipart/form-data")


def _blocks(content):
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def _text(content):
    return "".join(b.get("text", "") for b in _blocks(content) if b.get("type") == "text")


def _images(content):
    return [b for b in _blocks(content) if b.get("type") == "image"]


# ---------------------------------------------------------------------------
# The site upload -> job -> observation from the vision answer
# ---------------------------------------------------------------------------

NOTES = "{# look-notes: Kept: the split first screen, the three-column grid, the FAQ rows. Dropped: the logo row. #}"


def test_an_image_upload_creates_a_job_and_the_observation_comes_from_the_vision_answer(client, monkeypatch):  # noqa: F811
    template = _valid_template().replace('{% extends "base.html" %}', '{% extends "base.html" %}\n' + NOTES, 1)
    fake = FakeClient([_vision(), _gen(template)])
    monkeypatch.setattr(look_import, "make_client", lambda: fake)
    resp = _post_images(client, [("top.png", _png()), ("bottom.PNG", _png(colour=(20, 20, 20)))])
    assert resp.status_code == 303
    job = jobs.list_jobs(TENANT)[0]
    assert job["payload"]["kind"] == "images" and job["payload"]["files"] == ["top.png", "bottom.PNG"]
    pending = look_vision.pending_dir(TENANT, job["payload"]["images"])
    assert sorted(p.name for p in pending.iterdir()) == ["01.png", "02.png"]
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "done", done["reason"]
    assert not pending.exists()
    vision, draft = fake.messages.calls
    # cycle 102b: the draft streams (the SDK refuses a long non-streaming call); the vision call does not
    assert fake.messages.streamed == [1]
    # the vision call: two sheets, the role and pattern vocabulary, Opus with adaptive thinking
    assert len(_images(vision["messages"][0]["content"])) == 2
    assert "## Section roles" in vision["system"] and "`faq-accordion`" in vision["system"]
    assert vision["model"] == "claude-opus-5-5" and vision["output_config"] == {"effort": "medium"}
    assert "thinking" not in vision
    # the draft sees the screenshots, then the observation and the hand-conversion instructions (cached)
    content = draft["messages"][0]["content"]
    assert len(_images(content)) == 2 and content[-1]["cache_control"] == {"type": "ephemeral"}
    assert "Observed from: images" in content[-1]["text"] and "PHOTO CADENCE" in content[-1]["text"]
    assert draft["model"] == "claude-opus-5-5" and draft["max_tokens"] == look_import.MAX_OUTPUT_TOKENS
    folder = TENANT.looks_dir / "landing" / "shot-look"
    obs = json.loads((folder / "observation.json").read_text())
    assert obs["observed_from"] == "images" and obs["source"]["kind"] == "images"
    assert [s["role"] for s in obs["sections"]] == [s["role"] for s in VISION["sections"]]
    assert obs["global"]["photo_cadence"] == VISION["photo_cadence"] and obs["patterns"][0]["name"] == "faq-accordion"
    lib = json.loads((folder / "library.json").read_text())
    assert lib["library"] == "pending" and lib["observed_from"] == "images"
    assert lib["summary"].startswith("Kept: the split first screen") and "Dropped: the logo row." in lib["summary"]
    assert lib["source"]["images"] == ["source-1.jpg", "source-2.jpg"] and lib["source_thumb"] == "source-thumb.jpg"
    assert (folder / "source-1.jpg").is_file() and (folder / "source-thumb.jpg").is_file()
    # opus prices: vision 9k/3k + draft 20k/9k
    assert lib["cost_usd"] == pytest.approx((9000 * 4 + 3000 * 20 + 20000 * 4 + 9000 * 20) / 1e6)
    assert done["result"]["observed_from"] == "images" and done["result"]["images"] == 2


def test_the_vision_answer_is_normalised_to_the_observer_shape():
    answer = json.loads(json.dumps(VISION))
    answer["sections"][2]["role"] = "carousel"                                   # unknown -> content
    answer["sections"].insert(3, _section("hero", heading="h1"))                 # a second hero -> content
    answer["sections"].append(_section("cta", cta=1, pinned="bottom", heading="none"))
    answer["sections"][1]["bg"] = "dark"
    obs = look_vision.to_observation(answer, origin="2 screenshots", images=2)
    roles = [s["role"] for s in obs["sections"]]
    assert roles.count("hero") == 1 and roles[2] == "content" and roles[3] == "content"
    names = [p["name"] for p in obs["patterns"]]
    assert "sticky-bar" in names and "dark-hero" in names
    assert obs["global"]["sticky"] == ["bottom"] and obs["global"]["dark_sections"] == 2
    assert obs["global"]["palette"][1] == {"role": "accent", "tone": "mid", "hue": "red", "token": "--pk-red"}
    hero = obs["sections"][1]
    assert hero["style"]["type"] == {"el": "h1", "size": "38px", "weight": "700", "font": "sans"}
    assert hero["style"]["layout"] == {"display": "grid", "cols": 1, "cols_desktop": 2}
    # the shape the rest of the pipeline reads
    assert look_import.summarise(obs).startswith("Learned a")
    assert look_import.dark_allowance(obs) == 2          # the dark hero and the closing band
    assert "Photos: one wide photo" in obs["brief"] and "Buttons:" in obs["brief"]
    with pytest.raises(look_vision.VisionError, match="no sections"):
        look_vision.to_observation({"sections": []}, origin="x")


def test_merge_takes_order_rhythm_and_palette_from_the_images_and_css_values_from_the_html():
    html_obs, _css = look_import.observe_with_css(look_import.load_source(
        {"kind": "upload", "data": FIXTURE.read_bytes(), "filename": FIXTURE.name}), tenant=TENANT)
    answer = json.loads(json.dumps(VISION))
    answer["sections"][3]["bg"] = "dark"           # the screenshot shows the proof band dark
    answer["sections"][4]["patterns"] = []         # the FAQ looked like plain rows in the screenshot
    img = look_vision.to_observation(answer, origin="shots", images=3)
    merged = look_vision.merge(img, html_obs)
    assert [s["role"] for s in merged["sections"]] == [s["role"] for s in img["sections"]]
    assert [s["background"] for s in merged["sections"]] == [s["background"] for s in img["sections"]]
    hero_html = next(s for s in html_obs["sections"] if s["role"] == "hero")
    hero = next(s for s in merged["sections"] if s["role"] == "hero")
    assert hero["style"]["type"] == hero_html["style"]["type"]         # exact CSS from the HTML
    assert hero["skeleton"] == hero_html["skeleton"] and hero["style"]["layout"] == img["sections"][1]["style"]["layout"]
    g = merged["global"]
    assert g["fonts"] == html_obs["global"]["fonts"] and g["max_width_px"] == 1200
    assert g["palette"] == img["global"]["palette"] and g["photo_cadence"] == VISION["photo_cadence"]
    faq = [p for p in merged["patterns"] if p["name"] == "faq-accordion"]
    assert faq and faq[0]["evidence"].startswith("from the HTML")          # an exact HTML pattern is kept
    assert merged["observed_from"] == "images+html" and merged["merge"]["matched_sections"] == 7
    assert merged["source"]["kind"] == "upload" and merged["source"]["images"] == 3


# ---------------------------------------------------------------------------
# A URL: screenshots, and a page over the HTML cap
# ---------------------------------------------------------------------------

def test_a_url_page_is_screenshotted_from_a_sanitised_copy_and_merged(browser):
    page = FIXTURE.read_bytes()
    transport = FakeTransport({"https://shop.example/lp": (200, {"Content-Type": "text/html"}, page),
                               "https://shop.example/assets/site.css": (200, {"Content-Type": "text/css"}, b"")})
    fake = FakeClient([_vision(), _gen(_valid_template())])
    result = look_import.import_look(TENANT, cartridge="landing", name="url-look", client=fake, transport=transport,
                                     resolver=_public, source={"kind": "url", "url": "https://shop.example/lp"})
    assert result["observed_from"] == "images+html"
    staged = [html for kind, _w, html in browser if kind == "probe" and "<base" in html]
    assert staged and '<base href="https://shop.example/lp">' in staged[0]
    assert "<script" not in staged[0] and "onclick" not in staged[0]
    widths = [w for kind, w, *_ in browser if kind == "shot"]
    assert widths[:2] == [390, 1280] and widths[2:] == [390]             # the page twice, then the render
    labels = _text(fake.messages.calls[0]["messages"][0]["content"])
    assert "phone, 390 px wide" in labels and "desktop, 1280 px wide" in labels
    assert ".band{" in fake.messages.calls[1]["messages"][0]["content"][-1]["text"]   # the HTML's CSS too
    lib = result["library"]
    assert lib["render_shot"] == "render-390.png" and lib["source"]["kind"] == "url"
    assert (TENANT.looks_dir / "landing" / "url-look" / "render-390.png").is_file()


def test_a_url_over_the_html_cap_is_imported_from_its_screenshots_alone(browser):
    big = FIXTURE.read_bytes().replace(b"</body>", b"<!--" + b"x" * look_import.MAX_SOURCE_BYTES + b"--></body>")
    assert look_import.MAX_SOURCE_BYTES < len(big) < look_vision.MAX_CAPTURE_BYTES
    transport = FakeTransport({"https://shop.example/big": (200, {"Content-Type": "text/html"}, big)})
    fake = FakeClient([_vision(), _gen(_valid_template())])
    result = look_import.import_look(TENANT, cartridge="landing", name="big-look", client=fake, transport=transport,
                                     resolver=_public, source={"kind": "url", "url": "https://shop.example/big"})
    assert result["observed_from"] == "images" and result["observation"]["observed_from"] == "images"
    assert [u for u, *_ in transport.calls] == ["https://shop.example/big"]     # no CSS fetch: no HTML observation
    assert transport.calls[0][2] == look_vision.MAX_CAPTURE_BYTES


def test_a_url_over_the_cap_with_no_screenshot_is_refused_before_any_model_call():
    big = b"<html><body>" + b"x" * (look_import.MAX_SOURCE_BYTES + 10) + b"</body></html>"
    transport = FakeTransport({"https://shop.example/big": (200, {"Content-Type": "text/html"}, big)})
    fake = FakeClient([])
    with pytest.raises(look_import.LookImportError, match="larger than 2048 KB and no screenshot"):
        look_import.import_look(TENANT, cartridge="landing", name="big-look", client=fake, transport=transport,
                                resolver=_public, source={"kind": "url", "url": "https://shop.example/big"})
    assert fake.messages.calls == []


# ---------------------------------------------------------------------------
# The revise round
# ---------------------------------------------------------------------------

LETTERS = json.loads(json.dumps(VISION))
LETTERS["sections"][1]["patterns"] = ["display-letters", "pill-image"]


def test_low_fidelity_gets_one_revise_round_with_the_render_beside_the_source(tmp_path, browser):
    fake = FakeClient([_vision(LETTERS), _gen(OPEN_LOOK), _gen(_valid_template())])
    seen = []
    result = look_import.import_look(TENANT, cartridge="landing", name="rev-look", client=fake, progress=seen.append,
                                     source={"kind": "images", "paths": _image_files(tmp_path)})
    assert len(fake.messages.calls) == 3 and result["attempts"] == 2
    assert fake.messages.streamed == [1, 2]                  # the draft and the revise round stream
    assert seen.count("revising") == 1 and seen.index("observing") < seen.index("drafting")
    revise = fake.messages.calls[2]["messages"]
    assert [m["role"] for m in revise] == ["user", "assistant", "user"]
    content = revise[2]["content"]
    assert "fidelity to the observed page is" in _text(content) and "- display-letters:" in _text(content)
    assert "Side by side, the first phone screen" in _text(content) and "Your whole render at 390 px" in _text(content)
    assert len(_images(content)) >= 2
    assert look_design.FIDELITY_THRESHOLD == 0.75


def test_the_revise_round_is_skipped_once_the_import_has_spent_its_cap(tmp_path):
    costly = _vision(LETTERS, input_tokens=60000, output_tokens=30000)     # $0.84 before the draft
    fake = FakeClient([costly, _gen(OPEN_LOOK)])
    result = look_import.import_look(TENANT, cartridge="landing", name="cap-look", client=fake,
                                     source={"kind": "images", "paths": _image_files(tmp_path, 1)})
    assert len(fake.messages.calls) == 2 and result["attempts"] == 1
    assert result["fidelity"] < look_design.FIDELITY_THRESHOLD and "display-letters" in result["missing_patterns"]


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------

def test_the_cli_takes_images(capsys, monkeypatch, tmp_path):
    paths = [str(p) for p in _image_files(tmp_path, 2)]
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("a dry run makes no model call"))
    args = ["look", "import", "--tenant", TENANT.name, "--name", "cli-shots"]
    for p in paths:
        args += ["--image", p]
    assert cli.main(args + ["--dry-run"]) == 0
    out = capsys.readouterr()
    assert [s["file"] for s in json.loads(out.out)] == ["source-1.jpg", "source-2.jpg"]
    assert "2 image(s) would go to the vision observation" in out.err
    monkeypatch.setattr(cli, "make_client", lambda: FakeClient([_vision(), _gen(_valid_template())]))
    assert cli.main(args) == 0
    printed = capsys.readouterr().out
    assert "observed from: images" in printed and "fidelity " in printed
    lib = json.loads((TENANT.looks_dir / "landing" / "cli-shots" / "library.json").read_text())
    assert "library" not in lib                     # the CLI adds it to the Library straight away
    with pytest.raises(SystemExit):
        cli.main(args + ["--file", str(FIXTURE)])   # one source kind
    assert cli.main(["look", "import", "--tenant", TENANT.name, "--name", "x-shots", "--image",
                     str(tmp_path / "missing.png")]) != 0


# ---------------------------------------------------------------------------
# The result page, Add to Library, Discard, the Library chip
# ---------------------------------------------------------------------------

def _run_image_job(client, monkeypatch, name="Card look"):  # noqa: F811
    monkeypatch.setattr(look_import, "make_client", lambda: FakeClient([_vision(), _gen(_valid_template())]))
    assert _post_images(client, [("top.png", _png())], name=name).status_code == 303
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "done", done["reason"]
    return done


def test_the_result_page_shows_the_source_beside_the_render_and_adds_to_the_library(client, monkeypatch, browser):  # noqa: F811
    done = _run_image_job(client, monkeypatch)
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    card = page[page.index('<div class="li-compare">'):]
    assert '<img src="/looks/landing/card-look/source.jpg"' in card and "<figcaption>Source</figcaption>" in card
    assert '<img src="/looks/landing/card-look/render.png"' in card and "Our render, 390 px" in card
    assert f"Fidelity <b>{done['result']['fidelity']:.2f}</b>" in page and "Observed from the screenshots." in page
    assert 'action="/looks/landing/card-look/add"' in card and 'action="/looks/landing/card-look/discard"' in card
    assert f'<input type="hidden" name="job" value="{done["id"]}">' in card
    src = client.get("/looks/landing/card-look/source.jpg", headers=_auth())
    assert src.status_code == 200 and src.mimetype == "image/jpeg"
    assert client.get("/looks/landing/card-look/render.png", headers=_auth()).mimetype == "image/png"
    assert all(e["key"] != "landing/card-look" for e in library.entries(TENANT))        # pending: not listed
    form = {"csrf": site.csrf_token(REVIEWER), "job": str(done["id"])}
    assert client.post("/looks/landing/card-look/add", headers=_auth(), data={"job": "1"}).status_code == 403
    resp = client.post("/looks/landing/card-look/add", headers=_auth(), data=form)
    build = jobs.list_jobs(TENANT)[0]
    assert resp.status_code == 303 and resp.headers["Location"].endswith(f"/job/{build['id']}")
    assert build["type"] == "library_build" and build["payload"]["only"] == ["landing"]
    lib = json.loads((TENANT.looks_dir / "landing" / "card-look" / "library.json").read_text())
    assert lib["library"] == "added" and lib["added_by"] == REVIEWER
    assert any(e["key"] == "landing/card-look" for e in library.entries(TENANT))
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "Added to the Library." in page and 'action="/looks/landing/card-look/add"' not in page
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    lib_card = body[body.index('id="lib-landing-card-look"'):][:6000]
    assert 'class="chip lib-source" href="/looks/landing/card-look/source.jpg"' in lib_card
    assert "Modelled on top.png" in lib_card
    assert any(r["action"] == "look_add" for r in jobs.audit_rows(TENANT))


def test_discard_moves_the_pending_look_out_and_frees_its_name(client, monkeypatch):  # noqa: F811
    done = _run_image_job(client, monkeypatch, name="Gone look")
    form = {"csrf": site.csrf_token(REVIEWER), "job": str(done["id"])}
    resp = client.post("/looks/landing/gone-look/discard", headers=_auth(), data=form)
    assert resp.status_code == 303 and resp.headers["Location"].endswith(f"/job/{done['id']}")
    assert not (TENANT.looks_dir / "landing" / "gone-look").exists()
    kept = list((TENANT.looks_dir / "landing" / look_import.DISCARDED_DIR).iterdir())
    assert len(kept) == 1 and kept[0].name.startswith("gone-look-") and (kept[0] / "template.html").is_file()
    assert look_import.check_name(TENANT, "landing", "gone-look") == "gone-look"
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "Discarded: it was not added to the Library." in page and '<div class="li-compare">' not in page
    # a second click does nothing
    assert client.post("/looks/landing/gone-look/add", headers=_auth(), data=form).status_code == 303
    assert [j["type"] for j in jobs.list_jobs(TENANT)] == ["look_import"]


def test_the_form_takes_screenshots_and_the_job_page_shows_the_steps(client):  # noqa: F811
    body = client.get("/looks/import", headers=_auth()).get_data(as_text=True)
    assert 'name="images" multiple' in body and "image/heic" in body and "up to 10, 15 MB each" in body
    job_id = jobs.enqueue(TENANT, "look_import", {"kind": "images", "slug": "x", "cartridge": "landing", "by": "t"},
                          by="t", target="look:landing/x")
    jobs.set_progress(TENANT, job_id, "revising")
    page = client.get(f"/job/{job_id}", headers=_auth()).get_data(as_text=True)
    steps = page[page.index('<ol class="li-steps">'):page.index("</ol>", page.index('<ol class="li-steps">'))]
    assert '<li class="done">Observing</li>' in steps and '<li class="done">Drafting</li>' in steps
    assert '<li class="now">Revising</li>' in steps and '<li class="">Storing</li>' in steps


# ---------------------------------------------------------------------------
# Upload checks: HEIC, limits, one source
# ---------------------------------------------------------------------------

HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"0" * 100


def test_heic_is_accepted_like_the_photos_page(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(photo_uploads, "heic_supported", lambda: True)
    assert _post_images(client, [("IMG_0001.HEIC", HEIC)], name="Heic look").status_code == 303
    job = jobs.list_jobs(TENANT)[0]
    assert [p.name for p in look_vision.pending_dir(TENANT, job["payload"]["images"]).iterdir()] == ["01.heic"]
    monkeypatch.setattr(photo_uploads, "heic_supported", lambda: False)
    resp = _post_images(client, [("IMG_0002.HEIC", HEIC)], name="Heic two")
    assert resp.status_code == 400 and "cannot read HEIC" in resp.get_data(as_text=True)


def test_a_real_heic_screenshot_is_read(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    path = tmp_path / "IMG_1.HEIC"
    Image.new("RGB", (390, 844), (200, 10, 10)).save(path, format="HEIF")
    assert look_vision.check_image_paths([path]) == [path]
    assert look_vision.open_image(path).size == (390, 844)


def test_the_upload_limits_and_one_source(client):  # noqa: F811
    resp = _post_images(client, [(f"s{i}.png", _png(20, 40)) for i in range(11)], name="Many look")
    assert resp.status_code == 400 and "at most 10 screenshots" in resp.get_data(as_text=True)
    resp = _post_images(client, [("fake.png", b"GIF89a" + b"0" * 64)], name="Fake look")
    assert resp.status_code == 400 and "not a valid png" in resp.get_data(as_text=True)
    resp = _post_images(client, [("a.png", _png())], name="Two look", url="https://x.example/")
    assert resp.status_code == 400 and "exactly one source" in resp.get_data(as_text=True)
    assert not list((TENANT.jobs_dir / jobs.LOOK_IMPORT_PENDING).glob("img-*"))   # all or nothing
    assert site.LOOK_IMPORT_MAX_REQUEST_BYTES > 10 * 15 * 1024 * 1024


# ---------------------------------------------------------------------------
# The sheets the model sees
# ---------------------------------------------------------------------------

def test_tall_and_many_screenshots_become_at_most_six_sheets_of_1280(tmp_path):
    tall = Image.new("RGB", (390, 9000), (250, 250, 250))
    sheets = look_vision.prepare([(tall, "The page on a phone")], tmp_path / "a")
    assert 1 < len(sheets) <= look_vision.MAX_IMAGES
    for s in sheets:
        with Image.open(tmp_path / "a" / s["file"]) as im:
            assert max(im.size) <= look_vision.LONG_SIDE and im.size == (s["width"], s["height"])
    assert "part 1 of 11" in sheets[0]["label"] and "side by side" in sheets[0]["label"]
    phones = [(Image.new("RGB", (1170, 2532), (i * 20, 0, 0)), f"Screenshot {i + 1}") for i in range(10)]
    sheets = look_vision.prepare(phones, tmp_path / "b")
    assert len(sheets) == 5 and sheets[0]["label"].startswith("Screenshot 1; Screenshot 2")
    one = look_vision.prepare([(Image.new("RGB", (2000, 1000)), "Desktop")], tmp_path / "c")
    assert len(one) == 1 and (one[0]["width"], one[0]["height"]) == (1280, 640)


# ---------------------------------------------------------------------------
# Exemplars: the hand conversions
# ---------------------------------------------------------------------------

def test_hand_converted_tenant_looks_are_exemplars_by_the_patterns_they_mark():
    folder = TENANT.looks_dir / "listicle" / "hand-look"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text('{% extends "base.html" %}<div data-look-pattern="sticky-bar big-numerals">'
                                          '</div><div data-look-pattern="faq-accordion"></div>')
    (folder / "library.json").write_text(json.dumps({
        "name": "hand-look", "summary": "Kept: numbered items with a photo each. Dropped: the countdown.",
        "source": {"kind": "url", "origin": "x.example", "converted": {"by": "hand", "cycle": "98b"}}}))
    (folder / "observation.json").write_text(json.dumps({"version": 2, "sections": [{"role": "content"}],
                                                         "patterns": [{"name": "dark-page", "section": None}]}))
    found = look_design.exemplars(TENANT, "listicle")
    assert [e["slug"] for e in found] == ["hand-look"] and found[0]["kind"] == "hand"
    assert [p["name"] for p in found[0]["observation"]["patterns"]] == ["sticky-bar", "big-numerals", "faq-accordion"]
    demo = look_import.load_demo(TENANT, "listicle")
    system = look_import.build_system_prompt("listicle", look_import.contract("listicle", TENANT, demo),
                                             look_import.load_schema("listicle", TENANT), exemplar=found[0])
    assert "### What was kept and dropped" in system and "Dropped: the countdown." in system
    assert "### Its page's observation" not in system and 'data-look-pattern="faq-accordion"' in system


def test_a_pending_look_is_not_tried_by_the_design_gate_until_it_is_added():
    from harness import design_gate

    folder = TENANT.looks_dir / "landing" / "wait-look"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text(OPEN_LOOK)
    (folder / "library.json").write_text(json.dumps({"name": "wait-look", "library": "pending",
                                                     "source": {"kind": "images", "origin": "a.png"}}))
    assert not any(lay["look"] == "wait-look" for lay in design_gate.extra_layouts("landing", TENANT))
    look_import.add_to_library(TENANT, "landing", "wait-look", by="t@example.com")
    assert any(lay["look"] == "wait-look" for lay in design_gate.extra_layouts("landing", TENANT))
