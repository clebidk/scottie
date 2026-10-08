"""Cycle 54: the LOOK mechanism, shared by every cartridge that has looks.

Cycle 51 gave the listicle cartridge looks: a cartridge template.html that
is only a dispatcher, and one template per look under
cartridges/<cartridge>/looks/<look>/. The look is a LAYOUT choice, never a
copy choice, so a page can change look with no writer call
(`harness rerender --look`). Cycle 54 gives the product-page cartridge the
same mechanism (looks `pdp` and `classic`), and cycle 82 the longform
cartridge (looks `open` and `classic`), so the rule now lives here and
harness/listicle.py keeps only what is listicle-specific (its style ->
look pairing).

One resolution rule for every cartridge:
  1. an explicit look (the `--look` flag, or page.json's own recorded
     "look") always wins, when it is one of this cartridge's looks;
  2. else the listicle pairs its look with the page's style
     (listicle.resolve_look);
  3. else the cartridge's default look, when the tenant allows it
     (tenant.yaml `cartridges.<cartridge>.looks`), else the first look the
     tenant allows.
The value returned is always one of the cartridge's own looks, so the path
a dispatcher template builds from it can never leave its looks folder.

Cycle 87: a TENANT look (harness/look_import.py, "Import a page") lives at
tenants/<t>/looks/<cartridge>/<slug>/template.html. It resolves after the
cartridge's own looks: an explicit look that is not one of the cartridge's
own is accepted when it is one of this tenant's imported looks. A slug is
checked against LOOK_SLUG_RE before it is ever joined to a path, and
harness/render.py loads it through a prefix loader rooted at the tenant's
looks folder for that cartridge, so it can never leave that folder either.
An imported look is never a default: it renders only when named.
"""
import re
from pathlib import Path

from . import listicle
from . import tenant as tenant_mod

# Cycle 87: an imported look's name -- lower-case letters, digits and single
# hyphens, 3-40 characters, starting with a letter. "_demo" style names and
# anything with a dot or a slash can never match.
LOOK_SLUG_RE = re.compile(r"^[a-z](?:[a-z0-9]|-(?=[a-z0-9])){2,39}$")

# Every cartridge that has looks, and its looks in order. The first entry of
# a non-listicle cartridge is only its display order; DEFAULT_LOOKS below is
# what a page with no look gets.
CARTRIDGE_LOOKS = {
    "listicle": listicle.LOOKS,
    "product-page": ("pdp", "classic"),
    # Cycle 82: the cycle 79 "open" design language for the longform page.
    # The default stays "classic" (the template longform always had), so a
    # tenant that pins nothing renders exactly as before; a tenant opts in
    # with tenant.yaml `cartridges.longform.looks: [open]`.
    "longform": ("open", "classic"),
    # Cycle 85: the landing page's "proof" look (rating first, checkmark
    # benefits, trust facts under the CTA, icon row). The default stays
    # "open", the one look landing had before.
    # Cycle 91: two looks built from the block catalog (harness/blocks/
    # catalog/): `cinematic` (pack page 08) and `buybox` (pack page 06).
    "landing": ("open", "proof", "cinematic", "buybox"),
}

DEFAULT_LOOKS = {
    "listicle": listicle.DEFAULT_LOOK,
    "product-page": "pdp",
    "longform": "classic",
    "landing": "open",
}


def canonical(cartridge_name, look):
    """Cycle 79: a retired listicle look name ("cards", "lander", ...) is the
    look it now renders in ("open"); every other value is itself."""
    if cartridge_name == "listicle" and look:
        return listicle.canonical_look(look)
    return look


def cartridge_looks(cartridge_name):
    """This cartridge's looks, or () for a cartridge that has none."""
    return tuple(CARTRIDGE_LOOKS.get(cartridge_name, ()))


def has_looks(cartridge_name):
    return bool(cartridge_looks(cartridge_name))


def all_looks():
    """Every look of every cartridge, each once, in declaration order -- the
    `--look` flag's argparse choices."""
    seen = []
    # Cycle 79: the retired listicle look names stay valid flag values (they
    # resolve to "open"), so an old command line or A/B/C entry still works.
    for looks in list(CARTRIDGE_LOOKS.values()) + [listicle.RETIRED_LOOKS]:
        for look in looks:
            if look not in seen:
                seen.append(look)
    return tuple(seen)


def imported_looks_dir(cartridge_name, tenant=None, looks_dir=None):
    """tenants/<t>/looks/<cartridge> (or `looks_dir`/<cartridge>), or None
    for a tenant object with no looks folder (a test double)."""
    root = looks_dir if looks_dir is not None else getattr(tenant or tenant_mod.active(), "looks_dir", None)
    return Path(root) / cartridge_name if root is not None else None


def imported_looks(cartridge_name, tenant=None, looks_dir=None):
    """Cycle 87: this tenant's imported looks for one cartridge, sorted: every
    folder whose name is a valid slug and holds a template.html."""
    folder = imported_looks_dir(cartridge_name, tenant, looks_dir)
    if folder is None or not folder.is_dir():
        return ()
    return tuple(sorted(
        p.name for p in folder.iterdir()
        if LOOK_SLUG_RE.match(p.name) and (p / "template.html").is_file()
    ))


def is_look_of(cartridge_name, look, tenant=None, looks_dir=None):
    """True when `look` is one of this cartridge's own looks or one of this
    tenant's imported looks for it."""
    look = canonical(cartridge_name, look)
    if not look:
        return False
    return look in cartridge_looks(cartridge_name) or look in imported_looks(cartridge_name, tenant, looks_dir)


def look_arg(value):
    """argparse `type` for `--look`: a built-in look (all_looks), or an
    imported look of any tenant on this machine. Which cartridge and tenant
    it belongs to is checked later, where the tenant is known."""
    import argparse

    if value in all_looks():
        return value
    if LOOK_SLUG_RE.match(value or ""):
        for name in tenant_mod.list_tenants():
            try:
                tenant = tenant_mod.load_tenant(name)
            except Exception:  # noqa: BLE001 -- a broken tenant must not break the flag
                continue
            if any(value in imported_looks(c, tenant) for c in CARTRIDGE_LOOKS):
                return value
    raise argparse.ArgumentTypeError(
        f"invalid choice: {value!r} (choose from {', '.join(all_looks())}, or a tenant's imported look)"
    )


def tenant_looks(cartridge_name, tenant=None):
    """The looks this tenant allows for this cartridge, in order --
    tenant.yaml's `cartridges.<cartridge>.looks` filtered to real looks,
    else all of them. A pin that names nothing valid is ignored rather than
    leaving a page with no look (same rule as listicle.tenant_looks)."""
    if cartridge_name == "listicle":
        return listicle.tenant_looks(tenant)
    tenant = tenant or tenant_mod.active()
    known = cartridge_looks(cartridge_name)
    pinned = tenant.get(f"cartridges.{cartridge_name}.looks") or ()
    chosen = tuple(look for look in pinned if look in known)
    return chosen or known


def resolve_look(cartridge_name, requested=None, *, style=None, tenant=None, looks_dir=None):
    """The look one page of this cartridge is rendered in (see the module
    docstring). "" for a cartridge that has no looks. Raises ValueError for
    an explicit `requested` that is not one of this cartridge's looks or
    one of the tenant's imported looks for it (cycle 87)."""
    known = cartridge_looks(cartridge_name)
    if not known:
        return ""
    if (requested and canonical(cartridge_name, requested) not in known
            and requested in imported_looks(cartridge_name, tenant, looks_dir)):
        return requested
    if cartridge_name == "listicle":
        return listicle.resolve_look(requested, style=style, tenant=tenant)
    if requested:
        if requested not in known:
            raise ValueError(
                f"unknown {cartridge_name} look {requested!r}; choose one of {list(known)}"
            )
        return requested
    allowed = tenant_looks(cartridge_name, tenant)
    default = DEFAULT_LOOKS.get(cartridge_name)
    return default if default in allowed else allowed[0]


def requested_for(cartridge_name, requested, tenant=None):
    """The run-wide `--look` flag, as it applies to one cartridge: the flag
    itself when it names one of this cartridge's looks, else None (so the
    cartridge falls back to its own default). `harness run --cartridges
    listicle,product-page --look pdp` sets the product page's look and
    leaves the listicle on its style pairing. Cycle 87: also a look this
    tenant imported for this cartridge, when `tenant` is given."""
    requested = canonical(cartridge_name, requested)
    if not requested:
        return None
    if requested in cartridge_looks(cartridge_name):
        return requested
    if tenant is not None and requested in imported_looks(cartridge_name, tenant):
        return requested
    return None
