"""Cycle 69: the listicle site (listicle.peaksaunasteam.com, docs/LISTICLE-SITE.md).

The team-facing front of `harness serve`: the same Flask app, the same login
(harness/serve.py's _authenticate runs first on every route here), plain
server-rendered HTML, a few lines of inline JS (copy a link, phone/desktop
preview, the post-live dialog). Cycle 80: the look lives in
harness/site_ui.py. Pages:

  /                          every ad, grouped by ad name (harness/ads.py),
                             each generation as a phone-shaped card
  /gen/<run>/<page>          one generation: preview, details, versions,
                             feedback -> regenerate job, publish / replace,
                             post live / update live / unpublish (cycle 80)
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

from . import abevents, abtest, ads as ads_mod, budget, jobs, meta_ingest, postlive, runstate, site_ui, textutil
from . import serve as _serve
from . import upload as upload_mod

ENDPOINTS = (
    "ads_home", "generation", "gen_feedback", "gen_publish", "gen_replace", "page_review_version",
    "run_thumb", "ad_media", "upload_ad", "job_list", "job_detail", "audit_log",
    "gen_post_live", "gen_update_live", "gen_unpublish", "ad_frame", "site_favicon", "site_static", "site_manifest",
)
CSRF_ENDPOINTS = frozenset({"gen_feedback", "gen_publish", "gen_replace", "upload_ad",
                            "gen_post_live", "gen_update_live", "gen_unpublish"})

ADS_PER_PAGE = 20
MAX_FEEDBACK_CHARS = 2000
FEEDBACK_PER_MIN = 10
UPLOADS_PER_WINDOW = 6
UPLOAD_WINDOW_S = 600
# The upload form's whole body: the 500 MB file plus the text fields.
MAX_REQUEST_BYTES = 520 * 1024 * 1024

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
LIVE_JOB_TYPES = ("post_live", "update_live", "unpublish_live")
PUBLISH_JOB_TYPES = ("publish_page", "replace_variant") + LIVE_JOB_TYPES
FILTERS = (("all", "All"), ("review", "Needs review"), ("live", "Live"))
JOB_LABELS = {"regenerate": "Regenerate", "create_test": "Build A/B/C test", "publish_page": "Publish new version",
              "replace_variant": "Replace live variant", "post_live": "Post live", "update_live": "Update live page",
              "unpublish_live": "Unpublish"}
HERO_LABELS = {"face": "Face", "story": "Story", "display": "Display"}


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
        return _page("Form expired", _message("Form expired", "Missing or wrong form token. Reload the page and "
                                              "try again."), 403)
    return None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

e = html.escape
icon, chip = site_ui.icon, site_ui.chip


def _page(title, body, status=200, refresh=None):
    return Response(site_ui.shell(title, body, refresh=refresh), status=status, mimetype="text/html")


def _message(title, text, *, back=None):
    link = f'<p><a class="btn ghost" href="{back}">{icon("back")}Back</a></p>' if back else ""
    return (f'<div class="card" style="max-width:640px"><h1 style="font-size:1.25rem;margin-bottom:8px">{e(title)}</h1>'
            f'<p class="muted">{text}</p>{link}</div>')


def _date(value):
    return e(str(value or "")[:10]) or "-"


def _when(value):
    text = str(value or "")
    return e(text[:16].replace("T", " ")) if text else "-"


def _copy_row(elem_id, value, label="Split link"):
    return site_ui.copy_field(elem_id, value, label=label)


def _spend_line(tenant):
    today = datetime.date.today().isoformat()
    spent = budget.daily_spend(tenant, today)
    reserved = budget.daily_reserved(tenant, today)
    cap = budget.daily_cap_usd(tenant)
    text = f"Model spend today: ${spent:.2f}"
    text += f" of the ${cap:.2f} daily cap" if cap is not None else " (no daily cap)"
    if reserved:
        text += f", plus ${reserved:.2f} reserved by runs in progress"
    return f'<span>{e(text)}</span>'


def _storefront_host(tenant):
    return tenant.get("site_host") or ""


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
    Shopify page, live flag or None). Found by the url in its note. Cycle 80:
    an unpublish entry is not a publish of the current version."""
    if not record or not record.get("url"):
        return None, None
    marker = f"url={record['url']} "
    for i in range(len(state.get("history") or []) - 1, -1, -1):
        entry = state["history"][i]
        note = entry.get("note") or ""
        if entry.get("state") == "published" and note.startswith(marker) and " unpublished " not in note:
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
    key, unlinked}. `newer` is True when the page was regenerated after its
    last publish."""
    record = runstate.published_page_record(run_dir, page)
    pub_index, _live = _publish_position(state, page, record)
    rev_index = _last_revise_index(state, page)
    newer = record is not None and rev_index is not None and (pub_index is None or rev_index > pub_index)
    ab = runstate.abtest_record(run_dir, page)
    rec = abtest.find_test(tenant, ab["test_id"]) if ab else None
    st = postlive.page_status(run_dir, state, page)
    return {"record": record, "live": st["live"], "newer": newer, "test": rec, "key": (ab or {}).get("key"),
            "unlinked": st["unlinked"] and rec is None, "redirected": st["redirected"]}


def _ad_name_for_run(tenant, run_dir, status):
    if status["test"]:
        return status["test"].get("name") or status["test"]["test_id"]
    brief = ads_mod._load_json(run_dir / "ad_brief.json")
    source = Path(str(brief.get("source_file") or "")).name
    for item in meta_ingest.Inbox(tenant.meta_inbox_dir).items():
        if source and item.get("media_file") == source:
            return item.get("ad_name") or item.get("ad_id")
    return ads_mod.name_from_source_file(brief.get("source_file")) or run_dir.name


def _first_at(state):
    for entry in state.get("history") or []:
        if entry.get("at"):
            return entry["at"]
    return ""


def _gen_info(tenant, run_id, page, *, test=None, variant=None, taken=None):
    """Everything a generation card or the generation page shows, read from
    the run directory. Never raises for a half-written run."""
    run_dir = Path(tenant.out_dir) / run_id
    try:
        state = runstate.load_state(run_dir)
    except (FileNotFoundError, ValueError):
        state = {"pages": {}}
    page_json = ads_mod._load_json(run_dir / page / "page.json")
    choice = (state.get("listicle") if page == "listicle" else state.get(page)) or {}
    jev = (state.get("jev") or {}) if page == "listicle" else {}
    shipped = next((d for d in jev.get("drafts") or [] if d.get("draft") == jev.get("shipped")), None)
    st = postlive.page_status(run_dir, state, page)
    page_state = (state.get("pages") or {}).get(page) or ""
    record = st["record"] or {}
    in_test = bool(test or st["test"])

    if in_test and (test or {}).get("status") == "live":
        status, label = "live", "Live"
    elif record and st["live"] is True:
        status, label = "live", "Live"
    elif record and st["live"] is False and record.get("page_id"):
        status, label = "hidden", "Hidden"
    elif page_state in ("approved", "published"):
        status, label = "approved", "Approved"
    elif page_state == "rejected":
        status, label = "rejected", "Rejected"
    elif page_state == "changes_requested":
        status, label = "draft", "Changes requested"
    else:
        status, label = "draft", "Draft"

    if shipped and shipped.get("gate"):
        gate = shipped["gate"]
    elif state.get("state") and state.get("state") != "generated":
        gate = "PASS"
    else:
        gate = ""
    rendered = (run_dir / page / "index.html").exists()
    can_post = (rendered and not in_test and not st["redirected"] and page_state != "rejected"
                and (not record or (st["live"] is False and record.get("page_id"))))
    info = {
        "run_id": run_id, "page": page, "run_dir": run_dir, "state": state, "page_state": page_state,
        "status": status, "label": label, "record": record, "live": st["live"], "unlinked": st["unlinked"] and not in_test,
        "in_test": in_test, "variant": variant, "test": test,
        "headline": page_json.get("headline") or "", "display_headline": page_json.get("display_headline") or "",
        "hero": choice.get("hero_style") or page_json.get("hero_style") or "",
        "style": choice.get("style") or page_json.get("style") or "",
        "look": choice.get("look") or page_json.get("look") or "",
        "template": choice.get("headline_template_id") or page_json.get("headline_template_id") or "",
        "skeleton": choice.get("skeleton_id") or "",
        "jev": shipped.get("composite") if shipped else None, "jev_record": jev, "gate": gate,
        "created": _first_at(state), "rendered": rendered, "can_post": bool(can_post),
        "gen_url": url_for("generation", run_id=run_id, page=page),
        "review_url": url_for("page_review", run_id=run_id, page=page),
        "thumb_url": url_for("run_thumb", run_id=run_id, page=page),
        "post_url": url_for("gen_post_live", run_id=run_id, page=page),
    }
    if can_post:
        info["handle"] = record.get("handle") if record else postlive.suggest_handle(tenant, run_dir, page, taken=taken)
    return info


def _job_box(job, *, what):
    if job is None:
        return ""
    state = job["state"]
    times = (f"Requested by {e(job['by'])} at {_when(job['created_at'])}"
             + (f", started {_when(job['started_at'])}" if job.get("started_at") else "")
             + (f", finished {_when(job['finished_at'])}" if job.get("finished_at") else ""))
    lines = [f'<div class="jobline"><b>{e(what)}</b>{chip(state, state)}'
             f'<a class="muted small" href="{url_for("job_detail", job_id=job["id"])}">job #{job["id"]}</a></div>',
             f'<div class="muted">{times}</div>']
    if state == "failed":
        lines.append(f'<div class="error">Failed: {e(job["reason"])}</div>')
    elif state in ("queued", "running"):
        lines.append('<div class="muted">This page reloads every 10 seconds until the job ends.</div>')
    return '<div class="job-box">' + "".join(lines) + "</div>"


def _latest(tenant, target, types):
    found = [j for j in (jobs.latest_job(tenant, job_type=t, target=target) for t in types) if j]
    return max(found, key=lambda j: j["id"]) if found else None


def _active_live_handles(tenant):
    return {(j.get("payload") or {}).get("handle") for j in jobs.list_jobs(tenant, limit=200)
            if j["type"] == "post_live" and j["state"] in ("queued", "running")}


# ---------------------------------------------------------------------------
# components: the post-live form, a generation card, an ad card
# ---------------------------------------------------------------------------

def _post_live_form(tenant, info, *, dialog_id=None, error=None, value=None):
    """The "Post live" form: in a <dialog> on the cards and the generation
    page, and as a page of its own without JS or after an error."""
    host = _storefront_host(tenant)
    record = info["record"]
    handle = value if value is not None else info.get("handle") or ""
    key = dialog_id or "pl"
    readonly = bool(record)
    field = (
        f'<label for="{key}-h">Page address</label>'
        f'<div class="field-pre"><span>{e(host)}/pages/</span>'
        f'<input type="text" name="handle" id="{key}-h" value="{e(handle)}" required maxlength="{postlive.HANDLE_MAX}" '
        f'autocomplete="off" autocapitalize="off" spellcheck="false"'
        + (' readonly' if readonly else f' data-preview="{key}-u" data-msg="{key}-m"') + "></div>"
        + (f'<p class="hint">This page is a hidden draft on Shopify already; it goes live at its own address.</p>'
           if readonly else
           f'<div class="handle-msg" id="{key}-m" aria-live="polite"></div>'
           '<p class="hint">Edit the slug if you like. It starts with <code>lp-</code>; lower-case letters, digits and '
           'hyphens. Checked against every page on the store before anything is created.</p>')
        + f'<div class="url-preview">Goes live at <b>https://{e(host)}/pages/<span id="{key}-u">{e(handle)}</span></b></div>'
    )
    checks = (
        '<ul class="checks">'
        f'<li>{icon("check")}<span>Approved by you ({e(getattr(g, "reviewer_email", ""))}) and the packet stamped ship</span></li>'
        f'<li>{icon("check")}<span>Published live on its own page, at the address above</span></li>'
        f'<li class="no">{icon("x")}<span>No redirect, no A/B/C test, not linked to any ad. The rest of the store stays '
        'locked.</span></li>'
        f'<li>{icon("check")}<span>You can update it or hide it again from this site</span></li></ul>'
    )
    title = info["display_headline"] or info["headline"] or info["page"]
    form = (
        f'<form method="post" action="{info["post_url"]}">{_csrf_field()}<input type="hidden" name="confirm" value="yes">'
        f'<div class="dlg-h"><div><h2 id="{key}-t">Post live</h2><p>{e(title)}</p></div>'
        + (f'<button type="button" class="iconbtn" data-close aria-label="Close">{icon("x")}</button>' if dialog_id else "")
        + '</div><div class="dlg-b">' + (f'<div class="error">{e(error)}</div>' if error else "") + field + checks
        + '</div><div class="dlg-f">'
        + ('<button type="button" class="btn ghost" data-close>Cancel</button>' if dialog_id else
           f'<a class="btn ghost" href="{info["gen_url"]}">Cancel</a>')
        + f'<button class="btn" type="submit">{icon("live")}Post live</button></div></form>'
    )
    if dialog_id:
        return f'<dialog id="{dialog_id}" aria-labelledby="{key}-t">{form}</dialog>'
    return f'<div class="card" style="max-width:600px;padding:0">{form}</div>'


def _facts(info):
    bits = []
    if info["style"]:
        bits.append(f'<span>{e(info["style"].replace("-", " ").capitalize())}</span>')
    if info["template"]:
        bits.append(f'<span>Headline <b>{e(info["template"])}</b></span>')
    jev = info["jev"]
    bits.append(f'<span>Jev <b>{e(f"{float(jev):.2f}") if jev is not None else "&mdash;"}</b></span>')
    if info["look"] and info["look"] != "open":
        bits.append(f'<span>Look <b>{e(info["look"])}</b></span>')
    return '<div class="facts">' + "".join(bits) + "</div>"


def _status_chips(info, *, with_gate=True):
    parts = [chip(info["label"], info["status"], dot=info["status"] in ("live", "approved"))]
    if with_gate and info["gate"]:
        parts.append(chip(info["gate"], "pass" if info["gate"] == "PASS" else "fail"))
    return "".join(parts)


def _gen_card(tenant, ad, v, info, *, dom_id):
    if info is None:
        return (f'<div class="gen"><div class="shot"></div><div class="gen-body"><div class="chips">'
                f'{chip(v.get("key") or "", "solid") if v.get("key") else ""}{chip(v.get("status") or "pending")}</div>'
                f'<div class="gen-title">{e(v.get("arm") or "")}</div><p class="muted small">Not built yet.</p></div></div>')
    hero = HERO_LABELS.get(info["hero"], info["hero"].capitalize() if info["hero"] else "")
    corner = "".join(x for x in (chip(f"Variant {v['key']}", "solid") if v.get("key") else "",
                                 chip(hero) if hero else "") if x)
    title = info["headline"] or v.get("arm") or info["page"]
    parts = [
        f'<div class="gen{" is-live" if info["status"] == "live" else ""}">',
        f'<a class="shot" href="{info["gen_url"]}" tabindex="-1" aria-hidden="true">',
        f'<div class="corner">{corner}</div><div class="phone"><div class="screen">'
        f'<img class="poster" loading="lazy" src="{info["thumb_url"]}" alt="">'
        f'<iframe loading="lazy" sandbox="" tabindex="-1" scrolling="no" title="" src="{info["review_url"]}"></iframe>'
        "</div></div></a>",
        f'<div class="gen-body"><div class="chips">{_status_chips(info)}</div>',
        f'<a class="gen-title" href="{info["gen_url"]}">{e(title)}</a>',
        _facts(info),
    ]
    if "views" in v:
        parts.append(
            '<div class="abstats">'
            f'<div><span>Views</span><b>{v["views"]}</b></div><div><span>CTA</span><b>{v["clicks"]}</b></div>'
            f'<div><span>CTR</span><b>{v["ctr"]:.1%}</b></div><div><span>P(best)</span><b>{v["p_best"]:.0%}</b></div></div>'
        )
        if v.get("replacements"):
            parts.append(f'<div class="muted small">Stats since the page was replaced at '
                         f'{e(v["replacements"][-1]["at"])}</div>')
    url = info["record"].get("url") or v.get("url")
    if url and info["status"] == "live":
        parts.append(site_ui.copy_field(f"{dom_id}-url", url, live=True))
    elif url and info["status"] == "hidden":
        parts.append(f'<div class="muted small">Hidden on Shopify at /pages/{e(info["record"].get("handle") or "")}</div>')
    actions = f'<a class="btn ghost sm" href="{info["gen_url"]}">Open</a>'
    dialog = ""
    if info["can_post"]:
        actions += (f'<a class="btn sm" href="{info["post_url"]}" data-dialog="{dom_id}-post">'
                    f'{icon("live")}Post live</a>')
        dialog = _post_live_form(tenant, info, dialog_id=f"{dom_id}-post")
    parts.append(f'<div class="gen-actions">{actions}</div></div>{dialog}</div>')
    return "".join(parts)


def _ad_thumb(tenant, ad):
    item = ad["inbox"][-1] if ad["inbox"] else None
    if item is not None and item.get("media_type") == "image" and item.get("media_file"):
        return f'<img loading="lazy" src="{url_for("ad_media", ad_id=item["ad_id"])}" alt="">'
    for info in ad.get("gens") or []:
        if info and (info["run_dir"] / "ad-frame" / "frame.jpg").is_file():
            return f'<img loading="lazy" src="{url_for("ad_frame", run_id=info["run_id"])}" alt="">'
    kind = (item or {}).get("media_type")
    return icon("video" if kind == "video" else "image" if kind == "image" else "text")


def _ad_card(tenant, ad, index):
    rec = ad["tests"][0] if ad["tests"] else None
    item = ad["inbox"][-1] if ad["inbox"] else None
    gens = [i for i in ad["gens"] if i]
    meta = []
    if item is not None:
        kind = item.get("media_type") or "ad"
        source = "Uploaded" if item.get("source") == "upload" else "Meta"
        who = f" by {item.get('uploaded_by')}" if item.get("uploaded_by") else ""
        meta.append(f'<span>{icon("video" if kind == "video" else "image")}{e(source)} {e(kind)}{e(who)}</span>')
        meta.append(f'<span title="{e(item.get("reason") or "")}">inbox {e(item.get("state") or "")}</span>')
    meta.append(f'<span>created {_date(ad["created"])}</span>')
    if gens:
        meta.append(f'<span>{len(gens)} generation{"s" if len(gens) != 1 else ""}</span>')
    if any(i["status"] == "live" for i in gens):
        head_chip = chip("Live", "live", dot=True)
    elif rec is not None:
        head_chip = chip(f"test {rec.get('status')}", "attn" if rec.get("status") == "failed" else "")
    else:
        head_chip = chip(ad["status"] or "-")
    parts = [
        f'<article class="ad" id="ad-{index}"><div class="ad-head"><div class="ad-thumb">{_ad_thumb(tenant, ad)}</div>'
        f'<div class="ad-main"><div class="ad-title"><h2>{e(ad["name"])}</h2>{head_chip}</div>'
        f'<div class="ad-meta">{"".join(meta)}</div></div></div>'
    ]
    if rec is not None:
        parts.append(f'<p class="muted small" style="margin:12px 0 0">A/B/C test {e(rec["test_id"])}'
                     + (f": {e(rec['reason'])}" if rec.get("reason") else "") + "</p>")
        split = (rec.get("split") or {}).get("url")
        if split:
            parts.append(f'<div class="ad-split">{_copy_row(f"split-{index}", split)}</div>')
    if ad["variants"]:
        cards = [_gen_card(tenant, ad, v, info, dom_id=f"g{index}-{j}")
                 for j, (v, info) in enumerate(zip(ad["variants"], ad["gens"]))]
        parts.append('<div class="gens">' + "".join(cards) + "</div>")
    else:
        parts.append('<p class="more">No pages yet.</p>')
    extra = len(ad["runs"]) - (0 if rec else len(ad["variants"]))
    if extra > 0:
        parts.append(f'<p class="more">{extra} more generation(s) of this ad under '
                     f'<a href="{url_for("run_list")}">Runs</a>.</p>')
    parts.append("</article>")
    return "".join(parts)


def _ad_gens(tenant, ad, taken):
    rec = ad["tests"][0] if ad["tests"] else None
    ad["gens"] = [
        _gen_info(tenant, v["run_id"], v["page"], test=rec, variant=v, taken=taken)
        if v.get("run_id") and v.get("page") else None
        for v in ad["variants"]
    ]
    return ad


def render_home(tenant):
    q = (request.args.get("q") or "").strip()
    f = request.args.get("f") or "all"
    f = f if f in dict(FILTERS) else "all"
    page_arg = request.args.get("page", "1")
    page_no = int(page_arg) if page_arg.isdigit() else 1
    everything = ads_mod.collect_ads(
        tenant, include_run=lambda run_dir, state: not _serve._looks_like_test_run(tenant, run_dir, state))
    if q:
        needle, key = q.lower(), ads_mod.ad_key(q)
        everything = [a for a in everything if needle in a["name"].lower() or (key and key in a["key"])]
    taken = postlive.handles_in_use(tenant) | _active_live_handles(tenant)
    for ad in everything:
        _ad_gens(tenant, ad, taken)
    has = {
        "all": lambda a: True,
        "review": lambda a: any(i and i["status"] == "draft" for i in a["gens"]),
        "live": lambda a: any(i and i["status"] == "live" for i in a["gens"]),
    }
    counts = {k: sum(1 for a in everything if has[k](a)) for k, _ in FILTERS}
    all_gens = [i for a in everything for i in a["gens"] if i]
    live_gens = sum(1 for i in all_gens if i["status"] == "live")
    everything = [a for a in everything if has[f](a)]
    pages = max(1, -(-len(everything) // ADS_PER_PAGE))
    page_no = max(1, min(page_no, pages))
    shown = everything[(page_no - 1) * ADS_PER_PAGE: page_no * ADS_PER_PAGE]
    for ad in shown:
        ads_mod.add_stats(tenant, ad)
    busy = [j for j in jobs.list_jobs(tenant, limit=100) if j["state"] in ("queued", "running")]

    def link(**kw):
        args = {"q": q or None, "f": None if f == "all" else f}
        args.update(kw)
        return url_for("ads_home", **args)

    seg = "".join(
        f'<a href="{link(f=None if k == "all" else k, page=None)}"{" class=on" if k == f else ""}>'
        f'{label}<span class="n">{counts[k]}</span></a>' for k, label in FILTERS)
    pager = '<div class="pager">'
    if page_no > 1:
        pager += f'<a class="btn ghost sm" href="{link(page=page_no - 1)}">{icon("back")}Newer</a>'
    pager += f'<span class="muted small">page {page_no} of {pages} ({len(everything)} ad(s))</span>'
    if page_no < pages:
        pager += f'<a class="btn ghost sm" href="{link(page=page_no + 1)}">Older</a>'
    pager += "</div>"
    statline = '<div class="statline">' + _spend_line(tenant)
    if busy:
        statline += (f'<a class="busy" href="{url_for("job_list")}">{chip("running", "running")}'
                     f'{len(busy)} job(s) queued or running</a>')
    statline += "</div>"
    empty = ("No ads match." if q or f != "all" else "No ads yet. Upload one, or wait for the next Meta pull.")
    body = (
        '<div class="page-head"><div><h1>Ads</h1>'
        f'<p class="sub"><span class="num">{counts["all"]}</span> ads &middot; <span class="num">{len(all_gens)}</span> '
        f'generations &middot; <span class="num">{live_gens}</span> live</p></div>'
        f'<div class="actions"><a class="btn" href="{url_for("upload_ad")}">{icon("upload")}Upload an ad</a></div></div>'
        + statline
        + f'<div class="toolbar"><nav class="seg" aria-label="Filter">{seg}</nav>'
        f'<form method="get" action="{url_for("ads_home")}" class="search" role="search">'
        + (f'<input type="hidden" name="f" value="{e(f)}">' if f != "all" else "")
        + f'<div class="field">{icon("search")}<input type="search" name="q" value="{e(q)}" '
        'placeholder="Search by ad name" aria-label="Search by ad name"></div>'
        '<button class="btn ghost" type="submit">Search</button></form></div>'
        + "".join(_ad_card(tenant, ad, i) for i, ad in enumerate(shown))
        + ("" if shown else f'<div class="empty">{e(empty)}</div>')
        + pager
    )
    return _page("Ads", body)


# ---------------------------------------------------------------------------
# the generation page
# ---------------------------------------------------------------------------

def _version_frames(run_dir, page, superseded):
    run_id = run_dir.name
    current = url_for("page_review", run_id=run_id, page=page)

    def device(src, caption, title):
        return (f'<div class="device"><div class="cap">{caption}</div><div class="device-frame">'
                f'<iframe class="preview" src="{src}" title="{e(title)}"></iframe></div></div>')

    bar = (
        '<div class="preview-bar"><div class="seg" role="group" aria-label="Preview size">'
        f'<button type="button" class="on" data-view="mobile">{icon("phone")}Phone</button>'
        f'<button type="button" data-view="desktop">{icon("desktop")}Desktop</button></div>'
        f'<a class="btn ghost sm" href="{current}" target="_blank" rel="noopener">{icon("external")}Open full page</a></div>'
    )
    if superseded:
        old = url_for("page_review_version", run_id=run_id, page=page, version=superseded)
        return (bar + '<div class="stage compare">'
                + device(old, f"Version {superseded} (before the last feedback)", f"version {superseded}")
                + device(current, f"<b>Version {superseded + 1} (current)</b>", "current version") + "</div>")
    return bar + '<div class="stage">' + device(current, "Version 1 (current)", "current version") + "</div>"


def _history_list(run_dir, state, page, superseded):
    run_id = run_dir.name
    items = [f'<a class="chip" href="{url_for("page_review_version", run_id=run_id, page=page, version=n)}" '
             f'target="_blank" rel="noopener">Version {n}</a>' for n in range(1, superseded + 1)]
    items.append(f'<a class="chip approved" href="{url_for("page_review", run_id=run_id, page=page)}" target="_blank" '
                 f'rel="noopener">Version {superseded + 1} (current)</a>')
    marker = f"page={page}"
    events = [h for h in state.get("history") or [] if marker in (h.get("note") or "")]
    event_items = "".join(
        f'<li class="{"hot" if h.get("state") == "published" else ""}"><b>{e(h.get("state") or "")}</b> '
        f'<span class="muted">by {e(h.get("by") or "")}</span><div class="t">{_when(h.get("at"))}</div>'
        f'<div class="muted small">{e(_history_note(h.get("note") or ""))}</div></li>' for h in reversed(events[-15:])
    )
    return ('<h3 style="margin-bottom:10px">Versions</h3><div class="versions">' + "".join(items) + "</div>"
            + ('<h3 style="margin:20px 0 10px">History</h3><ul class="timeline">' + event_items + "</ul>"
               if event_items else ""))


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
        cuts = "".join(f'<div class="muted small">cut: {e(c)}</div>' for c in f.get("cuts") or [])
        rows.append(f'<li><div class="row"><b>{e(f.get("by") or "")}</b><span class="muted small">{_when(f.get("at"))}'
                    f'</span></div><div style="white-space:pre-wrap;margin-top:4px">{e(f.get("notes") or "")}</div>{cuts}</li>')
    return ('<div class="card"><h3 style="margin-bottom:12px">Feedback so far</h3><ul class="plain">'
            + "".join(rows) + "</ul></div>")


def _live_block(tenant, info):
    record = info["record"]
    if not record.get("url"):
        return ""
    if info["status"] == "live":
        who = ""
        if record.get("unlinked") and record.get("by"):
            who = f"Posted by {e(record['by'])} &middot; {_when(record.get('at'))}"
            if record.get("updated_by"):
                who += f"<br>Updated by {e(record['updated_by'])} &middot; {_when(record.get('updated_at'))}"
        elif record.get("at"):
            who = f"Published {_when(record.get('at'))}"
        note = ('<p class="hint">Unlinked: no ad, no A/B/C test and no redirect point at it.</p>'
                if info["unlinked"] else "")
        return (site_ui.copy_field("live-url", record["url"], label="Live URL", live=True)
                + (f'<p class="hint">{who}</p>' if who else "") + note)
    hidden = ""
    if record.get("unpublished_by"):
        hidden = f" Hidden by {e(record['unpublished_by'])} &middot; {_when(record.get('unpublished_at'))}."
    return (f'<p class="small muted">Hidden on Shopify at <code>/pages/{e(record.get("handle") or "")}</code>; '
            f'visitors get a 404.{hidden}</p>')


def _publish_section(tenant, run_dir, page, status, superseded, info):
    """The Status card's actions: post live / update / unpublish (cycle 80),
    publish new version, replace live variant."""
    run_id = run_dir.name
    rec, key = status["test"], status["key"]
    target = _target(run_dir, page)
    job = _latest(tenant, target, PUBLISH_JOB_TYPES)
    box = _job_box(job, what=JOB_LABELS.get(job["type"], job["type"])) if job else ""
    if rec is not None:
        if rec.get("status") != "live":
            return (f'<p class="small muted">Variant {e(key or "")} of test {e(rec["test_id"])} '
                    f'({e(rec.get("status") or "")}). The test is not live, so there is nothing to replace.</p>{box}')
        if status["newer"]:
            return (f'<div class="warn">This page is variant {e(key)} of the live test {e(rec["test_id"])}. The live '
                    f'page still shows the version that was published; version {superseded + 1} waits for you. '
                    'Replacing the live variant resets its views and CTA clicks to zero (the other variants keep '
                    f'theirs).</div><div class="side-actions"><a class="btn" '
                    f'href="{url_for("gen_replace", run_id=run_id, page=page)}">Replace live variant {e(key)}</a></div>{box}')
        return (f'<p class="small muted">Variant {e(key)} of the live test {e(rec["test_id"])}. The live page shows the '
                f'current version.</p>{box}')
    record = status["record"]
    buttons = []
    text = ""
    if info["can_post"]:
        buttons.append(f'<a class="btn block" href="{info["post_url"]}" data-dialog="post-live">{icon("live")}Post live</a>')
        text = ("" if record else '<p class="small muted">Not on Shopify yet. <b>Post live</b> approves it as you '
                'and puts it on its own live page, linked from no ad.</p>')
    if record is not None and status["unlinked"] and info["live"] is True:
        text = ('<p class="small muted">The live page shows an older version. Version '
                f'{superseded + 1} is not published yet.</p>' if status["newer"] else
                '<p class="small muted">The live page shows the current version.</p>')
        buttons.append(f'<a class="btn block{"" if status["newer"] else " ghost"}" '
                       f'href="{url_for("gen_update_live", run_id=run_id, page=page)}">{icon("refresh")}Update live page</a>')
        buttons.append(f'<a class="btn block danger" href="{url_for("gen_unpublish", run_id=run_id, page=page)}">'
                       f'{icon("hide")}Unpublish</a>')
    elif record is not None and status["newer"] and info["live"] is not True:
        text += ('<p class="small muted">The Shopify page shows an older version. Version '
                 f'{superseded + 1} is not published yet.</p>')
        buttons.append(f'<a class="btn block ghost" href="{url_for("gen_publish", run_id=run_id, page=page)}">'
                       "Publish new version</a>")
    elif record is not None and status["newer"]:
        text += ('<p class="small muted">The Shopify page shows an older version. Version '
                 f'{superseded + 1} is not published yet.</p>')
        buttons.append(f'<a class="btn block" href="{url_for("gen_publish", run_id=run_id, page=page)}">'
                       "Publish new version</a>")
    elif record is not None and not info["can_post"]:
        text += '<p class="small muted">The Shopify page shows the current version.</p>'
    actions = f'<div class="side-actions">{"".join(buttons)}</div>' if buttons else ""
    return text + actions + box


def _jev_card(state, page):
    """Cycle 76: the listicle's drafts (harness/drafts.py) -- gate result,
    Jev scores per dimension (0 = the rubric's lowest level, 1 = its
    highest), composite, which draft shipped and why."""
    record = state.get("jev") if page == "listicle" else None
    if not record or not record.get("drafts"):
        return ""

    def num(value):
        if value is None:
            return '<span class="muted">&mdash;</span>'
        v = max(0.0, min(1.0, float(value)))
        return f'<span class="score"><i><b style="width:{v * 100:.0f}%"></b></i>{float(value):.2f}</span>'

    head = "".join(f'<th class="num">{e(label)}</th>' for _key, label in _JEV_COLUMNS)
    rows = []
    for d in record["drafts"]:
        shipped = d.get("draft") == record.get("shipped")
        scores = d.get("scores") or {}
        cells = "".join(f'<td class="num">{num(scores.get(key))}</td>' for key, _label in _JEV_COLUMNS)
        gate = d.get("gate") or ""
        rows.append(
            f'<tr class="{"shipped" if shipped else ""}"><td class="nowrap">{e(str(d.get("draft")))}'
            f'{" <b>shipped</b>" if shipped else ""}</td>'
            f'<td>{chip(gate, "pass" if gate == "PASS" else "fail") if gate else ""}</td>'
            f'<td>{e(d.get("headline_template_id") or "")}</td>'
            f'<td>{e(d.get("skeleton_id") or "")}</td>{cells}<td class="num"><b>{num(d.get("composite"))}</b></td></tr>'
        )
    usage = record.get("usage") or {}
    tokens = (f' Jev tokens: {int(usage.get("input_tokens") or 0)} in, {int(usage.get("output_tokens") or 0)} out.'
              if usage else "")
    return (
        '<div class="card" style="margin-top:16px"><div class="card-h"><h3>Drafts (best of '
        + e(str(len(record["drafts"]))) + ")</h3></div>"
        f'<p class="small muted">{e(record.get("reason") or "")}.{e(tokens)} Scores 0-1 from TypeSafe Jev; '
        "the composite is the mean of every column except Overall.</p>"
        '<div class="table-wrap"><table class="stats"><thead><tr><th>Draft</th><th>Gate</th><th>Headline</th>'
        f"<th>Skeleton</th>{head}<th class=\"num\">Composite</th></tr></thead><tbody>" + "".join(rows)
        + "</tbody></table></div></div>"
    )


_JEV_COLUMNS = (("hook", "Hook"), ("specificity", "Specific"), ("proof", "Proof"), ("objections", "Objections"),
                ("offer", "Offer"), ("flow", "Flow"), ("voice", "Voice"), ("message_match", "Ad match"),
                ("overall", "Overall"))


def _details_card(tenant, run_dir, info, name):
    review_md = run_dir / "REVIEW.md"
    cost, _nr = _serve._parse_review_md(review_md.read_text() if review_md.exists() else "")
    jev = info["jev"]
    rows = [
        ("Ad", f'<a href="{url_for("ads_home", q=name)}">{e(name)}</a>'),
        ("Page", e(info["page"])),
        ("Style", e(info["style"] or "-")),
        ("Hero", e(HERO_LABELS.get(info["hero"], info["hero"] or "-"))),
        ("Headline", f'<code>{e(info["template"])}</code>' if info["template"] else "-"),
        ("Skeleton", e(info["skeleton"] or "-")),
        ("Look", e(info["look"] or "-")),
        ("Jev composite", e(f"{float(jev):.2f}") if jev is not None else '<span class="muted">not scored</span>'),
        ("Gate", chip(info["gate"], "pass" if info["gate"] == "PASS" else "fail") if info["gate"] else "-"),
        ("Cost", e(f"${cost:.4f}") if cost is not None else "-"),
        ("Run", f'<code>{e(run_dir.name)}</code>'),
        ("Created", _when(info["created"])),
    ]
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (f'<div class="card"><div class="card-h"><h3>Details</h3>'
            f'<a class="small muted" href="{url_for("run_detail", run_id=run_dir.name)}">scores and approve (reviewer page)'
            f'</a></div><dl class="meta">{dl}</dl></div>')


def render_generation(tenant, run_dir, state, page, *, error=None, status_code=200, text=""):
    run_id = run_dir.name
    status = _publish_status(tenant, run_dir, state, page)
    superseded = _versions(run_dir, page)
    name = _ad_name_for_run(tenant, run_dir, status)
    info = _gen_info(tenant, run_id, page, test=status["test"],
                     taken=postlive.handles_in_use(tenant) | _active_live_handles(tenant))
    target = _target(run_dir, page)
    job = jobs.latest_job(tenant, job_type="regenerate", target=target)
    busy = any(j and j["state"] in ("queued", "running")
               for j in [job] + [jobs.latest_job(tenant, job_type=t, target=target) for t in PUBLISH_JOB_TYPES])
    arm = ""
    if status["test"]:
        v = next((v for v in status["test"]["variants"] if v["key"] == status["key"]), {})
        arm = f'variant {status["key"]}: {v.get("arm", "")}'
    hero = HERO_LABELS.get(info["hero"], info["hero"])
    head = (
        f'<a class="crumb" href="{url_for("ads_home", q=name)}">{icon("back")}{e(name)}</a>'
        f'<div class="page-head"><div><p class="eyebrow">{e(arm or page)}'
        f'{(" &middot; look " + e(info["look"])) if info["look"] else ""} &middot; run {e(run_id)}</p>'
        f'<h1 class="gv-title">{e(info["headline"] or page)}</h1>'
        f'<div class="chips" style="margin-top:12px">{_status_chips(info)}'
        + (chip(f"{hero} first screen") if hero else "") + chip(state["pages"][page])
        + "</div></div></div>"
    )
    active = jobs.active_job(tenant, job_type="regenerate", target=target)
    if active:
        form = ('<div class="card"><h3 style="margin-bottom:8px">Feedback</h3><p class="small muted">A regenerate job for '
                f'this page is {e(active["state"])}. Send more feedback when it ends.</p></div>')
    else:
        form = (
            f'<form class="card" method="post" action="{url_for("gen_feedback", run_id=run_id, page=page)}">'
            f'<h3 style="margin-bottom:4px">Feedback</h3>{_csrf_field()}'
            + (f'<div class="error" style="margin-top:12px">{e(error)}</div>' if error else "")
            + f'<label for="feedback">What should change? <span class="opt">(up to {MAX_FEEDBACK_CHARS} characters)'
            '</span></label>'
            f'<textarea id="feedback" name="feedback" required maxlength="{MAX_FEEDBACK_CHARS}" '
            f'placeholder="Shorter headline. Warmer first item.&#10;cut: An exact sentence to remove.">{e(text)}</textarea>'
            '<p class="hint">Start a line with <code>cut:</code> and paste an exact sentence to remove it. Everything '
            'else goes to the writer as notes. Sending queues a new version of this page; nothing is published.</p>'
            f'<div class="side-actions"><button class="btn block" type="submit">{icon("send")}Send feedback and '
            'regenerate</button></div></form>'
        )
    ready = ""
    if job and job["state"] == "done" and job.get("result"):
        ready = (f'<div class="notice">{icon("check")}<div><b>Version {job["result"]["new_version"]} is ready.</b> '
                 f'Gate: {e(str(job["result"].get("gate", "")))}. Compare it with the old version below.</div></div>')
    regen_box = _job_box(job, what="Regenerate")
    status_card = (
        f'<div class="card"><div class="card-h"><h3>Status</h3>{chip(info["label"], info["status"], dot=True)}</div>'
        + _live_block(tenant, info)
        + _publish_section(tenant, run_dir, page, status, superseded, info)
        + "</div>"
    )
    dialog = _post_live_form(tenant, info, dialog_id="post-live") if info["can_post"] else ""
    body = (
        head
        + '<div class="gv"><div class="gv-main">'
        + (f'<div style="margin-bottom:16px">{regen_box}</div>' if regen_box else "")
        + ready
        + _version_frames(run_dir, page, superseded)
        + _jev_card(state, page)
        + '<div style="margin-top:16px">' + _feedback_list(state, page) + "</div>"
        + '</div><div class="gv-top">' + status_card + "</div>"
        + '<div class="gv-rest stack">' + form + _details_card(tenant, run_dir, info, name)
        + '<div class="card">' + _history_list(run_dir, state, page, superseded) + "</div>"
        + "</div></div>" + dialog
    )
    return _page(f"{name} / {page}", body, status=status_code, refresh=10 if busy else None)


def _confirm_page(title, lines, action_url, button, cancel_url, *, danger=False):
    body = (
        f'<div class="card" style="max-width:640px"><h1 style="font-size:1.3rem;margin-bottom:14px">{e(title)}</h1>'
        '<div class="warn">' + "".join(f"<p>{line}</p>" for line in lines)
        + f'</div><form method="post" action="{action_url}" class="row">{_csrf_field()}'
        '<input type="hidden" name="confirm" value="yes">'
        f'<button class="btn{" danger" if danger else ""}" type="submit">{e(button)}</button>'
        f'<a class="btn ghost" href="{cancel_url}">Cancel</a></form></div>'
    )
    return _page(title, body)


# ---------------------------------------------------------------------------
# upload, jobs
# ---------------------------------------------------------------------------

def _upload_form(tenant, *, error=None, values=None, status=200):
    values = values or {}
    auto = abtest.settings(tenant)["auto_publish"]
    note = ("When the three pages are built, the test is published and the split link shows here and on the "
            "Ads page. Paste it into the ad in Ads Manager." if auto else
            "When the three pages are built, publish the test with <code>harness abtest publish</code>.")

    def field(name, label, *, area=False, hint=""):
        value = e(values.get(name) or "")
        control = (f'<textarea id="{name}" name="{name}" maxlength="{upload_mod.MAX_COPY_CHARS}" '
                   f'style="min-height:96px">{value}</textarea>'
                   if area else f'<input type="text" id="{name}" name="{name}" value="{value}" '
                                f'maxlength="{upload_mod.MAX_COPY_CHARS}" placeholder="{e(hint)}">')
        return f'<label for="{name}">{label} <span class="opt">(optional)</span></label>{control}'

    body = (
        '<div class="page-head"><div><h1>Upload an ad</h1><p class="sub">The harness builds three landing pages from '
        'it (an A/B/C test).</p></div></div>'
        '<div class="two"><form class="card" method="post" enctype="multipart/form-data" '
        f'action="{url_for("upload_ad")}">{_csrf_field()}'
        + (f'<div class="error">{e(error)}</div>' if error else "")
        + '<label for="ad_name" style="margin-top:0">Ad name</label>'
        f'<input type="text" id="ad_name" name="ad_name" required maxlength="{upload_mod.MAX_NAME_CHARS}" '
        f'value="{e(values.get("ad_name") or "")}" placeholder="The name the ad has in Ads Manager">'
        '<label for="media">Video or image</label>'
        f'<div class="drop">{icon("upload")}<div><b>Choose a file</b> or drop it here</div>'
        '<div class="small">mp4, mov, jpg, png, webp &middot; up to 500 MB</div><div class="file"></div>'
        '<input type="file" id="media" name="media" required '
        'accept="video/mp4,video/quicktime,.mp4,.mov,image/jpeg,image/png,image/webp"></div>'
        '<hr class="hr" style="margin-top:24px"><p class="eyebrow" style="margin-bottom:0">Ad copy</p>'
        + field("primary_text", "Primary text", area=True)
        + field("headline", "Headline")
        + field("description", "Description")
        + field("cta", "Call to action", hint="e.g. SHOP_NOW")
        + f'<div class="side-actions" style="margin-top:22px"><button class="btn" type="submit">{icon("upload")}'
        'Upload and build the test</button></div></form>'
        '<div class="card"><h3 style="margin-bottom:10px">What happens next</h3>'
        '<ul class="timeline">'
        '<li><b>Saved to the inbox</b><div class="muted small">The type is read from the file itself.</div></li>'
        '<li><b>Three pages are built</b><div class="muted small">One job in the queue; it takes a few minutes. '
        'You land on the job page.</div></li>'
        f'<li><b>Published</b><div class="muted small">{note}</div></li></ul></div></div>'
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
                         + '<p class="hint">Paste this split link as the ad\'s website URL in Ads Manager.</p>')
        elif result.get("publish_error"):
            parts.append(f'<div class="error">Auto publish failed: {e(result["publish_error"])}</div>')
    elif payload.get("run_id") and payload.get("page"):
        parts.append(f'<p><a href="{url_for("generation", run_id=payload["run_id"], page=payload["page"])}">'
                     f'{e(payload["run_id"])} / {e(payload["page"])}</a></p>')
        if payload.get("handle") and job["type"] == "post_live":
            parts.append(f'<p class="small muted">Requested handle: <code>{e(payload["handle"])}</code></p>')
        if result.get("url"):
            if job["type"] in LIVE_JOB_TYPES and result.get("live"):
                parts.append(site_ui.copy_field("job-url", result["url"], label="Live URL", live=True))
            else:
                parts.append(f'<p>Page: <a href="{e(result["url"])}" rel="noopener" target="_blank">{e(result["url"])}</a>'
                             + (" (hidden)" if job["type"] == "unpublish_live" else "") + "</p>")
        if result.get("new_version"):
            parts.append(f"<p>New version: {int(result['new_version'])}</p>")
    return "".join(parts)


def _target_link(target):
    m = re.match(r"^run:([^/]+)/(.+)$", target or "")
    if m:
        return f'<a href="{url_for("generation", run_id=m.group(1), page=m.group(2))}">{e(target)}</a>'
    return e(target or "")


def _thumb_placeholder():
    return Response(_serve._THUMB_PLACEHOLDER_SVG.replace("could not load", "no image"), mimetype="image/svg+xml")


def _cached_thumb(tenant, source, name, size):
    cache = Path(tenant.runs_dir) / "thumb-cache"
    thumb = cache / textutil.safe_filename(name)
    try:
        if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
            cache.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as im:
                im = im.convert("RGB")
                im.thumbnail((size, size), Image.LANCZOS)
                im.save(thumb, format="JPEG", quality=80)
        resp = send_file(thumb, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp
    except Exception:  # noqa: BLE001 -- a broken asset shows the placeholder
        return _thumb_placeholder()


def _run_thumb(tenant, run_dir, page):
    assets = run_dir / textutil.safe_filename(page) / "assets"
    if not assets.is_dir():
        return _thumb_placeholder()
    images = sorted(p for p in assets.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES)
    if not images:
        return _thumb_placeholder()
    return _cached_thumb(tenant, images[0], f"{run_dir.name}-{page}.jpg", 480)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def register(app, tenant):
    """Adds the listicle site's routes to serve.build_app's app. serve.py's
    _require_auth (login + Origin check) is registered first, so it runs
    before the CSRF check here."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.config["SITE_BRAND"] = f"{tenant.display_name} listicle"
    app.config["SITE_TENANT"] = tenant
    app.config["SITE_NAME"] = tenant.display_name
    app.config["SITE_BRAND_DIR"] = str(tenant.brand_dir)
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

    @app.errorhandler(404)
    def _not_found(_err):
        return _page("Not found", _message("Not found", "There is no such page, run or job. It may have been "
                                           "archived.", back=url_for("ads_home")), 404)

    @app.errorhandler(500)
    def _server_error(_err):
        return _page("Something broke", _message("Something broke", "The server hit an error on this page. The "
                                                 "details are in the service log.", back=url_for("ads_home")), 500)

    # Cycle 80: the site's own icon and web manifest (harness/site_ui.py).
    @app.route("/favicon.ico")
    def site_favicon():
        return _static_file("favicon.ico")

    @app.route("/site-static/<name>")
    def site_static(name):
        if name not in site_ui.STATIC_FILES:
            abort(404)
        return _static_file(name)

    @app.route("/site.webmanifest")
    def site_manifest():
        body = json.dumps(site_ui.manifest(f"{tenant.display_name} {app.config.get('SITE_TAG') or 'Listicles'}"))
        return Response(body, mimetype="application/manifest+json")

    def _static_file(name):
        resp = send_file(site_ui.STATIC_DIR / name, mimetype=site_ui.STATIC_FILES[name])
        resp.headers["Cache-Control"] = "max-age=86400"
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
        gen_url = url_for("generation", run_id=run_dir.name, page=page)
        if replace:
            if rec is None or rec.get("status") != "live" or not status["key"]:
                return _page("Not a live variant", _message("Not a live variant", "This page is not a variant of a "
                                                            "live A/B/C test.", back=gen_url), 409)
        elif rec is not None:
            return _page("Part of a test", _message("Part of a test", "This page is an A/B/C test variant. Use "
                                                    "<b>Replace live variant</b> on its page instead.", back=gen_url), 409)
        elif status["record"] is None:
            return _page("Not published", _message("Not published", "This page is not on Shopify yet.", back=gen_url),
                         409)
        elif status["live"] is None:
            return _page("Unknown publish state", _message(
                "Unknown publish state", "The run's history does not say whether the Shopify page is live or a "
                "draft. Publish it from the command line.", back=gen_url), 409)
        if not status["newer"]:
            return _page("Nothing new", _message("Nothing new", "The Shopify page already shows the current "
                                                 "version.", back=gen_url), 409)
        job_type = "replace_variant" if replace else "publish_page"
        if jobs.active_job(tenant, job_type=job_type, target=target):
            return _page("Already queued", _message("Already queued", "That job is already waiting or running.",
                                                    back=gen_url), 409)
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
            return _page("Confirm first", _message("Confirm first", f'Open <a href="{gen_url}">the page</a> and use the '
                                                   "button there; it asks you to confirm."), 400)
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

    # Cycle 80: post live (unlinked), update live page, unpublish -- harness/postlive.py.
    def _live_action(run_id, page, job_type):
        run_dir, state = _run_page_or_404(tenant, run_id, page)
        gen_url = url_for("generation", run_id=run_dir.name, page=page)
        email = g.reviewer_email
        if runstate.find_reviewer(tenant, email) is None:
            return _page("Not a reviewer", _message("Not a reviewer", f"{e(email)} is not in this tenant's reviewers "
                                                    "list.", back=gen_url), 403)
        status = _publish_status(tenant, run_dir, state, page)
        target = _target(run_dir, page)
        taken = postlive.handles_in_use(tenant) | _active_live_handles(tenant)
        info = _gen_info(tenant, run_dir.name, page, test=status["test"], taken=taken)
        if any(jobs.active_job(tenant, job_type=t, target=target) for t in PUBLISH_JOB_TYPES):
            return _page("Already queued", _message("Already queued", "A publish job for this page is already "
                                                    "waiting or running.", back=gen_url), 409)
        if job_type == "post_live" and not info["can_post"]:
            why = ("it is part of an A/B/C test" if info["in_test"] else "it is already live" if info["live"]
                   else "a redirect points at it" if status["redirected"] else "it is rejected or not rendered")
            return _page("Cannot post live", _message("Cannot post live", f"This page cannot be posted live: {why}.",
                                                      back=gen_url), 409)
        if job_type != "post_live" and not (status["unlinked"] and info["live"] is True):
            return _page("Not an unlinked live page", _message(
                "Not an unlinked live page", "Only a live page that no ad, A/B/C test or redirect points at can be "
                "updated or hidden from here.", back=gen_url), 409)
        record = status["record"] or {}
        if request.method == "GET":
            if job_type == "post_live":
                return _page("Post live", _post_live_form(tenant, info))
            if job_type == "update_live":
                lines = [f"Publish version {_versions(run_dir, page) + 1} over the live page "
                         f"<b>{e(record.get('url') or '')}</b>. It stays live at the same address.",
                         f"This approves the current version as you ({e(email)}). Visitors see it as soon as the job "
                         "ends. No redirect and no test are created."]
                return _confirm_page("Update live page", lines, url_for("gen_update_live", run_id=run_id, page=page),
                                     "Yes, update the live page", gen_url)
            lines = [f"Set <b>{e(record.get('url') or '')}</b> back to hidden. Visitors get a 404; the page stays on "
                     "Shopify as a hidden draft.",
                     "You can post it live again later at the same address."]
            return _confirm_page("Unpublish", lines, url_for("gen_unpublish", run_id=run_id, page=page),
                                 "Yes, unpublish", gen_url, danger=True)
        if request.form.get("confirm") != "yes":
            return _page("Confirm first", _message("Confirm first", f'Open <a href="{gen_url}">the page</a> and use the '
                                                   "button there; it asks you to confirm."), 400)
        payload = {"run_id": run_dir.name, "page": page, "by": email}
        if job_type == "post_live":
            handle = record.get("handle") if record else (request.form.get("handle") or "").strip().lower()
            problem = None if record else postlive.handle_problem(handle, tenant)
            if not problem and not record and handle in taken:
                problem = f"The handle {handle} is already used by another generation. Pick another."
            if problem:
                return _page("Post live", _post_live_form(tenant, info, error=problem, value=handle), 400)
            payload["handle"] = handle
        job_id = jobs.enqueue(tenant, job_type, payload, by=email, target=target)
        jobs.audit(tenant, by=email, action=job_type, target=target,
                   detail={"handle": payload.get("handle") or record.get("handle"), "url": record.get("url"),
                           "job_id": job_id})
        return redirect(gen_url, code=303)

    @app.route("/gen/<run_id>/<page>/post-live", methods=["GET", "POST"])
    def gen_post_live(run_id, page):
        return _live_action(run_id, page, "post_live")

    @app.route("/gen/<run_id>/<page>/update-live", methods=["GET", "POST"])
    def gen_update_live(run_id, page):
        return _live_action(run_id, page, "update_live")

    @app.route("/gen/<run_id>/<page>/unpublish", methods=["GET", "POST"])
    def gen_unpublish(run_id, page):
        return _live_action(run_id, page, "unpublish_live")

    @app.route("/run/<run_id>/review/<page>/v/<int:version>")
    def page_review_version(run_id, page, version):
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        safe_page = textutil.safe_filename(page)
        path = (_serve._safe_path(run_dir, f"{safe_page}-review.v{version}.html")
                or _serve._safe_path(run_dir, f"{safe_page}/index.v{version}.html"))
        if path is None:
            return Response(_serve._page_shell("No such version", "<p>No such version.</p>", chrome=False),
                            mimetype="text/html")
        return send_file(path, mimetype="text/html")

    @app.route("/run/<run_id>/thumb/<page>")
    def run_thumb(run_id, page):
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        return _run_thumb(tenant, run_dir, page)

    @app.route("/run/<run_id>/ad-frame")
    def ad_frame(run_id):
        """Cycle 80: the ad still harness/ad_frames.py saved for a video ad,
        as a small thumbnail for the ad card."""
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        frame = _serve._safe_path(run_dir, "ad-frame/frame.jpg")
        if frame is None:
            abort(404)
        return _cached_thumb(tenant, frame, f"{run_dir.name}-ad-frame.jpg", 240)

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
        listed = jobs.list_jobs(tenant, limit=100)
        rows = "".join(
            f'<tr><td data-label="Job"><a href="{url_for("job_detail", job_id=j["id"])}">#{j["id"]}</a></td>'
            f'<td data-label="Type">{e(JOB_LABELS.get(j["type"], j["type"]))}'
            f'<div class="muted small">{e(j["type"])}</div></td>'
            f'<td data-label="State">{chip(j["state"], j["state"])}</td>'
            f'<td data-label="For">{_target_link(j["target"])}</td><td data-label="By">{e(j["by"])}</td>'
            f'<td data-label="Created" class="nowrap">{_when(j["created_at"])}</td>'
            f'<td data-label="Reason">{e((j["reason"] or "")[:160])}</td></tr>'
            for j in listed
        )
        busy = any(j["state"] in ("queued", "running") for j in listed)
        body = ('<div class="page-head"><div><h1>Jobs</h1><p class="sub">Run by <code>harness worker</code>, one at a '
                'time, oldest first.</p></div></div>'
                '<div class="table-wrap"><table class="rtable"><thead><tr><th>Job</th><th>Type</th><th>State</th>'
                '<th>For</th><th>By</th><th>Created</th><th>Reason</th></tr></thead><tbody>'
                + (rows or '<tr><td colspan="7" class="muted">No jobs yet.</td></tr>') + "</tbody></table></div>")
        return _page("Jobs", body, refresh=10 if busy else None)

    @app.route("/job/<int:job_id>")
    def job_detail(job_id):
        job = jobs.get_job(tenant, job_id)
        if job is None:
            abort(404)
        busy = job["state"] in ("queued", "running")
        label = JOB_LABELS.get(job["type"], job["type"])
        steps = [("Requested", f'by {e(job["by"])}', job["created_at"]),
                 ("Started", "", job.get("started_at")), ("Finished", "", job.get("finished_at"))]
        timeline = "".join(
            f'<li class="{"hot" if at and name == "Finished" and job["state"] == "done" else ""}"><b>{name}</b> '
            f'<span class="muted">{who}</span><div class="t">{_when(at) if at else "&mdash;"}</div></li>'
            for name, who, at in steps)
        reason = f'<div class="error">Failed: {e(job["reason"])}</div>' if job["state"] == "failed" else ""
        wait = ('<p class="small muted">This page reloads every 10 seconds until the job ends.</p>' if busy else "")
        body = (
            f'<a class="crumb" href="{url_for("job_list")}">{icon("back")}Jobs</a>'
            f'<div class="page-head"><div><p class="eyebrow">Job #{job["id"]} &middot; {e(job["type"])}</p>'
            f'<h1>Job #{job["id"]}: {e(label)}</h1><div class="chips" style="margin-top:12px">'
            f'{chip(job["state"], job["state"])}<span class="muted small">for {_target_link(job["target"])}</span>'
            '</div></div></div>'
            '<div class="two"><div class="card">'
            '<h3 style="margin-bottom:12px">Result</h3>' + reason
            + (_job_result_html(tenant, job) or '<p class="muted">No result yet.</p>') + wait + "</div>"
            '<div class="card"><h3 style="margin-bottom:12px">Timeline</h3>'
            f'<ul class="timeline">{timeline}</ul></div></div>'
        )
        return _page(f"Job {job['id']}", body, refresh=10 if busy else None)

    @app.route("/audit")
    def audit_log():
        rows = "".join(
            f'<tr><td data-label="When" class="nowrap">{_when(r["at"])}</td><td data-label="Who">{e(r["by"])}</td>'
            f'<td data-label="What">{chip(r["action"], "live" if r["action"].startswith(("post_live", "publish")) else "")}'
            f'</td><td data-label="On">{_target_link(r["target"])}</td>'
            f'<td data-label="Detail"><pre class="detail">{e(json.dumps(r["detail"], ensure_ascii=False, indent=1))}'
            "</pre></td></tr>"
            for r in jobs.audit_rows(tenant, limit=300)
        )
        body = ('<div class="page-head"><div><h1>Audit log</h1><p class="sub">Who gave feedback, uploaded, published '
                'or posted live, and when.</p></div></div><div class="table-wrap"><table class="rtable"><thead><tr>'
                '<th>When</th><th>Who</th><th>What</th><th>On</th><th>Detail</th></tr></thead><tbody>'
                + (rows or '<tr><td colspan="5" class="muted">Nothing yet.</td></tr>') + "</tbody></table></div>")
        return _page("Audit log", body)
