"""Cycle 87: "Import a page" (harness/look_import.py).

What is asserted here, offline (FakeClient, fake HTTP transport, fake DNS):
  - the observation of a fixture page (hero, benefits grid, testimonial,
    FAQ, CTA band): section order and roles, the 3-column grid, the dark
    band, the sticky header, page-wide counts -- and none of its words;
  - the sanitiser drops scripts, frames, objects, event handlers and
    javascript: URLs; a URL is refused when it is not HTML, over the cap,
    or on a non-public address (also after a redirect); same-origin CSS
    only;
  - a FakeClient template that passes is stored as a TENANT look that
    looks.py resolves, render_page renders and the A/B/C library accepts;
  - an invalid template (unknown variable, colour literal, a second CTA)
    is repaired (twice since cycle 90: three calls at most), then refused
    with every message, storing nothing;
  - name collisions are refused before any model call;
  - the look_import job (progress, result, failure) and the site's dialog
    and form (login, form token, one source, rate limit);
  - `harness look import ... --dry-run` prints the observation only.
"""
import ast
import base64
import copy
import json

import pytest

from harness import abtest, cli, heroes, jobs, look_import, looks, serve, site
from harness.render import render_page
from tests.conftest import FakeClient, FakeResponse
from tests.support import REPO_ROOT, TENANT

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "look_import" / "sample-landing.html"
# Cycle 94: the shipped look includes the shared hero partial (harness/heroes/);
# an imported look is one file, so the reference is the look with it inline.
OPEN_LOOK = heroes.standalone_source(
    (REPO_ROOT / "cartridges" / "landing" / "looks" / "open" / "template.html").read_text(), "landing", "open")
REVIEWER = "caleb@peaksaunas.com"
PASSWORD = "pw-cycle-87"
# Words that exist only in the fixture page's copy; none may survive.
SOURCE_WORDS = ("Glacierberry", "frostline", "Jordan", "plunge", "tracker.example.com")


def _valid_template():
    """The approved open look with an import marker: a template the
    validator must accept. Cycle 90: it marks the fixture's one observed
    pattern (the FAQ accordion), so its fidelity passes on the first call."""
    return OPEN_LOOK.replace('{% extends "base.html" %}', '{% extends "base.html" %}{# imported split layout #}', 1) \
        .replace('{% if page.faq and page.faq.questions %}\n  <section class="ld-sec">',
                 '{% if page.faq and page.faq.questions %}\n  <section class="ld-sec" data-look-pattern="faq-accordion">',
                 1)


def _fixture_source():
    return {"kind": "upload", "data": FIXTURE.read_bytes(), "filename": FIXTURE.name}


def _client(*texts):
    return FakeClient([FakeResponse(t, input_tokens=12000, output_tokens=7000) for t in texts])


def _import(name="split-hero", client=None, **kw):
    return look_import.import_look(TENANT, cartridge="landing", name=name, source=_fixture_source(),
                                   client=client or _client(_valid_template()), **kw)


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

def _observation():
    return look_import.observe(look_import.load_source(_fixture_source()))


def test_the_observation_reads_the_fixtures_structure():
    obs = _observation()
    assert [s["role"] for s in obs["sections"]] == ["nav", "hero", "benefits", "proof", "faq", "cta", "footer"]
    hero, benefits, proof, faq, band = obs["sections"][1:6]
    assert hero["heading_level"] == 1 and hero["images"] == 1 and hero["image_aspects"] == [1.78]
    assert hero["cta"] == {"count": 1, "links": 0, "type": "button"}
    assert benefits["columns_desktop"] == 3 and benefits["repeated_items"] == 3
    assert proof["heading_level"] == 2
    assert faq["repeated_items"] == 3
    assert band["background"] == "dark" and band["cta"]["type"] == "button"
    g = obs["global"]
    assert g["max_width_px"] == 1200 and g["nav"] is True and g["sticky"] == ["top"]
    assert g["fonts"] == 2 and g["headings"] == {"h1": 1, "h2": 4, "h3": 3}
    assert g["cta_buttons"] == 2 and g["dark_sections"] == 1 and g["images"] == 1
    assert g["scripts"] == {"inline": 1, "external": 1, "inline_bytes": 56}
    assert g["estimated_weight_kb"] > 0


def test_the_observation_keeps_none_of_the_pages_words():
    text = json.dumps(_observation())
    for word in SOURCE_WORDS:
        assert word.lower() not in text.lower()


def test_the_summary_is_two_sentences_about_the_layout():
    summary = look_import.summarise(_observation())
    assert summary.count(". ") == 1 and summary.endswith(".")
    assert "hero > benefits > proof > faq > cta" in summary and "3-column" in summary


# ---------------------------------------------------------------------------
# Sanitise and fetch
# ---------------------------------------------------------------------------

def test_the_sanitiser_drops_scripts_frames_and_handlers():
    tree, counts, scripts = look_import.sanitise(look_import.parse_html(FIXTURE.read_text()))
    out = look_import.sanitised_html(tree)
    assert "<script" not in out and "<iframe" not in out and "<object" not in out
    assert "onclick" not in out and "onload" not in out and "javascript:" not in out
    assert counts == {"script": 2, "event_attributes": 2, "iframe": 1, "object": 1, "javascript_urls": 1}
    assert scripts["external"] == 1
    assert "Why frostline owners never go back" in out   # content stays (it is only counted, never stored)


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes))
        status, headers, body = self.routes[url]
        return status, headers, body[:max_bytes + 1]


def _public(host):
    return ["93.184.216.34"]


PAGE = FIXTURE.read_bytes()


def test_a_url_page_is_fetched_with_its_same_origin_css_only():
    transport = FakeTransport({
        "https://shop.example/lp": (200, {"Content-Type": "text/html; charset=utf-8"}, PAGE),
        "https://shop.example/assets/site.css": (200, {"Content-Type": "text/css"}, b".band{background:#000}"),
    })
    src = look_import.load_source({"kind": "url", "url": "https://shop.example/lp"}, transport=transport,
                                  resolver=_public)
    obs = look_import.observe(src, transport=transport, resolver=_public)
    fetched = [u for u, *_ in transport.calls]
    assert fetched == ["https://shop.example/lp", "https://shop.example/assets/site.css"]
    assert all(timeout == look_import.FETCH_TIMEOUT_S for _u, timeout, _m in transport.calls)
    assert obs["source"] == {"kind": "url", "origin": "shop.example", "bytes": len(PAGE)}
    assert obs["global"]["stylesheets_not_read"] == 1   # the Google Fonts sheet: another origin


@pytest.mark.parametrize("headers,body,why", [
    ({"Content-Type": "image/png"}, b"\x89PNG", "not text/html"),
    ({"Content-Type": "application/json"}, b"{}", "not text/html"),
    ({"Content-Type": "text/html"}, b"<html>" + b"x" * look_import.MAX_SOURCE_BYTES, "larger than"),
    ({"Content-Type": "text/html", "Content-Length": str(look_import.MAX_SOURCE_BYTES + 1)}, b"<html></html>",
     "larger than"),
])
def test_a_url_is_refused_when_not_html_or_over_the_cap(headers, body, why):
    transport = FakeTransport({"https://shop.example/x": (200, headers, body)})
    with pytest.raises(look_import.LookImportError, match=why):
        look_import.fetch_url("https://shop.example/x", transport=transport, resolver=_public)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1"])
def test_a_url_on_a_non_public_address_is_refused_before_any_request(address):
    transport = FakeTransport({})
    with pytest.raises(look_import.LookImportError, match="non-public"):
        look_import.fetch_url("http://internal.example/", transport=transport, resolver=lambda h: [address])
    assert transport.calls == []


def test_a_redirect_to_a_non_public_address_is_refused():
    transport = FakeTransport({"https://shop.example/a": (302, {"Location": "http://metadata.internal/"}, b"")})
    resolver = {"shop.example": ["93.184.216.34"], "metadata.internal": ["169.254.169.254"]}
    with pytest.raises(look_import.LookImportError, match="non-public"):
        look_import.fetch_url("https://shop.example/a", transport=transport, resolver=resolver.__getitem__)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://shop.example/x", "https://user:pw@shop.example/"])
def test_only_plain_http_urls_are_fetched(url):
    with pytest.raises(look_import.LookImportError, match="refused"):
        look_import.fetch_url(url, transport=FakeTransport({}), resolver=_public)


def test_an_upload_must_be_an_html_file():
    with pytest.raises(look_import.LookImportError, match=r"\.html"):
        look_import.load_source({"kind": "upload", "data": PAGE, "filename": "page.pdf"})
    with pytest.raises(look_import.LookImportError, match="binary"):
        look_import.load_source({"kind": "upload", "data": b"\x00\x01<html>", "filename": "page.html"})
    with pytest.raises(look_import.LookImportError, match="not an HTML page"):
        look_import.load_source({"kind": "paste", "html": "just some words"})


# ---------------------------------------------------------------------------
# Convert, validate, store
# ---------------------------------------------------------------------------

def test_a_valid_template_is_stored_as_a_tenant_look_that_resolves_and_renders(tmp_path):
    client = _client(_valid_template())
    result = _import(client=client, title="Split hero")
    folder = TENANT.looks_dir / "landing" / "split-hero"
    assert result["folder"] == str(folder) and result["attempts"] == 1
    assert sorted(p.name for p in folder.iterdir()) == ["library.json", "observation.json", "review.html",
                                                        "template.html"]
    library = json.loads((folder / "library.json").read_text())
    assert library["name"] == "split-hero" and library["title"] == "Split hero" and library["cartridge"] == "landing"
    assert library["source"]["kind"] == "upload" and library["source"]["origin"] == FIXTURE.name
    assert library["source"]["observation_sha"] == look_import.observation_sha(
        json.loads((folder / "observation.json").read_text()))
    assert library["derived_from_sections"] == ["hero", "benefits", "proof", "faq", "cta"]
    assert library["summary"].count(". ") == 1
    # 12k in + 7k out on claude-sonnet-5 ($2 / $10 per M)
    assert library["cost_usd"] == pytest.approx(0.094)
    # the one call: the observation and the rules went in, no copy of the page did
    call = client.messages.calls[0]
    assert call["model"] == TENANT.model_for("write")
    assert "Build observation" in call["messages"][0]["content"] and "Boxless rules" in call["system"]
    assert "`headline_html`" in call["system"] and "{{CONTRACT}}" not in call["system"]
    for word in SOURCE_WORDS:
        assert word.lower() not in call["messages"][0]["content"].lower()
        for path in folder.iterdir():
            assert word.lower() not in path.read_text().lower()
    # looks.py resolves it after the cartridge's own looks
    assert looks.imported_looks("landing", TENANT) == ("split-hero",)
    assert looks.resolve_look("landing", "split-hero", tenant=TENANT) == "split-hero"
    assert looks.resolve_look("landing", None, tenant=TENANT) == "open"   # never a default
    assert looks.requested_for("landing", "split-hero", TENANT) == "split-hero"
    assert looks.requested_for("landing", "split-hero") is None
    # render_page renders a page in it from the tenant's own folder
    demo = look_import.load_demo(TENANT, "landing")
    page = dict(copy.deepcopy(demo["page"]), look="split-hero")
    out = tmp_path / "landing"
    render_page(cartridge_name="landing", page=page, ad_brief=demo["ad_brief"], facts_pack=demo["facts_pack"],
                cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
                templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out, published="2026-10-07",
                updated="2026-10-07", tenant=TENANT, download_assets=False)
    html = (out / "index.html").read_text()
    assert 'class="pk-lp adv-landing"' in html and "Shop the Fuji" in html
    # ... and the A/B/C library takes it
    assert abtest.parse_arm("landing:split-hero", TENANT).look == "split-hero"
    with pytest.raises(abtest.AbtestError):
        abtest.parse_arm("landing:no-such-look", TENANT)


def test_the_cartridges_own_look_wins_over_a_tenant_folder_of_the_same_name(tmp_path):
    folder = TENANT.looks_dir / "landing" / "proof"
    folder.mkdir(parents=True)
    (folder / "template.html").write_text("{% extends 'base.html' %}{% block content %}SHADOW{% endblock %}")
    assert looks.resolve_look("landing", "proof", tenant=TENANT) == "proof"
    with pytest.raises(look_import.LookImportError, match="built-in look"):
        look_import.check_name(TENANT, "landing", "proof")


BAD_TEMPLATE = (
    _valid_template()
    .replace("{{ page.hero.subhead }}", "{{ page.hero.tagline }}")
    .replace("</style>", ".adv-landing .ld-x{color:#ff0000}\n</style>", 1)
    .replace('<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n        {% if ld.secondary %}',
             '<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n'
             '        <a class="ld-btn" href="{{ page.cta_url }}">Buy it now</a>\n        {% if ld.secondary %}', 1)
)
SECOND_LINK_TEMPLATE = _valid_template().replace(
    "{% if ld.secondary %}", '<a class="ld-text" href="/collections/all">See all</a>{% if ld.secondary %}', 1)


def _errors(text):
    demo = look_import.load_demo(TENANT, "landing")
    return look_import.validate_template(text, "landing", "x-look", TENANT, demo)[0]


def test_the_validator_passes_the_approved_look_and_names_each_broken_rule():
    assert _errors(_valid_template()) == []
    assert any("'tagline' is not in the landing page schema" in e for e in _errors(BAD_TEMPLATE))
    assert any("colour literal" in e and "#ff0000" in e for e in _errors(BAD_TEMPLATE))
    assert any("literal href='/collections/all'" in e for e in _errors(SECOND_LINK_TEMPLATE))
    # the second CTA phrase is a render-time check: alone it is the only error
    second_cta = _valid_template().replace(
        '<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n        {% if ld.secondary %}',
        '<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n'
        '        <a class="ld-btn" href="{{ page.cta_url }}">Buy it now</a>\n        {% if ld.secondary %}', 1)
    assert [e for e in _errors(second_cta) if "one CTA phrase" in e]


@pytest.mark.parametrize("change,needle", [
    (lambda t: t.replace("{{ ld.eyebrow }}", "{{ ld.eyebrow }}{{ facts_pack }}"), "unknown variable 'facts_pack'"),
    (lambda t: t.replace("{{ ld.eyebrow }}", "{{ ld.bogus }}"), "'bogus' is not in the renderer's cartridge_data"),
    (lambda t: t.replace("{% if ld.trust %}", "{% if false %}").replace("{% for t in ld.trust %}", "{% for t in [] %}"),
     "never uses `cartridge_data.trust`"),
    (lambda t: t.replace("border-top:1px solid var(--pk-line)}", "border:1px solid var(--pk-line)}", 1),
     "four-sided `border"),
    (lambda t: t.replace("</style>", ".adv-landing .ld-y{box-shadow:0 1px 2px var(--pk-line)}</style>", 1),
     "no shadows"),
    (lambda t: t.replace("</style>", "@media (max-width:600px){.adv-landing .ld-y{gap:0}}</style>", 1),
     "mobile first"),
    (lambda t: t.replace("</style>", ".adv-landing .ld-y{width:600px}</style>", 1), "wider than a 390px phone"),
    (lambda t: t.replace("</style>", ".adv-landing .ld-y{font-family:Georgia,serif}</style>", 1),
     "fonts come from the tenant tokens"),
    (lambda t: t.replace("</style>", ".ld-y{gap:0}</style>", 1), "not scoped under .adv-landing"),
    (lambda t: t.replace("</style>", ".adv-landing .ld-y{background:url(https://x.example/a.png)}</style>", 1),
     "url()"),
    (lambda t: t.replace("{% endblock %}", '<script src="https://x.example/a.js"></script>{% endblock %}'),
     "<script src>"),
    (lambda t: t.replace("{{ page.hero.subhead }}", "{{ page.hero.subhead | safe }}"), "`| safe` only"),
    (lambda t: t.replace("{{ ld.eyebrow }}", "{{ ld.eyebrow.__class__ }}"), "starts with an underscore"),
    (lambda t: t.replace("{% block content %}", "{% include 'x.html' %}{% block content %}"), "include"),
    (lambda t: t.replace("<h2 class=\"ld-h2\">Questions before you buy</h2>",
                         "<h2 class=\"ld-h2\">Questions before you buy from the team that has served 4000 owners</h2>"),
     "has a number"),
    (lambda t: t.replace('class="pk-lp adv-landing"', 'class="adv-landing"'), "wrapper element"),
])
def test_each_rule_is_enforced(change, needle):
    errors = _errors(change(_valid_template()))
    assert any(needle in e for e in errors), errors


def test_an_invalid_template_is_repaired_then_refused_and_nothing_is_stored():
    client = _client(BAD_TEMPLATE, BAD_TEMPLATE, BAD_TEMPLATE)
    with pytest.raises(look_import.LookImportError) as info:
        _import(client=client)
    message = look_import.format_errors(info.value)
    assert "after 2 repair(s)" in message and "nothing was stored" in message
    assert "'tagline' is not in the landing page schema" in message and "colour literal" in message
    assert len(client.messages.calls) == 3
    repair = client.messages.calls[1]["messages"]
    assert repair[1] == {"role": "assistant", "content": BAD_TEMPLATE.strip() + "\n"}
    assert "The harness refused this template" in repair[2]["content"] and "tagline" in repair[2]["content"]
    assert not (TENANT.looks_dir / "landing" / "split-hero").exists()
    assert not list(TENANT.looks_dir.glob("landing/.staging-*"))


def test_a_second_cta_phrase_is_refused():
    second_cta = _valid_template().replace(
        '<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n        {% if ld.secondary %}',
        '<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>\n'
        '        <a class="ld-btn" href="{{ page.cta_url }}">Buy it now</a>\n        {% if ld.secondary %}', 1)
    with pytest.raises(look_import.LookImportError) as info:
        _import(client=_client(second_cta, second_cta, second_cta))
    assert any("one CTA phrase" in e and "Buy it now" in e for e in info.value.errors)


def test_a_repair_that_passes_is_stored_on_attempt_two():
    result = _import(client=_client(BAD_TEMPLATE, _valid_template()))
    assert result["attempts"] == 2
    assert json.loads((TENANT.looks_dir / "landing" / "split-hero" / "library.json").read_text())[
        "validation"] == {"result": "PASS", "attempts": 2}


def test_a_fenced_answer_is_unwrapped():
    assert look_import.extract_template("```html\n" + _valid_template() + "```") == _valid_template().strip() + "\n"


def test_name_collisions_are_refused_before_any_model_call():
    _import()
    client = _client()
    with pytest.raises(look_import.LookImportError, match="already has a landing look 'split-hero'"):
        _import(name="Split Hero", client=client)
    for name in ("open", "proof", "pdp", "cards"):
        with pytest.raises(look_import.LookImportError, match="built-in look"):
            _import(name=name, client=client)
    with pytest.raises(look_import.LookImportError, match="does not make a look name"):
        _import(name="!!", client=client)
    assert client.messages.calls == []


def test_a_listicle_look_can_be_imported_too():
    listicle_open = heroes.standalone_source(
        (REPO_ROOT / "cartridges" / "listicle" / "looks" / "open" / "template.html").read_text(), "listicle", "open")
    # the reference still has one legacy colour literal; an import may not
    fixed = listicle_open.replace("rgba(255,255,255,.18)", "var(--pk-line)").replace(
        '<section class="op-sec op-faq op-narrow">',
        '<section class="op-sec op-faq op-narrow" data-look-pattern="faq-accordion">', 1)
    result = look_import.import_look(TENANT, cartridge="listicle", name="long-read", source=_fixture_source(),
                                     client=_client(listicle_open, fixed))
    assert result["attempts"] == 2
    assert looks.resolve_look("listicle", "long-read", style="reasons", tenant=TENANT) == "long-read"
    assert abtest.parse_arm("listicle:reasons:long-read", TENANT).look == "long-read"


def test_the_contract_names_are_all_passed_by_render_page():
    tree = ast.parse((REPO_ROOT / "harness" / "render.py").read_text())
    render_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_page")
    passed = set()
    for node in ast.walk(render_fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "render":
            passed |= {kw.arg for kw in node.keywords}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "globals":
            passed.add(node.slice.value)
    for names in look_import.CONTRACT_NAMES.values():
        assert set(names) <= passed, set(names) - passed


def test_the_rules_file_documents_the_landing_contract():
    rules = look_import.RULES_PATH.read_text()
    demo = look_import.load_demo(TENANT, "landing")
    for key in look_import.contract("landing", TENANT, demo)["data"]["cartridge_data"]:
        assert f"`{key}`" in rules, key


# ---------------------------------------------------------------------------
# The job and the site
# ---------------------------------------------------------------------------

def _auth(email=REVIEWER):
    return {"Authorization": "Basic " + base64.b64encode(f"{email}:{PASSWORD}".encode()).decode()}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


def _post(client, data, **kw):
    data = {"csrf": site.csrf_token(REVIEWER), "cartridge": "landing", **data}
    return client.post("/looks/import", data=data, headers=_auth(), content_type="multipart/form-data", **kw)


def test_the_ads_page_has_the_import_dialog(client):
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert 'data-dialog="look-import"' in body and '<dialog id="look-import"' in body
    assert 'enctype="multipart/form-data" action="/looks/import"' in body
    assert 'name="page_file"' in body and 'name="url"' in body and 'name="html"' in body
    assert '<option value="landing" selected>' in body and '<option value="listicle">' in body
    page = client.get("/looks/import", headers=_auth())
    assert page.status_code == 200 and "Import a page" in page.get_data(as_text=True)


def test_the_import_needs_the_login_and_the_form_token(client):
    assert client.get("/looks/import").status_code == 401
    resp = client.post("/looks/import", data={"name": "x-look", "html": "<p>x</p>"}, headers=_auth(),
                       content_type="multipart/form-data")
    assert resp.status_code == 403
    assert jobs.list_jobs(TENANT) == []


@pytest.mark.parametrize("data,status,needle", [
    ({"name": "split-hero"}, 400, "exactly one source"),
    ({"name": "split-hero", "url": "https://x.example/", "html": "<p>x</p>"}, 400, "exactly one source"),
    ({"name": "open", "html": "<p>x</p>"}, 400, "built-in look"),
    ({"name": "split-hero", "url": "ftp://x.example/"}, 400, "http"),
    ({"name": "split-hero", "html": "just words"}, 400, "not an HTML page"),
])
def test_a_bad_import_request_is_refused_with_a_message(client, data, status, needle):
    resp = _post(client, data)
    assert resp.status_code == status and needle in resp.get_data(as_text=True)
    assert jobs.list_jobs(TENANT) == []


def test_imports_are_rate_limited_like_uploads(client):
    for i in range(site.LOOK_IMPORTS_PER_WINDOW):
        assert _post(client, {"name": f"look-{chr(97 + i)}aa", "url": "https://x.example/"}).status_code == 303
    resp = _post(client, {"name": "look-zzz", "url": "https://x.example/"})
    assert resp.status_code == 429


def test_the_job_imports_the_page_reports_progress_and_links_the_reference(client, monkeypatch):
    fake = _client(_valid_template())
    monkeypatch.setattr(look_import, "make_client", lambda: fake)
    seen = []
    real = jobs.set_progress
    monkeypatch.setattr(jobs, "set_progress", lambda t, job_id, step: (seen.append(step), real(t, job_id, step)))
    with FIXTURE.open("rb") as fh:
        resp = _post(client, {"name": "Split hero", "page_file": (fh, FIXTURE.name)})
    assert resp.status_code == 303
    job = jobs.list_jobs(TENANT)[0]
    assert job["type"] == "look_import" and job["state"] == "queued" and job["target"] == "look:landing/split-hero"
    pending = jobs.pending_source_path(TENANT, job["payload"]["pending"])
    assert pending.exists()
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "done", done["reason"]
    assert seen == ["reading", "observing", "converting", "validating", "storing"]
    assert not pending.exists()
    assert done["result"]["slug"] == "split-hero" and done["result"]["validation"] == "PASS on attempt 1"
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "Reference render" in page and "/looks/landing/split-hero/review" in page and "landing:split-hero" in page
    review = client.get("/looks/landing/split-hero/review", headers=_auth())
    assert review.status_code == 200 and 'class="pk-lp adv-landing"' in review.get_data(as_text=True)
    assert client.get("/looks/landing/no-such/review", headers=_auth()).status_code == 404
    assert any(r["action"] == "look_import_done" for r in jobs.audit_rows(TENANT))


def test_a_failed_job_shows_the_errors_and_keeps_nothing(client, monkeypatch):
    fake = _client(BAD_TEMPLATE, BAD_TEMPLATE, BAD_TEMPLATE)
    monkeypatch.setattr(look_import, "make_client", lambda: fake)
    assert _post(client, {"name": "split-hero", "html": FIXTURE.read_text()}).status_code == 303
    job = jobs.list_jobs(TENANT)[0]
    pending = jobs.pending_source_path(TENANT, job["payload"]["pending"])
    done = jobs.run_one(TENANT, log=lambda *_: None)
    assert done["state"] == "failed" and "tagline" in done["reason"]
    assert not pending.exists()
    assert not (TENANT.looks_dir / "landing" / "split-hero").exists()
    page = client.get(f"/job/{done['id']}", headers=_auth()).get_data(as_text=True)
    assert "<li>" in page and "colour literal" in page and "Nothing was stored." in page


def test_the_job_page_shows_the_running_step(client):
    job_id = jobs.enqueue(TENANT, "look_import", {"kind": "url", "url": "https://x.example/", "slug": "x-look",
                                                  "cartridge": "landing", "by": REVIEWER}, by=REVIEWER,
                          target="look:landing/x-look")
    jobs.claim_next(TENANT)
    jobs.set_progress(TENANT, job_id, "converting")
    page = client.get(f"/job/{job_id}", headers=_auth()).get_data(as_text=True)
    assert "Step: <b>converting</b>" in page and "(3 of 6)" in page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_the_cli_dry_run_prints_the_observation_only(capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("a dry run makes no model call"))
    rc = cli.main(["look", "import", "--tenant", TENANT.name, "--cartridge", "landing", "--file", str(FIXTURE),
                   "--name", "split-hero", "--dry-run"])
    out = capsys.readouterr()
    assert rc == 0
    obs = json.loads(out.out)
    assert [s["role"] for s in obs["sections"]][1:6] == ["hero", "benefits", "proof", "faq", "cta"]
    assert "observation only" in out.err
    assert not (TENANT.looks_dir / "landing").exists()


def test_the_cli_imports_with_the_client_and_refuses_with_messages(capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda: _client(_valid_template()))
    args = ["look", "import", "--tenant", TENANT.name, "--file", str(FIXTURE), "--name", "split-hero"]
    assert cli.main(args) == 0
    assert "imported landing look 'split-hero'" in capsys.readouterr().out
    assert cli.main(args) == 1
    assert "already has a landing look" in capsys.readouterr().err


def test_run_and_rerender_accept_an_imported_look_name():
    _import()
    parser = cli.build_parser()
    assert parser.parse_args(["run", "ad.txt", "--look", "split-hero"]).look == "split-hero"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "ad.txt", "--look", "brochure"])
