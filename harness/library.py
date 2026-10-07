"""Cycle 86: the Library -- one reference render of every page version.

A "version" is a cartridge x one of its looks (looks.CARTRIDGE_LOOKS) and,
for the listicle, x its first screen (first_screen.HERO_STYLES) x its copy
style (listicle.STYLES). A cartridge with no looks is one version. A
tenant-level imported look (cycle 87, harness/look_import.py: "Import a
page"), tenants/<t>/looks/<cartridge>/<slug>/ with template.html and
library.json, is one more version of that cartridge: found through
looks.imported_looks, rendered through the same fake run (looks.resolve_look
accepts the slug and render.py loads the template from the tenant folder),
and described from its library.json (title, summary, source). An imported
listicle look is one version, with the story first screen and the reasons
style, so "Generate with this" has a complete build to pin.

`harness library build --tenant <t> [--only landing,...] [--force]` renders
each version once with NO model call: evals/fake_run.py's canned page for
the cartridge goes through the real pipeline (tenant brand, facts pack,
photo library when present, renderer), exactly as `python -m
evals.fake_run` does. Each version gets

    tenants/<t>/library/<cartridge>/<look>[/<first screen>/<style>]/
        index.html + assets/   the rendered page
        review.html            self-contained (review.build_review_for_page)
        thumb.png              390x844 at scale 1, only when a headless
                               chromium/google-chrome is on PATH
        meta.json              cartridge, look, first_screen, style, title,
                               summary, audience_temperature, word_range,
                               built_at, sha, arm (the A/B/C build string
                               "Generate with this" pre-selects)

A cartridge with no looks uses the folder name "default". A version whose
sha (its templates + cartridge.md + schema.json) is unchanged is skipped
unless --force. A version that cannot be rendered offline (the listicle's
`face` first screen needs a still from the ad; the canned ad is text) gets
a meta.json with an "unavailable" reason and no page.

The fake runs happen in a scratch folder: out/, runs/ (logs and the spend
ledger) and evals/ point there for the build, reviewer notifications are
off, one listicle draft (no Jev call), and every socket connect in this
process fails -- a slip in the fakes is an error, never a paid call.
"""
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import first_screen, listicle, looks, pipeline
from . import tenant as tenant_mod
from .config import REPO_ROOT
from .errors import HarnessError

DEFAULT_LOOK_DIR = "default"
# Bump when the build's output changes for the same templates, so every
# version rebuilds once without --force.
FORMAT = "library-v1"
FIXTURE = "founder-warranty-demo.txt"
BROWSERS = ("chromium", "google-chrome", "chromium-browser")
THUMB_SIZE = (390, 844)
THUMB_TIMEOUT_S = 90
JOB_TARGET = "library"
NOTE = "Reference renders use a canned demo page and the tenant's brand; copy is not real."
# Cycle 87 imported listicle looks: the first screen and style their one
# Library version renders with (story needs no ad still; reasons is the
# first style).
IMPORTED_LISTICLE = ("story", "reasons")

CARTRIDGE_LABELS = {
    "article": "Article", "comparison": "Comparison", "landing": "Landing page", "listicle": "Listicle",
    "longform": "Long-form page", "product-page": "Product page", "quiz": "Quiz",
}
LOOK_NOTES = {
    ("landing", "open"): "Ad-first and mobile-first: headline, the ad's still, the price, checked claims, one CTA.",
    ("landing", "proof"): "The open page with the rating first, check-mark benefits, trust facts under the CTA "
                          "and an icon row.",
    ("listicle", "open"): "Boxless white page: centred headline with one red accent, numbered items with a "
                          "red-bar proof line, one dark closing band.",
    ("longform", "open"): "The listicle's boxless design on the long page: stats strip, red numerals, hairline "
                          "spec table, one closing band.",
    ("longform", "classic"): "The original long page: hero, problem, how it works, specs and proof, FAQ, "
                             "sticky CTA.",
    ("product-page", "pdp"): "Laid out like the store's own product page: gallery and buy panel, one promise "
                             "band, what's included, specs, five FAQs.",
    ("product-page", "classic"): "The older, stripped product page.",
}
FIRST_SCREEN_NOTES = {
    "face": "First screen: a still of the ad's creator with her own words as the caption.",
    "story": "First screen: text first; the lede quotes the ad and item 01 starts on screen one.",
    "display": "First screen: a wide display headline, the product cut-out and a stats strip.",
}


class LibraryError(HarnessError):
    """A library command that cannot go on; one line, exit 1."""


@dataclass(frozen=True)
class Version:
    cartridge: str
    look: str = None
    first_screen: str = None
    style: str = None
    tenant_look: bool = False

    @property
    def key(self):
        """The folder under tenants/<t>/library/, and the site's id for it."""
        parts = [self.cartridge, self.look or DEFAULT_LOOK_DIR]
        parts += [p for p in (self.first_screen, self.style) if p]
        return "/".join(parts)


# ---------------------------------------------------------------------------
# what exists
# ---------------------------------------------------------------------------

def tenant_look_versions(tenant, cartridge):
    """The tenant's imported looks for this cartridge (looks.imported_looks:
    every valid slug folder with a template.html). A slug that is, or is an
    alias of, one of the cartridge's own looks is left out -- the built-in
    look wins there, as in looks.resolve_look."""
    out = []
    for slug in looks.imported_looks(cartridge, tenant):
        if slug == DEFAULT_LOOK_DIR or looks.canonical(cartridge, slug) in looks.cartridge_looks(cartridge):
            continue
        fs, style = IMPORTED_LISTICLE if cartridge == "listicle" else (None, None)
        out.append(Version(cartridge, slug, fs, style, tenant_look=True))
    return out


def versions(tenant):
    """Every version, cartridge by cartridge (discover order), harness looks
    first, then the tenant's imported looks."""
    out = []
    for cartridge in pipeline.discover_cartridges():
        for look in looks.cartridge_looks(cartridge) or (None,):
            if cartridge == "listicle":
                out += [Version(cartridge, look, fs, style)
                        for fs in first_screen.HERO_STYLES for style in listicle.STYLES]
            else:
                out.append(Version(cartridge, look))
        out += tenant_look_versions(tenant, cartridge)
    return out


def find(tenant, key):
    """The version with this key, or None (the site's routes go through
    this, so a key can never name a path outside the library)."""
    return next((v for v in versions(tenant) if v.key == key), None)


def _renderable(tenant, version):
    """True for a harness version. An imported look renders when
    looks.resolve_look accepts its slug for this tenant (cycle 87)."""
    if not version.tenant_look:
        return True
    try:
        return looks.resolve_look(version.cartridge, version.look, style=version.style, tenant=tenant) == version.look
    except ValueError:
        return False


def arm_for(version, tenant=None):
    """The A/B/C build string (abtest.parse_arm) that builds this version,
    or None when there is none (e.g. an imported look the harness cannot
    build yet)."""
    from . import abtest

    if version.cartridge == "listicle":
        if not version.style:
            return None
        text = ":".join(p for p in ("listicle", version.style, version.look, version.first_screen) if p)
    else:
        text = version.cartridge + (f":{version.look}" if version.look else "")
    try:
        return abtest.parse_arm(text, tenant).id
    except abtest.AbtestError:
        return None


def arm_from_fields(cartridge, look=None, hero_style=None, style=None, tenant=None):
    """The upload form's pre-selection (cartridge, look, hero style, style)
    as an abtest.Arm (its .id is the canonical build string). Raises
    LibraryError for a combination the harness cannot build."""
    from . import abtest

    cartridge, look, hero_style, style = ((x or "").strip() or None for x in (cartridge, look, hero_style, style))
    if not cartridge:
        raise LibraryError("no page version given")
    if cartridge == "listicle":
        text = ":".join(p for p in ("listicle", style, look or listicle.DEFAULT_LOOK, hero_style) if p)
    elif hero_style or style:
        raise LibraryError(f"{cartridge} has no first-screen or copy style")
    else:
        text = cartridge + (f":{look}" if look else "")
    try:
        return abtest.parse_arm(text, tenant)
    except abtest.AbtestError as e:
        raise LibraryError(str(e)) from e


def arm_label(arm):
    """The human name of an abtest.Arm (the upload form's pre-selection)."""
    return label(arm.cartridge, arm.look, arm.hero_style, arm.style)


# ---------------------------------------------------------------------------
# what each version is
# ---------------------------------------------------------------------------

def _cartridge_md(cartridge):
    path = pipeline.CARTRIDGES_DIR / cartridge / "cartridge.md"
    return path.read_text() if path.exists() else ""


def _purpose(text, tenant):
    m = re.search(r"^Purpose:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return ""
    first = re.split(r"(?<=[.!?])\s+", m.group(1).strip())[0]
    first = tenant.render(first) if tenant is not None else first
    return first[:1].upper() + first[1:]


def _audience(text):
    m = re.search(r"^Audience temperature:\s*([^.\n]+)", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _tenant_look_info(tenant, version):
    folder = looks.imported_looks_dir(version.cartridge, tenant)
    if folder is None:
        return {}
    path = folder / version.look / "library.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def label(cartridge, look=None, first_screen_style=None, style=None):
    """A short human name: "Landing page, proof look", "Listicle: myths
    style, story first screen"."""
    name = CARTRIDGE_LABELS.get(cartridge, cartridge.replace("-", " ").capitalize())
    if cartridge == "listicle" and (style or first_screen_style):
        bits = [f"{style} style" if style else "", f"{first_screen_style} first screen" if first_screen_style else ""]
        return f"{name}: " + ", ".join(b for b in bits if b)
    return f"{name}, {look} look" if look else name


def describe(tenant, version):
    """meta.json's descriptive fields for a version (no build data)."""
    from . import write

    text = _cartridge_md(version.cartridge)
    purpose = _purpose(text, tenant)
    word_range = write.parse_word_range(text)
    info = {
        "key": version.key,
        "cartridge": version.cartridge,
        "look": version.look,
        "first_screen": version.first_screen,
        "style": version.style,
        "source": "tenant" if version.tenant_look else "harness",
        "title": label(version.cartridge, version.look, version.first_screen, version.style),
        "audience_temperature": _audience(text),
        "word_range": list(word_range) if word_range else None,
        "arm": arm_for(version, tenant) if _renderable(tenant, version) else None,
    }
    notes = []
    if version.tenant_look:
        lib = _tenant_look_info(tenant, version)
        info["title"] = str(lib.get("title") or "").strip() or info["title"]
        notes.append(str(lib.get("summary") or "").strip())
        # cycle 87 writes source as {kind, origin, imported_at}; a plain
        # string and a top-level added_at are read too.
        source = lib.get("source")
        source = source if isinstance(source, dict) else {"origin": source}
        info["imported_from"] = str(source.get("origin") or "").strip() or None
        info["added_at"] = str(lib.get("added_at") or source.get("imported_at") or "").strip() or None
    else:
        notes.append(LOOK_NOTES.get((version.cartridge, version.look), ""))
    if version.first_screen:
        notes.append(FIRST_SCREEN_NOTES.get(version.first_screen, ""))
    if version.style:
        notes.append(f"Headline formula: {listicle.HEADLINE_FORMULAS[version.style]}.")
    # purpose: the cartridge's (the site shows it once per group); notes: this
    # version's own look / first screen / style; summary: both.
    info["purpose"] = purpose
    info["notes"] = " ".join(n for n in notes if n)
    info["summary"] = " ".join(n for n in (purpose, info["notes"]) if n)
    return info


def version_sha(tenant, version):
    """sha256 of what decides a version's page: the cartridge's template,
    cartridge.md and schema.json, and the look's template (+ library.json
    for an imported look, and whether the harness can render it)."""
    cdir = pipeline.CARTRIDGES_DIR / version.cartridge
    files = [cdir / "template.html", cdir / "cartridge.md", cdir / "schema.json"]
    if version.tenant_look:
        ldir = looks.imported_looks_dir(version.cartridge, tenant) / version.look
        files += [ldir / "template.html", ldir / "library.json"]
    elif version.look:
        files.append(cdir / "looks" / version.look / "template.html")
    h = hashlib.sha256(f"{FORMAT}\n{version.key}\n".encode())
    if version.tenant_look:
        h.update(b"renderable" if _renderable(tenant, version) else b"listed")
    for path in files:
        h.update(f"\n{path.name}\n".encode())
        h.update(path.read_bytes() if path.is_file() else b"")
    return h.hexdigest()


def read_meta(tenant, version):
    try:
        data = json.loads((Path(tenant.library_dir) / version.key / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def entries(tenant):
    """Every version for the site: its meta.json when built (else its
    description), plus key, built, has_review, has_thumb."""
    out = []
    for version in versions(tenant):
        folder = Path(tenant.library_dir) / version.key
        meta = read_meta(tenant, version)
        entry = dict(meta) if meta else describe(tenant, version)
        entry.update(key=version.key, built=meta is not None,
                     has_review=(folder / "review.html").is_file(), has_thumb=(folder / "thumb.png").is_file())
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def find_browser():
    """A headless-capable Chromium on PATH, or None."""
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    return None


def capture_thumb(browser, html_path, png_path, *, popen=subprocess.Popen, timeout_s=THUMB_TIMEOUT_S):
    """A 390x844 screenshot at scale 1 of html_path. True when thumb.png
    was written; any browser failure is False (never raised). Headless
    Chrome on some hosts writes the file and then does not exit, so the
    browser is stopped as soon as the file is complete. The page is framed
    at exactly 390x844 inside a wrapper page, because a headless window can
    keep a wider layout viewport than --window-size asks for."""
    png_path = Path(png_path)
    png_path.unlink(missing_ok=True)  # never report an older file as this capture
    w, h = THUMB_SIZE
    with tempfile.TemporaryDirectory(prefix="harness-thumb-") as work:
        frame = Path(work) / "frame.html"
        frame.write_text(
            f'<!doctype html><html><body style="margin:0;background:#fff">'
            f'<iframe src="{Path(html_path).resolve().as_uri()}" scrolling="no" '
            f'style="position:fixed;top:0;left:0;width:{w}px;height:{h}px;border:0"></iframe></body></html>')
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--mute-audio",
               "--no-first-run", "--no-default-browser-check", f"--user-data-dir={Path(work) / 'profile'}",
               f"--window-size={w},{h}", "--force-device-scale-factor=1",
               f"--screenshot={png_path}", frame.as_uri()]
        try:
            proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        deadline = time.monotonic() + timeout_s
        last_size = -1
        try:
            while time.monotonic() < deadline and proc.poll() is None:
                size = png_path.stat().st_size if png_path.is_file() else 0
                if size and size == last_size:
                    break
                last_size = size
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    return png_path.is_file() and png_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# the offline build
# ---------------------------------------------------------------------------

def _blocked_connect(*_args, **_kwargs):
    raise ConnectionError("harness library build runs offline: a network call was blocked")


@contextlib.contextmanager
def offline_run_env():
    """The scratch world the fake runs happen in (see the module docstring).
    Yields the scratch folder; everything is put back afterwards."""
    from . import notify

    scratch = Path(tempfile.mkdtemp(prefix="harness-library-"))
    cls = tenant_mod.Tenant
    props = {"out_dir": scratch / "out", "runs_dir": scratch / "runs", "evals_path": scratch / "evals" / "scores.jsonl"}
    saved_props = {name: cls.__dict__[name] for name in props}
    saved_env = os.environ.get("HARNESS_JEV_DRAFTS")
    saved_notify = notify.notify_needs_review
    saved_socket = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    try:
        for name, path in props.items():
            setattr(cls, name, property(lambda _self, _p=path: _p))
        os.environ["HARNESS_JEV_DRAFTS"] = "1"
        notify.notify_needs_review = lambda *_a, **_k: None
        socket.socket.connect = _blocked_connect
        socket.socket.connect_ex = _blocked_connect
        socket.create_connection = _blocked_connect
        yield scratch
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved_socket
        notify.notify_needs_review = saved_notify
        if saved_env is None:
            os.environ.pop("HARNESS_JEV_DRAFTS", None)
        else:
            os.environ["HARNESS_JEV_DRAFTS"] = saved_env
        for name, prop in saved_props.items():
            setattr(cls, name, prop)
        shutil.rmtree(scratch, ignore_errors=True)


def _fake_run_module():
    """evals/fake_run.py (and the tests/ doubles it imports) live beside the
    package in the repo, not in it -- put the repo root on sys.path."""
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from evals import fake_run

    return fake_run


def _fixture(tenant):
    """The demo ad: the founder text fixture (its canned brief has a
    quotable speaker, which the story first screen needs), else the
    tenant's first text fixture."""
    folder = Path(tenant.fixtures_dir)
    preferred = folder / FIXTURE
    if preferred.is_file():
        return preferred
    texts = sorted(folder.glob("*.txt")) if folder.is_dir() else []
    if not texts:
        raise LibraryError(f"no text fixture under {folder} to render the library from")
    return texts[0]


def _up_to_date(folder, sha):
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        return False
    if meta.get("sha") != sha:
        return False
    return bool(meta.get("unavailable")) or (folder / "review.html").is_file()


def _swap_in(staging, dest):
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # shutil.move copies across filesystems (prod stages in /tmp, which is a
    # different device from the tenant folder; a bare rename raised EXDEV there).
    shutil.move(str(staging), str(dest))


def _write_meta(folder, meta):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def _render_one(tenant, version, fixture, fake_run):
    """One fake run. Returns (run_dir, None) or (None, error line)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc, run_dir, _pages = fake_run.run_once(
                str(fixture), tenant=tenant.name, cartridges=version.cartridge, look=version.look,
                style=version.style, hero_style=version.first_screen,
            )
    except Exception as e:  # noqa: BLE001 -- one broken version must not stop the rest
        return None, f"{type(e).__name__}: {e}"
    if rc != 0:
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        return None, f"fake run exit {rc}" + (f": {lines[-1][:300]}" if lines else "")
    return run_dir, None


def _mismatch(version, page):
    """Why the rendered page is not this version, or None."""
    actual_fs = page.get("hero_style")
    if version.first_screen and actual_fs != version.first_screen:
        why = (" -- the face first screen needs a still from the ad, and the demo ad is a text ad"
               if version.first_screen == "face" else "")
        return f"not renderable offline: the run rendered the {actual_fs} first screen instead{why}"
    if version.look and page.get("look") and page["look"] != version.look:
        return f"not renderable offline: the run rendered the {page['look']} look instead of {version.look}"
    return None


def build(tenant, *, only=None, force=False, log=print):
    """Renders every version that is not up to date (all with force). `only`
    is a list of cartridge names. Returns {"built", "skipped",
    "unavailable", "failed" (lists of keys; failed as [key, reason]),
    "thumbs" (count), "browser" (path or None)}."""
    from . import review

    selected = versions(tenant)
    if only:
        known = set(pipeline.discover_cartridges())
        unknown = sorted(set(only) - known)
        if unknown:
            raise LibraryError(f"unknown cartridge(s) {unknown}; choose from {sorted(known)}")
        selected = [v for v in selected if v.cartridge in set(only)]
    browser = find_browser()
    summary = {"built": [], "skipped": [], "unavailable": [], "failed": [], "thumbs": 0, "browser": browser}
    if browser is None:
        log("no chromium, google-chrome or chromium-browser on PATH: no thumbnails (the page shows text cards)")
    root = Path(tenant.library_dir)
    todo = []
    for version in selected:
        sha = version_sha(tenant, version)
        if not force and _up_to_date(root / version.key, sha):
            summary["skipped"].append(version.key)
        else:
            todo.append((version, sha))
    if not todo:
        return summary
    fixture = _fixture(tenant)
    fake_run = _fake_run_module()
    with offline_run_env() as scratch:
        for i, (version, sha) in enumerate(todo, 1):
            dest = root / version.key
            meta = describe(tenant, version)
            meta.update(sha=sha, built_at=datetime.datetime.now().isoformat(timespec="seconds"),
                        demo_input=fixture.name)
            if not _renderable(tenant, version):
                reason = "an imported look looks.resolve_look does not accept for this tenant; listed from its library.json"
                _swap_in(_staged_meta(scratch, version, dict(meta, unavailable=reason)), dest)
                summary["unavailable"].append(version.key)
                log(f"[{i}/{len(todo)}] {version.key}: {reason}")
                continue
            run_dir, error = _render_one(tenant, version, fixture, fake_run)
            if error is None:
                page = json.loads((run_dir / version.cartridge / "page.json").read_text())
                reason = _mismatch(version, page)
                if reason:
                    _swap_in(_staged_meta(scratch, version, dict(meta, unavailable=reason)), dest)
                    summary["unavailable"].append(version.key)
                    log(f"[{i}/{len(todo)}] {version.key}: {reason}")
                    continue
                review_path = review.build_review_for_page(run_dir, version.cartridge)
                if review_path is None:
                    error = "the run wrote no index.html"
            if error is not None:
                summary["failed"].append([version.key, error])
                log(f"[{i}/{len(todo)}] {version.key}: FAILED {error}")
                continue
            staging = scratch / "staging" / version.key
            staging.mkdir(parents=True)
            src = run_dir / version.cartridge
            shutil.copy2(src / "index.html", staging / "index.html")
            if (src / "assets").is_dir():
                shutil.copytree(src / "assets", staging / "assets")
            shutil.copy2(review_path, staging / "review.html")
            meta["headline"] = page.get("headline") or (page.get("hero") or {}).get("headline")
            thumb = bool(browser) and capture_thumb(browser, staging / "review.html", staging / "thumb.png")
            summary["thumbs"] += int(thumb)
            meta["thumb"] = thumb
            _write_meta(staging, meta)
            _swap_in(staging, dest)
            summary["built"].append(version.key)
            log(f"[{i}/{len(todo)}] {version.key}: built" + (" + thumb" if thumb else ""))
    return summary


def _staged_meta(scratch, version, meta):
    staging = scratch / "staging" / version.key
    _write_meta(staging, meta)
    return staging


def queue_if_missing(tenant, *, by="harness serve"):
    """Called at review-site startup: when the library folder does not
    exist and no library job is waiting, queue one (the worker builds it).
    Returns the new job id or None."""
    from . import jobs

    if Path(tenant.library_dir).exists():
        return None
    if jobs.active_job(tenant, job_type="library_build", target=JOB_TARGET):
        return None
    return jobs.enqueue(tenant, "library_build", {"by": by, "force": False}, by=by, target=JOB_TARGET)
