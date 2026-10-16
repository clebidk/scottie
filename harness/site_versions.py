"""Cycle 112: the review site's Versions and Showcase pages (data in
harness/versions.py).

  /versions                  the timeline, newest cycle first: date, FIXLOG
                             summary, runs, pass rate, median cost, up to 3
                             first screens (each links to its run) and store
                             links
  /showcase                  what the current version can do: one card per
                             capability with its newest passing example
  /versions/thumb/<source>/<run>/<page>   a first-screen thumbnail (out/ or an
                             archive), made on first use and cached
  /versions/archive/<source>/<run>/<page>/[file]   an archived run page,
                             served read-only

`harness versions export --out <dir>` writes both pages and their thumbnails
as static files (export below): no login, run links point to the review site.
"""
import html
import shutil
from pathlib import Path

from flask import Response, abort, send_file, url_for

from . import capture, site, site_ui, versions
from . import serve as _serve

e = html.escape
icon, chip = site_ui.icon, site_ui.chip

CSS = """
.ver-row{display:grid;grid-template-columns:minmax(200px,300px) 1fr;gap:16px 24px;align-items:start}
.ver-row h2{font-size:1.1rem;margin:0}
.ver-row .ver-sum{margin:6px 0 8px;font-size:.88rem}
.ver-row.thin{padding:12px 20px}
.ver-thumbs,.sc-cards{display:flex;flex-wrap:wrap;gap:12px}
.ver-thumb{display:flex;flex-direction:column;gap:4px;width:124px;font-size:.72rem;color:var(--muted);
  text-decoration:none;overflow-wrap:anywhere}
.ver-thumb img,.sc-card img{display:block;width:120px;height:260px;object-fit:cover;object-position:top;
  border:1px solid var(--line);border-radius:var(--r);background:#E9DFCD}
.sc-card{width:300px;display:flex;flex-direction:column;gap:8px;margin:0}
.card + .card.sc-card{margin-top:0}
.sc-card h3{font-size:.95rem;margin:0}
.sc-body{display:flex;gap:12px;align-items:flex-start}
.sc-card .empty{padding:24px 12px;font-size:.85rem}
@media (max-width:720px){.ver-row{grid-template-columns:1fr}.sc-card{width:100%}}
"""


def _money(value):
    return "-" if value is None else f"${value:.2f}"


def _pct(value):
    return "-" if value is None else f"{value:.0%}"


def _live(live):
    if not live:
        return ""
    label = "Store page (replaced later)" if live.get("replaced") else "Store page"
    return (f'<a class="small" href="{e(live["url"])}" target="_blank" rel="noopener">{icon("external")}'
            f'{e(label)}</a>')


def _thumb(links, run, page):
    src = links["thumb"](run, page)
    img = (f'<img src="{e(src)}" loading="lazy" width="120" height="260" alt="First screen of {e(run["run_id"])}">'
           if src else '<div class="empty small">no picture</div>')
    return f'<a href="{e(links["run"](run, page))}" target="_blank" rel="noopener">{img}</a>'


def _cycle_title(cycle):
    return f"Cycle {cycle}" if cycle else "Before the first dated cycle"


def versions_body(rows, current, links):
    head = (f'<div class="page-head"><div><h1>Versions</h1><p class="sub">Each harness version (a FIXLOG cycle) '
            f'and the runs it made, newest first. Current version: cycle {e(current.get("cycle") or "-")}, '
            f'commit <code>{e(current.get("commit") or "-")}</code> ({e(current.get("date") or "-")}).</p></div>'
            f'<div class="actions"><a class="btn ghost" href="{e(links["showcase"])}">{icon("eye")}'
            'What the current version can do</a></div></div>'
            '<p class="muted small">A run made before cycle 112 has a derived version: the newest cycle that was on '
            'master when the run started. Pass rate: runs that passed the gates (state needs review, approved, '
            'published or rejected). Cost: the run\'s model cost from the spend ledger. Pictures: the first phone '
            'screen of up to three passing runs.</p>')
    cards = []
    for row in rows:
        title = (f'<h2>{e(_cycle_title(row["cycle"]))}</h2>'
                 + (chip(row["milestone"], "live") if row["milestone"] else "")
                 + f'<div class="muted small">{e(row["date"] or "")}</div>')
        summary = f'<p class="ver-sum">{e(row["summary"])}</p>' if row["summary"] else ""
        if not row["runs"]:
            cards.append(f'<div class="card ver-row thin" id="cycle-{e(row["cycle"])}"><div>{title}</div>'
                         f'<div>{summary}<p class="muted small">No runs recorded on this cycle.</p></div></div>')
            continue
        facts = (f'<div class="facts"><span>Runs <b>{row["runs"]}</b></span>'
                 f'<span>Passed <b>{_pct(row["pass_rate"])}</b></span>'
                 f'<span>Median cost <b>{_money(row["median_cost"])}</b></span>'
                 + (f'<span>Derived <b>{row["derived"]}</b></span>' if row["derived"] else "") + "</div>")
        thumbs = "".join(
            f'<div class="ver-thumb">{_thumb(links, x["run"], x["page"])}<span>{e(x["run"]["run_id"])} '
            f'&middot; {e(x["page"]["page"])}</span>{_live(x["live"])}</div>' for x in row["examples"])
        thumbs = thumbs or '<p class="muted small">No passing run.</p>'
        cards.append(f'<div class="card ver-row" id="cycle-{e(row["cycle"])}"><div>{title}{summary}{facts}</div>'
                     f'<div class="ver-thumbs">{thumbs}</div></div>')
    return head + ("".join(cards) or '<div class="empty">No runs yet.</div>')


def showcase_body(data, links):
    current = data["current"]
    head = (f'<div class="page-head"><div><h1>What the current version can do</h1><p class="sub">Cycle '
            f'{e(current.get("cycle") or "-")}, commit <code>{e(current.get("commit") or "-")}</code> '
            f'({e(current.get("date") or "-")}). One card per page type the harness makes, with its newest passing '
            'run. A card from an older cycle says so.</p></div>'
            f'<div class="actions"><a class="btn ghost" href="{e(links["versions"])}">{icon("tag")}All versions'
            '</a></div></div>')
    cards = []
    for card in data["cards"]:
        x = card["example"]
        if x is None:
            body = '<div class="empty">Example missing: not generated on this version yet.</div>'
        else:
            run, page = x["run"], x["page"]
            older = run.get("cycle") != current.get("cycle")
            cycle = chip(f'cycle {run.get("cycle") or "-"}' + (" (older)" if older else ""), "" if older else "live")
            jev = f'<span>Jev <b>{run["jev"]:.2f}</b></span>' if run.get("jev") is not None else ""
            body = (f'<div class="sc-body">{_thumb(links, run, page)}<div>{cycle}'
                    f'<p class="small"><a href="{e(links["run"](run, page))}" target="_blank" rel="noopener">'
                    f'{e(run["run_id"])}</a></p>'
                    f'<div class="facts"><span>Cost <b>{_money(x["cost"])}</b></span>{jev}</div>'
                    f'{_live(x["live"])}</div></div>' + site_ui.quality_facts(page.get("quality")))
        cards.append(f'<div class="card sc-card" id="cap-{e(card["id"])}"><h3>{e(card["title"])}</h3>{body}</div>')
    return head + f'<div class="sc-cards">{"".join(cards)}</div>'


def _site_links():
    def thumb(run, page):
        return url_for("versions_thumb", source=run["source"], run_id=run["run_id"], page=page["page"])

    def run_url(run, page):
        if run["source"] == "out":
            return url_for("generation", run_id=run["run_id"], page=page["page"])
        return url_for("archive_page", source=run["source"], run_id=run["run_id"], page=page["page"])

    return {"thumb": thumb, "run": run_url, "versions": url_for("versions_home"),
            "showcase": url_for("showcase_home")}


def register(app, tenant):
    """The Versions routes; called by site.register (login, Origin check and
    form token run first)."""

    @app.route("/versions")
    def versions_home():
        body = versions_body(versions.timeline(tenant), versions.current(), _site_links())
        return Response(site_ui.shell("Versions", body, extra_css=CSS), mimetype="text/html")

    @app.route("/showcase")
    def showcase_home():
        body = showcase_body(versions.showcase(tenant), _site_links())
        return Response(site_ui.shell("Showcase", body, extra_css=CSS), mimetype="text/html")

    @app.route("/versions/thumb/<source>/<run_id>/<page>")
    def versions_thumb(source, run_id, page):
        if versions.page_dir(tenant, source, run_id, page) is None:
            abort(404)
        path = versions.thumb(tenant, source, run_id, page, browser=capture.find_browser())
        if path is None:
            return site._thumb_placeholder()
        resp = send_file(path, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=300"
        return resp

    @app.route("/versions/archive/<source>/<run_id>/<page>/", defaults={"name": "index.html"})
    @app.route("/versions/archive/<source>/<run_id>/<page>/<path:name>")
    def archive_page(source, run_id, page, name):
        if source == "out":
            abort(404)
        folder = versions.page_dir(tenant, source, run_id, page)
        path = _serve._safe_path(folder, name) if folder is not None else None
        if path is None or not path.is_file():
            abort(404)
        return send_file(path)


# ---------------------------------------------------------------------------
# static export
# ---------------------------------------------------------------------------

def _static_page(title, body, nav):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><title>{e(title)}</title>'
            f'<link rel="stylesheet" href="{site_ui.FONTS_HREF}"><style>{site_ui.CSS}{CSS}</style></head>'
            f'<body><main class="content" style="margin:0 auto">{nav}{body}</main></body></html>')


def export(tenant, out_dir, *, review_url, log=print):
    """index.html (Versions), showcase.html and thumbs/*.jpg in out_dir.
    Run links point to the review site (`review_url`, login needed); store
    links are public. Returns the count of thumbnails written."""
    out = Path(out_dir)
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    browser = capture.find_browser()
    review_url = review_url.rstrip("/")
    written = {}

    def thumb(run, page):
        key = (run["source"], run["run_id"], page["page"])
        if key not in written:
            path = versions.thumb(tenant, *key, browser=browser)
            if path is not None:
                shutil.copyfile(path, out / "thumbs" / path.name)
            written[key] = f"thumbs/{path.name}" if path is not None else ""
        return written[key]

    def run_url(run, page):
        if run["source"] == "out":
            return f"{review_url}/gen/{run['run_id']}/{page['page']}"
        return f"{review_url}/versions/archive/{run['source']}/{run['run_id']}/{page['page']}/"

    links = {"thumb": thumb, "run": run_url, "versions": "index.html", "showcase": "showcase.html"}
    nav = ('<p class="muted small">Exported from the review site. Run links open the review site (login needed); '
           'store links are public.</p>')
    current = versions.current()
    (out / "index.html").write_text(_static_page("Versions", versions_body(versions.timeline(tenant), current, links),
                                                 nav))
    (out / "showcase.html").write_text(_static_page("Showcase", showcase_body(versions.showcase(tenant), links), nav))
    count = sum(1 for v in written.values() if v)
    log(f"versions export: index.html, showcase.html, {count} thumbnail(s) -> {out}")
    return count
