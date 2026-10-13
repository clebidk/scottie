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
                             (cycle 86: optionally with a pinned page version)
  /library                   cycle 86: a reference render of every page
                             version (harness/library.py), "Generate with this"
  /looks/import              cycle 87: "Import a page" -> look_import job
                             (harness/look_import.py); the form is also a
                             dialog on the Ads page
  /looks/<c>/<slug>/review   an imported look's reference render
  /looks/<c>/<slug>/add|discard  cycle 102: the import job's "Add to Library"
                             (a Library build job) or "Discard"
  /looks/<c>/<slug>/source.jpg|render.png  its source thumbnail and its
                             render at 390
  /photos                    cycle 96: the photo library -- coverage, add
                             photos (photos_import job), curate each photo
                             (harness/site_photos.py)
  /jobs, /job/<id>           the job queue (harness/jobs.py)
  /audit                     who did what, when
  /test/<id>/go-live|revert-go-live  cycle 107: put a live test's split link
                             on its Meta ad, or set the old creative back
                             (meta_go_live / meta_revert_go_live jobs,
                             harness/meta_golive.py)
  /results                   cycle 107: per test and arm results with Meta
                             spend, and the pooled leaderboard by build / look

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

from . import abevents, abtest, abtest_inbox, ads as ads_mod, budget, jobs, library, meta_ingest, postlive, runstate
from . import site_ui, textutil
from . import design_gate as design_gate_mod
from . import heroes
from . import look_import as look_import_mod
from . import look_vision as look_vision_mod
from . import looks as looks_mod
from . import meta_golive
from . import serve as _serve
from . import upload as upload_mod

ENDPOINTS = (
    "ads_home", "generation", "gen_feedback", "gen_publish", "gen_replace", "page_review_version",
    "run_thumb", "ad_media", "upload_ad", "job_list", "job_detail", "audit_log",
    "gen_post_live", "gen_update_live", "gen_unpublish", "ad_frame", "site_favicon", "site_static", "site_manifest",
    "library_home", "library_review", "library_thumb", "library_rebuild",
    "look_import", "look_review",
    # cycle 102: the import's result card and the Library chip
    "look_source", "look_render", "look_add", "look_discard",
    # cycle 92: the design gate's shots
    "design_shot",
    # cycle 96: the Photos page (harness/site_photos.py)
    "photos_home", "photo_thumb", "photos_upload", "photo_edit", "photos_pull_drive",
    # cycle 107: go live on Meta, revert, results
    "test_go_live", "test_revert_go_live", "results_home",
)
CSRF_ENDPOINTS = frozenset({"gen_feedback", "gen_publish", "gen_replace", "upload_ad",
                            "gen_post_live", "gen_update_live", "gen_unpublish", "library_rebuild", "look_import",
                            "photos_upload", "photo_edit", "photos_pull_drive", "look_add", "look_discard",
                            "test_go_live", "test_revert_go_live"})

ADS_PER_PAGE = 20
MAX_FEEDBACK_CHARS = 2000
FEEDBACK_PER_MIN = 10
UPLOADS_PER_WINDOW = 6
UPLOAD_WINDOW_S = 600
# The upload form's whole body: the 500 MB file plus the text fields.
MAX_REQUEST_BYTES = 520 * 1024 * 1024
# Cycle 87: "Import a page" -- rate limited like uploads; the page itself is
# at most look_import.MAX_SOURCE_BYTES (a file and a paste are both refused
# above it), plus room for the form's other fields.
LOOK_IMPORTS_PER_WINDOW = UPLOADS_PER_WINDOW
# Cycle 102: or up to ten screenshots of 15 MB (harness/look_vision.py).
LOOK_IMPORT_MAX_REQUEST_BYTES = (look_vision_mod.MAX_FILES * look_vision_mod.MAX_FILE_BYTES
                                 + look_import_mod.MAX_SOURCE_BYTES + 256 * 1024)

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
LIVE_JOB_TYPES = ("post_live", "update_live", "unpublish_live")
PUBLISH_JOB_TYPES = ("publish_page", "replace_variant") + LIVE_JOB_TYPES
FILTERS = (("all", "All"), ("review", "Needs review"), ("live", "Live"))
JOB_LABELS = {"regenerate": "Regenerate", "create_test": "Build A/B/C test", "publish_page": "Publish new version",
              "replace_variant": "Replace live variant", "post_live": "Post live", "update_live": "Update live page",
              "unpublish_live": "Unpublish", "library_build": "Rebuild library", "look_import": "Import a page",
              "photos_import": "Add photos", "meta_go_live": "Go live on Meta", "meta_revert_go_live": "Revert Meta ad"}
GO_LIVE_JOB_TYPES = ("meta_go_live", "meta_revert_go_live")
# Cycle 107: what the go-live confirm says before anything changes on Meta.
GO_LIVE_CONSEQUENCES = (
    "Meta does not allow editing a creative: this makes a copy of the ad's creative with the split link and points "
    "the ad at the copy. The UTM tags (url_tags) stay the same.",
    "<b>Learning phase:</b> Meta restarts the ad's learning phase, so delivery and cost per result can swing for a "
    "few days.",
    "<b>Social proof:</b> the new post starts with no likes, comments or shares. The old post keeps them; "
    "<b>Revert</b> puts the old creative back.",
)
REVERT_CONSEQUENCES = (
    "<b>Learning phase:</b> Meta restarts the ad's learning phase again.",
    "<b>Social proof:</b> the old post comes back with the likes and comments it had. Reactions on the split-link "
    "post stay on that post.",
)
RESULTS_DRAWS = 5000
# Cycle 94: every hero composition by its title (harness/heroes/).
HERO_LABELS = {hero_id: heroes.get(hero_id).title for hero_id in heroes.ids()}
# Cycle 86: the upload form's pre-selected page version (the Library's
# "Generate with this"), as query args on GET and hidden fields on POST.
PIN_FIELDS = ("cartridge", "look", "hero_style", "style")


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


def _auto_line(tenant):
    """Cycle 108: the hourly pipeline's last run (runs/auto-pipeline.json)."""
    text = abtest_inbox.status_line(tenant)
    return f'<span class="auto-run">{e(text)}</span>' if text else ""


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


def _inbox_items(tenant):
    """The inbox's items, read once per request (every card asks)."""
    if getattr(g, "_inbox_items", None) is None:
        g._inbox_items = meta_ingest.Inbox(tenant.meta_inbox_dir).items()
    return g._inbox_items


def _ad_link(ads):
    """Cycle 83: the link icon next to the status pill when a Meta ad's
    current destination is this page. Muted when no ACTIVE ad does. ""
    when none does."""
    if not ads:
        return ""
    active = any(a["effective_status"] == postlive.LIVE_AD_STATUS for a in ads)
    text = "\n".join(f"Destination of Meta ad {a['ad_name'] or a['ad_id']} ({a['effective_status'] or 'unknown'})"
                     for a in ads)
    return (f'<span class="adlink{"" if active else " off"}" role="img" title="{e(text)}" aria-label="{e(text)}">'
            f'{icon("link")}</span>')


def _first_at(state):
    for entry in state.get("history") or []:
        if entry.get("at"):
            return entry["at"]
    return ""


def _jev_composite(shipped):
    """The shipped draft's Jev composite (harness/drafts.py records it on the
    draft's row whenever Jev scored it, also on a batch run or when draft 2
    was skipped). A row with scores but no composite (none recorded so far)
    gets the mean of its scores except Overall, as the drafts table says.
    None when Jev never scored the shipped draft."""
    if not shipped:
        return None
    if shipped.get("composite") is not None:
        return float(shipped["composite"])
    scores = [float(v) for k, v in (shipped.get("scores") or {}).items() if k != "overall" and v is not None]
    return round(sum(scores) / len(scores), 4) if scores else None


def _jev_note(jev):
    if not jev:
        return "no best-of-N record for this run"
    reasons = {"repaired": "only one draft passed, after repairs, so Jev was not asked",
               "single_pass": "only one draft passed, so Jev was not asked",
               "jev_unavailable": "Jev was unavailable", "none_passed": "no draft passed",
               "budget": "the budget cap stopped the drafts"}
    return reasons.get(jev.get("status"), jev.get("reason") or "not scored")


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
    # The headline readers see: a display first screen sets display_headline;
    # face and story set the page headline (which is also the page <title>).
    hero = choice.get("hero_style") or page_json.get("hero_style") or ""
    headline = page_json.get("headline") or ""
    display = page_json.get("display_headline") or ""
    title = display if hero == "display" and display else headline
    page_url = record.get("url") or (variant or {}).get("url") or ""
    linked = postlive.linked_ads(tenant, page_url, test=test or st["test"], items=_inbox_items(tenant))
    can_post = (rendered and not in_test and not st["redirected"] and page_state != "rejected"
                and (not record or (st["live"] is False and record.get("page_id"))))
    info = {
        "run_id": run_id, "page": page, "run_dir": run_dir, "state": state, "page_state": page_state,
        "status": status, "label": label, "record": record, "live": st["live"], "unlinked": st["unlinked"] and not in_test,
        "in_test": in_test, "variant": variant, "test": test, "ads": linked,
        "headline": headline, "display_headline": display, "title": title,
        "seo_title": headline if headline and headline != title else "",
        "hero": hero,
        "style": choice.get("style") or page_json.get("style") or "",
        "look": choice.get("look") or page_json.get("look") or "",
        "template": choice.get("headline_template_id") or page_json.get("headline_template_id") or "",
        "skeleton": choice.get("skeleton_id") or "",
        "jev": _jev_composite(shipped), "jev_note": _jev_note(jev), "jev_record": jev, "gate": gate,
        "design": design_gate_mod.load_scores(run_dir / page),
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
    title = info["title"] or info["page"]
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
    if jev is not None:
        bits.append(f'<span title="TypeSafe Jev composite of the shipped draft">Jev <b>{float(jev):.2f}</b></span>')
    else:
        bits.append(f'<span title="Not scored: {e(info["jev_note"])}">Jev <b>&mdash;</b></span>')
    if info["look"] and info["look"] != "open":
        bits.append(f'<span>Look <b>{e(info["look"])}</b></span>')
    design = info.get("design")
    if design:
        bits.append(_design_fact(design))
    return '<div class="facts">' + "".join(bits) + "</div>"


DESIGN_STATUS_LABELS = {"pass": "passed", "warn": "below min score (warn)", "stop": "below min score (STOP)",
                        "unscored": "not scored", "below": "below min score"}


def _design_fact(design):
    """Cycle 92: the design gate's score on a card (Jev's copy score is a
    separate fact)."""
    score = design.get("score")
    status = DESIGN_STATUS_LABELS.get(design.get("status"), design.get("status") or "")
    title = f"Design gate: {status}; min {design.get('min_score', '-')}; judge {design.get('model', '')}"
    value = f"{score}" if score is not None else "&mdash;"
    return f'<span title="{e(title)}">Design <b>{value}</b></span>'


def _status_chips(info, *, with_gate=True):
    parts = [chip(info["label"], info["status"], dot=info["status"] in ("live", "approved")), _ad_link(info["ads"])]
    if with_gate and info["gate"]:
        parts.append(chip(info["gate"], "pass" if info["gate"] == "PASS" else "fail"))
    return "".join(parts)


def _pct(value, fmt):
    return "-" if value is None else format(value, fmt)


def _external_card(ad, v):
    """Cycle 88: an external arm (PDP / home / URL) of the ad's test: where
    the split page sends visitors, how many it sent (split-page assign
    events), and where the rest of the numbers live."""
    rec = ad["tests"][0] if ad["tests"] else {}
    label = abtest.TARGET_LABELS.get(v.get("target"), "URL")
    parts = [
        '<div class="gen gen-ext"><div class="shot"></div><div class="gen-body"><div class="chips">'
        + chip("Variant " + v["key"], "solid") + chip(label) + "</div>",
        f'<div class="gen-title">{e(v["key"])} = {e(label)}</div>',
        (f'<p class="small"><a href="{e(v["url"])}" rel="noopener" target="_blank">{e(v["url"])}</a></p>'
         if v.get("url") else '<p class="muted small">Destination not resolved yet.</p>'),
    ]
    if "assigns" in v:
        parts.append('<div class="abstats">'
                     f'<div><span>Assigned</span><b>{v["assigns"]}</b></div><div><span>CTA</span><b>-</b></div>'
                     '<div><span>CTR</span><b>-</b></div><div><span>P(best)</span><b>-</b></div></div>')
    utm = abtest.utm_content(rec.get("test_id") or "", v["key"])
    parts.append(f'<p class="muted small">No beacon on this page: {e(abtest.NO_BEACON_NOTE)}='
                 f'<code>{e(utm)}</code>.</p></div></div>')
    return "".join(parts)


def _gen_card(tenant, ad, v, info, *, dom_id):
    if v.get("kind") == "external":
        return _external_card(ad, v)
    if info is None:
        return (f'<div class="gen"><div class="shot"></div><div class="gen-body"><div class="chips">'
                f'{chip(v.get("key") or "", "solid") if v.get("key") else ""}{chip(v.get("status") or "pending")}</div>'
                f'<div class="gen-title">{e(v.get("arm") or "")}</div><p class="muted small">Not built yet.</p></div></div>')
    hero = HERO_LABELS.get(info["hero"], info["hero"].capitalize() if info["hero"] else "")
    corner = "".join(x for x in (chip(f"Variant {v['key']}", "solid") if v.get("key") else "",
                                 chip(hero) if hero else "") if x)
    title = info["title"] or v.get("arm") or info["page"]
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
            f'<div><span>CTR</span><b>{v["ctr"]:.1%}</b></div><div><span>P(best)</span><b>{_pct(v["p_best"], ".0%")}</b>'
            "</div></div>"
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


def _go_live_info(tenant, rec):
    """Cycle 107: what the go-live box, confirm and routes need for a test,
    or None when the test was not built from a Meta ad. Ad name, status and
    current link are the inbox's (as fresh as the last pull / refresh-links);
    the job reads the ad again before it changes anything."""
    ad_id = str((rec.get("source") or {}).get("ad_id") or "")
    if not (ad_id.isdigit() and ad_id.isascii()):
        return None
    item = next((i for i in _inbox_items(tenant) if str(i.get("ad_id")) == ad_id), None) or {}
    block = rec.get("meta_go_live") or {}
    target = meta_golive.target(rec["test_id"])
    job = _latest(tenant, target, GO_LIVE_JOB_TYPES)
    return {"ad_id": ad_id, "ad_name": item.get("ad_name") or (rec.get("source") or {}).get("ad_name") or ad_id,
            "status": item.get("effective_status") or "", "current": item.get("destination_url") or "",
            "checked": item.get("links_checked_at") or item.get("pulled_at") or "",
            "split": (rec.get("split") or {}).get("url") or "", "block": block,
            "live": bool(block) and not block.get("reverted_at"), "job": job,
            "busy": bool(job and job["state"] in ("queued", "running"))}


def _go_live_lines(gl, *, revert):
    ad = (f"Ad: <b>{e(gl['ad_name'])}</b> (id {e(gl['ad_id'])}, {e(gl['status'] or 'status unknown')}"
          + (f" when last read {_when(gl['checked'])}" if gl["checked"] else "") + ")")
    if revert:
        block = gl["block"]
        return [ad,
                f"Current link: <code>{e(block.get('verified_destination') or gl['split'])}</code> (creative "
                f"{e(block.get('new_creative_id') or '-')})",
                f"Link after revert: <code>{e(block.get('old_destination') or '-')}</code> (creative "
                f"{e(block.get('old_creative_id') or '-')})", *REVERT_CONSEQUENCES]
    return [ad, f"Current link: <code>{e(gl['current'] or 'unknown')}</code>",
            f"Split link (new link): <code>{e(gl['split'])}</code>", *GO_LIVE_CONSEQUENCES]


def _confirm_dialog(dialog_id, title, lines, action_url, button, *, danger=False):
    """The _confirm_page content in a <dialog> (the cards' data-dialog)."""
    return (f'<dialog id="{dialog_id}" aria-labelledby="{dialog_id}-t"><form method="post" action="{action_url}">'
            f'{_csrf_field()}<input type="hidden" name="confirm" value="yes">'
            f'<div class="dlg-h"><div><h2 id="{dialog_id}-t">{e(title)}</h2></div>'
            f'<button type="button" class="iconbtn" data-close aria-label="Close">{icon("x")}</button></div>'
            '<div class="dlg-b"><div class="warn">' + "".join(f"<p>{line}</p>" for line in lines) + "</div></div>"
            '<div class="dlg-f"><button type="button" class="btn ghost" data-close>Cancel</button>'
            f'<button class="btn{" danger" if danger else ""}" type="submit">{e(button)}</button></div></form></dialog>')


def _meta_box(tenant, rec, index):
    """Cycle 107: a live test's Meta state on its card: "Go live on Meta" (or
    a note when the ad is not ACTIVE), "Live on Meta" with "Revert", or the
    running job."""
    if rec.get("status") != "live" or not (rec.get("split") or {}).get("url"):
        return ""
    gl = _go_live_info(tenant, rec)
    if gl is None:
        return ""
    test_id = rec["test_id"]
    parts = ['<div class="golive" style="margin:10px 0 0">']
    job = gl["job"]
    if gl["busy"]:
        parts.append(_job_box(job, what=JOB_LABELS[job["type"]]))
    elif gl["live"]:
        block = gl["block"]
        parts.append(f'<p class="small">{chip("Live on Meta", "live", dot=True)} since {_when(block.get("at"))} by '
                     f'{e(block.get("by") or "")} &middot; link '
                     f'<code>{e(block.get("verified_destination") or "-")}</code>'
                     + ("" if block.get("verified") else " <b>(not verified: check Ads Manager)</b>") + "</p>")
        dialog_id = f"golive-{index}-revert"
        parts.append(f'<a class="btn ghost sm" href="{url_for("test_revert_go_live", test_id=test_id)}" '
                     f'data-dialog="{dialog_id}">{icon("back")}Revert</a>')
        parts.append(_confirm_dialog(dialog_id, "Revert the Meta ad", _go_live_lines(gl, revert=True),
                                     url_for("test_revert_go_live", test_id=test_id), "Yes, revert", danger=True))
    else:
        if job and job["state"] == "failed":
            parts.append(_job_box(job, what=JOB_LABELS[job["type"]]))
        if gl["status"] and gl["status"] != meta_golive.LIVE_STATUS:
            parts.append(f'<p class="muted small">Meta ad {e(gl["ad_name"])} is {e(gl["status"])}: Go live on Meta '
                         "needs an ACTIVE (approved) ad. The command line takes --force.</p>")
        else:
            dialog_id = f"golive-{index}"
            parts.append(f'<a class="btn sm" href="{url_for("test_go_live", test_id=test_id)}" '
                         f'data-dialog="{dialog_id}">{icon("live")}Go live on Meta</a>')
            parts.append(_confirm_dialog(dialog_id, "Go live on Meta", _go_live_lines(gl, revert=False),
                                         url_for("test_go_live", test_id=test_id), "Yes, go live on Meta"))
    parts.append("</div>")
    return "".join(parts)


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
    if rec is None and item is not None and abtest_inbox.waiting_for_auto(item):
        why = abtest_inbox.not_auto_built(tenant, item)
        if why:
            parts.append(f'<p class="muted small" style="margin:12px 0 0">not auto-built: {e(why[1])}</p>')
    if rec is not None:
        parts.append(f'<p class="muted small" style="margin:12px 0 0">A/B/C test {e(rec["test_id"])}'
                     + (f": {e(rec['reason'])}" if rec.get("reason") else "") + "</p>")
        if any(abtest.is_external(v) for v in rec.get("variants") or []):
            parts.append(f'<p class="small" style="margin:6px 0 0">Destinations: <b>{e(abtest.destinations_label(rec))}'
                         '</b> <span class="muted">(fixed when the test was created)</span></p>')
        if rec.get("plan_rule"):
            parts.append(f'<p class="muted small" style="margin:4px 0 0">Plan {e(rec.get("plan") or "")}: '
                         f'{e(rec["plan_rule"])}</p>')
        if rec.get("manual_link"):
            parts.append(f'<p class="small" style="margin:6px 0 0"><b>Manual link:</b> {e(rec["manual_link"])}</p>')
        split = (rec.get("split") or {}).get("url")
        if split:
            parts.append(f'<div class="ad-split">{_copy_row(f"split-{index}", split)}</div>')
        parts.append(_meta_box(tenant, rec, index))
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
    statline = '<div class="statline">' + _spend_line(tenant) + _auto_line(tenant)
    if busy:
        statline += (f'<a class="busy" href="{url_for("job_list")}">{chip("running", "running")}'
                     f'{len(busy)} job(s) queued or running</a>')
    statline += "</div>"
    empty = ("No ads match." if q or f != "all" else "No ads yet. Upload one, or wait for the next Meta pull.")
    body = (
        '<div class="page-head"><div><h1>Ads</h1>'
        f'<p class="sub"><span class="num">{counts["all"]}</span> ads &middot; <span class="num">{len(all_gens)}</span> '
        f'generations &middot; <span class="num">{live_gens}</span> live</p></div>'
        f'<div class="actions"><a class="btn ghost" href="{url_for("look_import")}" data-dialog="look-import">'
        f'{icon("grid")}Import a page</a>'
        f'<a class="btn" href="{url_for("upload_ad")}">{icon("upload")}Upload an ad</a></div></div>'
        + statline
        + f'<div class="toolbar"><nav class="seg" aria-label="Filter">{seg}</nav>'
        + (f'<span class="small muted">Results for &ldquo;{e(q)}&rdquo; &middot; <a href="{link(q=None, page=None)}">'
           "clear</a></span>" if q else "")
        + "</div>"
        + ('<div class="ads">' + "".join(_ad_card(tenant, ad, i) for i, ad in enumerate(shown)) + "</div>"
           if shown else "")
        + ("" if shown else f'<div class="empty">{e(empty)}</div>')
        + pager
        + _look_import_form(dialog_id="look-import")
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


def _design_card(run_id, page, design):
    """Cycle 92: the design gate on the generation page -- result, score and
    subscores, the judge's top issues, the hard checks, the fonts, the cost,
    and the "Design variants" strip (every variant's phone shot and score)."""
    if not design:
        return ""
    status = DESIGN_STATUS_LABELS.get(design.get("status"), design.get("status") or "")
    kind = "pass" if design.get("status") == "pass" else "fail" if design.get("status") in ("stop", "warn", "below") else ""
    subs = design.get("subscores") or {}
    sub_html = "".join(f'<span>{e(k.replace("_", " "))} <b>{e(str(subs.get(k)) if subs.get(k) is not None else "-")}</b></span>'
                       for k in design_gate_mod.SUBSCORES)
    issues = "".join(f'<li>{e(i.get("what", ""))} <span class="muted">({e(i.get("where", ""))})</span><br>'
                     f'<span class="small">Fix: {e(i.get("fix_as_layout_choice", ""))}</span></li>'
                     for i in design.get("top_issues") or [])
    checks = "".join(f'<li>{e(name)}: {chip(c.get("status", ""), "pass" if c.get("status") == "pass" else "fail" if c.get("status") == "fail" else "")}'
                     f' <span class="small muted">{e(c.get("detail", ""))}</span></li>'
                     for name, c in (design.get("hard_checks") or {}).items())
    fonts = design.get("fonts") or {}
    font_line = ("fallback (" + ", ".join(fonts.get("fallback") or []) + ")") if fonts.get("fallback") else \
        ("loaded" if fonts else "unknown")
    notes = "".join(f'<p class="small">{e(n)}</p>' for n in design.get("notes") or [])
    strip = []
    for v in design.get("variants") or []:
        judged = v.get("judge") or {}
        shot = (v.get("shots") or {}).get("phone")
        label = ", ".join(x for x in (v.get("look"), v.get("hero_style")) if x) or "page as written"
        img = (f'<a href="{url_for("design_shot", run_id=run_id, page=page, name=shot)}" target="_blank" rel="noopener">'
               f'<img loading="lazy" src="{url_for("design_shot", run_id=run_id, page=page, name=shot, thumb=1)}" '
               f'alt="variant {v["n"]} phone screenshot" style="width:120px;height:260px;object-fit:cover;'
               f'object-position:top;border:1px solid var(--line);border-radius:4px;display:block"></a>') if shot else \
            '<div style="width:120px;height:260px;border:1px dashed var(--line);border-radius:4px"></div>'
        chosen = v["n"] == design.get("chosen")
        strip.append(
            f'<figure style="margin:0;flex:0 0 auto;width:120px">{img}<figcaption class="small">'
            f'<b>{e(str(judged.get("score", "-")) if judged else "-")}</b>{" chosen" if chosen else ""}<br>'
            f'<span class="muted">#{v["n"]} {e(label)}</span></figcaption></figure>')
    return (
        '<div class="card" style="margin-top:16px"><div class="card-h"><h3>Design</h3>'
        + (chip(status, kind) if status else "") + "</div>"
        + f'<p class="small muted">Score {e(str(design.get("score")) if design.get("score") is not None else "-")} '
        f'(min {e(str(design.get("min_score", "-")))}), verdict {e(design.get("verdict") or "-")}; '
        f'judge {e(design.get("model", ""))}, {e(str(design.get("judge_calls", 0)))} call(s), '
        f'${float(design.get("cost_usd") or 0):.4f}; fonts {e(font_line)}. Jev scores the copy; this scores the layout.</p>'
        + notes
        + f'<div class="facts">{sub_html}</div>'
        + (f'<h4 style="margin:12px 0 4px">Top issues</h4><ul class="small">{issues}</ul>' if issues else "")
        + f'<h4 style="margin:12px 0 4px">Design variants</h4><div class="design-strip" '
          f'style="display:flex;gap:12px;overflow-x:auto;padding-bottom:4px">{"".join(strip)}</div>'
        + (f'<h4 style="margin:12px 0 4px">Hard checks</h4><ul class="small">{checks}</ul>' if checks else "")
        + "</div>"
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
        ("Jev composite", e(f"{float(jev):.2f}") if jev is not None
         else f'<span class="muted">not scored ({e(info["jev_note"])})</span>'),
        ("Gate", chip(info["gate"], "pass" if info["gate"] == "PASS" else "fail") if info["gate"] else "-"),
        ("Design score", e(str(info["design"].get("score")) if info["design"].get("score") is not None else "not scored")
         if info.get("design") else '<span class="muted">gate off</span>'),
        ("Cost", e(f"${cost:.4f}") if cost is not None else "-"),
        ("Run", f'<code>{e(run_dir.name)}</code>'),
        ("Created", _when(info["created"])),
    ]
    if info["ads"]:
        rows.append(("Ads pointing here", "<br>".join(
            f'{e(a["ad_name"] or a["ad_id"])} &middot; {chip(a["effective_status"] or "unknown", "live" if a["effective_status"] == postlive.LIVE_AD_STATUS else "")}'
            f' <span class="muted small">checked {_when(a["links_checked_at"])}</span>' for a in info["ads"])))
    elif info["status"] == "live":
        rows.append(("Ads pointing here", '<span class="muted">none</span>'))
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
        f'<h1 class="gv-title">{e(info["title"] or page)}</h1>'
        + (f'<p class="sub muted small" style="margin:6px 0 0">Page title: {e(info["seo_title"])}</p>'
           if info["seo_title"] else "")
        + (f'<p class="sub small" style="margin:6px 0 0">Test destinations: '
           f'{e(abtest.destinations_label(status["test"], this_key=status["key"]))}</p>'
           if status["test"] and any(abtest.is_external(v) for v in status["test"].get("variants") or []) else "")
        +
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
        + _design_card(run_id, page, info.get("design"))
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

def _pin_from(source):
    """(arm, fields, error) for the upload form's pre-selection in
    `source` (request.args or request.form). (None, {}, None) when none."""
    fields = {k: (source.get(k) or "").strip() for k in PIN_FIELDS}
    if not any(fields.values()):
        return None, {}, None
    try:
        return library.arm_from_fields(**fields, tenant=current_app.config["SITE_TENANT"]), fields, None
    except library.LibraryError as exc:
        return None, {}, f"That page version cannot be built: {exc}"


def _pin_box(arm, fields):
    hidden = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in fields.items() if v)
    return (f'{hidden}<div class="notice pin"><p class="eyebrow" style="margin-bottom:6px">Page version</p>'
            f'<b>{e(library.arm_label(arm))}</b> <code class="small">{e(arm.id)}</code>'
            '<p class="small muted" style="margin:6px 0 0">Variant A of the test is built as this version; B and C '
            f'come from the test library. <a href="{url_for("upload_ad")}">Clear</a></p></div>')


def _upload_form(tenant, *, error=None, values=None, status=200, pin=None):
    values = values or {}
    auto = abtest.settings(tenant)["auto_publish"]
    note = ("When the pages are built, the test is published and the split link shows here and on the "
            "Ads page. Paste it into the ad in Ads Manager." if auto else
            "When the pages are built, publish the test with <code>harness abtest publish</code>.")
    plan = values.get("destinations") or abtest.DEFAULT_PLAN
    plan_options = "".join(f'<option value="{k}"{" selected" if k == plan else ""}>{e(label)}</option>'
                           for k, (label, _shape) in abtest.PLANS.items())

    def field(name, label, *, area=False, hint=""):
        value = e(values.get(name) or "")
        control = (f'<textarea id="{name}" name="{name}" maxlength="{upload_mod.MAX_COPY_CHARS}" '
                   f'style="min-height:96px">{value}</textarea>'
                   if area else f'<input type="text" id="{name}" name="{name}" value="{value}" '
                                f'maxlength="{upload_mod.MAX_COPY_CHARS}" placeholder="{e(hint)}">')
        return f'<label for="{name}">{label} <span class="opt">(optional)</span></label>{control}'

    body = (
        '<div class="page-head"><div><h1>Upload an ad</h1><p class="sub">The harness builds landing pages from it '
        'and puts them behind one split link (an A/B/C test).</p></div></div>'
        '<div class="two"><form class="card" method="post" enctype="multipart/form-data" '
        f'action="{url_for("upload_ad")}">{_csrf_field()}'
        + (f'<div class="error">{e(error)}</div>' if error else "")
        + (_pin_box(*pin) if pin else "")
        + '<label for="ad_name" style="margin-top:0">Ad name</label>'
        f'<input type="text" id="ad_name" name="ad_name" required maxlength="{upload_mod.MAX_NAME_CHARS}" '
        f'value="{e(values.get("ad_name") or "")}" placeholder="The name the ad has in Ads Manager">'
        '<label for="media">Video or image</label>'
        f'<div class="drop">{icon("upload")}<div><b>Choose a file</b> or drop it here</div>'
        '<div class="small">mp4, mov, jpg, png, webp &middot; up to 500 MB</div><div class="file"></div>'
        '<input type="file" id="media" name="media" required '
        'accept="video/mp4,video/quicktime,.mp4,.mov,image/jpeg,image/png,image/webp"></div>'
        # Cycle 88: the test's destination plan (abtest.PLANS).
        f'<label for="destinations">Destinations</label><select id="destinations" name="destinations">{plan_options}'
        '</select><p class="hint">Where the split link sends visitors: pages the harness builds, the product page '
        '(PDP) of the product the ad names, or the store home page. PDP and home pages carry no tracking, so the '
        'harness counts only the visitors it sends there; read the rest in GA4 or Shopify by utm_content. This '
        'choice is for this new test only: the destinations of a live test cannot be changed here. The harness '
        'never edits the ad in Meta; you paste the split link into the ad in Ads Manager by hand.</p>'
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
        '<li><b>The pages are built</b><div class="muted small">One job in the queue; it takes a few minutes. '
        'You land on the job page.</div></li>'
        f'<li><b>Published</b><div class="muted small">{note}</div></li></ul></div></div>'
    )
    return _page("Upload an ad", body, status=status)


def _look_import_form(*, dialog_id=None, error=None, values=None):
    """Cycle 87: the "Import a page" form -- a <dialog> on the Ads page, and
    a page of its own without JS or after an error."""
    values = values or {}
    key = dialog_id or "li"
    cartridge = values.get("cartridge") or "landing"
    options = "".join(
        f'<option value="{c}"{" selected" if c == cartridge else ""}>{label}</option>'
        for c, label in (("landing", "Landing page"), ("listicle", "Listicle")))
    form = (
        f'<form method="post" enctype="multipart/form-data" action="{url_for("look_import")}">{_csrf_field()}'
        f'<div class="dlg-h"><div><h2 id="{key}-t">Import a page</h2><p>Drop screenshots of any landing page or '
        "listicle, or paste its URL: you get a version of the same structure in our design, to add to the Library.</p></div>"
        + (f'<button type="button" class="iconbtn" data-close aria-label="Close">{icon("x")}</button>' if dialog_id
           else "")
        + '</div><div class="dlg-b">' + (f'<div class="error">{e(error)}</div>' if error else "")
        + f'<label for="{key}-name" style="margin-top:0">Name</label>'
        f'<input type="text" id="{key}-name" name="name" required maxlength="{look_import_mod.MAX_TITLE_CHARS}" '
        f'value="{e(values.get("name") or "")}" placeholder="e.g. Split hero" autocomplete="off">'
        f'<label for="{key}-c">Page type</label><select id="{key}-c" name="cartridge">{options}</select>'
        f'<label for="{key}-img">Screenshots</label>'
        f'<div class="drop">{icon("upload")}<div><b>Choose screenshots</b> or drop them here</div>'
        f'<div class="small">png, jpg, webp, heic &middot; up to {look_vision_mod.MAX_FILES}, '
        f'{look_vision_mod.MAX_FILE_BYTES // look_vision_mod.MB} MB each, top of the page first</div>'
        '<div class="file"></div>'
        f'<input type="file" id="{key}-img" name="images" multiple aria-label="Screenshots" '
        'accept="image/png,image/jpeg,image/webp,image/heic,image/heif,.png,.jpg,.jpeg,.webp,.heic,.heif"></div>'
        f'<label for="{key}-url">or the page URL</label>'
        f'<input type="text" id="{key}-url" name="url" maxlength="{look_import_mod.MAX_URL_CHARS}" '
        f'value="{e(values.get("url") or "")}" placeholder="https://..." autocomplete="off" spellcheck="false">'
        f'<label for="{key}-file">or an .html file</label>'
        f'<input type="file" id="{key}-file" name="page_file" accept=".html,.htm,text/html">'
        f'<label for="{key}-html">or paste the HTML</label>'
        f'<textarea id="{key}-html" name="html" maxlength="{look_import_mod.MAX_SOURCE_BYTES}" '
        f'style="min-height:96px" spellcheck="false">{e(values.get("html") or "")}</textarea>'
        f'<label for="{key}-ex">Exemplar look (optional)</label>'
        f'<input type="text" id="{key}-ex" name="exemplar" maxlength="40" value="{e(values.get("exemplar") or "")}" '
        'placeholder="a hand-made look name; empty = the closest" autocomplete="off" spellcheck="false">'
        '<p class="hint">Use one of the four. A URL is also screenshotted on a phone and a desktop. Only the '
        "layout and the design are learned: the page&rsquo;s words, prices, brand names and scripts never reach "
        "the look. Claude Opus looks at the screenshots, drafts the look with the closest hand-made look as its "
        "model, and revises it once if our rules or the fidelity score ask for it (about $1 an import). You see "
        "the source beside our render, then add it to the Library or discard it. A failed import keeps "
        "nothing.</p>"
        '</div><div class="dlg-f">'
        + ('<button type="button" class="btn ghost" data-close>Cancel</button>' if dialog_id else
           f'<a class="btn ghost" href="{url_for("ads_home")}">Cancel</a>')
        + f'<button class="btn" type="submit">{icon("upload")}Import</button></div></form>'
    )
    if dialog_id:
        return f'<dialog id="{dialog_id}" aria-labelledby="{key}-t">{form}</dialog>'
    return f'<div class="card" style="max-width:600px;padding:0">{form}</div>'


def _look_import_page(*, error=None, values=None, status=200):
    body = ('<div class="page-head"><div><h1>Import a page</h1><p class="sub">A new look for the landing page or '
            "the listicle, learned from any page.</p></div></div>" + _look_import_form(error=error, values=values))
    return _page("Import a page", body, status=status)


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
    elif job["type"] in GO_LIVE_JOB_TYPES:
        test_id = payload.get("test_id") or ""
        rec = abtest.find_test(tenant, test_id)
        parts.append(f'<p>A/B/C test <a href="{url_for("results_home")}#t-{e(test_id)}">{e(test_id)}</a>'
                     + (f" &middot; {e(rec.get('name') or '')}" if rec else "") + "</p>")
        if result.get("ad_id"):
            if job["type"] == "meta_go_live":
                rows = [("Ad", f"{result.get('ad_name') or ''} ({result['ad_id']})"),
                        ("Creative", f"{result.get('old_creative_id') or '-'} -> {result.get('new_creative_id') or '-'}"),
                        ("Link before", result.get("old_destination") or "-"),
                        ("Link after (read back)", result.get("verified_destination") or "-")]
            else:
                rows = [("Ad", f"{result.get('ad_name') or ''} ({result['ad_id']})"),
                        ("Creative", f"{result.get('from_creative_id') or '-'} -> {result.get('creative_id') or '-'}"),
                        ("Link before", result.get("split_url") or "-"),
                        ("Link after (read back)", result.get("verified_destination") or "-")]
            parts.append('<dl class="meta">' + "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows) + "</dl>")
            for change in result.get("changes") or []:
                parts.append(f'<p class="small"><code>{e(change["field"])}</code>: {e(change["old"] or "-")} &rarr; '
                             f'{e(change["new"])}</p>')
            if result.get("log"):
                parts.append('<pre class="detail">' + e("\n".join(result["log"])) + "</pre>")
    elif job["type"] == "library_build":
        parts.append(f'<p><a href="{url_for("library_home")}">Open the Library</a></p>')
        if result:
            parts.append(f'<p>{int(result.get("built") or 0)} built, {int(result.get("skipped") or 0)} up to date, '
                         f'{len(result.get("unavailable") or [])} not renderable offline, '
                         f'{len(result.get("pruned") or [])} pruned, '
                         f'{int(result.get("thumbs") or 0)} thumbnail(s).</p>')
            if not result.get("browser"):
                parts.append('<p class="small muted">No headless browser on the server: the cards are text '
                             'cards.</p>')
    elif job["type"] == "look_import":
        if result.get("slug"):
            obs = result.get("observation") or {}
            parts.append(f'<p><b>{e(result.get("title") or "")}</b> <span class="muted">{e(result["cartridge"])} look '
                         f'<code>{e(result["slug"])}</code></span></p>')
            parts.append(_import_result_card(tenant, job, result))
            parts.append(f'<p>{e(result.get("summary") or "")}</p>')
            parts.append('<p class="small muted">Sections: ' + e(" > ".join(result.get("sections") or []))
                         + f' &middot; {int(obs.get("words") or 0)} words, {int(obs.get("images") or 0)} images, '
                         f'about {int(obs.get("estimated_weight_kb") or 0)} KB</p>')
            cost = result.get("cost_usd")
            parts.append(f'<p>Validation: {e(result.get("validation") or "")}'
                         + (f" &middot; model cost ${float(cost):.3f}" if cost is not None else "") + "</p>")
            if result.get("brief"):
                parts.append(f'<p class="small"><b>Design brief.</b> {e(result["brief"])}</p>')
            if result.get("fidelity") is not None:
                missing = result.get("missing_patterns") or []
                parts.append(f'<p>Fidelity <b>{float(result["fidelity"]):.2f}</b>'
                             + (f' &middot; exemplar <code>{e(result["exemplar"])}</code>' if result.get("exemplar")
                                else "") + "</p>")
                if missing:
                    parts.append('<p class="small">Missing patterns: '
                                 + ", ".join(f"<code>{e(m)}</code>" for m in missing) + "</p>")
            parts.append(f'<p><a class="btn ghost sm" href="{url_for("look_review", cartridge=result["cartridge"], slug=result["slug"])}" '
                         f'target="_blank" rel="noopener">{icon("external")}Reference render</a></p>')
            parts.append(f'<p class="hint">Use it in the A/B/C library as <code>{e(result.get("abtest_entry") or "")}'
                         "</code>.</p>")
        else:
            source = payload.get("url") or payload.get("filename") or "pasted HTML"
            parts.append(f'<p>{e(payload.get("title") or payload.get("slug") or "")} &middot; '
                         f'{e(payload.get("cartridge") or "")} &middot; from {e(source)}</p>')
    elif job["type"] == "photos_import":
        what = (f'{int(payload["files"])} photo{"s" if payload["files"] != 1 else ""} uploaded'
                if payload.get("files") else "new photos from the Drive folder")
        parts.append(f'<p>{e(what)} &middot; <a href="{url_for("photos_home")}">Open Photos</a></p>')
        if result:
            parts.append(f'<p>{int(result.get("added") or 0)} added, {int(result.get("existing") or 0)} already in '
                         f'the library, {int(result.get("skipped") or 0)} not readable &middot; '
                         f'<b>{int(result.get("tagged") or 0)} of {int(result.get("photos") or 0)} tagged</b> '
                         f'&middot; model cost ${float(result.get("cost_usd") or 0):.3f}</p>')
            if result.get("untagged"):
                parts.append('<p class="small muted">An untagged photo is not used yet. Upload it again to tag it.'
                             '</p>')
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


IMPORT_STATE_TEXT = {
    "added": "Added to the Library.",
    "discarded": "Discarded: it was not added to the Library.",
    "stored": "In the Library.",
}


def _import_result_card(tenant, job, result):
    """Cycle 102: the import's result -- the source's first phone screen
    beside the look's render at 390, the fidelity, what the observation came
    from, and "Add to Library" / "Discard" while the look is pending."""
    cartridge, slug = result["cartridge"], result["slug"]
    state = look_import_mod.import_state(tenant, cartridge, slug)
    figures = []
    if result.get("source_thumb") and state != "discarded":
        figures.append(f'<figure><img src="{url_for("look_source", cartridge=cartridge, slug=slug)}" '
                       'alt="The source page, first phone screen"><figcaption>Source</figcaption></figure>')
    if result.get("render_shot") and state != "discarded":
        figures.append(f'<figure><img src="{url_for("look_render", cartridge=cartridge, slug=slug)}" '
                       'alt="Our render at 390 px"><figcaption>Our render, 390 px</figcaption></figure>')
    html_ = f'<div class="li-compare">{"".join(figures)}</div>' if figures else ""
    observed = {"images": "the screenshots", "images+html": "the screenshots and the HTML",
                "html": "the HTML"}.get(result.get("observed_from") or "html", "the HTML")
    html_ += f'<p class="small muted li-observed">Observed from {e(observed)}.</p>'
    if state == "pending":
        job_field = f'<input type="hidden" name="job" value="{int(job["id"])}">'
        html_ += (
            '<div class="li-actions">'
            f'<form method="post" action="{url_for("look_add", cartridge=cartridge, slug=slug)}">{_csrf_field()}'
            f'{job_field}<button class="btn" type="submit">{icon("layers")}Add to Library</button></form>'
            f'<form method="post" action="{url_for("look_discard", cartridge=cartridge, slug=slug)}">'
            f'{_csrf_field()}{job_field}<button class="btn ghost" type="submit">{icon("x")}Discard</button></form>'
            "</div>")
    else:
        text = IMPORT_STATE_TEXT[state]
        link = (f' <a href="{url_for("library_home")}#lib-{e(cartridge)}-{e(slug)}">Open the Library</a>'
                if state in ("added", "stored") else "")
        html_ += f'<p class="li-state">{e(text)}{link}</p>'
    return html_


def _import_steps(current):
    """Cycle 102: the import's steps as a row, the current one marked."""
    steps = look_import_mod.PROGRESS_STEPS
    at = steps.index(current) if current in steps else -1
    return '<ol class="li-steps">' + "".join(
        f'<li class="{"done" if i < at else "now" if i == at else ""}">{e(s.capitalize())}</li>'
        for i, s in enumerate(steps)) + "</ol>"


def _target_link(target):
    if (target or "").startswith(meta_golive.TARGET_PREFIX):
        test_id = target[len(meta_golive.TARGET_PREFIX):]
        return f'<a href="{url_for("results_home")}#t-{e(test_id)}">{e(target)}</a>'
    if target == "photos:library":
        return f'<a href="{url_for("photos_home")}">{e(target)}</a>'
    if target == library.JOB_TARGET:
        return f'<a href="{url_for("library_home")}">{e(target)}</a>'
    m = re.match(r"^run:([^/]+)/(.+)$", target or "")
    if m:
        return f'<a href="{url_for("generation", run_id=m.group(1), page=m.group(2))}">{e(target)}</a>'
    m = re.match(r"^look:([a-z]+)/([a-z0-9-]+)$", target or "")
    if m and m.group(1) in look_import_mod.CARTRIDGES and looks_mod.LOOK_SLUG_RE.match(m.group(2)):
        return f'<a href="{url_for("look_review", cartridge=m.group(1), slug=m.group(2))}">{e(target)}</a>'
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


def _design_thumb(tenant, source, name):
    """Cycle 92: the first phone screen (390x844) of a design-gate shot at
    240 px wide, cached like the run thumbnails."""
    cache = Path(tenant.runs_dir) / "thumb-cache"
    thumb = cache / (textutil.safe_filename(name).rsplit(".", 1)[0] + ".jpg")
    try:
        if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
            cache.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as im:
                im = im.convert("RGB").crop((0, 0, min(im.size[0], 390), min(im.size[1], 844)))
                im.thumbnail((240, 520), Image.LANCZOS)
                im.save(thumb, format="JPEG", quality=80)
        resp = send_file(thumb, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp
    except Exception:  # noqa: BLE001 -- a broken shot shows the placeholder
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
# library (cycle 86)
# ---------------------------------------------------------------------------

def _generate_url(item):
    args = {"cartridge": item["cartridge"]}
    for key, field in (("look", "look"), ("hero_style", "first_screen"), ("style", "style")):
        if item.get(field):
            args[key] = item[field]
    return url_for("upload_ad", **args)


# Cycle 99: the origin tag on a structure's card (library.origin).
LIB_ORIGIN_LABELS = {"shipped": "Shipped", "imported": "Imported", "hand-converted": "Hand-converted"}


def _lib_no_thumb_text(item):
    return item.get("headline") or ("No reference render yet." if not item["has_review"] else
                                    "No thumbnail: the server has no headless browser.")


def _lib_variant(item, current):
    """A ready variant in its card's strip: a small thumbnail and its name.
    A click swaps the card's big thumbnail and links (site_ui.JS,
    data-lib-variant); without script it opens the variant's reference."""
    key = item["key"]
    thumb = url_for("library_thumb", key=key) if item["has_thumb"] else ""
    img = f'<img loading="lazy" src="{thumb}" alt="">' if thumb else ""
    return (f'<a class="lib-var{" on" if item is current else ""}" id="libv-{e(key.replace("/", "-"))}" '
            f'href="{url_for("library_review", key=key)}" target="_blank" rel="noopener" data-lib-variant '
            f'data-thumb="{thumb}" data-text="{e(_lib_no_thumb_text(item))}" '
            f'data-review="{url_for("library_review", key=key)}" '
            f'data-generate="{e(_generate_url(item)) if item.get("arm") else ""}" data-label="{e(item["label"])}">'
            f'<span class="lib-var-shot">{img}</span><span class="lib-var-name">{e(item["label"])}</span></a>')


def _lib_off(group):
    """Variants with no reference page (not renderable offline, failed, not
    built yet) as one text chip, the full reason on hover. Listicle styles
    of one first screen with the same reason share a chip ("Face · 5
    styles")."""
    item = group[0]
    name = item["label"] if len(group) == 1 else f'{item["label"].split(" \u00b7 ")[0]} \u00b7 {len(group)} styles'
    title = item["why"] + (f' ({", ".join(v["style"] for v in group)})' if len(group) > 1 else "")
    return (f'<span class="chip lib-off" id="libv-{e(item["key"].replace("/", "-"))}" title="{e(title)}">'
            f'{e(name)}: {e(item["why"].split(":")[0])}</span>')


def _lib_source_chip(structure):
    """Cycle 102: an imported look's source -- a chip with its thumbnail,
    the first phone screen of the page it was modelled on, bigger on hover."""
    if not structure.get("source_thumb"):
        return ""
    src = url_for("look_source", cartridge=structure["cartridge"], slug=structure["look"])
    origin = structure.get("imported_from") or "a screenshot"
    return (f'<a class="chip lib-source" href="{src}" target="_blank" rel="noopener" '
            f'title="Modelled on {e(origin)}"><img src="{src}" alt="" loading="lazy">Modelled on {e(origin)}'
            f'<span class="lib-source-pop"><img src="{src}" alt="The source page, first phone screen" '
            'loading="lazy"></span></a>')


def _lib_toggle_text(variants):
    """Cycle 105: the card's one variants line: "6 first screens · 5
    styles" for a look with listicle styles, else "4 variants"."""
    if any(v.get("style") for v in variants):
        screens = len({v.get("first_screen") for v in variants})
        styles = len({v.get("style") for v in variants})
        return f'{_plural(screens, "first screen")} \u00b7 {_plural(styles, "style")}'
    return _plural(len(variants), "variant")


def _lib_card(structure):
    """Cycle 99: one card per structure (a cartridge x look). Its first
    screens (and listicle styles) are variants inside the card. Cycle 105:
    the card shows one picture (the newest real run's phone screen when
    `harness library build` cached one, else the default variant's
    reference render) and one variants line; the variant strip stays
    hidden until that line or the picture is clicked (site_ui.JS,
    data-lib-toggle; without script both open the review)."""
    item = structure["default"]
    variants = structure["variants"]
    ready = [v for v in variants if v["state"] == "ready"]
    off = [v for v in variants if v["state"] == "off"]
    card_id = structure["key"].replace("/", "-")
    review_url = url_for("library_review", key=item["key"]) if item["state"] == "ready" else ""
    real = structure.get("real")
    thumb = url_for("library_thumb", key=item["key"]) if item["has_thumb"] else ""
    if real:
        thumb = url_for("library_real_thumb", key=structure["key"])
    screen = (f'<div class="phone"><div class="screen"><img class="poster lib-poster" '
              + (f'src="{thumb}" ' if thumb else "hidden ")
              + f'alt=""><b class="lib-noshot"{" hidden" if thumb else ""}>'
              f'{e("Not renderable offline" if item["state"] == "off" else _lib_no_thumb_text(item))}</b></div></div>')
    more = len(variants) > 1 and bool(ready or off)
    toggle_attrs = f' data-lib-toggle aria-controls="libs-{e(card_id)}"' if more else ""
    if review_url:
        shot = (f'<a class="shot lib-shot" href="{review_url}" target="_blank" rel="noopener" tabindex="-1" '
                f'aria-hidden="true"{toggle_attrs}>{screen}</a>')
    else:
        shot = f'<div class="shot lib-shot"{toggle_attrs}>{screen}</div>'
    if real:
        shot += (f'<p class="small muted lib-from" data-lib-from title="{e(real["run_id"])}">'
                 f'from a real run \u00b7 {_date(real["at"]) if real.get("at") else e(real["run_id"])}</p>')
    chips = [chip(LIB_ORIGIN_LABELS.get(structure["origin"], structure["origin"]),
                  "solid" if structure["origin"] != "shipped" else ""),
             chip(f'{structure["look"]} look') if structure.get("look") else "",
             _lib_source_chip(structure)]
    words = item.get("word_range")
    design_fact = ""
    if structure["design"]:   # cycle 92 scores; cycle 99: the best among the variants
        best = structure["design"]
        design_fact = _design_fact({"score": best.get("design_score"), "status": best.get("design_status"),
                                    "min_score": "-", "model": ""})
    added = next((v.get("added_at") for v in variants if v.get("added_at")), None)
    facts = (f'<div class="facts"><span>Audience <b>{e(item.get("audience_temperature") or "-")}</b></span>'
             f'<span>Words <b>{f"{words[0]}-{words[1]}" if words else "no band"}</b></span>'
             f'<span>Variants <b>{len(variants)}</b></span>'
             + (f'<span>Added <b>{_date(added)}</b></span>' if added else "")
             + design_fact + "</div>")
    if structure["fidelity"] is not None:
        facts += f'<p class="small muted lib-fidelity">fidelity {round(structure["fidelity"], 2):g}</p>'
    strip = current = ""
    if len(variants) > 1 and ready:
        # the offline render's label; a real-run picture shows its own line until a variant is picked
        current = (f'<p class="small muted lib-current"{" hidden" if real else ""}>'
                   f'Showing <b data-lib-current>{e(item["label"])}</b></p>')
        strip = f'<div class="lib-strip">{"".join(_lib_variant(v, item) for v in ready)}</div>'
    if off:
        same = {}
        for v in off:
            same.setdefault((v.get("first_screen"), v["why"]) if v.get("style") else (v["key"],), []).append(v)
        strip += f'<div class="lib-offs">{"".join(_lib_off(g) for g in same.values())}</div>'
    if more:
        # cycle 105: the variants line is the toggle; without script it opens the review
        href = review_url or f"#libs-{e(card_id)}"
        target = ' target="_blank" rel="noopener"' if review_url else ""
        strip = (f'<a class="lib-toggle" href="{href}"{target} data-lib-toggle aria-expanded="false" '
                 f'aria-controls="libs-{e(card_id)}">{e(_lib_toggle_text(variants))}</a>'
                 f'<div class="lib-more" id="libs-{e(card_id)}" hidden>{strip}</div>')
    actions = ""
    if review_url:
        actions += (f'<a class="btn ghost sm" href="{review_url}" target="_blank" rel="noopener" data-lib-review>'
                    f'{icon("external")}Open review</a>')
    if any(v.get("arm") for v in ready):
        hidden = "" if item.get("arm") and item["state"] == "ready" else " hidden"
        href = e(_generate_url(item)) if not hidden else "#"
        actions += f'<a class="btn sm" href="{href}" data-lib-generate{hidden}>{icon("send")}Generate with this</a>'
    return (f'<div class="gen lib-card" id="lib-{e(card_id)}"><div class="lib-pic">{shot}{current}</div>'
            f'<div class="gen-body">'
            f'<div class="chips">{"".join(c for c in chips if c)}</div>'
            f'<div class="gen-title">{e(structure["title"])}</div>'
            f'<p class="small muted lib-summary" title="{e(structure["summary"])}">{e(structure["summary"])}</p>'
            f'{facts}{strip}'
            f'<div class="gen-actions">{actions}</div></div></div>')


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def render_library(tenant):
    cards = library.structures(tenant)
    groups = {}
    for card in cards:
        groups.setdefault(card["cartridge"], []).append(card)
    for group in groups.values():  # a structure with nothing to show goes to the end of its group
        group.sort(key=lambda c: c["default"]["state"] != "ready")
    job = jobs.active_job(tenant, job_type="library_build", target=library.JOB_TARGET)
    if job:
        rebuild = (f'<a class="btn ghost" href="{url_for("job_detail", job_id=job["id"])}">{icon("clock")}'
                   f'Rebuild {e(job["state"])}</a>')
    else:
        rebuild = (f'<form method="post" action="{url_for("library_rebuild")}">{_csrf_field()}'
                   f'<button class="btn" type="submit">{icon("refresh")}Rebuild library</button></form>')
    variants = [v for card in cards for v in card["variants"]]
    built = sum(1 for v in variants if v["state"] == "ready")
    nav = "".join(f'<a class="btn ghost sm" href="#lib-group-{e(c)}">'
                  f'{e(library.CARTRIDGE_LABELS.get(c, c))} <span class="num">{len(v)}</span></a>'
                  for c, v in groups.items())
    sections = []
    for cartridge, group in groups.items():
        count = sum(len(c["variants"]) for c in group)
        sections.append(
            f'<section class="lib-group" id="lib-group-{e(cartridge)}"><div class="card-h"><h2>'
            f'{e(library.CARTRIDGE_LABELS.get(cartridge, cartridge))}</h2>'
            f'<span class="muted small lib-count">{_plural(len(group), "structure")} &middot; '
            f'{_plural(count, "variant")}</span></div>'
            + (f'<p class="muted small lib-purpose">{e(group[0]["purpose"])}</p>' if group[0].get("purpose") else "")
            + f'<div class="lib-cards">{"".join(_lib_card(c) for c in group)}</div></section>')
    body = (
        '<div class="page-head"><div><h1>Library</h1><p class="sub">Every page structure the harness can generate, '
        'rendered once as a reference. Each card is one structure; its first screens and styles are variants '
        f'inside it ({_plural(len(cards), "structure")}, {built} of {_plural(len(variants), "variant")} rendered). '
        'Pick one and generate a test from it.</p>'
        f'</div><div class="actions">{rebuild}</div></div>'
        f'<div class="notice lib-note">{e(library.NOTE)}</div>'
        f'<div class="lib-nav">{nav}</div>' + "".join(sections)
    )
    return _page("Library", body, refresh=10 if job else None)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# cycle 107: results (per test and arm, and the pooled leaderboard)
# ---------------------------------------------------------------------------

def _cell(value, fmt=""):
    return "-" if value is None else e(format(value, fmt))


def _leaderboard_table(rows, min_views, label):
    body = "".join(
        f'<tr{" class=muted" if not r["enough"] else ""}><td data-label="#">{r["rank"] or "-"}</td>'
        f'<td data-label="{e(label)}"><code>{e(r["group"])}</code>'
        + ("" if r["enough"] else f'<div class="small">under {min_views} views: not ranked</div>') + "</td>"
        f'<td data-label="Tests">{r["tests"]}</td><td data-label="Views">{r["views"]}</td>'
        f'<td data-label="CTA">{r["clicks"]}</td><td data-label="CTA rate">{_cell(r["cta_rate"], ".1%")}</td>'
        f'<td data-label="Orders">{_cell(r["orders"])}</td>'
        f'<td data-label="Orders / 1000 views">{_cell(r["orders_per_1000"], ".1f")}</td></tr>'
        for r in rows)
    return ('<div class="table-wrap"><table class="rtable"><thead><tr><th>#</th>'
            f'<th>{e(label)}</th><th>Tests</th><th>Views</th><th>CTA</th><th>CTA rate</th><th>Orders</th>'
            '<th>Orders / 1000 views</th></tr></thead><tbody>'
            + (body or '<tr><td colspan="8" class="muted">No generated page in a live or finished test yet.</td></tr>')
            + "</tbody></table></div>")


def _test_results(tenant, rec):
    cache = rec.get("orders_cache") or {}
    rows = abtest.results(tenant, rec, cache.get("by_key"), draws=RESULTS_DRAWS)
    rows = meta_golive.with_costs(rows, rec.get("meta_insights"))
    block = rec.get("meta_go_live") or {}
    linked = bool(block) and not block.get("reverted_at")
    head = (f'<div class="jobline" id="t-{e(rec["test_id"])}"><b>{e(rec.get("name") or rec["test_id"])}</b>'
            f'{chip(rec.get("status") or "", "live" if rec.get("status") == "live" else "")}'
            + (chip("Live on Meta", "live", dot=True) if linked else chip("not linked on Meta"))
            + f'<span class="muted small">{e(rec["test_id"])}</span></div>')
    ins = rec.get("meta_insights")
    meta = (f'<p class="small">Meta ad {e(ins["ad_id"])}, {e(ins["since"])} to {e(ins["until"])}: spend '
            f'<b>${e(ins["spend"])}</b>, {ins.get("impressions") or 0} impressions, {ins.get("link_clicks") or 0} '
            f'link clicks <span class="muted">(as of {_when(ins.get("fetched_at"))})</span></p>' if ins else
            '<p class="muted small">No Meta numbers cached yet (harness abtest refresh-metrics).</p>')
    orders_note = (f'<span class="muted small">orders as of {_when(cache.get("fetched_at"))}</span>' if cache else
                   '<span class="muted small">no orders cached yet</span>')
    body = "".join(
        f'<tr><td data-label="Arm">{e(r["key"])}</td><td data-label="Build"><code>{e(r["arm"])}</code></td>'
        f'<td data-label="Assigned">{r.get("assigns", 0)}</td><td data-label="Views">{_cell(r["views"])}</td>'
        f'<td data-label="CTA">{_cell(r["clicks"])}</td><td data-label="CTR">{_cell(r["ctr"], ".1%")}</td>'
        f'<td data-label="Orders">{_cell(r.get("orders"))}</td><td data-label="Revenue">{_cell(r.get("revenue"))}</td>'
        f'<td data-label="P(best)">{_cell(r["p_best"], ".0%")}</td>'
        f'<td data-label="Spend">{"$" + e(r["spend"]) if r.get("spend") else "-"}</td>'
        f'<td data-label="Cost / order">{"$" + e(r["cost_per_order"]) if r.get("cost_per_order") else "-"}</td></tr>'
        for r in rows)
    return ('<div class="card" style="margin-bottom:16px">' + head + meta + orders_note
            + '<div class="table-wrap"><table class="rtable"><thead><tr><th>Arm</th><th>Build</th><th>Assigned</th>'
            '<th>Views</th><th>CTA</th><th>CTR</th><th>Orders</th><th>Revenue</th><th>P(best)</th><th>Spend</th>'
            f'<th>Cost / order</th></tr></thead><tbody>{body}</tbody></table></div></div>')


def render_results(tenant):
    by_build, min_views = abtest.leaderboard(tenant, by="build")
    by_look, _ = abtest.leaderboard(tenant, by="look")
    tests = [r for r in reversed(abtest.list_tests(tenant)) if r.get("status") in abtest.POOLED_STATUSES]
    body = (
        '<div class="page-head"><div><h1>Results</h1><p class="sub">Which generations perform best: every live and '
        'finished A/B/C test, and the builds pooled across them.</p></div></div>'
        f'<p class="muted small">Ranked by CTA rate. A build needs {min_views} views (abtest.min_views) to be ranked; '
        "the rest are listed with their views. Orders and Meta numbers come from the hourly cache "
        "(<code>harness abtest refresh-metrics</code>); orders per 1000 views counts only tests with order data. "
        "A test's Meta spend is shared by its arms in proportion to the visitors the split page sent each one.</p>"
        '<h2 style="font-size:1.05rem;margin:18px 0 8px">By build (look x first screen)</h2>'
        + _leaderboard_table(by_build, min_views, "Build")
        + '<h2 style="font-size:1.05rem;margin:18px 0 8px">By look</h2>'
        + _leaderboard_table(by_look, min_views, "Look")
        + '<h2 style="font-size:1.05rem;margin:24px 0 8px">Per test</h2>'
        + ("".join(_test_results(tenant, rec) for rec in tests) or '<div class="empty">No live or finished test yet.'
                                                                   "</div>")
    )
    return _page("Results", body)


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
    look_import_limiter = abevents.RateLimiter(limit=LOOK_IMPORTS_PER_WINDOW, window_s=UPLOAD_WINDOW_S)

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

    @app.route("/run/<run_id>/design/<page>/<name>")
    def design_shot(run_id, page, name):
        """Cycle 92: a design-gate screenshot (out/<run>/<page>/design/*.png);
        ?thumb=1 is the top of the phone page, small."""
        run_dir = _serve._run_dir_or_404(tenant, run_id)
        safe_page, safe_name = textutil.safe_filename(page), textutil.safe_filename(name)
        path = _serve._safe_path(run_dir, f"{safe_page}/design/{safe_name}")
        if path is None or not safe_name.endswith(".png"):
            abort(404)
        if request.args.get("thumb"):
            return _design_thumb(tenant, path, f"design-{run_id}-{safe_page}-{safe_name}")
        resp = send_file(path, mimetype="image/png")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp

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
            arm, fields, pin_error = _pin_from(request.args)
            return _upload_form(tenant, error=pin_error, pin=(arm, fields) if arm else None,
                                status=400 if pin_error else 200)
        email = g.reviewer_email
        values = {k: request.form.get(k) or "" for k in ("ad_name", "destinations") + upload_mod.COPY_FIELDS}
        plan = values["destinations"] or abtest.DEFAULT_PLAN
        if plan not in abtest.PLANS:
            return _upload_form(tenant, values={**values, "destinations": ""}, status=400,
                                error="Choose one of the listed destinations.")
        arm, fields, pin_error = _pin_from(request.form)
        pin = (arm, fields) if arm else None
        if pin_error:
            return _upload_form(tenant, values=values, error=pin_error, status=400)
        if arm and "gen" not in abtest.PLANS[plan][1]:
            return _upload_form(tenant, values=values, pin=pin, status=400,
                                error=f"{abtest.PLANS[plan][0]} builds no page, so the page version cannot be used. "
                                      "Choose destinations with a generated page, or clear the page version.")
        if not upload_limiter.allow(email.lower()):
            return _upload_form(tenant, values=values, status=429, pin=pin,
                                error=f"More than {UPLOADS_PER_WINDOW} uploads in {UPLOAD_WINDOW_S // 60} minutes. "
                                      "Wait a few minutes.")
        media = request.files.get("media")
        try:
            item = upload_mod.save_upload(
                tenant, media.stream if media and media.filename else None, media.filename if media else "",
                ad_name=values["ad_name"], copy={k: values[k] for k in upload_mod.COPY_FIELDS}, by=email,
                pinned_arm=arm.id if arm else None)
        except upload_mod.UploadRejected as exc:
            return _upload_form(tenant, values=values, error=str(exc), status=exc.status, pin=pin)
        ad_id = item["ad_id"]
        inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
        inbox.note(ad_id, f"destinations: {abtest.PLANS[plan][0]}", destinations=plan)
        inbox.set_state(ad_id, "queued", "uploaded; waiting for the worker")
        job_id = jobs.enqueue(tenant, "create_test", {"ad_id": ad_id, "by": email}, by=email, target=f"ad:{ad_id}")
        jobs.audit(tenant, by=email, action="upload", target=f"ad:{ad_id}",
                   detail={"ad_name": item["ad_name"], "media_file": item["media_file"],
                           "bytes": item["media_bytes"], "job_id": job_id, "destinations": plan,
                           **({"pinned_arm": arm.id} if arm else {})})
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/library")
    def library_home():
        return render_library(tenant)

    @app.route("/library/<path:key>/review")
    def library_review(key):
        """Cycle 86: a version's self-contained reference page; framed by the
        site like page_review (serve.py's framing rule names both)."""
        version = library.find(tenant, key)
        if version is None:
            abort(404)
        path = Path(tenant.library_dir) / version.key / "review.html"
        if not path.is_file():
            return Response(_serve._page_shell("Not built yet", "<p>This version has no reference render yet.</p>",
                                               chrome=False), mimetype="text/html")
        return send_file(path, mimetype="text/html")

    @app.route("/library/<path:key>/thumb")
    def library_thumb(key):
        version = library.find(tenant, key)
        path = Path(tenant.library_dir) / version.key / "thumb.png" if version else None
        if path is None or not path.is_file():
            abort(404)
        resp = send_file(path, mimetype="image/png")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp

    @app.route("/library/<path:key>/real-thumb")
    def library_real_thumb(key):
        """Cycle 105: a structure's picture from its newest real run
        (library.real_pictures caches it during `harness library build`)."""
        if key not in library.structure_keys(tenant):
            abort(404)
        path = Path(tenant.library_dir) / key / library.REAL_THUMB
        if not path.is_file():
            abort(404)
        resp = send_file(path, mimetype="image/png")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp

    @app.route("/library/rebuild", methods=["POST"])
    def library_rebuild():
        email = g.reviewer_email
        job = jobs.active_job(tenant, job_type="library_build", target=library.JOB_TARGET)
        if job is None:
            job_id = jobs.enqueue(tenant, "library_build", {"by": email, "force": True}, by=email,
                                  target=library.JOB_TARGET)
            jobs.audit(tenant, by=email, action="library_build", target=library.JOB_TARGET,
                       detail={"job_id": job_id, "force": True})
        else:
            job_id = job["id"]
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/looks/import", methods=["GET", "POST"])
    def look_import():
        """Cycle 87: "Import a page" -> a look_import job."""
        if request.method == "GET":
            return _look_import_page()
        email = g.reviewer_email
        values = {k: request.form.get(k) or "" for k in ("name", "cartridge", "url", "html", "exemplar")}
        if (request.content_length or 0) > LOOK_IMPORT_MAX_REQUEST_BYTES:
            return _look_import_page(values={**values, "html": ""}, status=413,
                                     error=f"The upload is too large: at most {look_vision_mod.MAX_FILES} "
                                           f"screenshots of {look_vision_mod.MAX_FILE_BYTES // look_vision_mod.MB} "
                                           f"MB, or a page of {look_import_mod.MAX_SOURCE_BYTES // 1024} KB.")
        if not look_import_limiter.allow(email.lower()):
            return _look_import_page(values=values, status=429,
                                     error=f"More than {LOOK_IMPORTS_PER_WINDOW} imports in "
                                           f"{UPLOAD_WINDOW_S // 60} minutes. Wait a few minutes.")
        cartridge = values["cartridge"] or "landing"
        target = f"look:{cartridge}/{look_import_mod.slugify(values['name'])}"
        if jobs.active_job(tenant, job_type="look_import", target=target):
            return _look_import_page(values=values, status=409,
                                     error="An import with this name is already waiting or running.")
        upload = request.files.get("page_file")
        file_bytes = upload.stream.read(look_import_mod.MAX_SOURCE_BYTES + 1) if upload and upload.filename else None
        images = [(f.stream, f.filename) for f in request.files.getlist("images") if f and f.filename]
        try:
            payload = look_import_mod.queue_payload(
                tenant, name=values["name"], cartridge=cartridge, title=values["name"], url=values["url"],
                html_text=values["html"], file_bytes=file_bytes, filename=upload.filename if upload else None,
                by=email, exemplar=values["exemplar"], images=images)
        except look_import_mod.LookImportError as exc:
            return _look_import_page(values=values, error=str(exc), status=400)
        job_id = jobs.enqueue(tenant, "look_import", payload, by=email, target=target)
        source = (payload.get("url") or payload.get("filename")
                  or (f"{len(payload['files'])} screenshot(s)" if payload.get("files") else "pasted HTML"))
        jobs.audit(tenant, by=email, action="look_import", target=target,
                   detail={"kind": payload["kind"], "source": source, "job_id": job_id})
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/looks/<cartridge>/<slug>/review")
    def look_review(cartridge, slug):
        if cartridge not in look_import_mod.CARTRIDGES or not looks_mod.LOOK_SLUG_RE.match(slug):
            abort(404)
        path = look_import_mod.look_folder(tenant, cartridge, slug) / "review.html"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="text/html")

    def _look_file(cartridge, slug, name, mimetype):
        if cartridge not in look_import_mod.CARTRIDGES or not looks_mod.LOOK_SLUG_RE.match(slug):
            abort(404)
        path = look_import_mod.look_folder(tenant, cartridge, slug) / name
        if not path.is_file():
            abort(404)
        resp = send_file(path, mimetype=mimetype)
        resp.headers["Cache-Control"] = "max-age=300"
        return resp

    @app.route("/looks/<cartridge>/<slug>/source.jpg")
    def look_source(cartridge, slug):
        """Cycle 102: the first phone screen of the page a look was modelled on."""
        return _look_file(cartridge, slug, look_vision_mod.SOURCE_THUMB, "image/jpeg")

    @app.route("/looks/<cartridge>/<slug>/render.png")
    def look_render(cartridge, slug):
        """Cycle 102: the look's render of the demo page at 390."""
        return _look_file(cartridge, slug, look_vision_mod.RENDER_SHOT, "image/png")

    def _back_to_job():
        job_id = request.form.get("job") or ""
        return redirect(url_for("job_detail", job_id=int(job_id)) if job_id.isdigit() else url_for("look_import"),
                        code=303)

    @app.route("/looks/<cartridge>/<slug>/add", methods=["POST"])
    def look_add(cartridge, slug):
        """Cycle 102: "Add to Library" -- the look leaves its pending state and
        the Library build (the existing path) renders it."""
        if cartridge not in look_import_mod.CARTRIDGES or not looks_mod.LOOK_SLUG_RE.match(slug):
            abort(404)
        email = g.reviewer_email
        try:
            look_import_mod.add_to_library(tenant, cartridge, slug, by=email)
        except look_import_mod.LookImportError:
            return _back_to_job()
        # a queued build will see the look; a running one may have listed the looks already
        job = jobs.active_job(tenant, job_type="library_build", target=library.JOB_TARGET)
        job_id = job["id"] if job and job["state"] == "queued" else jobs.enqueue(
            tenant, "library_build", {"by": email, "only": [cartridge]}, by=email, target=library.JOB_TARGET)
        jobs.audit(tenant, by=email, action="look_add", target=f"look:{cartridge}/{slug}",
                   detail={"library_job": job_id})
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/looks/<cartridge>/<slug>/discard", methods=["POST"])
    def look_discard(cartridge, slug):
        """Cycle 102: "Discard" -- the pending look is moved out of every listing."""
        if cartridge not in look_import_mod.CARTRIDGES or not looks_mod.LOOK_SLUG_RE.match(slug):
            abort(404)
        email = g.reviewer_email
        try:
            moved = look_import_mod.discard(tenant, cartridge, slug)
        except look_import_mod.LookImportError:
            return _back_to_job()
        jobs.audit(tenant, by=email, action="look_discard", target=f"look:{cartridge}/{slug}",
                   detail={"moved_to": moved.name})
        return _back_to_job()

    # Cycle 96: the Photos page (harness/site_photos.py).
    from . import site_photos

    site_photos.register(app, tenant)

    # Cycle 107: go live on Meta / revert (harness/meta_golive.py), as jobs.
    def _go_live_action(test_id, *, revert):
        rec = abtest.find_test(tenant, test_id)
        if rec is None:
            abort(404)
        back = url_for("ads_home", q=rec.get("name") or None)
        email = g.reviewer_email
        if runstate.find_reviewer(tenant, email) is None:
            return _page("Not a reviewer", _message("Not a reviewer", f"{e(email)} is not in this tenant's reviewers "
                                                    "list.", back=back), 403)
        gl = _go_live_info(tenant, rec) if rec.get("status") == "live" and (rec.get("split") or {}).get("url") else None
        if gl is None:
            return _page("Not a live Meta test", _message("Not a live Meta test", "Only a live A/B/C test with a split "
                                                          "link, built from a Meta ad, can go live on Meta.",
                                                          back=back), 409)
        if revert and not gl["live"]:
            return _page("Not live on Meta", _message("Not live on Meta", "This test's ad does not use the split link "
                                                      "from a go-live; there is nothing to revert.", back=back), 409)
        if not revert and gl["live"]:
            return _page("Already live on Meta", _message("Already live on Meta", "This test's ad uses its split link "
                                                          "already. Revert it first.", back=back), 409)
        if gl["busy"]:
            return _page("Already queued", _message("Already queued", "A Meta job for this test is already waiting "
                                                    "or running.", back=back), 409)
        title = "Revert the Meta ad" if revert else "Go live on Meta"
        action = url_for("test_revert_go_live" if revert else "test_go_live", test_id=rec["test_id"])
        if request.method == "GET":
            return _confirm_page(title, _go_live_lines(gl, revert=revert), action,
                                 "Yes, revert" if revert else "Yes, go live on Meta", back, danger=revert)
        if request.form.get("confirm") != "yes":
            return _page("Confirm first", _message("Confirm first", f'Open <a href="{back}">the test</a> and use the '
                                                   "button there; it asks you to confirm."), 400)
        job_type = "meta_revert_go_live" if revert else "meta_go_live"
        target = meta_golive.target(rec["test_id"])
        job_id = jobs.enqueue(tenant, job_type, {"test_id": rec["test_id"], "by": email}, by=email, target=target)
        jobs.audit(tenant, by=email, action=f"{job_type}_requested", target=target,
                   detail={"ad_id": gl["ad_id"], "split_url": gl["split"], "current_link": gl["current"],
                           "job_id": job_id})
        return redirect(url_for("job_detail", job_id=job_id), code=303)

    @app.route("/test/<test_id>/go-live", methods=["GET", "POST"])
    def test_go_live(test_id):
        return _go_live_action(test_id, revert=False)

    @app.route("/test/<test_id>/revert-go-live", methods=["GET", "POST"])
    def test_revert_go_live(test_id):
        return _go_live_action(test_id, revert=True)

    @app.route("/results")
    def results_home():
        return render_results(tenant)

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
        if job["state"] == "failed" and job["type"] == "look_import":
            # Cycle 87: a refused import lists every validation error.
            head, *lines = (job["reason"] or "").split("\n- ")
            reason = (f'<div class="error">Failed: {e(head)}'
                      + ("<ul>" + "".join(f"<li>{e(line)}</li>" for line in lines) + "</ul>" if lines else "")
                      + "</div><p class=\"small muted\">Nothing was stored.</p>")
        wait = ('<p class="small muted">This page reloads every 10 seconds until the job ends.</p>' if busy else "")
        if busy and job.get("progress"):
            steps = look_import_mod.PROGRESS_STEPS if job["type"] == "look_import" else ()
            step = job["progress"]
            wait = ((_import_steps(step) if job["type"] == "look_import" else "")
                    + f'<p>Step: <b>{e(step)}</b>'
                    + (f' <span class="muted">({steps.index(step) + 1} of {len(steps)})</span>' if step in steps
                       else "") + "</p>" + wait)
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
