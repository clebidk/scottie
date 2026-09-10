"""Cycle 22 findings R36/R37: nothing this harness does not control decides
where a file is written, and the one unescaped Jinja render is escaped.
"""
import jinja2
import pytest

from harness import render, tenant as tenant_mod
from harness.pipeline import make_run_id, slugify
from harness.tenant import UnknownTenant
from harness.textutil import is_safe_tenant_name, safe_filename


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "given, expected",
    [
        ("IMG_3988.JPG", "IMG_3988.JPG"),
        ("hidden-costs-v2.mov", "hidden-costs-v2.mov"),
        ("../../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\x.dll", "x.dll"),
        ("/absolute/path/file.txt", "file.txt"),
        ("..", "file"),
        (".", "file"),
        ("", "file"),
        ("   ", "file"),
        ("a b;c|d", "a-b-c-d"),
    ],
)
def test_safe_filename_reduces_to_one_harmless_component(given, expected):
    assert safe_filename(given) == expected


def test_safe_filename_never_returns_a_path():
    for hostile in ("../x", "a/b", "a\\b", "/etc/passwd"):
        assert "/" not in safe_filename(hostile)
        assert "\\" not in safe_filename(hostile)


# ---------------------------------------------------------------------------
# A Drive download cannot escape its workdir (the header is the remote's)
# ---------------------------------------------------------------------------

class _FakeHeaders(dict):
    def get(self, key, default=""):
        return dict.get(self, key, default)


def test_a_drive_download_cannot_escape_the_workdir(tmp_path, monkeypatch):
    from harness.sources import drive

    workdir = tmp_path / "run"
    outside = tmp_path / "pwned.txt"

    class _Resp:
        headers = _FakeHeaders({
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="../../pwned.txt"',
        })

        def read(self):
            return b"payload"

    class _Opener:
        def open(self, url, timeout=None):
            return _Resp()

    monkeypatch.setattr(drive.urllib.request, "build_opener", lambda *a, **k: _Opener())

    dest = drive.download_drive_file("abc123", workdir)

    assert dest.parent == workdir
    assert dest.name == "pwned.txt"
    assert not outside.exists()


# ---------------------------------------------------------------------------
# An asset filename is a single component
# ---------------------------------------------------------------------------

def test_a_downloaded_asset_stays_inside_its_assets_dir(tmp_path):
    assets_dir = tmp_path / "assets"
    asset = {"id": "../../escape", "url": "https://example.test/a.jpg"}
    path = render.download_asset(asset, assets_dir, fetch_url=lambda url: b"\xff\xd8\xff not-an-image")
    assert path is not None
    assert path.parent == assets_dir
    assert ".." not in path.name


# ---------------------------------------------------------------------------
# A run id is always one component
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("given", ["../../etc/passwd", "/tmp/x.mov", "a b/c.mov", "..", ""])
def test_a_run_id_is_always_one_path_component(given):
    run_id = make_run_id(slugify(given))
    assert "/" not in run_id
    assert ".." not in run_id


# ---------------------------------------------------------------------------
# A tenant name is one directory under tenants/
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["peak-saunas", "_template", "acme.co", "a_b-c.d"])
def test_a_real_tenant_name_is_accepted(name):
    assert is_safe_tenant_name(name)
    assert tenant_mod.tenant_dir(name).parent == tenant_mod.TENANTS_DIR


@pytest.mark.parametrize("name", ["../evil", "..", "a/b", "/etc", "", "a\\b", "-leading"])
def test_a_traversing_tenant_name_is_refused(name):
    assert not is_safe_tenant_name(name)
    with pytest.raises(UnknownTenant):
        tenant_mod.tenant_dir(name)


def test_tenant_init_refuses_to_write_outside_the_tenants_dir():
    with pytest.raises(UnknownTenant):
        tenant_mod.init_tenant("../evil")


# ---------------------------------------------------------------------------
# R37: the byline render escapes its values
# ---------------------------------------------------------------------------

def test_byline_html_escapes_a_substituted_value(tmp_path):
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "byline.html").write_text('<p class="byline">By {{ author }}</p>')

    class _FakeTenant:
        authors = {}

        def author(self, role="author"):
            if role == "author":
                return {"name": '<script>alert("xss")</script>', "title": "CEO"}
            return {"name": "Reviewer", "title": ""}

        display_name = "Acme"

    html = render.load_byline_html(brand_dir, "2026-09-11", "2026-09-11", tenant=_FakeTenant())
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_byline_html_leaves_the_templates_own_markup_alone(tmp_path):
    """Autoescape escapes substituted values, never the template's markup --
    which is why turning it on left both real tenants' output unchanged."""
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "byline.html").write_text('<div class="byline"><strong>By</strong> {{ author }}</div>')

    class _FakeTenant:
        authors = {}
        display_name = "Acme"

        def author(self, role="author"):
            return {"name": "Ann Example", "title": "CEO"} if role == "author" else {"name": "Rev", "title": ""}

    html = render.load_byline_html(brand_dir, "2026-09-11", "2026-09-11", tenant=_FakeTenant())
    assert '<div class="byline"><strong>By</strong> Ann Example</div>' == html


def test_the_real_tenants_byline_is_unchanged_by_autoescape():
    """The change had to be byte-for-byte invisible for the tenants that exist."""
    for name in ("peak-saunas", "_template"):
        t = tenant_mod.Tenant(name, tenant_mod.TENANTS_DIR / name)
        raw = (t.root / "brand" / "byline.html").read_text()
        author, contributor = render.byline_names(t)
        context = {
            "author": author, "contributor": contributor,
            "published": "2026-09-11", "updated": "2026-09-11",
        }
        assert jinja2.Template(raw).render(**context) == (
            jinja2.Environment(autoescape=True).from_string(raw).render(**context)
        )
