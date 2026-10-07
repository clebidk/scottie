"""Cycle 94: pluggable hero compositions -- the first screen as a part every
look shares (docs/HEROES.md).

A composition is one module in this package plus one Jinja partial next to
it (`heroes/<id>.html`). The module calls register(Hero(...)) with:

  id        the value of `hero_style` (page.json, --hero-style, an A/B/C arm)
  title     the human name (site chips, the Library)
  summary   one sentence for the Library card and docs/HEROES.md
  needs     what the composition must have to render as itself:
              {"image": {...} | None, "required": bool}
            the image spec is read by pick_image() (sources in preference
            order: "ad_still", "library", "cutout"; shots, settings,
            orient, dark_ok). A composition whose required image is not
            available falls back (`fallbacks`, then the look's defaults).
  supports  the cartridges it can render in
  context   context(page, ad_brief, facts_pack, tenant, image, *, cartridge,
            base, look) -> dict: what the partial prints that the writer did
            not write as such (renderer-owned; never a new writer field)
  fallbacks the compositions to try, in order, when its needs are not met
            (a callable (quote) -> tuple, or a tuple)
  adapter   True for the four compositions that were already inside a look
            before this cycle (face, story, display, ad_first): their
            partials print exactly what the look printed, and the look keeps
            their CSS, so a shipped look's output did not change.

Looks pick the partial with `{% include "heroes/_pick.html" %}`; the look's
look.json lists the compositions it supports and its default pool:

  cartridges/<c>/looks/<look>/look.json
    {"heroes": [<every id the look renders>],
     "default_heroes": [<the ids a run with no request is seeded among>]}

A look with no look.json (a tenant's imported look, cycle 87) supports its
cartridge's legacy compositions only: face/story/display for the listicle,
ad_first for the landing page.

Selection (resolve): the request (--hero-style, page.json's own value, an
A/B/C arm) when the look supports it and its needs are met; else its
fallbacks; else a pick seeded from the run among the look's default pool
(tenant.yaml `cartridges.<c>.hero_styles` narrows that pool to its own
order). Every choice comes with a reason the renderer logs.

Images go through ONE function, pick_image(): a simple local matcher over
the run's ad still, the photo-library tags (brand/photo-library.json) of the
photos the facts pack offers, and the product's cut-out. Cycle 93's resolver
will replace the matcher behind the same call.
"""
import importlib
import json
import pkgutil
import random
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
LOOK_FILE = "look.json"

# The compositions a cartridge had before this cycle, in their old order --
# what a look with no look.json supports, and its default pool.
LEGACY = {
    "listicle": ("face", "story", "display"),
    "landing": ("ad_first",),
}


@dataclass(frozen=True)
class Hero:
    id: str
    title: str
    supports: tuple
    context: object
    needs: dict = field(default_factory=dict)
    fallbacks: object = ()
    summary: str = ""
    adapter: bool = False

    def fallback_ids(self, quote=False):
        fb = self.fallbacks(quote) if callable(self.fallbacks) else self.fallbacks
        return tuple(fb or ())


REGISTRY = {}


def register(hero):
    if hero.id in REGISTRY:
        raise ValueError(f"hero composition {hero.id!r} registered twice")
    REGISTRY[hero.id] = hero
    return hero


def get(hero_id):
    return REGISTRY.get(hero_id)


def ids(cartridge=None):
    """Every registered composition id (for one cartridge when given), in a
    stable order: the legacy ones first, then the rest by name."""
    legacy = [i for c in ("listicle", "landing") for i in LEGACY[c]]
    order = legacy + sorted(i for i in REGISTRY if i not in legacy)
    return tuple(i for i in order if i in REGISTRY and (cartridge is None or cartridge in REGISTRY[i].supports))


def cartridges():
    """The cartridges at least one composition supports."""
    return tuple(sorted({c for h in REGISTRY.values() for c in h.supports}))


def has_heroes(cartridge):
    return cartridge in cartridges()


def requested_for(cartridge, requested):
    """The run-wide --hero-style as it applies to one cartridge: the value
    when this cartridge supports it, else None (like looks.requested_for)."""
    hero = REGISTRY.get(requested or "")
    return requested if hero and cartridge in hero.supports else None


def arg(value):
    """argparse `type` for --hero-style: any registered composition."""
    import argparse

    if value in REGISTRY:
        return value
    raise argparse.ArgumentTypeError(f"invalid choice: {value!r} (choose from {', '.join(ids())})")


# ---------------------------------------------------------------------------
# What a look supports
# ---------------------------------------------------------------------------

def _look_file(cartridge, look, tenant=None):
    from .. import looks as looks_mod
    from ..config import REPO_ROOT

    if not look:
        return None
    if look in looks_mod.cartridge_looks(cartridge):
        return REPO_ROOT / "cartridges" / cartridge / "looks" / look / LOOK_FILE
    folder = looks_mod.imported_looks_dir(cartridge, tenant)
    if folder is not None and looks_mod.LOOK_SLUG_RE.match(look):
        return folder / look / LOOK_FILE
    return None


def look_spec(cartridge, look, tenant=None):
    """{"heroes": (...), "default_heroes": (...)} for one look, read from
    its look.json; the cartridge's legacy compositions when there is none.
    Unknown ids and ids for another cartridge are dropped."""
    legacy = LEGACY.get(cartridge, ())
    spec = {}
    path = _look_file(cartridge, look, tenant)
    if path is not None and path.is_file():
        try:
            spec = json.loads(path.read_text())
        except (OSError, ValueError):
            spec = {}
    supported = ids(cartridge)
    heroes = tuple(h for h in (spec.get("heroes") or legacy) if h in supported)
    defaults = tuple(h for h in (spec.get("default_heroes") or heroes) if h in heroes)
    return {"heroes": heroes or tuple(h for h in legacy if h in supported),
            "default_heroes": defaults or tuple(h for h in legacy if h in supported)}


def look_heroes(cartridge, look, tenant=None):
    return look_spec(cartridge, look, tenant)["heroes"]


def default_pool(cartridge, look, tenant=None):
    """The compositions a run with no request is seeded among: tenant.yaml
    `cartridges.<c>.hero_styles` (filtered to what the look supports, in
    the pin's order) when it names any, else the look's default_heroes."""
    from .. import tenant as tenant_mod

    tenant = tenant or tenant_mod.active()
    spec = look_spec(cartridge, look, tenant)
    pinned = tenant.get(f"cartridges.{cartridge}.hero_styles") or ()
    chosen = tuple(h for h in pinned if h in spec["heroes"])
    return chosen or spec["default_heroes"]


def seeded(cartridge, seed, pool):
    """The seeded pick (the listicle's key is the cycle 79 one, so a run
    picks what it picked before)."""
    return random.Random(f"{cartridge}-hero-style:{seed}").choice(tuple(pool))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve(cartridge, requested=None, *, look=None, seed=0, tenant=None, available=None, quote=False):
    """(id, reason, note). `available(id) -> (ok, why)` says whether a
    composition's needs are met (default: always). `note` is non-empty only
    when the request (or the seeded pick) could not render as itself -- the
    line the renderer and the pipeline log as "hero style: <note>".
    Raises ValueError for a request that is no composition of this
    cartridge."""
    available = available or (lambda _id: (True, ""))
    if requested:
        hero = REGISTRY.get(requested)
        if hero is None or cartridge not in hero.supports:
            raise ValueError(f"unknown hero style {requested!r} for {cartridge}; choose one of {list(ids(cartridge))}")
    supported = look_heroes(cartridge, look, tenant)
    pool = default_pool(cartridge, look, tenant)
    note = ""
    if requested and requested not in supported:
        want = seeded(cartridge, seed, pool)
        note = f"the {look} look does not render {requested}, so {want} (seeded)"
        reason = note
    elif requested:
        want, reason = requested, "requested"
    else:
        want, reason = seeded(cartridge, seed, pool), f"seeded from the run among {list(pool)}"
    ok, why = available(want)
    if ok:
        return want, reason, note
    tried = [want]
    candidates = list(REGISTRY[want].fallback_ids(quote)) + [h for h in pool if h != want]
    for alt in candidates:
        if alt in tried or alt not in supported:
            continue
        tried.append(alt)
        alt_ok, _alt_why = available(alt)
        if alt_ok:
            note = f"{want} needs {why}; none usable, so {alt}"
            return alt, note, note
    last = pool[0]
    note = f"{want} needs {why}; nothing else usable, so {last}"
    return last, note, note


# ---------------------------------------------------------------------------
# Images: the one call (cycle 93's resolver replaces the matcher behind it)
# ---------------------------------------------------------------------------

def _library_photos(tenant):
    from .. import photo_library

    try:
        return photo_library.photos_by_id(photo_library.load_manifest(tenant))
    except Exception:  # noqa: BLE001 -- a bad manifest only means no library photos
        return {}


def _luma(path):
    """Mean luminance 0-255 of a local image, or None."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("L")
            im.thumbnail((64, 64))
            data = list(im.getdata())
        return sum(data) / max(1, len(data))
    except Exception:  # noqa: BLE001 -- unreadable: no luminance evidence
        return None


def _orient(width, height):
    if not width or not height:
        return None
    ratio = width / height
    return "wide" if ratio >= 1.2 else ("portrait" if ratio <= 0.9 else "square")


def _library_candidates(spec, *, assets, tenant, exclude_ids, require_files):
    from .. import photo_library

    photos = _library_photos(tenant)
    shots = tuple(spec.get("shots") or ())
    settings = tuple(spec.get("settings") or ())
    out = []
    for asset_id, asset in assets.items():
        photo = photos.get(asset_id)
        if photo is None or asset_id in exclude_ids or not photo_library._usable(photo):
            continue
        tags = photo["tags"]
        # Above the fold the page shows the current brand only (cycle 78).
        if photo_library.shows_old_logo(photo) or tags.get("blurry"):
            continue
        if shots and tags["shot"] not in shots:
            continue
        path = photo_library.local_file(tenant, photo)
        if require_files and not path.exists():
            continue
        orient = _orient(photo.get("width"), photo.get("height"))
        luma = _luma(path) if spec.get("dark_ok") and path.exists() else None
        score = (
            (2 if spec.get("orient") and orient == spec["orient"] else 0)
            + (1 if settings and tags["setting"] in settings else 0)
            + (1 if luma is not None and luma <= 150 else 0)
            - (1 if tags.get("cluttered") else 0)
            + (shots.index(tags["shot"]) * -0.1 if shots else 0)
        )
        out.append((score, photo_library.hero_rank(photo), asset_id, photo, orient))
    out.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return out


def pick_image(needs, *, page=None, facts_pack=None, tenant=None, assets=None, frame=None, exclude_ids=(),
               require_files=False):
    """The image a composition shows, or None.

    {"id", "source": "ad_still"|"library"|"cutout", "fit": "cover"|
    "contain", "position": CSS object-position, "orient", "why"} --
    `assets` is the renderer's assets_by_id (a cut-out the page does not
    hold yet is added to it). Sources are tried in the need's order: the
    run's ad still (asset-adframe), then the library photos the facts pack
    offers ranked by the need's shots / settings / orientation / darkness
    (tags from brand/photo-library.json; never one with the retired mark or
    a text overlay, never one already on the page), then the product's
    cut-out. `require_files` (a real render) skips a library photo whose
    file is not on this machine."""
    from .. import ad_frames, cutouts

    spec = (needs or {}).get("image")
    if not spec:
        return None
    assets = assets if assets is not None else {}
    exclude = set(exclude_ids or ())
    for source in spec.get("sources") or ():
        if source == "ad_still" and ad_frames.FRAME_ID in assets:
            position = (frame or {}).get("object_position") or "50% 30%"
            return {"id": ad_frames.FRAME_ID, "source": "ad_still", "fit": "cover", "position": position,
                    "orient": "portrait", "why": "the ad's own still"}
        if source == "library" and tenant is not None:
            rows = _library_candidates(spec, assets=assets, tenant=tenant, exclude_ids=exclude,
                                       require_files=require_files)
            if rows:
                _score, _rank, asset_id, photo, orient = rows[0]
                # a photo with people keeps their heads in a wide crop
                position = "50% 30%" if photo["tags"].get("people") else "50% 50%"
                return {"id": asset_id, "source": "library", "fit": "cover", "position": position,
                        "orient": orient, "why": f"library photo ({photo['tags']['shot']}, {photo['tags']['setting']})"}
        if source == "cutout" and tenant is not None:
            asset = cutouts.cutout_for_product(tenant, (facts_pack or {}).get("product"))
            if asset is not None:
                assets.setdefault(asset["id"], dict(asset))
                return {"id": asset["id"], "source": "cutout", "fit": "contain", "position": "50% 50%",
                        "orient": "portrait", "why": "the product's cut-out"}
    return None


def need_text(hero):
    """A short phrase for what a composition's image need is."""
    spec = (hero.needs or {}).get("image") or {}
    return spec.get("label") or "an image"


# ---------------------------------------------------------------------------
# The renderer's entry point
# ---------------------------------------------------------------------------

def render_context(cartridge, page, *, look, ad_brief, facts_pack, tenant, base, assets, frame, seed,
                   require_files=False, exclude_ids=(), quote=False):
    """(hero context for the template, note). Resolves the composition
    (resolve) with availability from pick_image, then builds what its
    partial prints. A page.json `hero_style` that is no composition of this
    cartridge (a hand edit) renders the seeded pick, with a note."""
    cache = {}

    def image_for(hero_id):
        if hero_id not in cache:
            cache[hero_id] = pick_image(REGISTRY[hero_id].needs, page=page, facts_pack=facts_pack, tenant=tenant,
                                        assets=assets, frame=frame, exclude_ids=exclude_ids,
                                        require_files=require_files)
        return cache[hero_id]

    def available(hero_id):
        hero = REGISTRY[hero_id]
        if (hero.needs or {}).get("required") and image_for(hero_id) is None:
            return False, need_text(hero)
        return True, ""

    requested = page.get("hero_style") or None
    try:
        hero_id, reason, note = resolve(cartridge, requested, look=look, seed=seed, tenant=tenant,
                                        available=available, quote=quote)
    except ValueError as e:
        hero_id, reason, note = resolve(cartridge, None, look=look, seed=seed, tenant=tenant,
                                        available=available, quote=quote)
        note = reason = f"{e}; rendered {hero_id}"
    hero = REGISTRY[hero_id]
    image = image_for(hero_id) if (hero.needs or {}).get("image") else None
    ctx = {"id": hero_id, "title": hero.title, "reason": reason, "pattern": f"hero:{hero_id}",
           "image": image, "adapter": hero.adapter}
    ctx.update(hero.context(page, ad_brief, facts_pack, tenant, image, cartridge=cartridge, base=base, look=look))
    return ctx, note


# ---------------------------------------------------------------------------
# A look's source with its legacy first screen written back in
# ---------------------------------------------------------------------------

_INCLUDE = '{% include "heroes/_pick.html" %}'
_LANDING_ANCHOR = 'document.querySelector("{{ hero.anchor }}")'


def _adapter_body(hero_id):
    """An adapter partial without its leading comment (exactly what the look
    held before cycle 94)."""
    text = (PACKAGE_DIR / f"{hero_id}.html").read_text()
    return text[text.index("#}") + 2:]


def standalone_source(text, cartridge, look):
    """`text` (a look template) with `{% include "heroes/_pick.html" %}`
    replaced by the cartridge's legacy first screen inline -- the
    face/story/display branches for the listicle, the look's own ad_first
    branch for the landing page -- and the sticky bar's anchor written out.
    For the shipped looks this is their pre-cycle-94 source byte for byte.
    Cycle 87's look import shows the model this form (an imported look is
    one file, no include) and its tests validate it."""
    if _INCLUDE not in text:
        return text
    if cartridge == "listicle":
        block = ('{% if fs.hero_style == "face" %}' + _adapter_body("face")
                 + '{% elif fs.hero_style == "story" %}' + _adapter_body("story")
                 + "{% else %}" + _adapter_body("display") + "{% endif %}")
    elif cartridge == "landing":
        body = _adapter_body("ad_first")
        proof_open = '{% if look == "proof" %}'
        split_at = body.index("{% else %}<header")
        proof, rest = body[len(proof_open):split_at], body[split_at + len("{% else %}"):]
        block = proof if look == "proof" else rest[:-len("{% endif %}")]
        text = text.replace(_LANDING_ANCHOR, 'document.querySelector(".adv-landing .ld-hero")')
    else:
        return text
    return text.replace(_INCLUDE, block)


def _discover():
    for info in pkgutil.iter_modules([str(PACKAGE_DIR)]):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")


from . import _common  # noqa: E402  (the shared field builder the modules use)

_discover()
