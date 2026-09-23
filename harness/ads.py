"""Cycle 69: every ad the harness knows, grouped by ad name -- the listicle
site's home page (harness/site.py).

Sources, all read from disk on each call:
- A/B/C tests (tenants/<t>/abtests/*.json): the test's `name`;
- inbox items (tenants/<t>/meta_inbox/*/ad.json, Meta pulls and uploads):
  `ad_name`;
- older runs (tenants/<t>/out/<run>/): the ad_brief's `source_file` without
  its extensions ("hidden-costs-v2.transcript.txt" -> "hidden-costs-v2").
  A run that is a test variant belongs to its test, never listed twice; a
  run built from an inbox item's media file takes that item's ad name.

Two names are the same ad when they match after lower-casing and treating
every run of non-letters/digits as one space ("Hidden Costs V2" ==
"hidden-costs-v2").
"""
import datetime
import json
import re
from pathlib import Path

from . import abstats, abtest, abevents, meta_ingest, runstate

VARIANTS_SHOWN_FOR_RUNS = 3


def ad_key(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def name_from_source_file(source_file):
    base = Path(str(source_file or "")).name
    return base.split(".")[0] if base else ""


def _load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _ts(value):
    """A sortable ISO string from a datetime string, an epoch, or ''."""
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds")
    text = str(value or "")
    m = re.match(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)", text)
    return f"{m.group(1)}T{m.group(2)}" if m else text


def _page_of(run_dir, state):
    pages = sorted(state.get("pages") or {})
    return pages[0] if pages else None


def _run_entry(run_dir, state, page):
    return {"run_id": run_dir.name, "page": page, "state": (state.get("pages") or {}).get(page) or state.get("state"),
            "mtime": run_dir.stat().st_mtime}


def collect_ads(tenant, *, include_run=None):
    """[ad] newest activity first. An ad is {"key", "name", "created",
    "updated", "status", "tests", "inbox", "runs", "variants"}. `variants`
    is what the home page shows: the newest test's three variants, else the
    newest runs' pages. `include_run(run_dir, state)` filters older runs
    (the site hides the test suite's own runs)."""
    groups = {}

    def group(name):
        key = ad_key(name)
        if not key:
            return None
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": key, "name": name, "created": "", "updated": "",
                               "tests": [], "inbox": [], "runs": []}
        return g

    def touch(g, created, updated=None):
        created, updated = _ts(created), _ts(updated or created)
        if created and (not g["created"] or created < g["created"]):
            g["created"] = created
        if updated and updated > g["updated"]:
            g["updated"] = updated

    test_runs = set()
    for rec in abtest.list_tests(tenant):
        g = group(rec.get("name") or rec.get("test_id"))
        if g is None:
            continue
        g["name"] = rec.get("name") or g["name"]
        g["tests"].append(rec)
        touch(g, rec.get("created_at"), rec.get("updated_at"))
        for v in rec.get("variants") or []:
            if v.get("run_dir"):
                test_runs.add(Path(v["run_dir"]).name)

    media_names = {}
    for item in meta_ingest.Inbox(tenant.meta_inbox_dir).items():
        if item.get("state") == "?":
            continue
        g = group(item.get("ad_name") or item.get("ad_id"))
        if g is None:
            continue
        g["inbox"].append(item)
        touch(g, item.get("created_time") or item.get("pulled_at"), item.get("updated_at"))
        if item.get("media_file"):
            media_names[item["media_file"]] = item.get("ad_name") or item.get("ad_id")

    out_dir = Path(tenant.out_dir)
    if out_dir.is_dir():
        for run_dir in out_dir.iterdir():
            if run_dir.name in test_runs or not runstate.state_path(run_dir).exists():
                continue
            try:
                state = runstate.load_state(run_dir)
            except ValueError:
                continue
            if include_run is not None and not include_run(run_dir, state):
                continue
            source_file = _load_json(run_dir / "ad_brief.json").get("source_file") or ""
            name = media_names.get(Path(source_file).name) or name_from_source_file(source_file)
            if not name:
                continue
            page = _page_of(run_dir, state)
            if page is None:
                continue
            g = group(name)
            if g is None:
                continue
            g["runs"].append(_run_entry(run_dir, state, page))
            touch(g, run_dir.stat().st_mtime)

    ads = []
    for g in groups.values():
        g["tests"].sort(key=lambda r: r.get("created_at") or "", reverse=True)
        g["runs"].sort(key=lambda r: r["mtime"], reverse=True)
        g["variants"] = _variants(g)
        g["status"] = _status(g)
        ads.append(g)
    ads.sort(key=lambda a: (a["updated"], a["key"]), reverse=True)
    return ads


def _variants(g):
    if g["tests"]:
        rec = g["tests"][0]
        return [{"key": v["key"], "arm": v["arm"], "status": v.get("status"),
                 "run_id": Path(v["run_dir"]).name if v.get("run_dir") else None,
                 "page": v.get("cartridge"), "url": (v.get("shopify") or {}).get("url"),
                 "replacements": v.get("replacements") or []}
                for v in rec.get("variants") or []]
    return [{"key": None, "arm": r["page"], "status": r["state"], "run_id": r["run_id"], "page": r["page"],
             "url": None, "replacements": []} for r in g["runs"][:VARIANTS_SHOWN_FOR_RUNS]]


def _status(g):
    if g["tests"]:
        rec = g["tests"][0]
        return f"test {rec.get('status')}"
    if g["inbox"]:
        item = g["inbox"][-1]
        return f"inbox {item.get('state')}"
    if g["runs"]:
        return g["runs"][0]["state"] or ""
    return ""


def add_stats(tenant, ad):
    """Adds views / clicks / ctr / p_best to each variant of the ad's newest
    test (only a live or finished test has events). Call it only for the
    ads a page shows: P(best) is a Monte Carlo."""
    if not ad["tests"]:
        return ad
    rec = ad["tests"][0]
    if rec.get("status") not in abtest.POOLED_STATUSES:
        return ad
    counts = abevents.counts(abevents.db_path(tenant), rec["test_id"])
    data = {}
    for v in ad["variants"]:
        c = counts.get(v["key"], {"views": 0, "clicks": 0})
        data[v["key"]] = (min(c["clicks"], c["views"]), c["views"])
    pb = abstats.p_best(data, draws=5000)
    for v in ad["variants"]:
        clicks, views = data[v["key"]]
        v.update(views=views, clicks=clicks, ctr=abstats.ctr(clicks, views), p_best=pb.get(v["key"], 0.0))
    return ad
