"""Cycle 69: the listicle site (listicle.peaksaunasteam.com, docs/LISTICLE-SITE.md).

The team-facing front of `harness serve`: the same Flask app, the same login
(harness/serve.py's _authenticate runs first on every route here), plain
server-rendered HTML, a few lines of inline JS (copy a link, desktop/mobile
preview width). Pages:

  /                          every ad, grouped by ad name (harness/ads.py)
  /gen/<run>/<page>          one generation: review render, versions,
                             feedback -> regenerate job, publish / replace
  /upload                    upload an ad -> inbox item -> create-test job
  /jobs, /job/<id>           the job queue (harness/jobs.py)
  /audit                     who did what, when

Nothing slow runs in a request: feedback, uploads, publishes and replacements
only write a job; `harness worker` runs it. Every POST here carries a CSRF
token (an HMAC of the reviewer's email, keyed from REVIEW_PASSWORD) on top
of serve.py's Origin/Referer check. Feedback and uploads are rate limited
per reviewer. Every value that came from a person or from disk goes through
html.escape.
"""
import datetime
import hashlib
import hmac
import html
import json
import os
import re
from pathlib import Path

from flask import Response, abort, current_app, g, redirect, request, send_file, url_for
from PIL import Image

from . import abevents, abtest, ads as ads_mod, budget, jobs, meta_ingest, runstate, textutil
from . import serve as _serve
from . import upload as upload_mod

ENDPOINTS = (
    "ads_home", "generation", "gen_feedback", "gen_publish", "gen_replace", "page_review_version",
    "run_thumb", "ad_media", "upload_ad", "job_list", "job_detail", "audit_log",
)
CSRF_ENDPOINTS = frozenset({"gen_feedback", "gen_publish", "gen_replace", "upload_ad"})

ADS_PER_PAGE = 20
MAX_FEEDBACK_CHARS = 2000
FEEDBACK_PER_MIN = 10
UPLOADS_PER_WINDOW = 6
UPLOAD_WINDOW_S = 600
# The upload form's whole body: the 500 MB file plus the text fields.
MAX_REQUEST_BYTES = 520 * 1024 * 1024

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def csrf_token(email):
    """Per reviewer, stable across restarts, unguessable without
    REVIEW_PASSWORD. Basic auth has no session to hang a random token on, so
    the token is derived from the login itself."""
    key = hashlib.sha256(b"harness-listicle-csrf:" + (os.environ.get("REVIEW_PASSWORD") or "").encode()).digest()
    return hmac.new(key, (email or "").strip().lower().encode(), hashlib.sha256).hexdigest()[:40]


def _csrf_field():
    return f'<input type="hidden" name="csrf" value="{csrf_token(g.reviewer_email)}">'


def _check_csrf():
    if request.method != "POST" or request.endpoint not in CSRF_ENDPOINTS:
        return None
    sent = request.form.get("csrf") or ""
    if not hmac.compare_digest(sent, csrf_token(g.reviewer_email)):
        return Response("Missing or wrong form token. Reload the page and try again.\n", status=403)
    return None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

e = html.escape

SITE_CSS = """
:root{--basalt:#181918;--stone:#EFE3D2;--flare:#F27046;--fossil:#C0C8C3;--paper:#FFFFFF;--muted:#55585A}
*{box-sizing:border-box}
body{margin:0;background:var(--stone);color:var(--basalt);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow-wrap:anywhere}
a{color:var(--basalt)}
header.top{background:var(--basalt);color:var(--stone);padding:10px 16px;display:flex;flex-wrap:wrap;
  gap:6px 18px;align-items:center}
header.top a{color:var(--stone);text-decoration:none}
header.top .brand{font-weight:700;letter-spacing:.02em}
header.top nav{display:flex;flex-wrap:wrap;gap:4px 14px}
header.top .who{margin-left:auto;font-size:.8rem;opacity:.8}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0 .6em} h2{font-size:1.15rem;margin:0} h3{font-size:1rem;margin:1em 0 .4em}
.card{background:var(--paper);border:1px solid var(--fossil);border-radius:4px;padding:14px;margin:0 0 14px}
.row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.btn{display:inline-block;background:var(--flare);color:var(--basalt);border:1px solid var(--flare);
  border-radius:4px;padding:8px 14px;font:inherit;font-weight:600;text-decoration:none;cursor:pointer;line-height:1.2}
.btn.secondary{background:var(--paper);border-color:var(--fossil)}
.btn.small{padding:4px 10px;font-size:.85rem}
label{display:block;font-weight:600;margin:.8em 0 .25em}
input[type=text],input[type=search],input[type=file],textarea,select{width:100%;border:1px solid var(--fossil);
  border-radius:4px;padding:8px;font:inherit;background:var(--paper);color:var(--basalt)}
textarea{min-height:120px}
.muted{color:var(--muted);font-size:.88rem}
.pill{display:inline-block;border:1px solid var(--fossil);border-radius:4px;padding:0 8px;font-size:.8rem;
  background:var(--stone);white-space:nowrap}
.pill.failed{background:var(--basalt);color:var(--stone);border-color:var(--basalt)}
.pill.done,.pill.live{background:var(--flare);border-color:var(--flare)}
.variants{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin-top:12px}
.variant{border:1px solid var(--fossil);border-radius:4px;padding:10px;background:var(--paper)}
.variant img.thumb{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:4px;background:var(--stone);display:block}
.ad-media{width:96px;height:96px;object-fit:cover;border-radius:4px;border:1px solid var(--fossil)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--fossil);vertical-align:top;font-size:.9rem}
table.stats td,table.stats th{font-size:.82rem;padding:2px 4px}
.table-wrap{overflow-x:auto}
.split{display:flex;gap:8px;margin-top:8px}
.split input{flex:1;min-width:0}
.frames{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.frames.one{grid-template-columns:1fr}
iframe.preview{width:100%;height:72vh;border:1px solid var(--fossil);border-radius:4px;background:var(--paper);display:block}
iframe.preview.mobile{width:390px;max-width:100%}
.warn{border-left:4px solid var(--flare);background:var(--paper);padding:10px 12px;border-radius:4px;margin:10px 0}
.error{border-left:4px solid var(--basalt);background:var(--paper);padding:10px 12px;border-radius:4px;margin:10px 0;font-weight:600}
.pager{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
ul.plain{list-style:none;padding:0;margin:0} ul.plain li{padding:4px 0;border-bottom:1px solid var(--fossil)}
pre.detail{white-space:pre-wrap;margin:0;font-size:.8rem}
img,video,iframe{max-width:100%}
@media (max-width:800px){.frames{grid-template-columns:1fr}}
@media (max-width:600px){main{padding:12px} h1{font-size:1.3rem} header.top .who{margin-left:0;width:100%}}
"""

SITE_JS = """
function pkCopy(id){var el=document.getElementById(id);if(!el)return;
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(el.value);}
  else{el.select();document.execCommand('copy');}
  var b=document.getElementById(id+'-btn');if(b){b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1500);}}
function pkWidth(mode){var f=document.querySelectorAll('iframe.preview');
  for(var i=0;i<f.length;i++){f[i].className='preview'+(mode==='mobile'?' mobile':'');}}
"""


def _shell(title, body, *, refresh=None):
    email = getattr(g, "reviewer_email", "") or ""
    nav = "".join(
        f'<a href="{url_for(ep)}">{label}</a>'
        for ep, label in (("ads_home", "Ads"), ("upload_ad", "Upload an ad"), ("job_list", "Jobs"),
                          ("run_list", "Runs"), ("image_library", "Images"), ("audit_log", "Audit"))
    )
    meta_refresh = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"{meta_refresh}<title>{e(title)}</title><style>{SITE_CSS}</style><script>{SITE_JS}</script></head>"
        f'<body><header class="top"><a class="brand" href="{url_for("ads_home")}">'
        f'{e(current_app.config.get("SITE_BRAND") or "listicle")}</a>'
        f'<nav>{nav}</nav><span class="who">{e(email)}</span></header><main>{body}</main></body></html>'
    )


def _page(title, body, status=200, refresh=None):
    return Response(_shell(title, body, refresh=refresh), status=status, mimetype="text/html")


def _pill(text, kind=None):
    kind = kind or str(text).split()[-1] if text else ""
    return f'<span class="pill {e(kind)}">{e(str(text))}</span>'


def _date(value):
    return e(str(value or "")[:10]) or "-"


def _copy_row(elem_id, value, label="Split link"):
    return (
        f'<div class="muted">{e(label)}</div><div class="split">'
        f'<input type="text" readonly id="{elem_id}" value="{e(value)}" aria-label="{e(label)}">'
        f'<button type="button" class="btn small" id="{elem_id}-btn" onclick="pkCopy(\'{elem_id}\')">Copy</button></div>'
    )


def _spend_line(tenant):
    today = datetime.date.today().isoformat()
    spent = budget.daily_spend(tenant, today)
    reserved = budget.daily_reserved(tenant, today)
    cap = budget.daily_cap_usd(tenant)
    text = f"Model spend today: ${spent:.2f}"
    text += f" of the ${cap:.2f} daily cap" if cap is not None else " (no daily cap)"
    if reserved:
        text += f", plus ${reserved:.2f} reserved by runs in progress"
    return f'<p class="muted">{e(text)}</p>'


# ---------------------------------------------------------------------------
# run / page helpers
# ---------------------------------------------------------------------------

def _run_page_or_404(tenant, run_id, page):
    run_dir = _serve._run_dir_or_404(tenant, run_id)
    state = runstate.load_state(run_dir)
    if page not in (state.get("pages") or {}):
        abort(404)
    return run_dir, state


def _target(run_dir, page):
    return f"run:{Path(run_dir).name}/{page}"


def _versions(run_dir, page):
    """Number of superseded versions (page.v1.json ... page.vN.json); the
    current page is version N + 1."""
    from . import revise as revise_mod

    return revise_mod.next_version(Path(run_dir) / page) - 1


def _publish_position(state, page, record):
    """(index of the last history entry that published this page's current
    Shopify page, live flag or None). Found by the url in its note."""
    if not record or not record.get("url"):
        return None, None
    marker = f"url={record['url']} "
    for i in range(len(state.get("history") or []) - 1, -1, -1):
        entry = state["history"][i]
        note = entry.get("note") or ""
        if entry.get("state") == "published" and note.startswith(marker):
            live = True if "live=True" in note else False if "live=False" in note else None
            return i, live
    return None, None


def _last_revise_index(state, page):
    prefix = f"page={page}; revised to v"
    idx = None
    for i, entry in enumerate(state.get("history") or []):
        if (entry.get("note") or "").startswith(prefix):
            idx = i
    return idx


def _publish_status(tenant, run_dir, state, page):
    """What the publish / replace section needs: {record, live, newer, test,
    key}. `newer` is True when the page was regenerated after its last
    publish."""
    record = runstate.published_page_record(run_dir, page)
    pub_index, live = _publish_position(state, page, record)
    rev_index = _last_revise_index(state, page)
    newer = record is not None and rev_index is not None and (pub_index is None or rev_index > pub_index)
    ab = runstate.abtest_record(run_dir, page)
    rec = abtest.find_test(tenant, ab["test_id"]) if ab else None
    return {"record": record, "live": live, "newer": newer, "test": rec, "key": (ab or {}).get("key")}


def _ad_name_for_run(tenant, run_dir, status):
    if status["test"]:
        return status["test"].get("name") or status["test"]["test_id"]
    brief = _serve._load_json(run_dir / "ad_brief.json")
    return ads_mod.name_from_source_file(brief.get("source_file")) or run_dir.name


def _job_box(job, *, what):
    if job is None:
        return ""
    state = job["state"]
    lines = [f'<div class="row"><b>{e(what)}</b> {_pill(state, state)} '
             f'<a class="muted" href="{url_for("job_detail", job_id=job["id"])}">job #{job["id"]}</a></div>',
             f'<div class="muted">requested by {e(job["by"])} at {e(job["created_at"])}'
             + (f", started {e(job['started_at'])}" if job.get("started_at") else "")
             + (f", finished {e(job['finished_at'])}" if job.get("finished_at") else "") + "</div>"]
    if state == "failed":
        lines.append(f'<div class="error">Failed: {e(job["reason"])}</div>')
    elif state in ("queued", "running"):
        lines.append('<div class="muted">This page reloads every 10 seconds until the job ends.</div>')
    return '<div class="card">' + "".join(lines) + "</div>"


# ---------------------------------------------------------------------------
# page renderers
# ---------------------------------------------------------------------------

def _variant_card(tenant, ad, v):
    if not v.get("run_id") or not v.get("page"):
        return (f'<div class="variant"><div><b>{e(v.get("key") or "")}</b> {e(v.get("arm") or "")}</div>'
                f'<div class="muted">not built yet ({e(v.get("status") or "pending")})</div></div>')
    gen = url_for("generation", run_id=v["run_id"], page=v["page"])
    thumb = url_for("run_thumb", run_id=v["run_id"], page=v["page"])
    head = f"<b>{e(v['key'])}</b> " if v.get("key") else ""
    parts = [f'<div class="variant"><a href="{gen}"><img class="thumb" loading="lazy" src="{thumb}" alt=""></a>',
             f'<div>{head}{e(v.get("arm") or "")}</div>',
             f'<div class="muted">{e(v.get("status") or "")}</div>']
    if "views" in v:
        parts.append(
            '<table class="stats"><tr><th>Views</th><th>CTA</th><th>CTR</th><th>P(best)</th></tr>'
            f'<tr><td>{v["views"]}</td><td>{v["clicks"]}</td><td>{v["ctr"]:.1%}</td><td>{v["p_best"]:.0%}</td></tr></table>'
        )
        if v.get("replacements"):
            parts.append(f'<div class="muted">stats since the page was replaced at '
                         f'{e(v["replacements"][-1]["at"])}</div>')
    if v.get("url"):
        parts.append(f'<div class="muted"><a href="{e(v["url"])}" rel="noopener" target="_blank">live page</a></div>')
    parts.append(f'<p><a class="btn secondary small" href="{gen}">Open and give feedback</a></p></div>')
    return "".join(parts)


def _ad_card(tenant, ad, index):
    rec = ad["tests"][0] if ad["tests"] else None
    item = ad["inbox"][-1] if ad["inbox"] else None
    status = ad["status"] or "-"
    parts = [f'<article class="card"><div class="row"><h2>{e(ad["name"])}</h2>{_pill(status)}'
             f'<span class="muted">created {_date(ad["created"])}</span></div>']
    if item is not None:
        media = ""
        if item.get("media_type") == "image" and item.get("media_file"):
            media = f'<img class="ad-media" loading="lazy" src="{url_for("ad_media", ad_id=item["ad_id"])}" alt="">'
        source = "uploaded" if item.get("source") == "upload" else "from Meta"
        who = f" by {item.get('uploaded_by')}" if item.get("uploaded_by") else ""
        parts.append(f'<div class="row" style="margin-top:8px">{media}<div class="muted">'
                     f'{e(item.get("media_type") or "")} ad {e(source + who)}, inbox state '
                     f'{e(item.get("state") or "")}' + (f": {e(item.get('reason') or '')}" if item.get("reason") else "")
                     + "</div></div>")
    if rec is not None:
        parts.append(f'<div class="muted" style="margin-top:6px">A/B/C test {e(rec["test_id"])}'
                     + (f": {e(rec['reason'])}" if rec.get("reason") else "") + "</div>")
        split = (rec.get("split") or {}).get("url")
        if split:
            parts.append(_copy_row(f"split-{index}", split))
    if ad["variants"]:
        parts.append('<div class="variants">' + "".join(_variant_card(tenant, ad, v) for v in ad["variants"]) + "</div>")
    else:
        parts.append('<p class="muted">No pages yet.</p>')
    extra = len(ad["runs"]) - (0 if rec else len(ad["variants"]))
    if extra > 0:
        parts.append(f'<p class="muted">{extra} more generation(s) of this ad under '
                     f'<a href="{url_for("run_list")}">Runs</a>.</p>')
    parts.append("</article>")
    return "".join(parts)


def render_home(tenant):
    q = (request.args.get("q") or "").strip()
    page_arg = request.args.get("page", "1")
    page_no = int(page_arg) if page_arg.isdigit() else 1
    everything = ads_mod.collect_ads(
        tenant, include_run=lambda run_dir, state: not _serve._looks_like_test_run(tenant, run_dir, state))
    if q:
        needle, key = q.lower(), ads_mod.ad_key(q)
        everything = [a for a in everything if needle in a["name"].lower() or (key and key in a["key"])]
    pages = max(1, -(-len(everything) // ADS_PER_PAGE))
    page_no = max(1, min(page_no, pages))
    shown = everything[(page_no - 1) * ADS_PER_PAGE: page_no * ADS_PER_PAGE]
    for ad in shown:
        ads_mod.add_stats(tenant, ad)
    busy = [j for j in jobs.list_jobs(tenant, limit=100) if j["state"] in ("queued", "running")]
    pager = '<div class="pager">'
    if page_no > 1:
        pager += f'<a class="btn secondary small" href="{url_for("ads_home", q=q or None, page=page_no - 1)}">Newer</a>'
    pager += f'<span class="muted">page {page_no} of {pages} ({len(everything)} ad(s))</span>'
    if page_no < pages:
        pager += f'<a class="btn secondary small" href="{url_for("ads_home", q=q or None, page=page_no + 1)}">Older</a>'
    pager += "</div>"
    body = (
        '<div class="row"><h1>Ads</h1><a class="btn" href="' + url_for("upload_ad") + '">Upload an ad</a></div>'
        + _spend_line(tenant)
        + (f'<p class="muted"><a href="{url_for("job_list")}">{len(busy)} job(s) queued or running</a></p>' if busy else "")
        + f'<form method="get" action="{url_for("ads_home")}" class="row" style="margin-bottom:14px">'
        f'<input type="search" name="q" value="{e(q)}" placeholder="Search by ad name" aria-label="Search by ad name" '
        'style="flex:1;min-width:0"><button class="btn secondary" type="submit">Search</button></form>'
        + "".join(_ad_card(tenant, ad, i) for i, ad in enumerate(shown))
        + ("" if shown else '<div class="card">No ads match.</div>')
        + pager
    )
    return _page("Ads", body)


def _version_frames(run_dir, page, superseded):
    run_id = run_dir.name
    current = url_for("page_review", run_id=run_id, page=page)
    toggle = ('<div class="row" style="margin:8px 0"><button type="button" class="btn secondary small" '
              'onclick="pkWidth(\'desktop\')">Desktop</button><button type="button" class="btn secondary small" '
              'onclick="pkWidth(\'mobile\')">Mobile</button></div>')
    if superseded:
        old = url_for("page_review_version", run_id=run_id, page=page, version=superseded)
        return (toggle + '<div class="frames">'
                f'<div><h3>Version {superseded} (before the last feedback)</h3>'
                f'<iframe class="preview" src="{old}" title="version {superseded}"></iframe></div>'
                f'<div><h3>Version {superseded + 1} (current)</h3>'
                f'<iframe class="preview" src="{current}" title="current version"></iframe></div></div>')
    return (toggle + '<div class="frames one"><div><h3>Version 1 (current)</h3>'
            f'<iframe class="preview" src="{current}" title="current version"></iframe></div></div>')


def _history_list(run_dir, state, page, superseded):
    run_id = run_dir.name
    items = [f'<li><a href="{url_for("page_review_version", run_id=run_id, page=page, version=n)}" '
             f'target="_blank" rel="noopener">Version {n}</a></li>' for n in range(1, superseded + 1)]
    items.append(f'<li><a href="{url_for("page_review", run_id=run_id, page=page)}" target="_blank" '
                 f'rel="noopener">Version {superseded + 1} (current)</a></li>')
    marker = f"page={page}"
    events = [h for h in state.get("history") or [] if marker in (h.get("note") or "")]
    event_items = "".join(
        f'<li>{e(h.get("at") or "")} <b>{e(h.get("state") or "")}</b> by {e(h.get("by") or "")}'
        f'<div class="muted">{e(_history_note(h.get("note") or ""))}</div></li>' for h in events[-15:]
    )
    return ('<h3>Versions</h3><ul class="plain">' + "".join(items) + "</ul>"
            + ('<h3>History</h3><ul class="plain">' + event_items + "</ul>" if event_items else ""))


def _history_note(note):
    """runstate.mark_revised writes "revised to vN" where N is the number the
    OLD page was saved under (page.vN.json); on this site the new page is
    version N + 1, so say that."""
    return re.sub(r"revised to v(\d+)", lambda m: f"new version {int(m.group(1)) + 1} (old one kept as version "
                  f"{m.group(1)})", note)


def _feedback_list(state, page):
    entries = [f for f in state.get("feedback") or [] if f.get("page") == page]
    if not entries:
        return ""
    rows = []
    for f in reversed(entries[-10:]):
        cuts = "".join(f'<div class="muted">cut: {e(c)}</div>' for c in f.get("cuts") or [])
        rows.append(f'<li><b>{e(f.get("by") or "")}</b> <span class="muted">{e(f.get("at") or "")}</span>'
                    f'<div style="white-space:pre-wrap">{e(f.get("notes") or "")}</div>{cuts}</li>')
    return '<h3>Feedback so far</h3><ul class="plain">' + "".join(rows) + "</ul>"


def _publish_section(tenant, run_dir, page, status, superseded):
    run_id = run_dir.name
    rec, key = status["test"], status["key"]
    if rec is not None:
        if rec.get("status") != "live":
            return (f'<div class="card"><h3>A/B/C test</h3><p>Variant {e(key or "")} of test {e(rec["test_id"])} '
                    f'({e(rec.get("status") or "")}). The test is not live, so there is nothing to replace.</p></div>')
        job = jobs.latest_job(tenant, job_type="replace_variant", target=_target(run_dir, page))
        box = _job_box(job, what="Replace live variant")
        if status["newer"]:
            return (f'<div class="card"><h3>Live A/B/C variant</h3><div class="warn">This page is variant '
                    f'{e(key)} of the live test {e(rec["test_id"])}. The live page still shows the version that was '
                    f'published; version {superseded + 1} waits for you. Replacing the live variant resets its views '
                    f'and CTA clicks to zero (the other variants keep theirs).</div>'
                    f'<p><a class="btn" href="{url_for("gen_replace", run_id=run_id, page=page)}">Replace live variant '
                    f'{e(key)}</a></p>{box}</div>')
        return (f'<div class="card"><h3>Live A/B/C variant</h3><p class="muted">Variant {e(key)} of the live test '
                f'{e(rec["test_id"])}. The live page shows the current version.</p>{box}</div>')
    record = status["record"]
    if record is None:
        return ('<div class="card"><h3>Publish</h3><p class="muted">This page is not on Shopify yet. The first '
                'publish is done with <code>harness publish</code> (see docs/PUBLISHING.md).</p></div>')
    job = jobs.latest_job(tenant, job_type="publish_page", target=_target(run_dir, page))
    box = _job_box(job, what="Publish")
    link = f'<a href="{e(record.get("url") or "")}" rel="noopener" target="_blank">{e(record.get("url") or "")}</a>'
    if status["newer"]:
        return (f'<div class="card"><h3>Publish</h3><p>The Shopify page {link} shows an older version. Version '
                f'{superseded + 1} is not published yet.</p><p><a class="btn" '
                f'href="{url_for("gen_publish", run_id=run_id, page=page)}">Publish new version</a></p>{box}</div>')
    return f'<div class="card"><h3>Publish</h3><p class="muted">The Shopify page {link} shows the current version.</p>{box}</div>'


def render_generation(tenant, run_dir, state, page, *, error=None, status_code=200, text=""):
    run_id = run_dir.name
    status = _publish_status(tenant, run_dir, state, page)
    superseded = _versions(run_dir, page)
    name = _ad_name_for_run(tenant, run_dir, status)
    job = jobs.latest_job(tenant, job_type="regenerate", target=_target(run_dir, page))
    replace_job = jobs.latest_job(tenant, job_type="replace_variant", target=_target(run_dir, page))
    publish_job = jobs.latest_job(tenant, job_type="publish_page", target=_target(run_dir, page))
    busy = any(j and j["state"] in ("queued", "running") for j in (job, replace_job, publish_job))
    arm = ""
    if status["test"]:
        v = next((v for v in status["test"]["variants"] if v["key"] == status["key"]), {})
        arm = f'variant {status["key"]}: {v.get("arm", "")}'
    look = (state.get("listicle") or {}).get("look") or (state.get(page) or {}).get("look") or ""
    head = (
        f'<p class="muted"><a href="{url_for("ads_home", q=name)}">&laquo; {e(name)}</a></p>'
        f'<div class="row"><h1>{e(page)}</h1>{_pill(state["pages"][page])}</div>'
        f'<p class="muted">{e(arm or page)}{(" / look " + e(look)) if look else ""} &middot; run {e(run_id)} &middot; '
        f'<a href="{url_for("run_detail", run_id=run_id)}">scores and approve (reviewer page)</a></p>'
    )
    active = jobs.active_job(tenant, job_type="regenerate", target=_target(run_dir, page))
    if active:
        form = ('<div class="card"><h3>Feedback</h3><p class="muted">A regenerate job for this page is '
                f'{e(active["state"])}. Send more feedback when it ends.</p></div>')
    else:
        form = (
            f'<form class="card" method="post" action="{url_for("gen_feedback", run_id=run_id, page=page)}">'
            f'<h3>Feedback</h3>{_csrf_field()}'
            + (f'<div class="error">{e(error)}</div>' if error else "")
            + f'<label for="feedback">What should change? (required, up to {MAX_FEEDBACK_CHARS} characters)</label>'
            f'<textarea id="feedback" name="feedback" required maxlength="{MAX_FEEDBACK_CHARS}">{e(text)}</textarea>'
            '<p class="muted">Start a line with <code>cut:</code> and paste an exact sentence to remove it. '
            'Everything else goes to the writer as notes. Sending queues a new version of this page; '
            'nothing is published.</p><button class="btn" type="submit">Send feedback and regenerate</button></form>'
        )
    body = (
        head
        + _job_box(job, what="Regenerate")
        + (f'<div class="card"><b>Version {job["result"]["new_version"]} is ready.</b> '
           f'Gate: {e(str(job["result"].get("gate", "")))}. Compare it with the old version below.</div>'
           if job and job["state"] == "done" and job.get("result") else "")
        + _version_frames(run_dir, page, superseded)
        + '<div class="frames" style="margin-top:14px"><div>'
        + form + _feedback_list(state, page)
        + "</div><div>"
        + _publish_section(tenant, run_dir, page, status, superseded)
        + '<div class="card">' + _history_list(run_dir, state, page, superseded) + "</div>"
        + "</div></div>"
    )
    return _page(f"{name} / {page}", body, status=status_code, refresh=10 if busy else None)


def _confirm_page(title, lines, action_url, button, cancel_url):
    body = (
        f'<h1>{e(title)}</h1><div class="card"><div class="warn">' + "".join(f"<p>{line}</p>" for line in lines)
        + f'</div><form method="post" action="{action_url}" class="row">{_csrf_field()}'
        '<input type="hidden" name="confirm" value="yes">'
        f'<button class="btn" type="submit">{e(button)}</button>'
        f'<a class="btn secondary" href="{cancel_url}">Cancel</a></form></div>'
    )
    return _page(title, body)


def _upload_form(tenant, *, error=None, values=None, status=200):
    values = values or {}
    auto = abtest.settings(tenant)["auto_publish"]
    note = ("When the three pages are built, the test is published and the split link shows here and on the "
            "Ads page. Paste it into the ad in Ads Manager." if auto else
            "When the three pages are built, publish the test with <code>harness abtest publish</code>.")

    def field(name, label, *, area=False, hint=""):
        value = e(values.get(name) or "")
        control = (f'<textarea id="{name}" name="{name}" maxlength="{upload_mod.MAX_COPY_CHARS}">{value}</textarea>'
                   if area else f'<input type="text" id="{name}" name="{name}" value="{value}" '
                                f'maxlength="{upload_mod.MAX_COPY_CHARS}" placeholder="{e(hint)}">')
        return f'<label for="{name}">{label}</label>{control}'

    body = (
        '<h1>Upload an ad</h1><form class="card" method="post" enctype="multipart/form-data" '
        f'action="{url_for("upload_ad")}">{_csrf_field()}'
        + (f'<div class="error">{e(error)}</div>' if error else "")
        + '<label for="ad_name">Ad name (required)</label>'
        f'<input type="text" id="ad_name" name="ad_name" required maxlength="{upload_mod.MAX_NAME_CHARS}" '
        f'value="{e(values.get("ad_name") or "")}" placeholder="The name the ad has in Ads Manager">'
        '<label for="media">Video or image (mp4, mov, jpg, png, webp; up to 500 MB)</label>'
        '<input type="file" id="media" name="media" required '
        'accept="video/mp4,video/quicktime,.mp4,.mov,image/jpeg,image/png,image/webp">'
        + field("primary_text", "Primary text (optional)", area=True)
        + field("headline", "Headline (optional)")
        + field("description", "Description (optional)")
        + field("cta", "Call to action (optional)", hint="e.g. SHOP_NOW")
        + f'<p class="muted">The harness builds three landing pages from this ad (an A/B/C test). {note}</p>'
        '<button class="btn" type="submit">Upload and build the test</button></form>'
    )
    return _page("Upload an ad", body, status=status)


def _job_result_html(tenant, job):
    result = job.get("result") or {}
    payload = job.get("payload") or {}
    parts = []
    if job["type"] == "create_test":
        item = None
        try:
            item = meta_ingest.Inbox(tenant.meta_inbox_dir).read(payload.get("ad_id"))
        except ValueError:
            item = None
        if item:
            parts.append(f'<p>Ad: <a href="{url_for("ads_home", q=item.get("ad_name"))}">{e(item.get("ad_name") or "")}</a>'
                         f' <span class="muted">(inbox {e(item.get("state") or "")})</span></p>')
        if result.get("test_id"):
            parts.append(f"<p>A/B/C test {e(result['test_id'])}: {e(result.get('outcome') or '')}</p>")
        if result.get("split_url"):
            parts.append(_copy_row("split-job", result["split_url"])
                         + '<p>Paste this split link as the ad\'s website URL in Ads Manager.</p>')
        elif result.get("publish_error"):
            parts.append(f'<div class="error">Auto publish failed: {e(result["publish_error"])}</div>')
    elif payload.get("run_id") and payload.get("page"):
        parts.append(f'<p><a href="{url_for("generation", run_id=payload["run_id"], page=payload["page"])}">'
                     f'{e(payload["run_id"])} / {e(payload["page"])}</a></p>')
        if result.get("url"):
            parts.append(f'<p>Page: <a href="{e(result["url"])}" rel="noopener" target="_blank">{e(result["url"])}</a></p>')
        if result.get("new_version"):
            parts.append(f"<p>New version: {int(result['new_version'])}</p>")
    return "".join(parts)


def _thumb_placeholder():
    return Response(_serve._THUMB_PLACEHOLDER_SVG.replace("could not load", "no image"), mimetype="image/svg+xml")


def _run_thumb(tenant, run_dir, page):
    assets = run_dir / textutil.safe_filename(page) / "assets"
    if not assets.is_dir():
        return _thumb_placeholder()
    images = sorted(p for p in assets.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES)
    if not images:
        return _thumb_placeholder()
    cache = Path(tenant.runs_dir) / "thumb-cache"
    thumb = cache / textutil.safe_filename(f"{run_dir.name}-{page}.jpg")
    try:
        if not thumb.exists() or thumb.stat().st_mtime < images[0].stat().st_mtime:
            cache.mkdir(parents=True, exist_ok=True)
            with Image.open(images[0]) as im:
                im = im.convert("RGB")
                im.thumbnail((480, 480), Image.LANCZOS)
                im.save(thumb, format="JPEG", quality=80)
        resp = send_file(thumb, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp
    except Exception:  # noqa: BLE001 -- a broken asset shows the placeholder
        return _thumb_placeholder()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def register(app, tenant):
    """Adds the listicle site's routes to serve.build_app's app. serve.py's
    _require_auth (login + Origin check) is registered first, so it runs
    before the CSRF check here."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.config["SITE_BRAND"] = f"{tenant.display_name} listicle"
    feedback_limiter = abevents.RateLimiter(limit=FEEDBACK_PER_MIN, window_s=60.0)
    upload_limiter = abevents.RateLimiter(limit=UPLOADS_PER_WINDOW, window_s=UPLOAD_WINDOW_S)

    @app.before_request
    def _site_csrf():
        if request.endpoint == "beacon":
            return None
        return _check_csrf()

    @app.after_request
    def _site_headers(resp):
        # an uploaded image is served back as what its bytes say it is, never sniffed as HTML
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.route("/")
    def ads_home():
        return render_home(tenant)

    @app.route("/gen/<run_id>/<page>")
    def generation(run_id, page):
        run_dir, state = _run_page_or_404(tenant, run_id, page)
        return render_generation(tenant, run_dir, state, page)

    @app.route("/gen/<run_id>/<page>/feedback", methods=["POST"])
    def gen_feedback(run_id, page):
        from . import revise as revise_mod

        run_dir, state = _run_page_or_404(tenant, run_id, page)
        email = g.reviewer_email
        text = (request.form.get("feedback") or "").replace("\r\n", "\n").strip()
        if not text:
            return render_generation(tenant, run_dir, state, page, error="Feedback is required.", status_code=400)
        if len(text) > MAX_FEEDBACK_CHARS:
            return render_generation(tenant, run_dir, state, page, text=text[:MAX_FEEDBACK_CHARS], status_code=400,
                                     error=f"Feedback is {len(text)} characters; the limit is {MAX_FEEDBACK_CHARS}.")
        target = _target(run_dir, page)
        if jobs.active_job(tenant, job_type="regenerate", target=target):
            return render_generation(tenant, run_dir, state, page, status_code=409,
                                     error="A regenerate job for this page is still waiting or running.")
        if not feedback_limiter.allow(email.lower()):
            return render_generation(tenant, run_dir, state, page, text=text, status_code=429,
                                     error=f"More than {FEEDBACK_PER_MIN} feedback posts in a minute. Wait a moment.")
        cuts, notes = revise_mod.parse_cuts_and_notes(text)
        runstate.request_changes(run_dir, tenant, page=page, by=email, notes=notes, cuts=cuts,
                                 note="listicle site feedback")
        job_id = jobs.enqueue(tenant, "regenerate", {"run_id": run_dir.name, "page": page, "by": email},
                              by=email, target=target)
        jobs.audit(tenant, by=email, action="feedback", target=target, detail={"job_id": job_id, "text": text})
        return redirect(url_for("generation", run_id=run_dir.name, page=page), code=303)

    def _publish_or_replace(run_id, page, *, replace):
        run_dir, state = _run_page_or_404(tenant, run_id, page)
        status = _publish_status(tenant, run_dir, state, page)
        superseded = _versions(run_dir, page)
        target = _target(run_dir, page)
        rec = status["test"]
        if replace:
            if rec is None or rec.get("status") != "live" or not status["key"]:
                return _page("Not a live variant", "<p>This page is not a variant of a live A/B/C test.</p>", 409)
        elif rec is not None:
            return _page("Part of a test", "<p>This page is an A/B/C test variant. Use <b>Replace live variant</b> "
                         "on its page instead.</p>", 409)
        elif status["record"] is None:
            return _page("Not published", "<p>This page is not on Shopify yet.</p>", 409)
        elif status["live"] is None:
            return _page("Unknown publish state", "<p>The run's history does not say whether the Shopify page is "
                         "live or a draft. Publish it from the command line.</p>", 409)
        if not status["newer"]:
            return _page("Nothing new", "<p>The Shopify page already shows the current version.</p>", 409)
        job_type = "replace_variant" if replace else "publish_page"
        if jobs.active_job(tenant, job_type=job_type, target=target):
            return _page("Already queued", "<p>That job is already waiting or running.</p>", 409)
        gen_url = url_for("generation", run_id=run_dir.name, page=page)
        if request.method == "GET":
            if replace:
                lines = [
                    f"Replace the live page of variant <b>{e(status['key'])}</b> of test <b>{e(rec['test_id'])}</b> "
                    f"with version {superseded + 1}.",
                    f"This resets variant {e(status['key'])}'s views, CTA clicks, CTR and P(best) to zero: its old "
                    "events are kept apart and still count for the build in the library, but not in this test's "
                    "results. The other variants keep their numbers. The split link does not change.",
                ]
                return _confirm_page("Replace live variant", lines, url_for("gen_replace", run_id=run_dir.name, page=page),
                                     f"Yes, replace live variant {status['key']}", gen_url)
            where = "live" if status["live"] else "a draft (unpublished)"
            lines = [f"Publish version {superseded + 1} over the Shopify page "
                     f"<b>{e(status['record'].get('url') or '')}</b>, which stays {where}.",
                     "Visitors see the new version as soon as the job ends."]
            return _confirm_page("Publish new version", lines, url_for("gen_publish", run_id=run_dir.name, page=page),
                                 "Yes, publish", gen_url)
        if request.form.get("confirm") != "yes":
            return _page("Confirm first", f'<p>Open <a href="{gen_url}">the page</a> and use the button there; '
                         "it asks you to confirm.</p>", 400)
        email = g.reviewer_email
        if replace:
            payload = {"run_id": run_dir.name, "page": page, "test_id": rec["test_id"], "key": status["key"],
                       "by": email}
            detail = {"test_id": rec["test_id"], "key": status["key"], "version": superseded + 1}
        else:
            payload = {"run_id": run_dir.name, "page": page, "by": email, "live": status["live"]}
            detail = {"url": status["record"].get("url"), "live": status["live"], "version": superseded + 1}
        job_id = jobs.enqueue(tenant, job_type, payload, by=email, target=target)
        jobs.audit(tenant, by=email, action="replace" if replace else "publish", target=target,
                   detail={**detail, "job_id": job_id})
        return redirect(gen_url, code=303)

    @app.route("/gen/<run_id>/<page>/publish", methods=["GET", "POST"])
    def gen_publish(run_id, page):
        return _publish_or_replace(run_id, page, replace=False)

    @app.route("/gen/<run_id>/<page>/replace", methods=["GET", "POST"])
    def gen_replace(run_id, page):
        return _publish_or_replace(run_id, page, replace=True)

    @app.route("/run/<run_id>/review/<page>/v/<int:version>")
    def page_review_version(run_id, page, version):
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        safe_page = textutil.safe_filename(page)
        path = (_serve._safe_path(run_dir, f"{safe_page}-review.v{version}.html")
                or _serve._safe_path(run_dir, f"{safe_page}/index.v{version}.html"))
        if path is None:
            return Response(_serve._page_shell("No such version", "<p>No such version.</p>"), mimetype="text/html")
        return send_file(path, mimetype="text/html")

    @app.route("/run/<run_id>/thumb/<page>")
    def run_thumb(run_id, page):
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        return _run_thumb(tenant, run_dir, page)

    @app.route("/ad-media/<ad_id>")
    def ad_media(ad_id):
        inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
        try:
            directory = inbox.item_dir(ad_id)
        except ValueError:
            abort(404)
        item = inbox.read(ad_id)
        if not item or item.get("media_type") != "image" or not item.get("media_file"):
            abort(404)
        path = _serve._safe_path(directory, item["media_file"])
        if path is None:
            abort(404)
        return send_file(path, mimetype=item.get("media_content_type") or None)

    @app.route("/upload", methods=["GET", "POST"])
    def upload_ad():
        if request.method == "GET":
            return _upload_form(tenant)
        email = g.reviewer_email
        values = {k: request.form.get(k) or "" for k in ("ad_name",) + upload_mod.COPY_FIELDS}
        if not upload_limiter.allow(email.lower()):
            return _upload_form(tenant, values=values, status=429,
                                error=f"More than {UPLOADS_PER_WINDOW} uploads in {UPLOAD_WINDOW_S // 60} minutes. "
                                      "Wait a few minutes.")
        media = request.files.get("media")
        try:
            item = upload_mod.save_upload(
                tenant, media.stream if media and media.filename else None, media.filename if media else "",
                ad_name=values["ad_name"], copy={k: values[k] for k in upload_mod.COPY_FIELDS}, by=email)
        except upload_mod.UploadRejected as exc:
            return _upload_form(tenant, values=values, error=str(exc), status=exc.status)
        ad_id = item["ad_id"]
        meta_ingest.Inbox(tenant.meta_inbox_dir).set_state(ad_id, "queued", "uploaded; waiting for the worker")
        job_id = jobs.enqueue(tenant, "create_test", {"ad_id": ad_id, "by": email}, by=email, target=f"ad:{ad_id}")
        jobs.audit(tenant, by=email, action="upload", target=f"ad:{ad_id}",
                   detail={"ad_name": item["ad_name"], "media_file": item["media_file"],
                           "bytes": item["media_bytes"], "job_id": job_id})
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/jobs")
    def job_list():
        rows = "".join(
            f'<tr><td><a href="{url_for("job_detail", job_id=j["id"])}">#{j["id"]}</a></td><td>{e(j["type"])}</td>'
            f'<td>{_pill(j["state"], j["state"])}</td><td>{e(j["target"])}</td><td>{e(j["by"])}</td>'
            f'<td>{e(j["created_at"])}</td><td>{e((j["reason"] or "")[:160])}</td></tr>'
            for j in jobs.list_jobs(tenant, limit=100)
        )
        body = ('<h1>Jobs</h1><p class="muted">Run by <code>harness worker</code>, one at a time, oldest first.</p>'
                '<div class="card table-wrap"><table><tr><th>Job</th><th>Type</th><th>State</th><th>For</th>'
                '<th>By</th><th>Created</th><th>Reason</th></tr>'
                + (rows or '<tr><td colspan="7">No jobs yet.</td></tr>') + "</table></div>")
        return _page("Jobs", body)

    @app.route("/job/<int:job_id>")
    def job_detail(job_id):
        job = jobs.get_job(tenant, job_id)
        if job is None:
            abort(404)
        busy = job["state"] in ("queued", "running")
        body = (
            f'<h1>Job #{job["id"]}: {e(job["type"])}</h1>'
            + _job_box(job, what=job["type"])
            + '<div class="card">' + (_job_result_html(tenant, job) or '<p class="muted">No result yet.</p>')
            + "</div>"
        )
        return _page(f"Job {job['id']}", body, refresh=10 if busy else None)

    @app.route("/audit")
    def audit_log():
        rows = "".join(
            f'<tr><td>{e(r["at"])}</td><td>{e(r["by"])}</td><td>{e(r["action"])}</td><td>{e(r["target"])}</td>'
            f'<td><pre class="detail">{e(json.dumps(r["detail"], ensure_ascii=False, indent=1))}</pre></td></tr>'
            for r in jobs.audit_rows(tenant, limit=300)
        )
        body = ('<h1>Audit log</h1><div class="card table-wrap"><table><tr><th>When</th><th>Who</th><th>What</th>'
                '<th>On</th><th>Detail</th></tr>' + (rows or '<tr><td colspan="5">Nothing yet.</td></tr>')
                + "</table></div>")
        return _page("Audit log", body)
