"""Cycle 22 findings R36/R37: nothing this harness does not control decides
where a file is written, and the one unescaped Jinja render is escaped.

Cycle 26 adds harness/serve.py's _safe_path -- the reviewer web app's own
reuse of safe_filename (R36) for a URL path instead of a single filename;
tests/test_serve.py exercises it through real HTTP requests, the tests below
check the helper itself directly.
"""
import jinja2
import pytest

from harness import render, serve, tenant as tenant_mod
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
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")

    assets_dir = tmp_path / "assets"
    asset = {"id": "../../escape", "url": "https://example.test/a.jpg"}
    result = render.download_asset(asset, assets_dir, fetch_url=lambda url: buf.getvalue())
    assert result is not None
    assert result["path"].parent == assets_dir
    assert ".." not in result["path"].name


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
    """The change had to be byte-for-byte invisible for the tenants that exist.

    Built from byline_names() rather than a hardcoded context, so this keeps
    checking the real thing when the byline roles change (Cycle 19 added a
    third)."""
    for name in ("peak-saunas", "_template"):
        t = tenant_mod.Tenant(name, tenant_mod.TENANTS_DIR / name)
        raw = (t.root / "brand" / "byline.html").read_text()
        author, contributor, reviewer = render.byline_names(t)
        context = {
            "author": author, "contributor": contributor, "reviewer": reviewer,
            "published": "2026-09-11", "updated": "2026-09-11",
        }
        assert jinja2.Template(raw).render(**context) == (
            jinja2.Environment(autoescape=True).from_string(raw).render(**context)
        )


# ---------------------------------------------------------------------------
# Cycle 26: harness/serve.py's _safe_path
# ---------------------------------------------------------------------------

def test_safe_path_rejects_dotdot_traversal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "inside.txt").write_text("ok")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    assert serve._safe_path(base, "../outside.txt") is None
    assert serve._safe_path(base, "../../outside.txt") is None
    assert serve._safe_path(base, "a/../../outside.txt") is None


def test_safe_path_rejects_absolute_and_empty(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "inside.txt").write_text("ok")
    assert serve._safe_path(base, "/etc/passwd") is None
    assert serve._safe_path(base, "") is None
    assert serve._safe_path(base, "does-not-exist.txt") is None


def test_safe_path_allows_a_real_file_inside_base(tmp_path):
    base = tmp_path / "base"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "file.txt").write_text("ok")
    result = serve._safe_path(base, "sub/file.txt")
    assert result == (base / "sub" / "file.txt").resolve()


def test_safe_dir_rejects_traversal_in_run_id(tmp_path):
    base = tmp_path / "out"
    base.mkdir()
    (base / "real-run").mkdir()
    assert serve._safe_dir(base, "../out") is None
    assert serve._safe_dir(base, "real-run") is not None
