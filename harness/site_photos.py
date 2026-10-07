"""Cycle 96: the review site's Photos page (/photos, docs/LISTICLE-SITE.md
"Photos"). One place for the tenant photo library:

  - Coverage: the cycle 93 audit (shot x mood), the photos per model, "what
    to shoot next" and the owner's photo rules;
  - Add photos: many files at once (drag and drop), one photos_import job
    per upload (harness/photo_uploads.py), progress on this page and the
    job page; "Pull from Drive" when tenant.yaml photo_library.drive_folder
    is set;
  - the grid: every photo with its tags, flags and the runs that used it;
    filters and sort; per photo: edit tags / shot / mood / model, exclude,
    old mark, focal point (click the photo), delete to the trash, restore.

The older per-product image review (serve.py /images: alt text and
exclusions for every image a product page could use) is the second tab.
Same login, form token and rate limit as the ad upload (harness/site.py);
every change is one audit-log line.
"""
import html
import json
import urllib.parse

from flask import Response, abort, g, redirect, request, url_for

from . import abevents, images, jobs, site, site_ui
from . import photo_library as pl
from . import photo_uploads as pu

e = html.escape
icon, chip = site_ui.icon, site_ui.chip

PER_PAGE = 60
MAX_REQUEST_BYTES = pu.MAX_BATCH_BYTES + 1024 * 1024
FLAGS = (("excluded", "Excluded"), ("ai", "AI-generated"), ("old_mark", "Old mark"),
         ("fullbleed", "Full-bleed ok"), ("dark_ok", "Dark-page ok"), ("white_edge", "White studio edge"),
         ("untagged", "Not tagged yet"), ("removed", "In the trash"))
SORTS = (("newest", "Newest"), ("quality", "Quality"))
FILTER_KEYS = ("shot", "mood", "model", "flag", "sort", "page")

FOCAL_JS = """
document.addEventListener('click',function(ev){var b=ev.target.closest('.ph-img[data-focal]');if(!b)return;
  var img=b.querySelector('img');var r=img.getBoundingClientRect();if(!r.width||!r.height)return;
  var x=(ev.clientX-r.left)/r.width,y=(ev.clientY-r.top)/r.height;if(x<0||x>1||y<0||y>1)return;
  var f=document.getElementById(b.getAttribute('data-focal'));if(!f)return;
  f.querySelector('[name=x]').value=x.toFixed(2);f.querySelector('[name=y]').value=y.toFixed(2);
  var d=b.querySelector('.ph-dot');if(d){d.style.left=(x*100)+'%';d.style.top=(y*100)+'%';}
  b.classList.add('saving');f.submit();});
document.addEventListener('change',function(ev){if(ev.target.closest('form.ph-filters')){ev.target.form.submit();}});
"""


def _label(slug):
    return slug.replace("-", " ").title()


def _filters(args, models):
    """The valid filter values from the query string."""
    allowed = {"shot": set(images.SHOTS) | {"none"}, "mood": set(images.MOODS) | {"none"},
               "model": set(models) | {pl.UNKNOWN, pu.MODEL_ANY, "untagged"},
               "flag": {k for k, _ in FLAGS}, "sort": {k for k, _ in SORTS}}
    out = {k: args.get(k) for k in allowed if args.get(k) in allowed[k]}
    page = str(args.get("page") or "")
    if page.isdigit() and int(page) > 1:
        out["page"] = int(page)
    return out


def _matches(row, f):
    it = row.get(images.FIELD) or {}
    tags = row.get("tags") or {}
    removed = bool(row.get("removed"))
    if f.get("flag") == "removed":
        if not removed:
            return False
    elif removed:
        return False
    if "shot" in f:
        shot = images.effective_shot(row)[0]
        if (shot or "none") != f["shot"]:
            return False
    if "mood" in f and (it.get("mood") or "none") != f["mood"]:
        return False
    if "model" in f and (pu.model_choice(row) or "untagged") != f["model"]:
        return False
    test = {
        "excluded": lambda: row.get("excluded") and not removed,
        "ai": lambda: row.get("ai_generated"),
        "old_mark": lambda: tags.get("old_logo_visible"),
        "fullbleed": lambda: it.get("fullbleed_ok"),
        "dark_ok": lambda: it.get("dark_ok"),
        "white_edge": lambda: it.get("white_edge"),
        "untagged": lambda: not tags or images.needs_vision(row),
        "removed": lambda: True,
    }.get(f.get("flag"))
    return test is None or bool(test())


def _sorted(rows, sort):
    indexed = list(enumerate(rows))
    if sort == "quality":
        indexed.sort(key=lambda t: (-((t[1].get(images.FIELD) or {}).get("quality") or 0), -t[0]))
    else:  # newest: the manifest is append-only, so the last row is the newest
        indexed.sort(key=lambda t: -t[0])
    return [r for _i, r in indexed]


def _csrf():
    return site._csrf_field()


def _hidden_back(f):
    q = urllib.parse.urlencode({k: v for k, v in f.items() if k in FILTER_KEYS})
    return f'<input type="hidden" name="back" value="{e(q)}">'


def _back_url(back):
    args = {k: v[0] for k, v in urllib.parse.parse_qs(str(back or "")).items() if k in FILTER_KEYS and v}
    return url_for("photos_home", **args)


# ---------------------------------------------------------------------------
# page parts
# ---------------------------------------------------------------------------

def _coverage_html(cov):
    rep = cov["report"]
    moods = images.MOODS + (("unmeasured",) if any(c["unmeasured"] for c in rep["by_shot"].values()) else ())
    head = "".join(f"<th>{e(m)}</th>" for m in moods)
    rows = []
    for shot, counts in rep["by_shot"].items():
        total = sum(counts.values())
        cells = "".join(f'<td class="num{" z" if not counts[m] else ""}">{counts[m] or "&middot;"}</td>' for m in moods)
        rows.append(f'<tr><th scope="row">{e(shot)}</th>{cells}<td class="num tot">{total}</td></tr>')
    per_model = "".join(
        f'<span class="chip{" attn" if n == 0 else ""}">{e(_label(slug))} <b class="num">{n}</b></span>'
        for slug, n in cov["per_model"].items())
    per_model += f'<span class="chip">Any model <b class="num">{cov["agnostic"]}</b></span>'
    shoot = "".join(f"<li>{e(s)}</li>" for s in cov["shoot_next"][:8]) or "<li>Nothing missing: every want has 3 or more photos.</li>"
    rules = "".join(f"<li>{icon('check')}<span>{e(r)}</span></li>" for r in cov["rules"])
    f = rep["flags"]
    return (
        '<section class="card ph-cov" id="coverage"><div class="card-h"><h2>Coverage</h2>'
        f'<span class="muted small">{rep["usable"]} usable of {rep["total"]}'
        + (f' &middot; {rep["missing_file"]} without a file here' if rep.get("missing_file") else "")
        + f' &middot; {rep["measured"]} measured &middot; {rep["vision_tagged"]} vision-tagged</span></div>'
        '<div class="ph-cov-grid"><div>'
        f'<div class="table-wrap"><table class="ph-mat"><thead><tr><th>shot</th>{head}<th>total</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<p class="small muted ph-flagline">Full-bleed ok {f["fullbleed_ok"]} &middot; dark-page ok {f["dark_ok"]} '
        f'&middot; white studio edge {f["white_edge"]} &middot; old mark {f["old_brand"]} &middot; AI-generated '
        f'{f["ai_generated"]}</p>'
        f'<p class="eyebrow" style="margin:14px 0 6px">Photos per model</p><div class="chips ph-models">{per_model}</div>'
        '</div><div>'
        f'<h3>What to shoot next</h3><ol class="ph-shoot">{shoot}</ol>'
        f'<h3>The rules</h3><ul class="ph-rules">{rules}</ul>'
        '</div></div></section>'
    )


def _upload_html(tenant, models):
    options = "".join(f'<option value="{e(m)}">{e(_label(m))}</option>' for m in models)
    drive = ""
    if pu.drive_folder(tenant):
        drive = (f'<form method="post" action="{url_for("photos_pull_drive")}" class="ph-drive">{_csrf()}'
                 f'<button class="btn ghost sm" type="submit">{icon("refresh")}Pull from Drive</button>'
                 '<span class="hint">New photos in the Drive folder set in tenant.yaml; same steps.</span></form>')
    return (
        f'<form class="card ph-up" method="post" enctype="multipart/form-data" action="{url_for("photos_upload")}" '
        f'id="add">{_csrf()}<div class="card-h"><h2>Add photos</h2></div>'
        f'<div class="drop">{icon("upload")}<div><b>Choose photos</b> or drop them here</div>'
        f'<div class="small">jpg, png, webp, heic &middot; up to {pu.MAX_FILE_BYTES // pu.MB} MB each, '
        f'{pu.MAX_BATCH_FILES} at once</div><div class="file"></div>'
        '<input type="file" id="photos" name="photos" multiple required aria-label="Photos" '
        'accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif"></div>'
        f'<label for="ph-model">Model in these photos <span class="opt">(optional)</span></label>'
        f'<select id="ph-model" name="model"><option value="">Mixed or not sure</option>{options}</select>'
        '<p class="hint">Used like a Drive folder label: the vision check must agree before a photo is filed '
        'under that model.</p>'
        f'<div style="margin-top:16px"><button class="btn block" type="submit">{icon("upload")}Upload and tag'
        '</button></div>'
        f'<p class="hint">Each photo is resized to 1600 px, measured, then tagged by two model calls (about '
        f'${pu.TAG_COST_PER_PHOTO_USD:.3f} a photo, counted in the daily cap). A tagged photo is used on the next '
        'page that is generated.</p>' + drive + '</form>'
    )


def _jobs_html(tenant):
    active = [j for j in jobs.list_jobs(tenant, limit=50)
              if j["type"] == "photos_import" and j["state"] in ("queued", "running")]
    if not active:
        return "", False
    items = []
    for j in reversed(active):
        files = (j["payload"] or {}).get("files")
        what = (f"{files} photo{'s' if files != 1 else ''}" if files else "photos from Drive")
        step = j.get("progress") or ("waiting for the worker" if j["state"] == "queued" else "starting")
        items.append(f'<li><a href="{url_for("job_detail", job_id=j["id"])}">Job #{j["id"]}</a>: adding {e(what)} '
                     f'&middot; {chip(j["state"], j["state"])} <b class="ph-step">{e(step)}</b></li>')
    return (f'<div class="notice ph-jobs" id="jobs">{icon("clock")}<div><b>Adding photos</b><ul>{"".join(items)}'
            '</ul></div></div>'), True


def _select(name, options, current, *, label, elem_id):
    opts = "".join(f'<option value="{e(v)}"{" selected" if v == current else ""}>{e(t)}</option>' for v, t in options)
    return f'<label for="{elem_id}">{label}</label><select id="{elem_id}" name="{name}">{opts}</select>'


def _card(row, used, models, back):
    pid = row["id"]
    tags = row.get("tags") or {}
    it = row.get(images.FIELD) or {}
    removed = bool(row.get("removed"))
    shot, source = images.effective_shot(row)
    focal = it.get("focal") if isinstance(it.get("focal"), dict) else {"x": 0.5, "y": 0.5}
    model = pu.model_choice(row)
    q = it.get("quality")
    main = [chip(shot or "no shot", "" if shot else "attn"),
            chip(it.get("mood") or "unmeasured"),
            chip({"": "not tagged", pl.UNKNOWN: "model unknown", pu.MODEL_ANY: "any model"}.get(model, _label(model)),
                 "solid" if model not in ("", pl.UNKNOWN, pu.MODEL_ANY) else ""),
            chip(f"quality {q}" if q else "quality -")]
    flags = []
    if removed:
        flags.append(chip("in the trash", "attn"))
    elif row.get("excluded"):
        flags.append(chip("excluded", "attn"))
    if row.get("ai_generated"):
        flags.append(chip("AI-generated", "attn"))
    if tags.get("old_logo_visible"):
        flags.append(chip("old mark", "attn"))
    if tags.get("has_text_overlay"):
        flags.append(chip("text overlay", "attn"))
    if it.get("fullbleed_ok"):
        flags.append(chip("full-bleed ok"))
    if it.get("dark_ok"):
        flags.append(chip("dark ok"))
    if it.get("white_edge"):
        flags.append(chip("white studio edge"))
    if not tags:
        flags.append(chip("not tagged yet", "queued"))
    elif images.needs_vision(row) and pl._usable(row):
        flags.append(chip("look not tagged", "queued"))
    feats = pu.current_features(row)
    feat_html = ("".join(f"<span>{e(f)}</span>" for f in feats) if feats else '<span class="none">no tags</span>')
    runs = used.get(pid) or []
    used_html = ("Used in " + ", ".join(
        f'<a href="{url_for("generation", run_id=run, page=cartridge)}">{e(run)}</a> <span class="muted">'
        f'{e(cartridge)}</span>' for run, cartridge in runs)) if runs else '<span class="muted">Not used yet</span>'
    src = (" &middot; " + ("person" if source == "person" else "cycle 75 tags" if source == "legacy" else "vision")
           if shot else "")
    form_id = f"f-{pid}"
    shot_opts = [("", f"automatic ({shot or 'none'})" if it.get("shot_source") == "person" or not it.get("shot")
                  else "automatic")] + [(s, s) for s in images.SHOTS]
    person_mood = it.get("mood") if it.get("mood_source") == "person" else ""
    mood_opts = [("", f"measured ({it.get('mood') or 'not yet'})" if not person_mood else "measured")] + \
        [(m, m) for m in images.MOODS]
    model_opts = ([("", "not tagged yet")] if not model else []) + \
        [(pl.UNKNOWN, "unknown (not on model pages)"), (pu.MODEL_ANY, "any model (agnostic)")] + \
        [(m, _label(m)) for m in models]
    hidden = f'{_csrf()}{_hidden_back(back)}'
    edit = (
        f'<details class="ph-edit"><summary>Edit</summary>'
        f'<form method="post" action="{url_for("photo_edit", pid=pid)}">{hidden}'
        '<input type="hidden" name="action" value="save">'
        f'<label for="t-{pid}">Tags <span class="opt">(comma list)</span></label>'
        f'<input type="text" id="t-{pid}" name="features" value="{e(", ".join(feats))}" autocomplete="off">'
        '<div class="ph-sel">'
        + _select("shot", shot_opts, it.get("shot") or "", label="Shot", elem_id=f"s-{pid}")
        + _select("mood", mood_opts, person_mood, label="Mood", elem_id=f"m-{pid}")
        + _select("model", model_opts, model, label="Model", elem_id=f"o-{pid}")
        + f'</div><button class="btn sm" type="submit">{icon("check")}Save</button></form>'
        f'<form method="post" action="{url_for("photo_edit", pid=pid)}" id="{form_id}" class="ph-focal">{hidden}'
        '<input type="hidden" name="action" value="focal">'
        f'<label for="x-{pid}">Focal point <span class="opt">(or click the photo)</span></label><div class="row">'
        f'<input type="number" id="x-{pid}" name="x" min="0" max="1" step="0.01" value="{focal["x"]}" aria-label="x">'
        f'<input type="number" name="y" min="0" max="1" step="0.01" value="{focal["y"]}" aria-label="y">'
        '<button class="btn ghost sm" type="submit">Set</button></div></form></details>'
    )

    def toggle(action, value, text, cls="ghost"):
        return (f'<form method="post" action="{url_for("photo_edit", pid=pid)}">{hidden}'
                f'<input type="hidden" name="action" value="{action}"><input type="hidden" name="value" value="{value}">'
                f'<button class="btn {cls} sm" type="submit">{text}</button></form>')

    if removed:
        acts = toggle("restore", "1", f"{icon('refresh')}Restore")
    else:
        acts = (toggle("excluded", "0" if row.get("excluded") else "1",
                       "Include" if row.get("excluded") else f"{icon('hide')}Exclude")
                + (toggle("old_mark", "0" if tags.get("old_logo_visible") else "1",
                          "No old mark" if tags.get("old_logo_visible") else "Old mark") if tags else "")
                + f'<form method="post" action="{url_for("photo_edit", pid=pid)}" '
                  'onsubmit="return confirm(\'Move this photo to the trash? It is never used again until you '
                  'restore it.\')">'
                  f'{hidden}<input type="hidden" name="action" value="delete">'
                  f'<button class="btn danger sm" type="submit">{icon("x")}Delete</button></form>')
    desc = tags.get("description")
    return (
        f'<article class="ph-card{" off" if row.get("excluded") or removed else ""}" id="p-{pid}">'
        f'<div class="ph-img" data-focal="{form_id}" title="Click to set the focal point">'
        f'<img loading="lazy" src="{url_for("photo_thumb", pid=pid)}" alt="{e(desc or pid)}" '
        f'width="{row["width"]}" height="{row["height"]}">'
        f'<span class="ph-dot" style="left:{focal["x"] * 100:.0f}%;top:{focal["y"] * 100:.0f}%"></span></div>'
        '<div class="ph-b">'
        f'<div class="ph-id"><code>{e(pid)}</code><span>{row["width"]}&times;{row["height"]}{src}</span></div>'
        f'<div class="chips">{"".join(main)}</div>'
        + (f'<div class="chips">{"".join(flags)}</div>' if flags else "")
        + f'<div class="ph-tags">{feat_html}</div>'
        + (f'<p class="ph-desc">{e(desc)}</p>' if desc else "")
        + f'<p class="ph-src small muted" title="{e(row.get("source_name") or "")}">{e(row.get("source_name") or "")}'
        + (f' &middot; added by {e(row["added_by"])}' if row.get("added_by") else "") + "</p>"
        + f'<div class="ph-used small">{used_html}</div>{edit}<div class="ph-acts">{acts}</div></div></article>'
    )


def _filters_html(f, models, shown, total):
    def sel(name, label, options):
        opts = f'<option value="">{label}: all</option>' + "".join(
            f'<option value="{e(v)}"{" selected" if f.get(name) == v else ""}>{label}: {e(t)}</option>'
            for v, t in options)
        return f'<select name="{name}" aria-label="{label}">{opts}</select>'

    model_opts = ([(m, _label(m)) for m in models]
                  + [(pl.UNKNOWN, "unknown"), (pu.MODEL_ANY, "any model"), ("untagged", "not tagged")])
    sort = "".join(f'<option value="{k}"{" selected" if f.get("sort", "newest") == k else ""}>Sort: {t}</option>'
                   for k, t in SORTS)
    return (
        f'<form class="ph-filters" method="get" action="{url_for("photos_home")}">'
        + sel("shot", "Shot", [(s, s) for s in images.SHOTS] + [("none", "none")])
        + sel("mood", "Mood", [(m, m) for m in images.MOODS] + [("none", "unmeasured")])
        + sel("model", "Model", model_opts)
        + sel("flag", "Flag", list(FLAGS))
        + f'<select name="sort" aria-label="Sort">{sort}</select>'
        f'<noscript><button class="btn ghost sm" type="submit">Filter</button></noscript></form>'
        f'<p class="statline"><span>Showing <b>{shown}</b> of {total}</span>'
        + (f'<a href="{url_for("photos_home")}">Clear filters</a>' if any(k != "page" for k in f) else "")
        + "</p>"
    )


def _manifest_problem(tenant):
    """A line for the page when photo-library.json exists but does not load
    (load_manifest then returns an empty library and pages use none)."""
    path = pl.manifest_path(tenant)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return f"photo-library.json cannot be read, so no page uses the library: {exc}"
    errors = pl.validate_manifest(data, features=pl.features_for(tenant), models=pl.model_slugs(tenant))
    if errors:
        return "photo-library.json does not validate, so no page uses the library: " + "; ".join(errors[:3])
    return None


def render(tenant, args, *, error=None, status=200):
    models = pl.model_slugs(tenant)
    manifest = pl.load_manifest(tenant)
    rows = manifest.get("photos", [])
    f = _filters(args, models)
    listed = _sorted([r for r in rows if _matches(r, f)], f.get("sort"))
    page = f.get("page", 1)
    pages = max(1, (len(listed) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    shown = listed[(page - 1) * PER_PAGE: page * PER_PAGE]
    used = pu.used_in(tenant)
    jobs_html, busy = _jobs_html(tenant)
    problem = _manifest_problem(tenant)
    live = sum(1 for r in rows if not r.get("removed"))
    pager = ""
    if pages > 1:
        links = "".join(
            f'<a href="{url_for("photos_home", **dict(f, page=n))}"{" class=\"on\"" if n == page else ""}>{n}</a>'
            for n in range(1, pages + 1))
        pager = f'<nav class="seg ph-pager" aria-label="Pages">{links}</nav>'
    vocabulary = ", ".join(pl.features_for(tenant))
    body = (
        '<div class="page-head"><div><h1>Photos</h1><p class="sub">The photo library every generated page draws '
        f'from: {live} photos. Add photos here; they are tagged, then used on the next page that needs them.</p>'
        '</div></div>'
        + site_ui.photo_tabs("photos_home")
        + (f'<div class="error">{e(error)}</div>' if error else "")
        + (f'<div class="error">{e(problem)}</div>' if problem else "")
        + jobs_html
        + f'<div class="ph-top">{_coverage_html(pu.coverage(tenant, manifest))}{_upload_html(tenant, models)}</div>'
        + '<section class="ph-lib"><div class="card-h"><h2>Library</h2></div>'
        + _filters_html(f, models, len(listed), live)
        + f'<p class="hint ph-vocab">Tags you can use: {e(vocabulary)}. Click a photo to set its focal point '
          '(the part every crop keeps).</p>'
        + (f'<div class="ph-grid">{"".join(_card(r, used, models, f) for r in shown)}</div>' if shown else
           '<div class="empty">No photo matches these filters.</div>')
        + pager + f"</section><script>{FOCAL_JS}</script>"
    )
    refresh = 10 if busy and str(args.get("job") or "").isdigit() else None
    return Response(site_ui.shell("Photos", body, refresh=refresh), status=status, mimetype="text/html")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def register(app, tenant):
    """The Photos routes; called by site.register (serve.py's login and
    Origin check, then site.py's form-token check, run first)."""
    limiter = abevents.RateLimiter(limit=site.UPLOADS_PER_WINDOW, window_s=site.UPLOAD_WINDOW_S)

    @app.route("/photos")
    def photos_home():
        return render(tenant, request.args)

    @app.route("/photos/thumb/<pid>")
    def photo_thumb(pid):
        if not pl.ID_RE.match(pid):
            abort(404)
        row = pl.photos_by_id(pl.load_manifest(tenant)).get(pid)
        if row is None:
            abort(404)
        source = pl.local_file(tenant, row)
        if not source.exists():
            source = pu.trash_dir(tenant) / row["file"]
        if not source.exists():
            return site._thumb_placeholder()
        return site._cached_thumb(tenant, source, f"photo-{pid}.jpg", 640)

    @app.route("/photos/upload", methods=["POST"])
    def photos_upload():
        email = g.reviewer_email
        if (request.content_length or 0) > MAX_REQUEST_BYTES:
            return render(tenant, {}, status=413,
                          error=f"The photos are over {pu.MAX_BATCH_BYTES // pu.MB} MB together. Upload fewer at once.")
        if not limiter.allow(email.lower()):
            return render(tenant, {}, status=429,
                          error=f"More than {site.UPLOADS_PER_WINDOW} uploads in {site.UPLOAD_WINDOW_S // 60} "
                                "minutes. Wait a few minutes.")
        model = (request.form.get("model") or "").strip()
        if model and model not in pl.model_slugs(tenant):
            return render(tenant, {}, status=400, error="Choose a model from the list, or Mixed or not sure.")
        files = [(f.stream, f.filename) for f in request.files.getlist("photos") if f and f.filename]
        try:
            batch = pu.save_batch(tenant, files, label_model=model or None)
        except pu.PhotoUploadRejected as exc:
            return render(tenant, {}, status=exc.status, error=str(exc))
        job_id = jobs.enqueue(tenant, "photos_import",
                              {"kind": "upload", "batch": batch["batch"], "by": email, "files": len(batch["files"])},
                              by=email, target=pu.JOB_TARGET)
        jobs.audit(tenant, by=email, action="photos_upload", target=pu.JOB_TARGET,
                   detail={"files": [f["name"] for f in batch["files"]], "bytes": batch["bytes"], "job_id": job_id,
                           "model": model or None})
        return redirect(url_for("photos_home", job=job_id) + "#jobs", code=303)

    @app.route("/photos/pull-drive", methods=["POST"])
    def photos_pull_drive():
        email = g.reviewer_email
        folder = pu.drive_folder(tenant)
        if not folder:
            abort(404)
        if not limiter.allow(email.lower()):
            return render(tenant, {}, status=429,
                          error=f"More than {site.UPLOADS_PER_WINDOW} uploads in {site.UPLOAD_WINDOW_S // 60} "
                                "minutes. Wait a few minutes.")
        batch = pu.new_drive_batch(tenant)
        job_id = jobs.enqueue(tenant, "photos_import", {"kind": "drive", "folder": folder, "batch": batch, "by": email},
                              by=email, target=pu.JOB_TARGET)
        jobs.audit(tenant, by=email, action="photos_pull_drive", target=pu.JOB_TARGET,
                   detail={"folder": folder, "job_id": job_id})
        return redirect(url_for("photos_home", job=job_id) + "#jobs", code=303)

    @app.route("/photos/<pid>/edit", methods=["POST"])
    def photo_edit(pid):
        if not pl.ID_RE.match(pid):
            abort(404)
        email = g.reviewer_email
        action = request.form.get("action") or ""
        form = {k: request.form.get(k) for k in ("features", "shot", "mood", "model", "value", "x", "y")
                if k in request.form}
        back = request.form.get("back") or ""
        try:
            changes = pu.edit_photo(tenant, pid, action, form, by=email)
        except pl.PhotoNotFound:
            abort(404)
        except pu.PhotoEditRefused as exc:
            args = {k: v[0] for k, v in urllib.parse.parse_qs(back).items() if v}
            return render(tenant, args, status=400, error=f"{pid}: {exc}")
        for action_name, detail in changes:
            jobs.audit(tenant, by=email, action=action_name, target=f"photo:{pid}", detail=detail)
        return redirect(_back_url(back) + f"#p-{pid}", code=303)
