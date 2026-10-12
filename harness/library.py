"""Cycle 86: the Library -- one reference render of every page version.

A "version" is a cartridge x one of its looks (looks.CARTRIDGE_LOOKS) and,
for the listicle, x its first screen x its copy style (listicle.STYLES).
Cycle 94: the first screens are the hero compositions the look renders
(harness/heroes/, the look's look.json); a landing look is one version for
its own first screen plus one per other composition it renders
(landing/<look>/<hero>). A cartridge with no looks is one version. A
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
a meta.json with an "unavailable" reason and no page. Cycle 99: a version
that fails to render and has no older page gets a meta.json with a "failed"
reason (retried on the next build), and the build first removes version
folders whose version no longer exists (a tenant look that was deleted, a
first screen a look no longer renders) and prints each one.

Cycle 99: the site shows one card per structure -- a (cartridge, look) --
and every first screen / style of that look as a variant inside the card
(`structures`).

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
from dataclasses import dataclass
from pathlib import Path

from . import heroes, listicle, looks, pipeline
from .blocks import catalog as block_catalog
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
# Cycle 99: the Library page's cartridge order; the others follow in discover order.
CARTRIDGE_ORDER = ("landing", "listicle")
# Cycle 105: a card's picture from the newest real run of its look, cached in
# tenants/<t>/library/<cartridge>/<look>/ beside the library thumb.
REAL_THUMB = "real-thumb.png"
REAL_META = "real-thumb.json"
REAL_CARTRIDGES = ("landing", "listicle")

CARTRIDGE_LABELS = {
    "article": "Article", "comparison": "Comparison", "landing": "Landing page", "listicle": "Listicle",
    "longform": "Long-form page", "product-page": "Product page", "quiz": "Quiz",
}
LOOK_NOTES = {
    ("landing", "open"): "Ad-first and mobile-first: headline, the ad's still, the price, checked claims, one CTA.",
    ("landing", "proof"): "The open page with the rating first, check-mark benefits, trust facts under the CTA "
                          "and an icon row.",
    ("landing", "cinematic"): "Built from blocks: a dark full-bleed first screen with the stats under it, a "
                              "photo essay, a feature grid, a dark statement band, the FAQ.",
    ("landing", "cinematic-light"): "The cinematic page on white: near-black type, the red accent, the "
                                    "statement first screen dark on white; photos and blocks as cinematic.",
    ("landing", "buybox"): "Built from blocks: a trust bar, the buy box first screen (price, specs, one pill), "
                           "the product with labelled hotspots, checked claims, the FAQ.",
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


def first_screen_note(hero_id):
    """The Library note for a first screen (cycle 94: every hero
    composition's own summary, harness/heroes/)."""
    hero = heroes.get(hero_id)
    return f"First screen: {hero.summary[:1].lower()}{hero.summary[1:]}" if hero and hero.summary else ""


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
    look wins there, as in looks.resolve_look. Cycle 102: so is an import
    that waits for "Add to Library" on its job page (library.json
    `library: "pending"`). Cycle 105: a non-listicle look whose look.json
    declares "heroes" gets one more version per hero that is not in its
    "default_heroes", as versions() does for the cartridge's own looks (the
    look's own version renders its default first screen)."""
    out = []
    for slug in looks.imported_looks(cartridge, tenant):
        if slug == DEFAULT_LOOK_DIR or looks.canonical(cartridge, slug) in looks.cartridge_looks(cartridge):
            continue
        if looks.pending_review(cartridge, slug, tenant):
            continue
        fs, style = IMPORTED_LISTICLE if cartridge == "listicle" else (None, None)
        out.append(Version(cartridge, slug, fs, style, tenant_look=True))
        if cartridge != "listicle" and heroes.has_heroes(cartridge):
            spec = heroes.look_spec(cartridge, slug, tenant)
            out += [Version(cartridge, slug, hero_id, tenant_look=True) for hero_id in spec["heroes"]
                    if hero_id not in spec["default_heroes"]]
    return out


def hidden_looks(tenant):
    """Cycle 103: tenant.yaml `library: {hide: ["<cartridge>:<look>", ...]}`
    as a set of "<cartridge>:<look>" strings ("article:default" for a
    cartridge with no looks). Only the Library reads it: a hidden look still
    renders when a run pins it."""
    config = getattr(tenant, "config", None) or {}
    hide = (config.get("library") or {}).get("hide") or []
    return {str(entry).strip() for entry in hide if str(entry).strip()}


def unknown_hidden(tenant):
    """The library.hide entries that name no structure, sorted."""
    hidden = hidden_looks(tenant)
    if not hidden:
        return []
    known = {f"{v.cartridge}:{v.look or DEFAULT_LOOK_DIR}" for v in versions(tenant, include_hidden=True)}
    return sorted(hidden - known)


def source_thumb(tenant, cartridge, slug):
    """Cycle 102: the path of an imported look's source thumbnail (the first
    phone screen of the page it was modelled on), or None."""
    from .look_vision import SOURCE_THUMB

    folder = looks.imported_looks_dir(cartridge, tenant)
    path = folder / slug / SOURCE_THUMB if folder is not None else None
    return path if path is not None and path.is_file() else None


def versions(tenant, *, include_hidden=False):
    """Every version, cartridge by cartridge (discover order), harness looks
    first, then the tenant's imported looks. Cycle 103: without
    include_hidden, the looks tenant.yaml's library.hide names are left
    out (hidden_looks)."""
    out = []
    for cartridge in pipeline.discover_cartridges():
        for look in looks.cartridge_looks(cartridge) or (None,):
            if cartridge == "listicle":
                out += [Version(cartridge, look, fs, style)
                        for fs in heroes.look_heroes(cartridge, look, tenant) for style in listicle.STYLES]
            else:
                out.append(Version(cartridge, look))
                # Cycle 94: one card per other composition the look renders.
                if look and heroes.has_heroes(cartridge):
                    spec = heroes.look_spec(cartridge, look, tenant)
                    out += [Version(cartridge, look, hero_id) for hero_id in spec["heroes"]
                            if hero_id not in spec["default_heroes"]]
        out += tenant_look_versions(tenant, cartridge)
    if include_hidden:
        return out
    hidden = hidden_looks(tenant)
    return [v for v in out if f"{v.cartridge}:{v.look or DEFAULT_LOOK_DIR}" not in hidden]


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
        text = ":".join(p for p in (version.cartridge, version.look, version.first_screen) if p)
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
    elif style or (hero_style and not heroes.has_heroes(cartridge)):
        raise LibraryError(f"{cartridge} has no first-screen or copy style")
    elif hero_style:
        # cycle 94: "<cartridge>:<look>:<hero>"; the look defaults to the cartridge's own
        text = ":".join((cartridge, look or looks.resolve_look(cartridge, None, tenant=tenant), hero_style))
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
        bits = [f"{style} style" if style else "",
                f"{_hero_name(first_screen_style)} first screen" if first_screen_style else ""]
        return f"{name}: " + ", ".join(b for b in bits if b)
    if first_screen_style:
        return f"{name}, {look} look, {_hero_name(first_screen_style)} first screen"
    return f"{name}, {look} look" if look else name


def _hero_name(hero_id):
    """The cycle 79 names stay as they were ("story"); a cycle 94
    composition reads by its title in lower case ("caps over photo")."""
    hero = heroes.get(hero_id)
    return hero_id if hero is None or hero.adapter else hero.title.lower()


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
        # cycle 90: the import's fidelity score (0-1), shown on the card
        if isinstance(lib.get("fidelity"), (int, float)):
            info["fidelity"] = round(float(lib["fidelity"]), 2)
    else:
        notes.append(LOOK_NOTES.get((version.cartridge, version.look), ""))
    if version.first_screen:
        notes.append(first_screen_note(version.first_screen))
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
    for an imported look, and whether the harness can render it); cycle 91:
    + the look's look.json and its declared catalog blocks' files."""
    cdir = pipeline.CARTRIDGES_DIR / version.cartridge
    files = [cdir / "template.html", cdir / "cartridge.md", cdir / "schema.json"]
    if version.tenant_look:
        ldir = looks.imported_looks_dir(version.cartridge, tenant) / version.look
        files += [ldir / "template.html", ldir / "library.json"]
    elif version.look:
        files.append(cdir / "looks" / version.look / "template.html")
    # Cycle 94: the look's look.json and the partials of the first screens
    # this version can render (its own, or the look's default pool).
    look_file = None
    if version.look:
        look_file = (looks.imported_looks_dir(version.cartridge, tenant) / version.look / heroes.LOOK_FILE
                     if version.tenant_look else cdir / "looks" / version.look / heroes.LOOK_FILE)
    if look_file is not None and heroes.has_heroes(version.cartridge):
        files.append(look_file)
        hero_ids = ([version.first_screen] if version.first_screen
                    else heroes.look_spec(version.cartridge, version.look, tenant)["default_heroes"])
        files += [heroes.PACKAGE_DIR / f"{h}.html" for h in hero_ids]
    # Cycle 91: the catalog blocks the look declares (their module and
    # partial), so a block change rebuilds the versions using it.
    if look_file is not None:
        if look_file not in files:
            files.append(look_file)
        try:
            declared = block_catalog.look_blocks(version.cartridge, version.look, tenant=tenant)
        except ValueError:
            declared = ()
        for block_id in declared:
            files += [block_catalog.CATALOG_DIR / f"{block_id}.py", block_catalog.CATALOG_DIR / f"{block_id}.html"]
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


def _entry(tenant, version):
    folder = Path(tenant.library_dir) / version.key
    meta = read_meta(tenant, version)
    entry = dict(meta) if meta else describe(tenant, version)
    entry.update(key=version.key, built=meta is not None,
                 has_review=(folder / "review.html").is_file(), has_thumb=(folder / "thumb.png").is_file())
    return entry


def entries(tenant):
    """Every version for the site: its meta.json when built (else its
    description), plus key, built, has_review, has_thumb."""
    return [_entry(tenant, version) for version in versions(tenant)]


# ---------------------------------------------------------------------------
# cycle 99: one structure per (cartridge, look), its versions as variants
# ---------------------------------------------------------------------------

def origin(tenant, version):
    """"shipped" (a harness look, or a cartridge with no looks), "imported"
    (a cycle 87 import) or "hand-converted" (a tenant look whose
    library.json source records a hand conversion, or a hand-made pack
    look, source.kind "pack")."""
    if not version.tenant_look:
        return "shipped"
    source = _tenant_look_info(tenant, version).get("source")
    if isinstance(source, dict) and (source.get("converted") or source.get("kind") == "pack"):
        return "hand-converted"
    return "imported"


def variant_label(tenant, version):
    """The variant's name inside its structure's card: the first screen
    ("Story", "Caps over photo"), with the style for the listicle
    ("Story · myths"). A landing look's own version names its default
    first screen(s)."""
    hero_ids = [version.first_screen] if version.first_screen else []
    if not hero_ids and version.look and heroes.has_heroes(version.cartridge):
        try:
            hero_ids = list(heroes.look_spec(version.cartridge, version.look, tenant)["default_heroes"])
        except (KeyError, ValueError):
            hero_ids = []
    names = [heroes.get(h).title if heroes.get(h) else h for h in hero_ids]
    name = " or ".join(names) or "Default"
    return f"{name} \u00b7 {version.style}" if version.style else name


def _variant_state(entry):
    """("ready", None) for a variant with a reference page, else ("off",
    why) -- not renderable offline, failed, or not built yet."""
    if entry.get("unavailable"):
        return "off", entry["unavailable"]
    if entry.get("failed"):
        return "off", f"failed: {entry['failed']}"
    if not entry["has_review"]:
        return "off", "not built yet"
    return "ready", None


def structures(tenant):
    """The Library page's cards: one dict per (cartridge, look), in page
    order (CARTRIDGE_ORDER, then discover order; looks in versions()
    order) -- cartridge, look, key, title, summary, purpose, origin,
    variants (entries() rows plus label, state, why), default (the first
    ready variant, else the first), design (the variant row with the best
    design score, or None), fidelity (the best, or None)."""
    found = {}
    for version in versions(tenant):
        look_key = f"{version.cartridge}/{version.look or DEFAULT_LOOK_DIR}"
        if look_key not in found:
            info = describe(tenant, Version(version.cartridge, version.look, tenant_look=version.tenant_look))
            title = info["title"]
            if not version.tenant_look:
                title = label(version.cartridge, version.look)
            notes = info["notes"] if version.tenant_look else LOOK_NOTES.get((version.cartridge, version.look), "")
            found[look_key] = {"cartridge": version.cartridge, "look": version.look, "key": look_key,
                               "title": title, "summary": notes or info["purpose"], "purpose": info["purpose"],
                               "origin": origin(tenant, version), "variants": [],
                               # cycle 102: what an imported look was modelled on
                               "imported_from": info.get("imported_from"),
                               "source_thumb": bool(version.tenant_look
                                                    and source_thumb(tenant, version.cartridge, version.look)),
                               # cycle 105: the newest real run's phone screen, or None
                               "real": real_picture(tenant, look_key)}
        entry = _entry(tenant, version)
        state, why = _variant_state(entry)
        entry.update(label=variant_label(tenant, version), state=state, why=why)
        found[look_key]["variants"].append(entry)
    order = list(CARTRIDGE_ORDER) + [c for c in pipeline.discover_cartridges() if c not in CARTRIDGE_ORDER]
    out = sorted(found.values(), key=lambda s: order.index(s["cartridge"]) if s["cartridge"] in order else len(order))
    for item in out:
        variants = item["variants"]
        item["default"] = next((v for v in variants if v["state"] == "ready"), variants[0])
        scored = [v for v in variants if v.get("design_score") is not None]
        item["design"] = (max(scored, key=lambda v: v["design_score"]) if scored
                          else next((v for v in variants if v.get("design_status")), None))
        fids = [float(v["fidelity"]) for v in variants if isinstance(v.get("fidelity"), (int, float))]
        item["fidelity"] = max(fids) if fids else None
    return out


# ---------------------------------------------------------------------------
# cycle 105: a real run's phone screen as the card picture
# ---------------------------------------------------------------------------

def structure_keys(tenant):
    """The "<cartridge>/<look>" key of every structure on the page."""
    return {f"{v.cartridge}/{v.look or DEFAULT_LOOK_DIR}" for v in versions(tenant)}


def real_runs(tenant):
    """{"<cartridge>/<look>": the newest real run of that look} for the
    landing and listicle pages under tenants/<t>/out/. A real run has a
    state.json that is not a dry run (`harness run`, `harness abtest
    create`); the FakeClient runs (tests, `python -m evals.fake_run`) are
    dry runs, and the offline library build never writes to out/. A run
    counts for the look its <cartridge>/page.json was stamped with. Each
    value: run_id, run_dir, cartridge, look, at (the run's first history
    time)."""
    from . import runstate

    found = {}
    out = Path(tenant.out_dir)
    if not out.is_dir():
        return found
    for run_dir in out.iterdir():
        if not run_dir.is_dir():
            continue
        try:
            state = runstate.load_state(run_dir)
        except (FileNotFoundError, ValueError):
            continue
        if not isinstance(state, dict) or state.get("dry_run"):
            continue
        at = next((h.get("at") for h in state.get("history") or [] if isinstance(h, dict) and h.get("at")), "")
        for cartridge in REAL_CARTRIDGES:
            if not (run_dir / cartridge / "index.html").is_file():
                continue
            try:
                page = json.loads((run_dir / cartridge / "page.json").read_text())
            except (OSError, ValueError):
                continue
            look = looks.canonical(cartridge, page.get("look")) if isinstance(page, dict) else None
            if not look:
                continue
            key = f"{cartridge}/{look}"
            record = {"run_id": run_dir.name, "run_dir": run_dir, "cartridge": cartridge, "look": look,
                      "at": str(at)}
            if key not in found or (record["at"], record["run_id"]) > (found[key]["at"], found[key]["run_id"]):
                found[key] = record
    return found


def real_picture(tenant, look_key):
    """The cached real-run picture of a structure: {run_id, at, source}
    from real-thumb.json when real-thumb.png is there, else None."""
    folder = Path(tenant.library_dir) / look_key
    if not (folder / REAL_THUMB).is_file():
        return None
    try:
        data = json.loads((folder / REAL_META).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("run_id") else None


def _design_phone(run):
    """The run's design-gate phone screenshot of the chosen variant
    (<cartridge>/design/variant-<chosen>.png), or None."""
    from . import design_gate

    page_dir = run["run_dir"] / run["cartridge"]
    scores = design_gate.load_scores(page_dir) or {}
    chosen = scores.get("chosen")
    if chosen is None:
        return None
    path = design_gate.design_dir_for(page_dir) / f"variant-{chosen}.png"
    return path if path.is_file() else None


def real_pictures(tenant, *, only=None, browser=None, log=print):
    """Cycle 105: for every landing / listicle structure, the newest real
    run's phone screen cached as <cartridge>/<look>/real-thumb.png (the
    design gate's chosen variant screenshot when the run has one, else a
    390x844 capture of its index.html); refreshed when a newer run
    appears. Logs one line per structure with the source it uses. Returns
    the count of structures with a real picture."""
    runs = real_runs(tenant)
    root = Path(tenant.library_dir)
    count = 0
    for look_key in sorted(structure_keys(tenant), key=lambda k: (k.split("/")[0], k)):
        cartridge = look_key.split("/")[0]
        if cartridge not in REAL_CARTRIDGES or (only and cartridge not in only):
            continue
        run = runs.get(look_key)
        if run is None:
            log(f"{look_key}: picture from the offline render (no real run of this look)")
            continue
        folder = root / look_key
        cached = real_picture(tenant, look_key)
        if cached and cached.get("run_id") == run["run_id"]:
            count += 1
            log(f"{look_key}: picture from real run {run['run_id']} (up to date)")
            continue
        folder.mkdir(parents=True, exist_ok=True)
        phone = _design_phone(run)
        if phone is not None:
            shutil.copy2(phone, folder / REAL_THUMB)
            source = f"design {phone.name}"
        elif browser and capture_thumb(browser, run["run_dir"] / run["cartridge"] / "index.html",
                                       folder / REAL_THUMB, tenant=tenant):
            source = "captured index.html at 390"
        else:
            log(f"{look_key}: picture from the offline render (real run {run['run_id']} has no design "
                "screenshot and no browser to capture it)")
            continue
        (folder / REAL_META).write_text(json.dumps(
            {"run_id": run["run_id"], "cartridge": run["cartridge"], "at": run["at"], "source": source},
            indent=2) + "\n")
        count += 1
        log(f"{look_key}: picture from real run {run['run_id']} ({source})")
    return count


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def find_browser():
    """A headless-capable Chromium, or None (harness/capture.py: $HARNESS_CHROME,
    PATH, then the macOS application bundle)."""
    from . import capture

    return capture.find_browser()


def thumb_fonts(browser, html_path, tenant):
    """Cycle 92: {"status": loaded|fallback|unknown, "loaded", "fallback", ...}
    for the families the reference page's text uses, from the same capture
    path (harness/capture.py's probe) the thumbnail went through."""
    from . import capture, design_gate

    w, h = THUMB_SIZE
    return design_gate.font_report(capture.probe(browser, html_path, width=w, height=h, tenant=tenant))


def capture_thumb(browser, html_path, png_path, *, popen=subprocess.Popen, timeout_s=THUMB_TIMEOUT_S, tenant=None):
    """A 390x844 screenshot at scale 1 of html_path. True when thumb.png
    was written; any browser failure is False (never raised). Cycle 92: the
    shared capture path (harness/capture.py) -- the tenant's local brand
    fonts are injected and the page waits for document.fonts.ready, so a
    thumbnail never shows a fallback face because the host has no fonts."""
    from . import capture

    w, h = THUMB_SIZE
    tenant = tenant or tenant_mod.active_or_none()
    return capture.screenshot(browser, html_path, png_path, width=w, height=h, tenant=tenant, popen=popen,
                              timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# the offline build
# ---------------------------------------------------------------------------

def _blocked_connect(*_args, **_kwargs):
    raise ConnectionError("harness library build runs offline: a network call was blocked")


@contextlib.contextmanager
def offline_run_env(base_dir=None):
    """The scratch world the fake runs happen in (see the module docstring).
    Yields the scratch folder; everything is put back afterwards."""
    from . import notify

    # base_dir: the build passes the tenant library folder so staging, the fake
    # runs and the thumbnail work all stay on the same filesystem as the
    # destination (prod /tmp is another device, and snap Chromium cannot see it).
    if base_dir is not None:
        Path(base_dir).mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="harness-library-", dir=base_dir))
    cls = tenant_mod.Tenant
    props = {"out_dir": scratch / "out", "runs_dir": scratch / "runs", "evals_path": scratch / "evals" / "scores.jsonl"}
    saved_props = {name: cls.__dict__[name] for name in props}
    saved_env = os.environ.get("HARNESS_JEV_DRAFTS")
    saved_gate = os.environ.get("HARNESS_DESIGN_GATE")
    saved_notify = notify.notify_needs_review
    saved_socket = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    try:
        for name, path in props.items():
            setattr(cls, name, property(lambda _self, _p=path: _p))
        os.environ["HARNESS_JEV_DRAFTS"] = "1"
        # Cycle 92: no design gate inside the offline fake runs (no model call
        # is possible here); `harness library build --design-score` scores the
        # versions afterwards.
        os.environ["HARNESS_DESIGN_GATE"] = "off"
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
        if saved_gate is None:
            os.environ.pop("HARNESS_DESIGN_GATE", None)
        else:
            os.environ["HARNESS_DESIGN_GATE"] = saved_gate
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
        hero = heroes.get(version.first_screen)
        if version.first_screen == "face":
            why = " -- the face first screen needs a still from the ad, and the demo ad is a text ad"
        elif hero is not None and (hero.needs or {}).get("image"):
            why = (f" -- the {version.first_screen} first screen needs {heroes.need_text(hero)}, and none was "
                   "usable offline (a text ad has no still; the photo library files are not on this machine)")
        else:
            why = ""
        return f"not renderable offline: the run rendered the {actual_fs} first screen instead{why}"
    if version.look and page.get("look") and page["look"] != version.look:
        return f"not renderable offline: the run rendered the {page['look']} look instead of {version.look}"
    return None


def build(tenant, *, only=None, force=False, log=print):
    """Renders every version that is not up to date (all with force). `only`
    is a list of cartridge names. Returns {"built", "skipped",
    "unavailable", "failed" (lists of keys; failed as [key, reason]),
    "thumbs" (count), "browser" (path or None), "real" (structures with a
    real-run picture, cycle 105)}."""
    from . import review

    selected = versions(tenant)
    if only:
        known = set(pipeline.discover_cartridges())
        unknown = sorted(set(only) - known)
        if unknown:
            raise LibraryError(f"unknown cartridge(s) {unknown}; choose from {sorted(known)}")
        selected = [v for v in selected if v.cartridge in set(only)]
    browser = find_browser()
    for entry in unknown_hidden(tenant):   # cycle 103
        log(f"tenant.yaml library.hide: {entry!r} names no Library structure; ignored")
    summary = {"built": [], "skipped": [], "unavailable": [], "failed": [], "thumbs": 0, "browser": browser,
               "pruned": prune(tenant, only=only, log=log)}
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
        summary["real"] = real_pictures(tenant, only=only, browser=browser, log=log)
        return summary
    fixture = _fixture(tenant)
    fake_run = _fake_run_module()
    with offline_run_env(base_dir=tenant.library_dir) as scratch:
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
                review_path = review.build_review_for_page(run_dir, version.cartridge, tenant=tenant)
                if review_path is None:
                    error = "the run wrote no index.html"
            if error is not None:
                summary["failed"].append([version.key, error])
                log(f"[{i}/{len(todo)}] {version.key}: FAILED {error}")
                # cycle 99: the card lists why; an older page, when there is one, stays
                if not (dest / "review.html").is_file():
                    _swap_in(_staged_meta(scratch, version, dict(meta, failed=error)), dest)
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
            if thumb:
                # Cycle 92: which brand fonts the thumbnail browser actually loaded.
                meta["fonts"] = thumb_fonts(browser, staging / "review.html", tenant)
            _write_meta(staging, meta)
            _swap_in(staging, dest)
            summary["built"].append(version.key)
            log(f"[{i}/{len(todo)}] {version.key}: built" + (" + thumb" if thumb else ""))
    # cycle 105: after the renders, since a rebuilt look folder is replaced whole
    summary["real"] = real_pictures(tenant, only=only, browser=browser, log=log)
    return summary


def _staged_meta(scratch, version, meta):
    staging = scratch / "staging" / version.key
    _write_meta(staging, meta)
    return staging


def prune(tenant, *, only=None, log=print):
    """Cycle 99: removes every folder under the library with a meta.json
    whose key is no longer a version (its look, or the first screen, was
    removed), and the empty folders left above it. `only` keeps the prune
    to those cartridges; a build's scratch folder is never touched. Returns
    the removed keys."""
    root = Path(tenant.library_dir)
    if not root.is_dir():
        return []
    keep = {v.key for v in versions(tenant)}
    removed = []
    for meta in sorted(root.rglob("meta.json"), key=lambda p: len(p.parts)):
        folder = meta.parent
        key = folder.relative_to(root).as_posix()
        top = key.split("/")[0]
        if key in keep or not folder.is_dir() or top.startswith("harness-library-") or (only and top not in only):
            continue
        shutil.rmtree(folder)
        removed.append(key)
        log(f"pruned {key}: no such version any more")
        parent = folder.parent
        while parent != root and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return removed


# ---------------------------------------------------------------------------
# cycle 92: design scores on the Library cards
# ---------------------------------------------------------------------------

def versions_to_score(tenant, *, force=False):
    """Built versions with a page (index.html) and, unless force, no design
    score yet."""
    from . import design_gate

    out = []
    for version in versions(tenant):
        folder = Path(tenant.library_dir) / version.key
        if not (folder / "index.html").is_file():
            continue
        if not force and design_gate.load_scores(folder) is not None:
            continue
        out.append(version)
    return out


def score_versions(tenant, todo, *, client, cfg, log=None, say=print):
    """Scores each version folder with the design gate's judge (no
    variants) and puts design_score / design_status / design_cost_usd in
    its meta.json. Returns how many got a score."""
    from . import design_gate

    scored = 0
    for i, version in enumerate(todo, 1):
        folder = Path(tenant.library_dir) / version.key
        layout = {"look": version.look, "hero_style": version.first_screen, "block_set": None}
        result = design_gate.score_folder(folder, cartridge=version.cartridge, tenant=tenant, client=client,
                                          cfg=cfg, log=log, layout=layout)
        meta = read_meta(tenant, version) or describe(tenant, version)
        meta.update(design_score=result["score"], design_status=result["status"],
                    design_cost_usd=result["cost_usd"], design_scored_at=result["scored_at"])
        _write_meta(folder, meta)
        scored += int(result["score"] is not None)
        say(f"[{i}/{len(todo)}] {version.key}: {design_gate.summary_line(result)}")
    return scored


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
