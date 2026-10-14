"""Cycle 69: inbox items -> A/B/C tests.

One path for both kinds of inbox item (docs/META-INGEST.md): an ad pulled
from Meta (`harness abtest from-inbox`, run by crons/meta-pull.sh after the
pull) and an ad uploaded on the listicle site (a create_test job,
harness/jobs.py). For one item:

  new/queued -> building   `harness abtest create` on the item's media, with
                           the ad name; the test id goes on the item
  building   -> tested     the three builds exist; with abtest.auto_publish,
                           `harness abtest publish` has run too
  building   -> failed     the library ran out of builds
  building (kept)          the daily budget cap stopped a build: the test is
                           "queued" and the next from-inbox run resumes it

`from_inbox` checks the daily cap before each item. When the cap has no room
for another run, it stops and leaves every item it did not reach "queued"
with the reason "budget cap"; the next run (the next day) picks them up.

Cycle 88: the test's destination plan (abtest.PLANS) is the item's own
"destinations" (the upload form's choice), else the `plan` the caller
passes (`from-inbox --destinations`), else 3 generated.

Cycle 107: with abtest.auto_go_live (the owner's lever, off until the owner
turns it on), a test this module has just published also goes live on its
Meta ad (harness/meta_golive.py) when the ad is ACTIVE; a test whose ad is
not ACTIVE yet is marked pending and each later from-inbox run tries it
again. It never forces and never touches an existing-post creative.

Cycle 108 (owner decision 2026-10-13, docs/ABTEST.md "Automatic pipeline"):
- the plan of an item that names none is abtest.inbox_plan, by default
  "auto": `plan_for_item` picks it per ad (rule 1: the ad's own specific
  store page is arm A, B and C are generated; rule 2: the ad names a
  model, so generated, generated, PDP; rule 3: generated, generated, home);
- from-inbox builds only Meta ads whose effective_status is ACTIVE,
  PENDING_REVIEW or IN_PROCESS and that were created on or after
  abtest.auto_since; the others stay "new" (`not_auto_built` says why);
- at most abtest.auto_max_per_run items per run (the daily cap still
  applies); the rest wait for the next run;
- an ad whose creative has no spec (an existing Page post) is built, but
  its test is marked "manual link": go-live cannot change that ad;
- the lever never goes live for a test created before abtest.auto_since;
- each run writes its summary to runs/auto-pipeline.json (the Ads page).

Cycle 109 (owner, 2026-10-14: "if the ad is about a sale, the landing page
100% needs to have that as primary focus"): an ad whose brief states an offer
(harness/offer.py) that matched no verified promo claim is built and
published but NOT taken live on Meta: the test gets `offer_hold` {state
"held", reason "held: offer not verified", the ad's offer sentence}, the lever
skips it and the Ads page shows the sentence with a form that adds the promo
claim. Each later from-inbox run (`release_held`) checks the held tests
against claims/verified.json; once the claim matches, it rebuilds the
generated arms on their own pages (harness/abtest_rebuild.py, now offer-led)
and marks the lever pending, so the lever's hourly retry takes the test live.
"""
import argparse
import collections
import contextlib
import datetime
import io
import json
import os
import re
import urllib.parse
from pathlib import Path

from . import abtest, budget, exits, meta_ingest
from . import offer as offer_mod

BUDGET_REASON = "budget cap"
# Cycle 108: the effective_status values from-inbox builds for: approved and
# running, or still in Meta's review (go-live waits for ACTIVE).
AUTO_STATUSES = ("ACTIVE", "PENDING_REVIEW", "IN_PROCESS")
STATUS_FILE = "auto-pipeline.json"
MANUAL_LINK = ("the ad's creative has no object_story_spec or asset_feed_spec (an existing Page post): "
               "go-live cannot change its link. Paste the split link into the ad by hand in Ads Manager")


# ---------------------------------------------------------------------------
# cycle 108: the automatic plan, eligibility, the status file
# ---------------------------------------------------------------------------

_PRODUCT_PATH = re.compile(r"(?:^|/)products/[^/]+")


def specific_page(tenant, url):
    """Rule 1: `url` when it is a page on the tenant's own store that is not
    the home page and not a product page (/products/<handle>, also under a
    collection), else None. A harness test page (/pages/lp-...) is not one,
    so arm A is never another test's split link."""
    url = str(url or "").strip()
    parsed = urllib.parse.urlparse(url)
    host = (tenant.get("site_host") or "").lower()
    if parsed.scheme not in ("http", "https") or not host or parsed.netloc.lower() not in (host, f"www.{host}"):
        return None
    path = parsed.path.rstrip("/")
    if not path or _PRODUCT_PATH.search(path) or path.startswith("/pages/lp-"):
        return None
    return url


def named_product(tenant, item):
    """Rule 2: the product the ad's own words name (ad name, headline,
    primary text, description), found by the same detection as the PDP arm
    (ground.LocalFactsSource.pick_product_with_warning), else None."""
    from . import ground

    text = " ".join(str(item.get(k) or "") for k in ("ad_name", "headline", "primary_text", "description"))
    product, warning = ground.LocalFactsSource(tenant.claims_dir).pick_product_with_warning(
        None, {"transcript_or_text": text})
    if warning and warning.startswith(abtest.PRODUCT_NOT_NAMED):
        return None
    return product


def plan_for_item(item, tenant):
    """(plan name, arm specs, rule) for an inbox item under abtest.inbox_plan
    "auto". The name says which rule fired; the arms take A, B, C in order."""
    gen = {"kind": "generated"}
    page = specific_page(tenant, item.get("destination_url"))
    if page:
        return ("auto-page", [{"kind": "external", "target": "url", "url": page}, dict(gen), dict(gen)],
                f"rule 1: the ad links to a specific store page ({page}), so that page is arm A")
    product = named_product(tenant, item)
    if product and product.get("url"):
        return ("auto-pdp", [dict(gen), dict(gen), {"kind": "external", "target": "pdp", "url": product["url"]}],
                f"rule 2: the ad names {product['name']}, so its product page is arm C")
    return ("auto-home", [dict(gen), dict(gen), {"kind": "external", "target": "home"}],
            "rule 3: the ad names no model and links to no specific store page, so the home page is arm C")


def resolve_plan(tenant, item, plan=None):
    """(plan name, arm specs, rule or None): the item's own destinations (the
    upload form's choice), else `plan` (`from-inbox --destinations`), else
    abtest.inbox_plan; "auto" is `plan_for_item`."""
    chosen = item.get("destinations") or plan or abtest.settings(tenant)["inbox_plan"]
    if chosen == abtest.AUTO_PLAN:
        name, specs, rule = plan_for_item(item, tenant)
        return name, abtest.validate_plan(specs), rule
    name, specs = abtest.parse_plan(chosen)
    return name, specs, None


def _auto_since(tenant):
    since = abtest.settings(tenant)["auto_since"]
    return meta_ingest.parse_since(since, what="abtest.auto_since") if since else None


def not_auto_built(tenant, item):
    """(category, reason) when from-inbox must not build this Meta ad, else
    None. An upload is always built (its job and its form are the owner's
    own choice); so is anything `harness abtest create` is given."""
    if item.get("source") == "upload":
        return None
    since = _auto_since(tenant)
    if since is not None:
        created = meta_ingest.parse_meta_time(item.get("created_time"))
        if created is None or created < since:
            when = str(item.get("created_time") or "unknown")[:10]
            return "before auto_since", f"created {when}, before abtest.auto_since {since.date().isoformat()}"
    status = item.get("effective_status") or ""
    if status not in AUTO_STATUSES:
        category = "paused" if "PAUSED" in status else (status.lower() or "no status")
        return category, (f"ad status {status or 'unknown'}; it is built once it is "
                          f"{', '.join(AUTO_STATUSES[:-1])} or {AUTO_STATUSES[-1]}")
    return None


def waiting_for_auto(item):
    """True for an item from-inbox would pick up: new, or queued by the cap."""
    return item.get("state") == "new" or (item.get("state") == "queued" and item.get("reason") == BUDGET_REASON)


def manual_link_reason(item):
    """MANUAL_LINK for a Meta ad whose creative has no spec to copy (an
    existing Page post), else None."""
    from . import meta_golive

    creative = (item.get("meta") or {}).get("creative") or {}
    if item.get("source") == "upload" or not creative:
        return None
    if meta_golive.is_existing_post(creative) or not (creative.get("object_story_spec")
                                                       or creative.get("asset_feed_spec")):
        return MANUAL_LINK
    return None


def offer_hold(rec):
    """Cycle 109: the ad's offer block when a built generated arm's ad brief
    states an offer that matched no verified promo claim, else None."""
    for v in rec.get("variants") or []:
        if abtest.is_external(v) or not v.get("run_dir"):
            continue
        offer, claim_id = offer_mod.run_offer(Path(v["run_dir"]))
        if offer and not claim_id:
            return offer
    return None


def is_held(rec):
    return (rec.get("offer_hold") or {}).get("state") == "held"


def _mark_held(tenant, test_id, offer):
    rec = abtest.load_test(tenant, test_id)
    rec["offer_hold"] = {"state": "held", "reason": offer_mod.HOLD_REASON, "sentence": offer.get("sentence") or "",
                         "offer": offer, "at": abtest._utc_now()}
    abtest.save_test(tenant, rec)
    return rec


def _lever_skip(tenant, rec):
    """Why the go-live lever leaves this test alone, else None."""
    if is_held(rec):
        return offer_mod.HOLD_REASON
    if rec.get("manual_link"):
        return "manual link (the ad is an existing Page post)"
    since = _auto_since(tenant)
    if since is not None and str(rec.get("created_at") or "")[:10] < since.date().isoformat():
        return f"test created before abtest.auto_since {since.date().isoformat()}"
    return None


def status_path(tenant):
    return Path(tenant.runs_dir) / STATUS_FILE


def read_status(tenant):
    try:
        data = json.loads(status_path(tenant).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(tenant, section, data):
    """Sets one section ("pull" or "inbox") of runs/auto-pipeline.json."""
    status = read_status(tenant)
    status[section] = data
    path = status_path(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2) + "\n")
    os.replace(tmp, path)


def _clock(at, today):
    at = str(at or "")
    return at[11:16] if at[:10] == today else at[:16].replace("T", " ")


def status_line(tenant, today=None):
    """"Auto pipeline: last run 14:07, pulled 4, 2 built, 1 live, 3 skipped
    (paused), ..." from runs/auto-pipeline.json, or None before a run."""
    status = read_status(tenant)
    run = status.get("inbox")
    if not run:
        return None
    today = today or datetime.date.today().isoformat()
    parts = [f"last run {_clock(run.get('at'), today)}"]
    pull = status.get("pull")
    if pull:
        parts.append(f"pulled {pull.get('ingested', 0)}")
    parts.append(f"{run.get('built', 0)} built")
    if run.get("failed"):
        parts.append(f"{run['failed']} failed")
    if run.get("published"):
        parts.append(f"{run['published']} published")
    parts.append(f"{run.get('went_live', 0)} live")
    if run.get("pending"):
        parts.append(f"{run['pending']} pending")
    if run.get("held"):
        parts.append(f"{run['held']} held (offer not verified)")
    if run.get("waiting") or run.get("left"):
        parts.append(f"{(run.get('waiting') or 0) + (run.get('left') or 0)} waiting")
    by = run.get("skipped_by") or {}
    if by:
        detail = ", ".join(f"{n} {c}" for c, n in by.items()) if len(by) > 1 else next(iter(by))
        parts.append(f"{run.get('skipped', 0)} skipped ({detail})")
    else:
        parts.append("0 skipped")
    parts.append(f"${float(run.get('spend_usd') or 0):.2f} spend")
    levers = (f"auto publish {'on' if run.get('auto_publish') else 'off'}, "
              f"auto go-live {'on' if run.get('auto_go_live') else 'off'}")
    return "Auto pipeline: " + ", ".join(parts) + f" ({levers})"


def budget_ok(tenant, today_iso=None):
    """True when today's committed spend plus one more run's reservation
    stays within the tenant's daily cap (always True when uncapped)."""
    cap = budget.daily_cap_usd(tenant)
    if cap is None:
        return True
    today_iso = today_iso or datetime.date.today().isoformat()
    committed = budget.daily_spend(tenant, today_iso) + budget.daily_reserved(tenant, today_iso)
    return committed + budget.reservation_estimate_usd(tenant) <= cap


def default_by(tenant):
    """The reviewer a cron-run publish is recorded under: the first
    `reviewers` entry with role primary, else the first entry."""
    reviewers = tenant.get("reviewers") or []
    primary = next((r for r in reviewers if r.get("role") == "primary"), None)
    chosen = primary or (reviewers[0] if reviewers else None)
    return (chosen or {}).get("email")


def _auto_publish(tenant):
    return abtest.settings(tenant)["auto_publish"]


def publish_test(tenant, test_id, *, by):
    """`harness abtest publish <test_id> --by <by>`. Returns (split url, None)
    or (None, reason)."""
    from . import cli

    args = argparse.Namespace(test_id=test_id, by=by, draft=False, tenant=tenant.name)
    err, out = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = cli.cmd_abtest_publish(args)
    except Exception as e:  # noqa: BLE001 -- a store error must not lose the built test
        return None, f"{type(e).__name__}: {e}"
    if rc != exits.OK:
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        return None, lines[-1] if lines else f"exit {rc}"
    rec = abtest.load_test(tenant, test_id)
    return (rec.get("split") or {}).get("url"), None


def build_test_for_item(tenant, ad_id, *, by, runner=None, plan=None):
    """Builds (or resumes) the A/B/C test for one inbox item. Returns
    {"outcome": "built"|"live"|"budget"|"failed", "test_id", "split_url",
    "reason", ...}; cycle 108 adds "plan", "plan_rule" and "published"."""
    runner = runner or abtest.default_runner
    inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
    item = inbox.read(ad_id)
    if item is None:
        return {"outcome": "failed", "test_id": None, "split_url": None, "reason": f"no inbox item {ad_id}"}

    rec = None
    if item.get("state") == "building" and item.get("test_id"):
        rec = abtest.find_test(tenant, item["test_id"])
    if rec is not None and rec.get("status") in ("queued", "building"):
        rc, rec = abtest.build_variants(tenant, rec, runner)
    elif rec is not None and rec.get("status") in ("built", "live"):
        rc = exits.OK
    elif item.get("state") == "tested":
        return {"outcome": "failed", "test_id": item.get("test_id"), "split_url": None,
                "reason": f"inbox item {ad_id} already has a test ({item.get('test_id')})"}
    else:
        if item.get("state") == "building":  # a crash before the test was recorded
            inbox.set_state(ad_id, "failed", "the previous build left no test; starting again")
            item["state"] = "failed"
        if item.get("state") != "queued":
            inbox.set_state(ad_id, "queued", "picked for an A/B/C test")
        inbox.set_state(ad_id, "building", "building the A/B/C test")
        media = inbox.item_dir(ad_id) / (item.get("media_file") or "")
        if not item.get("media_file") or not media.is_file():
            inbox.set_state(ad_id, "failed", "the inbox item has no media file")
            return {"outcome": "failed", "test_id": None, "split_url": None,
                    "reason": "the inbox item has no media file"}
        pinned = item.get("pinned_arm")
        if pinned:
            # Cycle 86: checked at upload; a build the harness no longer has
            # (a look removed since) fails this item, not the whole run.
            try:
                abtest.parse_arm(pinned, tenant)
            except abtest.AbtestError as e:
                inbox.set_state(ad_id, "failed", f"pinned page version: {e}")
                return {"outcome": "failed", "test_id": None, "split_url": None,
                        "reason": f"pinned page version: {e}"}
        source = {"kind": item.get("source") or "meta", "ad_id": ad_id, "ad_name": item.get("ad_name")}
        for key in ("uploaded_by", "campaign", "adset", "creative_id"):
            if item.get(key):
                source[key] = item[key]

        manual = manual_link_reason(item)

        def link(new_rec):
            if manual:  # cycle 108: built, but the owner pastes the split link by hand
                new_rec["manual_link"] = manual
                abtest.save_test(tenant, new_rec)
            inbox.note(ad_id, f"building A/B/C test {new_rec['test_id']}", test_id=new_rec["test_id"])

        try:
            plan_name, specs, plan_rule = resolve_plan(tenant, item, plan)
            if pinned and not any(s["kind"] == "generated" for s in specs):
                raise abtest.AbtestError(f"destinations {plan_name!r} build no page, so the pinned page "
                                         f"version {pinned} cannot be used")
        except abtest.AbtestError as e:
            inbox.set_state(ad_id, "failed", str(e))
            return {"outcome": "failed", "test_id": None, "split_url": None, "reason": str(e)}
        rc, rec = abtest.create_test(tenant, input_path=media, name=item.get("ad_name") or ad_id,
                                     source=source, runner=runner, on_record=link, plan=specs,
                                     pinned_arm=pinned, plan_name=plan_name, plan_rule=plan_rule)

    test_id = rec["test_id"]
    plan_info = {"plan": rec.get("plan"), "plan_rule": rec.get("plan_rule")}
    if rc == exits.BUDGET:
        inbox.note(ad_id, f"{BUDGET_REASON}: test {test_id} is queued; the next from-inbox run resumes it")
        return {"outcome": "budget", "test_id": test_id, "split_url": None, "reason": rec.get("reason", ""),
                **plan_info}
    if rc != exits.OK:
        inbox.set_state(ad_id, "failed", f"test {test_id}: {rec.get('reason') or 'build failed'}")
        return {"outcome": "failed", "test_id": test_id, "split_url": None, "reason": rec.get("reason", ""),
                **plan_info}

    held = offer_hold(rec) if not rec.get("offer_hold") else None
    if held:
        rec = _mark_held(tenant, test_id, held)
    split_url, publish_error, published_now = None, None, False
    if _auto_publish(tenant) and rec.get("status") != "live":
        split_url, publish_error = publish_test(tenant, test_id, by=by)
        published_now = bool(split_url)
    elif rec.get("status") == "live":
        split_url = (rec.get("split") or {}).get("url")
    if publish_error:
        reason = f"test {test_id} built; auto publish failed: {publish_error}"
    elif split_url:
        reason = f"test {test_id} is live: {split_url}"
    else:
        reason = f"test {test_id} built; publish it with `harness abtest publish {test_id}`"
    go_live = None
    if published_now and abtest.settings(tenant)["auto_go_live"]:
        from . import meta_golive

        skip = _lever_skip(tenant, abtest.load_test(tenant, test_id))
        if skip:
            reason += f"; Meta go-live skipped: {skip}"
            if skip == offer_mod.HOLD_REASON:  # released (go-live pending) once the claim is verified
                meta_golive._set_auto(tenant, test_id, "held", skip)
        else:
            go_live = meta_golive.auto_go_live(tenant, test_id, by=by)
        if go_live is not None:
            reason += f"; Meta go-live {go_live['state']}" + (f": {go_live['reason']}" if go_live["reason"] else "")
    if held:
        reason += f"; {offer_mod.HOLD_REASON}: {held.get('sentence')!r} (add the promo claim on the Ads page)"
    inbox.set_state(ad_id, "tested", reason)
    return {"outcome": "live" if split_url else "built", "test_id": test_id, "split_url": split_url,
            "reason": reason, "publish_error": publish_error, "meta_go_live": go_live,
            "published": published_now, "held": bool(held), **plan_info}


def release_held(tenant, *, by, runner=None, log=print, today=None):
    """Cycle 109: each live test held for an unverified offer whose offer now
    matches a verified promo claim (claims/verified.json, read fresh): its
    generated arms are rebuilt on their own pages (offer-led now), the hold
    is released, and with the lever on its go-live is marked pending for
    meta_golive.retry_pending. A failed rebuild marks the hold
    "rebuild_failed" (not retried; `harness abtest rebuild-arm --all`). Returns
    the released test ids."""
    import json as _json

    from . import abtest_rebuild, meta_golive

    held = [r for r in abtest.list_tests(tenant) if is_held(r) and r.get("status") == "live"]
    if not held:
        return []
    try:
        claims = _json.loads((tenant.claims_dir / "verified.json").read_text())
    except (OSError, ValueError) as e:
        log(f"held tests: cannot read verified.json ({e})")
        return []
    today = today or datetime.date.today().isoformat()
    released = []
    for rec in held:
        test_id = rec["test_id"]
        claim = offer_mod.match_offer(rec["offer_hold"].get("offer"), claims, today)
        if claim is None:
            continue
        if not budget_ok(tenant):
            log(f"held test {test_id}: claim {claim['id']} verified; daily budget cap reached, rebuilt next run")
            break
        log(f"held test {test_id}: offer verified by claim {claim['id']}; rebuilding its generated arms")
        try:
            abtest_rebuild.rebuild_all(tenant, test_id, by=by, runner=runner, log=log)
        except abtest.AbtestError as e:
            rec = abtest.load_test(tenant, test_id)
            rec["offer_hold"].update(state="rebuild_failed", reason=f"rebuild failed: {e}", claim_id=claim["id"],
                                     checked_at=abtest._utc_now())
            abtest.save_test(tenant, rec)
            log(f"  {test_id}: rebuild failed: {e}")
            continue
        rec = abtest.load_test(tenant, test_id)
        rec["offer_hold"].update(state="released", claim_id=claim["id"], released_at=abtest._utc_now(),
                                 reason=f"offer verified by claim {claim['id']}; arms rebuilt")
        abtest.save_test(tenant, rec)
        if abtest.settings(tenant)["auto_go_live"] and (rec.get("auto_go_live") or {}).get("state") == "held":
            meta_golive._set_auto(tenant, test_id, "pending", f"offer verified by claim {claim['id']}")
        released.append(test_id)
    return released


def _pending(inbox, tenant):
    """(items to work on, skipped). Items in order: tests the cap stopped
    mid-build, items the cap left queued, then new items (oldest first).
    Cycle 108: skipped is [(item, category, reason)] for the queued and new
    Meta ads `not_auto_built` refuses; they keep their state."""
    resume, queued, new, skipped = [], [], [], []
    for item in inbox.items():
        state = item.get("state")
        if state == "building" and item.get("test_id"):
            rec = abtest.find_test(tenant, item["test_id"])
            if rec and rec.get("status") in ("queued", "building"):
                resume.append(item)
        elif waiting_for_auto(item):
            why = not_auto_built(tenant, item)
            if why:
                skipped.append((item, *why))
            else:
                (new if state == "new" else queued).append(item)
    return resume + queued + new, skipped


def _leave_for_tomorrow(inbox, items):
    for item in items:
        if item.get("state") == "new":
            inbox.set_state(item["ad_id"], "queued", BUDGET_REASON)
        elif item.get("state") == "queued":
            inbox.note(item["ad_id"], BUDGET_REASON)


def _today_spend(tenant):
    return float(budget.daily_spend(tenant, datetime.date.today().isoformat()) or 0)


def from_inbox(tenant, *, by, limit=None, runner=None, log=print, plan=None):
    """Builds a test for each pending inbox item until the daily cap stops
    it. Returns {"built": [...], "failed": [...], "left": [...], "stopped":
    None|"budget cap"}; cycle 108 adds "waiting" (over the per-run limit),
    "skipped" ({ad_id: reason}), "published" and "went_live", and writes
    the run's summary to runs/auto-pipeline.json."""
    inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
    cfg = abtest.settings(tenant)
    todo, skipped = _pending(inbox, tenant)
    summary = {"built": [], "failed": [], "left": [], "stopped": None, "waiting": [], "published": [],
               "went_live": [], "skipped": {i["ad_id"]: reason for i, _c, reason in skipped}}
    skipped_by = collections.Counter(c for _i, c, _r in skipped)
    if skipped:
        log("not auto-built (listed on the Ads page): "
            + ", ".join(f"{n} {c}" for c, n in skipped_by.most_common()))
    per_run = cfg["auto_max_per_run"] if limit is None else min(limit, cfg["auto_max_per_run"])
    if len(todo) > per_run:
        summary["waiting"] = [x["ad_id"] for x in todo[per_run:]]
        log(f"at most {per_run} item(s) per run (abtest.auto_max_per_run {cfg['auto_max_per_run']}"
            + (f", --limit {limit}" if limit is not None else "") + f"): {len(summary['waiting'])} wait for the "
            "next run")
        todo = todo[:per_run]
    spend_before = _today_spend(tenant)
    items = []
    for i, item in enumerate(todo):
        ad_id = item["ad_id"]
        if not budget_ok(tenant):
            rest = todo[i:]
            _leave_for_tomorrow(inbox, [inbox.read(x["ad_id"]) for x in rest])
            summary["left"] = [x["ad_id"] for x in rest]
            summary["stopped"] = BUDGET_REASON
            log(f"daily budget cap reached: {len(rest)} item(s) left queued for the next run")
            break
        log(f"building an A/B/C test for inbox item {ad_id} ({item.get('ad_name')})")
        outcome = build_test_for_item(tenant, ad_id, by=by, runner=runner, plan=plan)
        if outcome.get("plan"):
            log(f"  {ad_id} ({item.get('ad_name')}): plan {outcome['plan']}"
                + (f" -- {outcome['plan_rule']}" if outcome.get("plan_rule") else ""))
        log(f"  {ad_id}: {outcome['outcome']} {outcome.get('test_id') or ''} {outcome.get('reason') or ''}".rstrip())
        items.append({"ad_id": ad_id, "ad_name": item.get("ad_name") or "", "outcome": outcome["outcome"],
                      "test_id": outcome.get("test_id"), "plan": outcome.get("plan"),
                      "plan_rule": outcome.get("plan_rule"), "held": bool(outcome.get("held"))})
        if outcome.get("published"):
            summary["published"].append(ad_id)
        if (outcome.get("meta_go_live") or {}).get("state") == "done":
            summary["went_live"].append(outcome["test_id"])
        if outcome["outcome"] == "budget":
            rest = todo[i + 1:]
            _leave_for_tomorrow(inbox, [inbox.read(x["ad_id"]) for x in rest])
            summary["left"] = [ad_id] + [x["ad_id"] for x in rest]
            summary["stopped"] = BUDGET_REASON
            log(f"daily budget cap reached during {ad_id}: its test resumes on the next run")
            break
        summary["failed" if outcome["outcome"] == "failed" else "built"].append(ad_id)
    # Cycle 109: held tests whose offer is now a verified claim: rebuilt
    # offer-led, then (lever on) pending for the retry just below.
    summary["released"] = release_held(tenant, by=by, runner=runner, log=log)
    if cfg["auto_go_live"]:
        # Cycle 107: tests the lever left pending (ad not ACTIVE yet) go live
        # as soon as Meta has approved their ad.
        from . import meta_golive

        summary["went_live"] += meta_golive.retry_pending(tenant, by=by, log=log)
    pending = sum(1 for r in abtest.list_tests(tenant)
                  if (r.get("auto_go_live") or {}).get("state") == "pending" and r.get("status") == "live")
    write_status(tenant, "inbox", {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "built": len(summary["built"]), "failed": len(summary["failed"]), "published": len(summary["published"]),
        "went_live": len(summary["went_live"]), "pending": pending, "waiting": len(summary["waiting"]),
        "left": len(summary["left"]), "stopped": summary["stopped"], "skipped": len(skipped),
        "skipped_by": dict(skipped_by.most_common()), "spend_usd": round(_today_spend(tenant) - spend_before, 4),
        "auto_publish": cfg["auto_publish"], "auto_go_live": cfg["auto_go_live"], "inbox_plan": cfg["inbox_plan"],
        "held": sum(1 for r in abtest.list_tests(tenant) if is_held(r)), "released": len(summary["released"]),
        "items": items,
    })
    return summary

