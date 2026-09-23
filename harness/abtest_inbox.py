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
"""
import argparse
import contextlib
import datetime
import io

from . import abtest, budget, exits, meta_ingest

BUDGET_REASON = "budget cap"


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


def build_test_for_item(tenant, ad_id, *, by, runner=None):
    """Builds (or resumes) the A/B/C test for one inbox item. Returns
    {"outcome": "built"|"live"|"budget"|"failed", "test_id", "split_url",
    "reason"}."""
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
        source = {"kind": item.get("source") or "meta", "ad_id": ad_id, "ad_name": item.get("ad_name")}
        for key in ("uploaded_by", "campaign", "adset", "creative_id"):
            if item.get(key):
                source[key] = item[key]

        def link(new_rec):
            inbox.note(ad_id, f"building A/B/C test {new_rec['test_id']}", test_id=new_rec["test_id"])

        rc, rec = abtest.create_test(tenant, input_path=media, name=item.get("ad_name") or ad_id,
                                     source=source, runner=runner, on_record=link)

    test_id = rec["test_id"]
    if rc == exits.BUDGET:
        inbox.note(ad_id, f"{BUDGET_REASON}: test {test_id} is queued; the next from-inbox run resumes it")
        return {"outcome": "budget", "test_id": test_id, "split_url": None, "reason": rec.get("reason", "")}
    if rc != exits.OK:
        inbox.set_state(ad_id, "failed", f"test {test_id}: {rec.get('reason') or 'build failed'}")
        return {"outcome": "failed", "test_id": test_id, "split_url": None, "reason": rec.get("reason", "")}

    split_url, publish_error = None, None
    if _auto_publish(tenant) and rec.get("status") != "live":
        split_url, publish_error = publish_test(tenant, test_id, by=by)
    elif rec.get("status") == "live":
        split_url = (rec.get("split") or {}).get("url")
    if publish_error:
        reason = f"test {test_id} built; auto publish failed: {publish_error}"
    elif split_url:
        reason = f"test {test_id} is live: {split_url}"
    else:
        reason = f"test {test_id} built; publish it with `harness abtest publish {test_id}`"
    inbox.set_state(ad_id, "tested", reason)
    return {"outcome": "live" if split_url else "built", "test_id": test_id, "split_url": split_url,
            "reason": reason, "publish_error": publish_error}


def _pending(inbox, tenant):
    """Items to work on, in order: tests the cap stopped mid-build, items the
    cap left queued, then new items (oldest first)."""
    resume, queued, new = [], [], []
    for item in inbox.items():
        state = item.get("state")
        if state == "building" and item.get("test_id"):
            rec = abtest.find_test(tenant, item["test_id"])
            if rec and rec.get("status") in ("queued", "building"):
                resume.append(item)
        elif state == "queued" and item.get("reason") == BUDGET_REASON:
            queued.append(item)
        elif state == "new":
            new.append(item)
    return resume + queued + new


def _leave_for_tomorrow(inbox, items):
    for item in items:
        if item.get("state") == "new":
            inbox.set_state(item["ad_id"], "queued", BUDGET_REASON)
        elif item.get("state") == "queued":
            inbox.note(item["ad_id"], BUDGET_REASON)


def from_inbox(tenant, *, by, limit=None, runner=None, log=print):
    """Builds a test for each pending inbox item until the daily cap stops
    it. Returns {"built": [...], "failed": [...], "left": [...], "stopped":
    None|"budget cap"}."""
    inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
    todo = _pending(inbox, tenant)
    if limit is not None:
        todo = todo[:limit]
    summary = {"built": [], "failed": [], "left": [], "stopped": None}
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
        outcome = build_test_for_item(tenant, ad_id, by=by, runner=runner)
        log(f"  {ad_id}: {outcome['outcome']} {outcome.get('test_id') or ''} {outcome.get('reason') or ''}".rstrip())
        if outcome["outcome"] == "budget":
            rest = todo[i + 1:]
            _leave_for_tomorrow(inbox, [inbox.read(x["ad_id"]) for x in rest])
            summary["left"] = [ad_id] + [x["ad_id"] for x in rest]
            summary["stopped"] = BUDGET_REASON
            log(f"daily budget cap reached during {ad_id}: its test resumes on the next run")
            break
        summary["failed" if outcome["outcome"] == "failed" else "built"].append(ad_id)
    return summary

