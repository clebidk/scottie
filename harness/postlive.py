"""Cycle 80: "Post live (unlinked)" -- one generation on its own live Shopify
page, linked from no ad, no A/B/C test and no redirect.

What the owner did by hand on 2026-10-06 for five runs, as one action:

  harness approve <run> --by <reviewer> --pages <page>
  harness packet <run> --stamp ship --by <reviewer>
  harness publish <run> --page <page> --live --handle lp-<slug>   (no --redirect-from)

with tenant.yaml `publish_locked: true` left ON. The lock (cli.
LockedShopifyPublisher) keeps blocking every automatic or indirect path --
A/B/C publish and split pages, auto publish, redirects, a plain `harness
publish --live`. This module is the ONE explicit exception: it asks
cli._make_publisher for a publisher built with `unlinked_live=True`, which
allows a live create only with an explicit handle and a live update of a page
id this run already owns. create_redirect stays blocked for it too.

Three actions, each a listicle-site job (harness/jobs.py) and a CLI command
(`harness post-live`):
  post       approve + stamp ship + live publish at `lp-<slug>` (a page this
             run already has as a hidden draft is switched live in place)
  update     re-publish the current version over the live page (--update)
  unpublish  set the page back to hidden (the publisher's update with
             unpublished=True, which the lock always allowed)

state.json `published_pages[page]` records `unlinked: true`, `live`, `by`,
`at`, `handle`, `url` (plus `updated_by/at`, `unpublished_by/at`).
"""
import argparse
import contextlib
import io
import json
import re
from pathlib import Path

from . import abtest, exits, runstate
from .errors import HarnessError

HANDLE_PREFIX = "lp-"
HANDLE_MAX = 80
# Shopify page handles: lower-case letters, digits and single hyphens.
HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
ACTIONS = ("post", "update", "unpublish")

# Words that carry no topic in a handle.
_STOP = frozenset("""
a an the and or but no not nor your you yours our my her his hers she he it its they them their this that these
those is are was were be been being am do does did to of for in on at with from by as into onto than then so
can cant can't could would should will just still really actually once out get gets got what why how who when
where which there here right more most very too also even ever about up down off over only own same such
""".split())


class PostLiveRefused(HarnessError):
    """A post-live / update / unpublish request that must not go ahead."""

    exit_code = exits.USAGE


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# handles
# ---------------------------------------------------------------------------

def _banned(tenant):
    """The tenant's absolute word bans (vocab.yaml emf_terms), as handle parts."""
    from .asset_describe import forbidden_terms_for

    if tenant is None:
        return ()
    return tuple(re.sub(r"[^a-z0-9]+", "-", str(t).lower()).strip("-") for t in forbidden_terms_for(tenant) if t)


def handle_problem(handle, tenant=None):
    """None when `handle` may be used for a new unlinked page, else a plain
    sentence saying why not."""
    handle = handle or ""
    if not handle:
        return "The handle is empty."
    if len(handle) > HANDLE_MAX:
        return f"The handle is {len(handle)} characters; the limit is {HANDLE_MAX}."
    if not HANDLE_RE.match(handle) or "--" in handle:
        return ("A handle uses only lower-case letters, digits and single hyphens, and starts and ends "
                "with a letter or digit.")
    if not handle.startswith(HANDLE_PREFIX):
        return f"A posted page's handle starts with \"{HANDLE_PREFIX}\" so it never takes a store page's address."
    if any(bad and bad in handle for bad in _banned(tenant)):
        return "The handle uses a word this brand never uses."
    return None


def handles_in_use(tenant):
    """Every Shopify page handle this tenant's runs and A/B/C tests recorded."""
    used = set()
    out_dir = Path(tenant.out_dir)
    if out_dir.is_dir():
        for state_file in out_dir.glob("*/state.json"):
            for record in (_load_json(state_file).get("published_pages") or {}).values():
                if isinstance(record, dict) and record.get("handle"):
                    used.add(record["handle"])
    for rec in abtest.list_tests(tenant):
        for v in rec.get("variants") or []:
            if (v.get("shopify") or {}).get("handle"):
                used.add(v["shopify"]["handle"])
        if (rec.get("split") or {}).get("handle"):
            used.add(rec["split"]["handle"])
    return used


def _model_slug(run_dir, banned=()):
    name = ((_load_json(Path(run_dir) / "facts_pack.json").get("product") or {}).get("name") or "")
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "-".join(w for w in words if not any(b and b in w for b in banned))[:20]


def suggest_handle(tenant, run_dir, page, *, taken=None):
    """`lp-<short topic>-<model>`, unique among `taken` (default: every handle
    the tenant recorded). The topic is up to three content words of the
    page's display line, else its headline, else the ad's hook."""
    run_dir = Path(run_dir)
    page_json = _load_json(run_dir / page / "page.json")
    brief = _load_json(run_dir / "ad_brief.json")
    banned = _banned(tenant)
    model = _model_slug(run_dir, banned)
    words = []
    for text in (page_json.get("display_headline"), page_json.get("headline"), brief.get("hook")):
        tokens = [t for t in re.findall(r"[a-z0-9]+", str(text or "").lower())
                  if len(t) > 1 and t not in _STOP and t != model and not any(b and b in t for b in banned)]
        if tokens:
            words = tokens[:3]
            break
    words = words or [page]
    base = "-".join([HANDLE_PREFIX.rstrip("-")] + words + ([model] if model else []))
    base = base[:HANDLE_MAX - 3].rstrip("-")
    taken = handles_in_use(tenant) if taken is None else set(taken)
    handle, n = base, 2
    while handle in taken:
        handle, n = f"{base}-{n}", n + 1
    return handle


# ---------------------------------------------------------------------------
# what a page is now
# ---------------------------------------------------------------------------

def _last_publish_note(state, page, record):
    if not record or not record.get("url"):
        return None
    marker = f"url={record['url']} "
    for entry in reversed(state.get("history") or []):
        note = entry.get("note") or ""
        if entry.get("state") == "published" and note.startswith(marker):
            return note
    return None


def page_status(run_dir, state, page):
    """{"record", "live" (True/False/None), "redirected", "test", "unlinked"}.
    `unlinked` is True for a Shopify page this run owns that no A/B/C test
    and no redirect points at -- the pages the update / unpublish buttons
    may touch. A page posted from the site carries `unlinked: true`; one
    published by hand the same way (no test, no --redirect-from) counts too."""
    record = (state.get("published_pages") or {}).get(page)
    note = _last_publish_note(state, page, record)
    if record and "live" in record:
        live = bool(record["live"])
    elif note is None:
        live = None
    else:
        live = True if "live=True" in note else False if "live=False" in note else None
    redirected = bool(note and "redirect_from=" in note)
    test = (state.get("abtest") or {}).get(page)
    unlinked = bool(record and record.get("page_id") and not test and not redirected)
    return {"record": record, "live": live, "redirected": redirected, "test": test, "unlinked": unlinked}


# ---------------------------------------------------------------------------
# the action
# ---------------------------------------------------------------------------

def _publish(tenant, run_dir, page, *, by, handle, update, live):
    from . import cli

    args = argparse.Namespace(run_dir=str(run_dir), page=page, live=live, dry_run=False, redirect_from=None,
                              handle=handle, update=update, tenant=tenant.name, seo_hidden=False,
                              unlinked_live=live, by=by)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli.cmd_publish(args)
    lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
    if rc != exits.OK:
        raise PostLiveRefused(f"publish failed: {lines[-1] if lines else f'exit {rc}'}")
    return lines


def _check(tenant, run_dir, page, by):
    if runstate.find_reviewer(tenant, by) is None:
        raise PostLiveRefused(f"{by!r} is not a listed reviewer for tenant {tenant.name!r}.")
    state = runstate.load_state(run_dir)
    if page not in (state.get("pages") or {}):
        raise PostLiveRefused(f"run {Path(run_dir).name} has no page {page!r}.")
    if not (Path(run_dir) / page / "index.html").exists():
        raise PostLiveRefused(f"page {page!r} of {Path(run_dir).name} is not rendered.")
    status = page_status(run_dir, state, page)
    if status["test"]:
        raise PostLiveRefused(f"page {page!r} is variant {status['test'].get('key')} of A/B/C test "
                              f"{status['test'].get('test_id')}; a test page is never posted on its own.")
    if status["redirected"]:
        raise PostLiveRefused(f"page {page!r} has a redirect pointing at it; it is not an unlinked page.")
    return state, status


def post_live(tenant, run_dir, page, *, by, action="post", handle=None, note=""):
    """Runs one action for a reviewer. Returns the page's published record.
    Raises PostLiveRefused (a HarnessError) with a plain reason."""
    from . import tenant as tenant_mod

    if action not in ACTIONS:
        raise PostLiveRefused(f"unknown action {action!r}; use one of {', '.join(ACTIONS)}.")
    run_dir = Path(run_dir)
    tenant_mod.activate(tenant)
    state, status = _check(tenant, run_dir, page, by)
    record = status["record"]
    note = note or f"listicle site: {action} unlinked live page"

    if action == "post":
        if record and status["live"]:
            raise PostLiveRefused(f"page {page!r} is already live at {record.get('url')}; use update instead.")
        if record:  # a hidden draft this run owns: switched live in place, at its own handle
            handle = record.get("handle")
        else:
            problem = handle_problem(handle, tenant)
            if problem:
                raise PostLiveRefused(problem)
            if handle in handles_in_use(tenant):
                raise PostLiveRefused(f"the handle {handle!r} is already used by another generation.")
    elif not record or not record.get("page_id"):
        raise PostLiveRefused(f"page {page!r} has no Shopify page yet; post it live first.")
    elif action == "update" and not status["live"]:
        raise PostLiveRefused(f"page {page!r} is not live; post it live instead.")
    elif action == "unpublish" and status["live"] is False:
        raise PostLiveRefused(f"page {page!r} is already hidden.")

    if action == "unpublish":
        return _unpublish(tenant, run_dir, page, record, by=by, note=note)

    runstate.approve(run_dir, tenant, by=by, pages=[page], note=note)
    runstate.set_packet_stamp(run_dir, stamp="ship", by=by, note=note)
    _publish(tenant, run_dir, page, by=by, handle=None if record else handle, update=bool(record), live=True)
    return runstate.record_unlinked(run_dir, page=page, by=by, action=action, previous=record)


def _unpublish(tenant, run_dir, page, record, *, by, note):
    from . import cli
    from .publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher

    tenant.load_env()
    publisher = cli._make_publisher(tenant, export_dir=run_dir / page / "export")
    if not isinstance(publisher, ShopifyPublisher):
        raise PostLiveRefused("unpublish needs the shopify publisher.")
    try:
        # The lock always allowed this: a hidden page. Only `published` is sent.
        publisher.update_page(record["page_id"], {"storefront_host": tenant.get("site_host")}, unpublished=True)
    except (ShopifyCredentialsMissing, HarnessError) as e:
        raise PostLiveRefused(f"unpublish failed: {e}") from e
    runstate.record_unpublished(run_dir, page=page, by=by, note=note)
    return runstate.record_unlinked(run_dir, page=page, by=by, action="unpublish", previous=record)
