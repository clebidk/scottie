"""Cycle 54: the LOOK mechanism, shared by every cartridge that has looks.

Cycle 51 gave the listicle cartridge looks: a cartridge template.html that
is only a dispatcher, and one template per look under
cartridges/<cartridge>/looks/<look>/. The look is a LAYOUT choice, never a
copy choice, so a page can change look with no writer call
(`harness rerender --look`). Cycle 54 gives the product-page cartridge the
same mechanism (looks `pdp` and `classic`), so the rule now lives here and
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
"""
from . import listicle
from . import tenant as tenant_mod

# Every cartridge that has looks, and its looks in order. The first entry of
# a non-listicle cartridge is only its display order; DEFAULT_LOOKS below is
# what a page with no look gets.
CARTRIDGE_LOOKS = {
    "listicle": listicle.LOOKS,
    "product-page": ("pdp", "classic"),
}

DEFAULT_LOOKS = {
    "listicle": listicle.DEFAULT_LOOK,
    "product-page": "pdp",
}


def cartridge_looks(cartridge_name):
    """This cartridge's looks, or () for a cartridge that has none."""
    return tuple(CARTRIDGE_LOOKS.get(cartridge_name, ()))


def has_looks(cartridge_name):
    return bool(cartridge_looks(cartridge_name))


def all_looks():
    """Every look of every cartridge, each once, in declaration order -- the
    `--look` flag's argparse choices."""
    seen = []
    for looks in CARTRIDGE_LOOKS.values():
        for look in looks:
            if look not in seen:
                seen.append(look)
    return tuple(seen)


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


def resolve_look(cartridge_name, requested=None, *, style=None, tenant=None):
    """The look one page of this cartridge is rendered in (see the module
    docstring). "" for a cartridge that has no looks. Raises ValueError for
    an explicit `requested` that is not one of this cartridge's looks."""
    known = cartridge_looks(cartridge_name)
    if not known:
        return ""
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


def requested_for(cartridge_name, requested):
    """The run-wide `--look` flag, as it applies to one cartridge: the flag
    itself when it names one of this cartridge's looks, else None (so the
    cartridge falls back to its own default). `harness run --cartridges
    listicle,product-page --look pdp` sets the product page's look and
    leaves the listicle on its style pairing."""
    return requested if requested and requested in cartridge_looks(cartridge_name) else None
