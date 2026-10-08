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

Images go through ONE function, pick_image(), which asks cycle 93's slot
resolver (harness/images.py fill_slots) for the run's ad still, a library
photo by its richer tags (shot, fullbleed_ok, dark_ok, mood, focal point) or
the product's cut-out, in the composition's order.
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

# The cycle 93 slot keys a composition's image spec may carry, passed to
# images.fill_slots as the slot's `want` (shot, fullbleed_ok, dark_ok, mood,
# orientation, aspect_min/max ...).
_WANT_KEYS = ("fullbleed_ok", "dark_ok", "mood", "orientation", "aspect_min", "aspect_max", "min_quality")
_SOURCE_SHOTS = {"ad_still": ["ad-still"], "cutout": ["cutout"]}


def _position(focal):
    focal = focal or {"x": 0.5, "y": 0.5}
    return f"{round(focal.get('x', 0.5) * 100)}% {round(focal.get('y', 0.5) * 100)}%"


def pick_image(needs, *, facts_pack=None, tenant=None, assets=None, frame=None, exclude_ids=(), seed=0,
               page=None, pool=None):
    """The image a composition shows, or None.

    {"id", "source": "ad_still"|"library"|"cutout", "fit": "cover"|
    "contain", "position": CSS object-position at the focal point, "focal",
    "crop" (a cycle 93 crop name, e.g. "pill", or None), "why"}.

    Cycle 93's resolver chooses (images.fill_slots): each of the need's
    sources, in order, is one slot -- the run's ad still; the library
    photos whose image_tags/tags shot is one of the need's `shots`, filtered
    and ranked by the need's fullbleed_ok / dark_ok / mood / orientation,
    the page's model, quality, blur and clutter, never a retired-mark photo
    above the fold, never one already on the page (`exclude_ids`), with a
    relaxed second pass on the same shots; then the product cut-out. The
    picked asset is added to `assets` (render_page's assets_by_id) with its
    focal point, so render_image_slot(crop=...) cuts round it."""
    from .. import images

    spec = (needs or {}).get("image")
    if not spec or tenant is None:
        return None
    assets = assets if assets is not None else {}
    facts_pack = facts_pack or {}
    if pool is None:
        pool = images.candidates(facts_pack, tenant, ad_frame=frame)
    want = {k: spec[k] for k in _WANT_KEYS if k in spec}
    for source in spec.get("sources") or ():
        shots = _SOURCE_SHOTS.get(source) or list(spec.get("shots") or images.SHOTS)
        slot = {"name": "hero", "want": dict(want if source == "library" else {}, shot=shots), "n": 1,
                "above_fold": True, "last_resort": None}
        if source == "library":
            slot["fallback"] = list(shots)   # the same shots, frame filters relaxed
            if spec.get("crop"):
                slot["crop"] = spec["crop"]
        result = images.fill_slots([slot], facts_pack, tenant, ad_frames=frame, seed=seed, page=page,
                                   used=exclude_ids, pool=pool)[0]
        if not result["picks"]:
            continue
        pick = result["picks"][0]
        focal = pick.get("focal") or {"x": 0.5, "y": 0.5}
        entry = assets.setdefault(pick["id"], dict(pick["asset"]))
        entry.setdefault("focal", focal)
        if source == "ad_still":
            position = (frame or {}).get("object_position") or _position(focal)
        else:
            position = _position(focal)
        return {"id": pick["id"], "source": source, "fit": "contain" if source == "cutout" else "cover",
                "position": position, "focal": focal,
                "crop": spec.get("crop") if source == "library" else None,
                "why": f"{pick['via']}: {', '.join(pick['reasons'])}"}
    return None


def need_text(hero):
    """A short phrase for what a composition's image need is."""
    spec = (hero.needs or {}).get("image") or {}
    return spec.get("label") or "an image"


# ---------------------------------------------------------------------------
# The renderer's entry point
# ---------------------------------------------------------------------------

def render_context(cartridge, page, *, look, ad_brief, facts_pack, tenant, base, assets, frame, seed,
                   exclude_ids=(), quote=False):
    """(hero context for the template, note). Resolves the composition
    (resolve) with availability from pick_image, then builds what its
    partial prints. A page.json `hero_style` that is no composition of this
    cartridge (a hand edit) renders the seeded pick, with a note."""
    cache = {}

    def image_for(hero_id):
        if hero_id not in cache:
            if "pool" not in cache:
                from .. import images

                cache["pool"] = images.candidates(facts_pack or {}, tenant, ad_frame=frame)
            cache[hero_id] = pick_image(REGISTRY[hero_id].needs, page=page, facts_pack=facts_pack, tenant=tenant,
                                        assets=assets, frame=frame, exclude_ids=exclude_ids, seed=seed,
                                        pool=cache["pool"])
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
    # an adapter prints the look's own images (its still, the page's cut-out)
    image = image_for(hero_id) if (hero.needs or {}).get("image") and not hero.adapter else None
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
