"""Cycle 107: put an A/B/C test's split link on its Meta ad, take it off again,
and read the ad's spend for the results (docs/ABTEST.md "Go live on Meta").

Owner instruction 2026-10-12: tests go live as A/B/C on the Meta ads the owner
chooses (`harness abtest go-live`, the Ads page's "Go live on Meta") until the
owner turns on `abtest.auto_go_live`. With the lever on, `harness abtest
from-inbox` does it for each test it publishes, as soon as Meta has approved
the ad (effective_status ACTIVE).

Meta does not allow a change to an existing ad creative. A new destination is
a COPY of the creative with every link field set to the split link (url_tags
and every other field unchanged); then the ad's `creative` is set to the copy.
The old creative stays in the account, so a revert sets it back. Each change
restarts the ad's learning phase, and the new post starts with no likes,
comments or shares.

Graph calls (the token needs ads_management for the two POSTs; Meta's
"update an object" call is a POST to the object):
    GET  <ad_id>?fields=AD_READ_FIELDS               the ad and its creative
    POST act_<id>/adcreatives                        the creative copy
    POST <ad_id>  creative={"creative_id": "<id>"}   point the ad at a creative
    GET  <ad_id>?fields=VERIFY_FIELDS                read back and verify
    GET  <ad_id>/insights?fields=INSIGHTS_FIELDS&time_range=...  spend etc.
Every call goes through meta_ingest.GraphClient; tests inject a fake
transport. The token stays in the Authorization header.
"""
import copy
import datetime
from decimal import Decimal, InvalidOperation

from . import abtest, jobs, meta_ingest, postlive

CREATIVE_FIELDS = ("id,name,object_story_id,object_story_spec,asset_feed_spec,url_tags,object_url,"
                   "degrees_of_freedom_spec")
AD_READ_FIELDS = f"id,name,account_id,effective_status,creative{{{CREATIVE_FIELDS}}}"
VERIFY_FIELDS = "id,effective_status,creative{id,object_story_spec,asset_feed_spec,object_url,url_tags}"
# The creative fields the copy carries over. Everything else on a creative
# is set by Meta (id, effective_object_story_id, thumbnail_url, ...).
COPY_FIELDS = ("object_story_spec", "asset_feed_spec", "url_tags", "object_url", "degrees_of_freedom_spec")
INSIGHTS_FIELDS = "spend,impressions,inline_link_clicks"
INSIGHTS_MAX_AGE_S = 3600
LIVE_STATUS = postlive.LIVE_AD_STATUS
TARGET_PREFIX = "test:"

WARNING = ("Meta does not allow editing a creative, so this makes a copy of the ad's creative with the new link "
           "and points the ad at the copy. Meta restarts the ad's learning phase, and the new post starts with "
           "no likes, comments or shares (the old post keeps them).")


class GoLiveError(abtest.AbtestError):
    """A go-live or revert that cannot go on; one line, exit 1."""


class GoLiveRefused(GoLiveError):
    """Refused before any write to Meta."""


class NotActive(GoLiveRefused):
    """The ad is not ACTIVE (not approved yet, paused, ...)."""


class ExistingPost(GoLiveRefused):
    """The ad uses an existing Page post; its link cannot be changed here."""


class GoLiveUnverified(GoLiveError):
    """The write went through but the read-back does not show the expected
    creative and link."""


def target(test_id):
    return f"{TARGET_PREFIX}{test_id}"


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def client_for(tenant):
    """The Graph client from tenant.yaml's meta: block and the token in the
    environment (MetaTokenMissing before any call when it is not set)."""
    tenant.load_env()
    cfg = meta_ingest.meta_config(tenant)
    return meta_ingest.make_client(meta_ingest.token_from_env(cfg, tenant), cfg)


# ---------------------------------------------------------------------------
# the creative copy
# ---------------------------------------------------------------------------

def _cta_slot(node, base):
    value = (node.get("call_to_action") or {}).get("value") if isinstance(node.get("call_to_action"), dict) else None
    if isinstance(value, dict) and "link" in value:
        yield f"{base}.call_to_action.value.link", value, "link"


def _link_slots(creative):
    """(field path, containing dict, key) for every destination link the
    creative carries: object_story_spec link_data.link, the link_data /
    video_data call-to-action link, carousel cards, asset_feed_spec
    link_urls[].website_url, and object_url."""
    spec = creative.get("object_story_spec")
    if isinstance(spec, dict):
        for kind in ("link_data", "video_data"):
            data = spec.get(kind)
            if not isinstance(data, dict):
                continue
            base = f"object_story_spec.{kind}"
            if kind == "link_data" and "link" in data:
                yield f"{base}.link", data, "link"
            yield from _cta_slot(data, base)
            for i, card in enumerate(data.get("child_attachments") or []):
                if not isinstance(card, dict):
                    continue
                if "link" in card:
                    yield f"{base}.child_attachments[{i}].link", card, "link"
                yield from _cta_slot(card, f"{base}.child_attachments[{i}]")
    feed = creative.get("asset_feed_spec")
    if isinstance(feed, dict):
        for i, item in enumerate(feed.get("link_urls") or []):
            if isinstance(item, dict) and "website_url" in item:
                yield f"asset_feed_spec.link_urls[{i}].website_url", item, "website_url"
    if "object_url" in creative:
        yield "object_url", creative, "object_url"


def link_fields(creative):
    """[(field path, value)] of every destination link in the creative."""
    return [(path, node[key]) for path, node, key in _link_slots(creative or {})]


def is_existing_post(creative):
    """An ad made from an existing Page post: object_story_id, no spec."""
    creative = creative or {}
    return bool(creative.get("object_story_id")) and not (creative.get("object_story_spec")
                                                          or creative.get("asset_feed_spec"))



def _drop_read_only_echoes(payload):
    """Cycle 107b: a creative read from Meta echoes fields Meta refuses on
    create. `image_url` beside `image_hash` is one (code 100 / 1443051 on
    the first real go-live, 2026-10-12): the hash is the stored image, the
    url is Meta's own rendering of it. Drop the url wherever both appear."""
    spec = payload.get("object_story_spec") or {}
    nodes = [spec.get("video_data"), spec.get("link_data")]
    nodes += list((spec.get("link_data") or {}).get("child_attachments") or [])
    for node in nodes:
        if isinstance(node, dict) and node.get("image_hash") and "image_url" in node:
            del node["image_url"]

def copy_creative(creative, url, *, name):
    """(payload for POST act_<id>/adcreatives, [{"field", "old", "new"}]):
    the creative's COPY_FIELDS with every link field set to `url`. url_tags
    and every other field are unchanged."""
    if is_existing_post(creative):
        raise ExistingPost("the ad uses an existing Page post (object_story_id, no spec); its link cannot be "
                           "changed by copying the creative. Change it by hand in Ads Manager.")
    payload = {k: copy.deepcopy(creative[k]) for k in COPY_FIELDS if creative.get(k) not in (None, "", {}, [])}
    _drop_read_only_echoes(payload)
    changes = []
    for path, node, key in _link_slots(payload):
        changes.append({"field": path, "old": node[key], "new": url})
        node[key] = url
    if not changes:
        raise GoLiveRefused("the ad's creative has no link field (object_story_spec link / call-to-action link, "
                            "asset_feed_spec link_urls, object_url) to change")
    payload["name"] = name
    return payload, changes


def _verify(ad, creative_id, url):
    """(verified, destination): the read-back ad uses `creative_id` and every
    link field it carries is `url` (compared like the linked-ad icon does)."""
    creative = ad.get("creative") or {}
    links = [value for _path, value in link_fields(creative)]
    want = postlive.normalize_url(url)
    ok = (str(creative.get("id") or "") == str(creative_id) and bool(links)
          and all(postlive.normalize_url(v) == want for v in links))
    return ok, meta_ingest.destination_of(creative)


def _store_on_inbox(tenant, ad_id, destination, status):
    """The verified destination on the ad's inbox item, so the linked-ad
    icon (cycle 83) shows it without waiting for refresh-links."""
    inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
    try:
        item = inbox.read(ad_id)
    except ValueError:
        return
    if item is not None:
        meta_ingest.store_links(inbox, item, {"destination_url": destination, "effective_status": status or ""},
                                meta_ingest._now())


# ---------------------------------------------------------------------------
# go live / revert
# ---------------------------------------------------------------------------

def _meta_ad_id(rec):
    source = rec.get("source") or {}
    ad_id = str(source.get("ad_id") or "")
    if not (ad_id.isdigit() and ad_id.isascii()):
        raise GoLiveRefused(f"test {rec['test_id']} was not built from a Meta ad (source {source.get('kind') or '?'}); "
                            "there is no ad to change")
    return ad_id



def _use_the_pages_instagram_account(client, payload):
    """Cycle 107c: an ad's creative can name an Instagram id Meta no longer
    resolves (the first real go-live, 2026-10-12, carried 17841401298434556
    while the Page's connected account is 17841465606493804, the same
    @peaksaunas_usa). Meta refuses a new creative with the stale id (code
    200 / 1815199 "Ad account does not have access to Instagram account").
    The copy names the Page's connected Instagram account instead; the
    handle shown on Instagram does not change. Returns the change list."""
    spec = payload.get("object_story_spec") or {}
    page_id, old = spec.get("page_id"), spec.get("instagram_user_id")
    if not page_id or not old:
        return []
    try:
        page = client.get(str(page_id), {"fields": "instagram_business_account"})
    except meta_ingest.MetaError:
        return []
    new = str(((page or {}).get("instagram_business_account") or {}).get("id") or "")
    if not new or new == str(old):
        return []
    spec["instagram_user_id"] = new
    return [{"field": "object_story_spec.instagram_user_id", "old": str(old), "new": new}]

def plan(tenant, rec, *, client, force=False):
    """Reads the ad and builds the creative copy; writes nothing. Returns the
    summary go_live prints (and the dry run shows). Raises GoLiveRefused."""
    test_id = rec["test_id"]
    split_url = (rec.get("split") or {}).get("url")
    if not split_url:
        raise GoLiveRefused(f"test {test_id} has no split link; publish it first "
                            f"(harness abtest publish {test_id} --by <email>)")
    if rec.get("status") != "live":
        raise GoLiveRefused(f"test {test_id} is {rec.get('status')!r}; only a live test can go live on Meta")
    current = rec.get("meta_go_live")
    if current and not current.get("reverted_at"):
        raise GoLiveRefused(f"test {test_id} is already live on Meta (ad {current.get('ad_id')}, since "
                            f"{current.get('at')}); run harness abtest revert-go-live first")
    ad_id = _meta_ad_id(rec)
    account = meta_ingest.meta_config(tenant)["ad_account_id"]
    ad = client.get(ad_id, {"fields": AD_READ_FIELDS})
    owner = f"act_{ad.get('account_id') or '?'}"
    if owner != account:
        raise GoLiveRefused(f"ad {ad_id} is in ad account {owner}, not {account}")
    creative = ad.get("creative") or {}
    if is_existing_post(creative):
        raise ExistingPost(f"ad {ad_id} uses an existing Page post (object_story_id {creative.get('object_story_id')}, "
                           f"no spec): its link must be changed by hand in Ads Manager. Set it to {split_url}")
    status = ad.get("effective_status") or ""
    if status != LIVE_STATUS and not force:
        raise NotActive(f"ad {ad_id} is {status or 'of unknown status'}, not ACTIVE (approved and running); "
                        "use --force to change it anyway")
    name = f"{creative.get('name') or ad.get('name') or ad_id} | split {test_id}"[:250]
    payload, changes = copy_creative(creative, split_url, name=name)
    changes += _use_the_pages_instagram_account(client, payload)
    return {"test_id": test_id, "ad_id": ad_id, "ad_name": ad.get("name") or "", "effective_status": status,
            "account": account, "old_creative_id": str(creative.get("id") or ""),
            "old_destination": meta_ingest.destination_of(creative), "split_url": split_url,
            "url_tags": creative.get("url_tags") or "", "changes": changes, "payload": payload,
            "forced": bool(force and status != LIVE_STATUS)}


def _print_plan(summary, log):
    log(f"test {summary['test_id']}: Meta ad {summary['ad_id']} \"{summary['ad_name']}\" "
        f"({summary['effective_status'] or 'unknown status'}), creative {summary['old_creative_id']}")
    for change in summary["changes"]:
        log(f"  {change['field']}: {change['old'] or '-'} -> {change['new']}")
    log(f"  url_tags kept: {summary['url_tags'] or '(none)'}")
    log(f"WARNING: {WARNING}")


def go_live(tenant, test_id, *, by, client, dry_run=False, force=False, log=print):
    """`harness abtest go-live`: copy the creative with the split link, point
    the ad at the copy, read it back and verify, record it on the test, store
    the verified destination on the inbox item, and audit it. `dry_run`
    reads the ad and prints the change; it writes nothing. Returns the
    summary (with "new_creative_id", "verified_destination" after a write).
    Raises GoLiveRefused before any write, GoLiveUnverified after one."""
    rec = abtest.load_test(tenant, test_id)
    summary = plan(tenant, rec, client=client, force=force)
    _print_plan(summary, log)
    if dry_run:
        log("dry run: nothing was changed on Meta, in the test record or in the audit log")
        return summary
    created = client.post(f"{summary['account']}/adcreatives", summary["payload"])
    new_id = str(created.get("id") or "")
    if not new_id:
        raise meta_ingest.MetaAPIError(f"Meta created no creative id for ad {summary['ad_id']}; the ad is unchanged")
    log(f"created creative {new_id}")
    client.post(summary["ad_id"], {"creative": {"creative_id": new_id}})
    back = client.get(summary["ad_id"], {"fields": VERIFY_FIELDS})
    verified, seen = _verify(back, new_id, summary["split_url"])
    at = _utc_now()
    block = {"at": at, "by": by, "ad_id": summary["ad_id"], "ad_name": summary["ad_name"],
             "old_creative_id": summary["old_creative_id"], "new_creative_id": new_id,
             "old_destination": summary["old_destination"], "split_url": summary["split_url"],
             "verified_destination": seen, "verified": verified, "forced": summary["forced"]}
    rec = abtest.load_test(tenant, test_id)
    if rec.get("meta_go_live"):
        rec.setdefault("meta_go_live_history", []).append(rec["meta_go_live"])
    rec["meta_go_live"] = block
    if verified:
        rec["linked_at"] = at
    abtest.save_test(tenant, rec)
    _store_on_inbox(tenant, summary["ad_id"], seen, back.get("effective_status") or summary["effective_status"])
    jobs.audit(tenant, by=by, action="meta_go_live", target=target(test_id),
               detail={k: block[k] for k in ("ad_id", "old_creative_id", "new_creative_id", "old_destination",
                                             "verified_destination", "verified", "forced")})
    summary.update(new_creative_id=new_id, verified_destination=seen, verified=verified, at=at)
    if not verified:
        raise GoLiveUnverified(f"ad {summary['ad_id']} was changed, but reading it back shows creative "
                               f"{(back.get('creative') or {}).get('id')} and link {seen or '-'}, not {new_id} and "
                               f"{summary['split_url']}. Check it in Ads Manager; harness abtest revert-go-live "
                               f"{test_id} sets the old creative back")
    log(f"verified: ad {summary['ad_id']} now uses creative {new_id} and links to {seen}")
    return summary


def revert(tenant, test_id, *, by, client, log=print):
    """`harness abtest revert-go-live`: set the ad's creative back to the one
    it had before go-live, read it back and verify, and record it."""
    rec = abtest.load_test(tenant, test_id)
    block = rec.get("meta_go_live")
    if not block or block.get("reverted_at"):
        raise GoLiveRefused(f"test {test_id} is not live on Meta; there is nothing to revert")
    ad_id, old_id = block["ad_id"], block["old_creative_id"]
    log(f"test {test_id}: Meta ad {ad_id} back to creative {old_id} ({block.get('old_destination') or '-'})")
    log(f"WARNING: Meta restarts the ad's learning phase again. Likes and comments on the split-link post stay on "
        "that post; the old post still has its own.")
    client.post(ad_id, {"creative": {"creative_id": old_id}})
    back = client.get(ad_id, {"fields": VERIFY_FIELDS})
    creative = back.get("creative") or {}
    seen = meta_ingest.destination_of(creative)
    verified = str(creative.get("id") or "") == str(old_id) and (
        not block.get("old_destination")
        or postlive.normalize_url(seen) == postlive.normalize_url(block["old_destination"]))
    rec = abtest.load_test(tenant, test_id)
    rec["meta_go_live"].update(reverted_at=_utc_now(), reverted_by=by, revert_verified_destination=seen,
                               revert_verified=verified)
    rec["linked_at"] = None
    abtest.save_test(tenant, rec)
    _store_on_inbox(tenant, ad_id, seen, back.get("effective_status"))
    jobs.audit(tenant, by=by, action="meta_revert_go_live", target=target(test_id),
               detail={"ad_id": ad_id, "creative_id": old_id, "from_creative_id": block.get("new_creative_id"),
                       "verified_destination": seen, "verified": verified})
    if not verified:
        raise GoLiveUnverified(f"ad {ad_id} was changed, but reading it back shows creative {creative.get('id')} "
                               f"and link {seen or '-'}, not {old_id}. Check it in Ads Manager")
    log(f"verified: ad {ad_id} uses creative {old_id} again and links to {seen or '-'}")
    return {"test_id": test_id, "ad_id": ad_id, "ad_name": block.get("ad_name") or "", "creative_id": old_id,
            "from_creative_id": block.get("new_creative_id"), "split_url": block.get("split_url"),
            "verified_destination": seen, "verified": verified}


# ---------------------------------------------------------------------------
# the lever (abtest.auto_go_live)
# ---------------------------------------------------------------------------

def _set_auto(tenant, test_id, state, reason):
    rec = abtest.load_test(tenant, test_id)
    auto = rec.setdefault("auto_go_live", {"since": _utc_now()})
    auto.update(state=state, reason=reason, checked_at=_utc_now())
    abtest.save_test(tenant, rec)
    return auto


def _try_auto(tenant, test_id, *, by, client, log):
    try:
        summary = go_live(tenant, test_id, by=by, client=client, log=lambda _msg: None)
    except NotActive as e:
        return _set_auto(tenant, test_id, "pending", str(e))  # tried again on the next from-inbox run
    except GoLiveUnverified as e:
        return _set_auto(tenant, test_id, "unverified", str(e))
    except GoLiveRefused as e:
        return _set_auto(tenant, test_id, "refused", str(e))
    except meta_ingest.MetaError as e:  # token, permission, API: tried again on the next run
        return _set_auto(tenant, test_id, "pending", str(e))
    log(f"  {test_id}: live on Meta ad {summary['ad_id']} ({summary['verified_destination']})")
    return _set_auto(tenant, test_id, "done", f"live on Meta ad {summary['ad_id']}")


def auto_go_live(tenant, test_id, *, by, client=None, log=print):
    """With abtest.auto_go_live on: go live for a test from-inbox has just
    published, if its Meta ad is ACTIVE; else mark it pending for the next
    run. Never forces, never touches an existing-post creative. Returns the
    test's auto_go_live block, or None for a test with no Meta ad."""
    rec = abtest.load_test(tenant, test_id)
    try:
        _meta_ad_id(rec)
    except GoLiveRefused:
        return None
    if client is None:
        try:
            client = client_for(tenant)
        except meta_ingest.MetaError as e:
            return _set_auto(tenant, test_id, "pending", str(e))
    return _try_auto(tenant, test_id, by=by, client=client, log=log)


def retry_pending(tenant, *, by, client=None, log=print):
    """Each test the lever marked pending (its ad was not ACTIVE yet) is
    tried again. Tests the lever never touched are left alone. Returns the
    test ids that went live."""
    pending = [r["test_id"] for r in abtest.list_tests(tenant)
               if (r.get("auto_go_live") or {}).get("state") == "pending" and r.get("status") == "live"]
    done = []
    for test_id in pending:
        if client is None:
            try:
                client = client_for(tenant)
            except meta_ingest.MetaError as e:
                log(f"auto go-live: {e}")
                return done
        if _try_auto(tenant, test_id, by=by, client=client, log=log)["state"] == "done":
            done.append(test_id)
    return done


# ---------------------------------------------------------------------------
# insights (spend, impressions, link clicks), cached per hour on the test
# ---------------------------------------------------------------------------

def _money(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def insights_period(rec, today=None):
    """(ad_id, since, until) as YYYY-MM-DD for the Meta numbers of the
    period the ad sent traffic to the split link: from go-live (else, for a
    split link pasted by hand, from live_at) to the revert or finish (else
    today). None when the test has no Meta ad or never went live."""
    block = rec.get("meta_go_live") or {}
    ad_id = block.get("ad_id") or str((rec.get("source") or {}).get("ad_id") or "")
    start = block.get("at") or rec.get("live_at")
    if not (ad_id.isdigit() and start):
        return None
    end = block.get("reverted_at") or rec.get("finished_at") or (today or datetime.date.today()).isoformat()
    since, until = str(start)[:10], str(end)[:10]
    return ad_id, since, max(since, until)


def _age_s(stamp, now):
    try:
        then = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - then).total_seconds()


def refresh_insights(tenant, rec, *, client, now=None):
    """Reads the ad's insights for insights_period into rec["meta_insights"]
    unless the cached copy is for the same period and under an hour old.
    Returns (rec, "fetched" | "cached" | reason). Saves the record."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    period = insights_period(rec, today=now.date())
    if period is None:
        return rec, "no Meta ad or not live"
    ad_id, since, until = period
    cache = rec.get("meta_insights") or {}
    age = _age_s(cache.get("fetched_at"), now)
    if (cache.get("ad_id") == ad_id and cache.get("since") == since and cache.get("until") == until
            and age is not None and age < INSIGHTS_MAX_AGE_S):
        return rec, "cached"
    data = client.get(f"{ad_id}/insights", {"fields": INSIGHTS_FIELDS, "level": "ad",
                                            "time_range": {"since": since, "until": until}})
    rows = data.get("data") or []
    rec = abtest.load_test(tenant, rec["test_id"])
    rec["meta_insights"] = {
        "ad_id": ad_id, "since": since, "until": until, "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spend": f"{sum((_money(r.get('spend')) for r in rows), Decimal('0')):.2f}",
        "impressions": sum(_int(r.get("impressions")) for r in rows),
        "link_clicks": sum(_int(r.get("inline_link_clicks")) for r in rows),
    }
    abtest.save_test(tenant, rec)
    return rec, "fetched"


def with_costs(rows, insights):
    """Adds "spend" and "cost_per_order" to each results row from the test's
    cached Meta spend. The ad's spend is shared by the arms in proportion to
    the visitors the split page sent each one ("assign" events); with no
    assigns yet, equally. cost_per_order is None with no orders."""
    if not insights:
        return rows
    spend = _money(insights.get("spend"))
    total = sum(r.get("assigns") or 0 for r in rows)
    for r in rows:
        share = Decimal(r.get("assigns") or 0) / total if total else Decimal(1) / len(rows)
        r["spend"] = f"{spend * share:.2f}"
        orders = r.get("orders")
        r["cost_per_order"] = f"{spend * share / orders:.2f}" if orders else None
    return rows


def format_insights(rec):
    """One line for `harness abtest results`, or "" with no cached numbers."""
    ins = rec.get("meta_insights")
    if not ins:
        return ""
    clicks, impressions = ins.get("link_clicks") or 0, ins.get("impressions") or 0
    ctr = f"{clicks / impressions:.2%}" if impressions else "-"
    return (f"Meta ad {ins['ad_id']} {ins['since']}..{ins['until']}: spend ${ins['spend']}, {impressions} "
            f"impressions, {clicks} link clicks (CTR {ctr}); as of {ins['fetched_at']}. Spend per arm is split by "
            "each arm's share of split-page assignments.")
