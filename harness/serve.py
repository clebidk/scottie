"""Cycle 26: `harness serve --tenant <t> [--host 127.0.0.1] [--port 4870]` --
the reviewer web app. Plain HTML/CSS, no CDN, no JS framework (a few lines of
inline JS for the desktop/mobile iframe-width toggle).

Every action a reviewer takes goes through the SAME functions the CLI uses --
harness/runstate.py's approve/reject/request_changes, harness/cli.py's
record_score, harness/revise.py's revise_page (launched as a background
subprocess) -- this module builds HTML and handles auth, nothing else.

Auth (see build_app / _authenticate): Cloudflare Access header when the
tenant env turns that on, else HTTP basic auth against tenant.yaml's
`reviewers` list and the tenant env's REVIEW_PASSWORD. No env value is ever
logged or rendered.
"""
import hmac
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from flask import Flask, Response, abort, g, redirect, request, send_file, url_for
from PIL import Image

from . import abevents
from . import abtest
from . import asset_review
from . import ground as ground_mod
from . import notify
from . import render as render_mod
from . import runstate
from . import textutil
from .evals import record_score
from .config import REPO_ROOT

SCORE_FIELDS = ("angle", "brand", "claims", "publish")
SCORE_LABELS = {"angle": "Angle", "brand": "Brand", "claims": "Claims", "publish": "Publish"}


class ReviewServerNotConfigured(Exception):
    pass


# ---------------------------------------------------------------------------
# Path safety (Cycle 22 finding R36's textutil.safe_filename, reused here for
# a URL path instead of a filename): every component of a request-supplied
# path is reduced to one harmless path component before it ever touches the
# filesystem, and the final resolved path must still be inside `base_dir` --
# so "assets/../../../etc/passwd" can never escape, and neither can a
# component that IS itself ".." (safe_filename maps that to its fallback).
# ---------------------------------------------------------------------------

def _safe_path(base_dir, rel_path):
    base_dir = Path(base_dir).resolve()
    if not base_dir.is_dir():
        return None
    parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return None
    safe_parts = [textutil.safe_filename(p) for p in parts]
    candidate = base_dir.joinpath(*safe_parts).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _is_run_dir(path):
    """A run dir is one containing state.json or ad_brief.json -- everything
    else under a tenant's out/ (e.g. FRIDAY-2026-09-11, _archive-test-runs)
    is not a run this harness produced and must never show up in the run
    list or be reachable by run id."""
    return path.is_dir() and (runstate.state_path(path).exists() or (path / "ad_brief.json").exists())


def _run_dir_or_404(tenant, run_id):
    candidate = _safe_dir(tenant.out_dir, run_id)
    if candidate is None or not _is_run_dir(candidate):
        abort(404)
    return candidate


def _looks_like_test_run(tenant, run_dir, state):
    """Cycle 26b (bug 2): True for a run the test suite produced (state.json's
    `dry_run: true`, set going forward by pipeline.prepare_run -- or, for a
    run made before that fix, no `input_tokens=` line anywhere in its run
    log, since every real call records one), or for a run with no rendered
    page dir at all (a STOP, or a run that never got past the claims gate).
    Both are hidden from the run list by default."""
    if state.get("dry_run"):
        return True
    log_path = tenant.runs_dir / f"{run_dir.name}.log"
    if not log_path.exists() or "input_tokens=" not in log_path.read_text():
        return True
    if not any((run_dir / page).is_dir() for page in state.get("pages", {})):
        return True
    return False


def _safe_dir(base_dir, name):
    base_dir = Path(base_dir).resolve()
    safe_name = textutil.safe_filename(str(name or ""))
    candidate = (base_dir / safe_name).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        return None
    if not candidate.is_dir():
        return None
    return candidate


# ---------------------------------------------------------------------------
# REVIEW.md scraping -- cost and "ad claims not repeated" count are only ever
# written there (harness/cli.py's write_review_md), not persisted as JSON, so
# the run list reads them back out with a couple of small, tolerant regexes
# rather than duplicating write_review_md's own bookkeeping in this module.
# A run with no REVIEW.md (STOPped before it got that far) just shows blanks.
# ---------------------------------------------------------------------------

_COST_RE = re.compile(r"estimated_cost_usd \(estimate\): \$([\d.]+)")
_NOT_REPEATED_HEADER = "**AD CLAIMS NOT REPEATED ON PAGE"


def _parse_review_md(text):
    """(cost, not_repeated) -- cost is a float or None; not_repeated is the
    list of "AD CLAIMS NOT REPEATED ON PAGE" bullet lines (cli.write_review_md's
    `ad_not_repeated`), each with its leading "- " stripped. Empty list when
    the run's REVIEW.md has no such section (nothing to fix, or STOPped
    before render)."""
    cost = None
    m = _COST_RE.search(text or "")
    if m:
        cost = float(m.group(1))
    not_repeated = []
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(_NOT_REPEATED_HEADER):
            for later in lines[i + 1:]:
                if not later.strip():
                    break
                if later.startswith("- "):
                    not_repeated.append(later[2:])
            break
    return cost, not_repeated


def _load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def _run_summary(tenant, run_dir):
    state = runstate.load_state(run_dir)
    review_md = ""
    review_path = run_dir / "REVIEW.md"
    if review_path.exists():
        review_md = review_path.read_text()
    cost, not_repeated = _parse_review_md(review_md)
    ad_brief = _load_json(run_dir / "ad_brief.json")
    facts_pack = _load_json(run_dir / "facts_pack.json")
    gate_summary = "PASS" if state.get("state") != "generated" else "STOPPED (never reached review)"
    actions = [
        f"{h.get('state')} by {h.get('by')}" + (f" ({h.get('note')})" if h.get("note") else "")
        for h in state.get("history", []) if h.get("by") not in (None, "system")
    ]
    return {
        "run_id": run_dir.name,
        "state": state.get("state"),
        "ad_name": ad_brief.get("source_file") or run_dir.name,
        "product": (facts_pack.get("product") or {}).get("name") or "",
        "cartridges": sorted(state.get("pages", {})),
        "cost": cost,
        "gate_summary": gate_summary,
        "not_repeated": len(not_repeated),
        "actions": actions,
        "mtime": run_dir.stat().st_mtime,
    }


def _scores_for_run(tenant, run_dir):
    """{page: [entry, ...]} from evals/scores.jsonl for this run, newest
    last -- entries with no "page" (every score `harness score` itself wrote
    before Cycle 26) are ignored here; the review site only ever reads its
    own per-page scores back."""
    out = {}
    path = tenant.evals_path
    if not path.exists():
        return out
    run_dir_str = str(run_dir)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("run_dir") != run_dir_str or not entry.get("page"):
            continue
        out.setdefault(entry["page"], []).append(entry)
    return out


# ---------------------------------------------------------------------------
# HTML -- plain string building (same craft as cli.write_review_md's
# markdown), html.escape on every value that came from disk/reviewer input.
# ---------------------------------------------------------------------------

PAGE_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1100px;margin:0 auto;padding:24px;
  color:#1a1a1a;background:#fff;line-height:1.5}
h1{font-size:1.4rem} h2{font-size:1.15rem;margin-top:2em;border-bottom:1px solid #ddd;padding-bottom:.3em}
table{border-collapse:collapse;width:100%;margin:1em 0}
th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:.92rem;vertical-align:top}
th{background:#f4f4f4}
a{color:#0b5fff}
.state-needs_review{color:#a66a00} .state-approved{color:#0a7a28} .state-rejected{color:#b00020}
.state-generated{color:#888} .state-published{color:#0a7a28} .state-changes_requested{color:#a66a00}
.page-block{border:1px solid #ddd;border-radius:6px;padding:16px;margin:1.2em 0}
.page-block iframe{width:1200px;max-width:100%;height:700px;border:1px solid #ccc;display:block}
.page-block iframe.mobile{width:390px}
.toggle-btn{margin:.5em 0;padding:4px 10px;cursor:pointer}
form.feedback label{display:inline-block;min-width:70px}
form.feedback select{margin-right:14px}
textarea{width:100%;box-sizing:border-box;min-height:90px;font-family:inherit}
.help{color:#666;font-size:.85rem}
.actions button{margin-right:8px;padding:6px 14px;cursor:pointer}
.history{font-size:.85rem;color:#444}
.source-ad{background:#fafafa;border:1px solid #ddd;padding:12px;border-radius:6px;white-space:pre-wrap}
.image-grid{display:flex;flex-wrap:wrap;gap:12px}
.image-card{border:1px solid #ddd;border-radius:6px;padding:8px;width:220px;font-size:.85rem}
.image-card img{width:100%;height:150px;object-fit:contain;background:#f4f4f4;display:block}
.image-card.excluded{opacity:.45}
.image-card .asset-id{font-family:monospace;font-size:.75rem;color:#666;word-break:break-all}
.image-card .excluded-tag{color:#b00020;font-weight:bold}
.image-card textarea{min-height:50px}
.image-card select,.image-card input[type=text]{width:100%;box-sizing:border-box;margin:2px 0}
.pool-counts{color:#666}
"""

TOGGLE_JS = """
function pkToggleWidth(id, mode) {
  var frame = document.getElementById(id);
  if (!frame) return;
  frame.className = (mode === "mobile") ? "mobile" : "";
}
"""


def _page_shell(title, body):
    return (
        f"<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style>"
        f"<script>{TOGGLE_JS}</script></head><body>{body}</body></html>"
    )


def _render_run_list(tenant, runs, *, show_test=False, hidden_count=0):
    rows = []
    for r in runs:
        cost = f"${r['cost']:.4f}" if r["cost"] is not None else "-"
        rows.append(
            "<tr>"
            f"<td><a href=\"{url_for('run_detail', run_id=r['run_id'])}\">{html.escape(r['run_id'])}</a></td>"
            f"<td class=\"state-{html.escape(r['state'] or '')}\">{html.escape(r['state'] or '')}</td>"
            f"<td>{html.escape(r['ad_name'])}</td>"
            f"<td>{html.escape(r['product'])}</td>"
            f"<td>{html.escape(', '.join(r['cartridges']))}</td>"
            f"<td>{cost}</td>"
            f"<td>{html.escape(r['gate_summary'])}</td>"
            f"<td>{r['not_repeated']}</td>"
            f"<td class=\"history\">{'; '.join(html.escape(a) for a in r['actions']) or '-'}</td>"
            "</tr>"
        )
    if show_test:
        toggle = f"<p><a href=\"{url_for('run_list')}\">Hide test runs</a></p>"
    elif hidden_count:
        toggle = f"<p><a href=\"{url_for('run_list', show_test=1)}\">Show test runs ({hidden_count} hidden)</a></p>"
    else:
        toggle = ""
    body = (
        f"<h1>{html.escape(tenant.display_name)} -- runs</h1>"
        f"<p><a href=\"{url_for('image_library')}\">Image library</a></p>"
        + toggle
        + "<table><thead><tr><th>Run</th><th>State</th><th>Ad</th><th>Product</th>"
        "<th>Cartridges</th><th>Cost</th><th>Gate</th><th>Not repeated</th><th>Reviewer actions</th>"
        "</tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan=9>No runs yet.</td></tr>") + "</tbody></table>"
    )
    return _page_shell(f"{tenant.display_name} runs", body)


def _select(name, current=None):
    options = "".join(
        f"<option value=\"{v}\"{' selected' if str(current) == str(v) else ''}>{v}</option>" for v in range(1, 6)
    )
    return f"<select name=\"{name}\"><option value=\"\">-</option>{options}</select>"


def _feedback_form(run_id, page, existing_scores):
    latest = existing_scores[-1] if existing_scores else {}
    action_url = url_for("page_action", run_id=run_id, page=page)
    selects = "".join(
        f"<label>{SCORE_LABELS[f]}</label>{_select(f, latest.get(f))} " for f in SCORE_FIELDS
    )
    return (
        f"<form class=\"feedback\" method=\"post\" action=\"{action_url}\">"
        f"<div>{selects}</div>"
        "<p><textarea name=\"notes\" placeholder=\"Notes and cuts\"></textarea></p>"
        "<p class=\"help\">Prefix a line with <code>cut:</code> and paste the exact sentence to remove it.</p>"
        "<p><label><input type=\"checkbox\" name=\"regenerate\" value=\"1\"> Regenerate now (runs "
        "<code>harness revise</code> in the background after Request changes)</label></p>"
        "<div class=\"actions\">"
        "<button type=\"submit\" name=\"action\" value=\"approve\">Approve page</button>"
        "<button type=\"submit\" name=\"action\" value=\"changes\">Request changes</button>"
        "<button type=\"submit\" name=\"action\" value=\"reject\">Reject page (rejects the whole run)</button>"
        "</div></form>"
    )


def _history_for_page(state, page):
    lines = []
    for entry in state.get("history", []):
        note = entry.get("note") or ""
        if page and f"page={page}" not in note and entry.get("state") not in ("needs_review",) and note:
            continue
        lines.append(f"{entry.get('state')} by {entry.get('by')} at {entry.get('at')}" + (f" -- {note}" if note else ""))
    return lines


def _render_source_ad(tenant, run_id, ad_brief):
    input_type = ad_brief.get("input_type")
    if input_type == "still":
        src = url_for("source_image", run_id=run_id)
        return f"<img src=\"{src}\" alt=\"source ad\" style=\"max-width:100%\">"
    transcript = ad_brief.get("transcript_or_text") or ""
    return f"<div class=\"source-ad\">{html.escape(transcript)}</div>"


def _render_run_detail(tenant, run_dir):
    state = runstate.load_state(run_dir)
    ad_brief = _load_json(run_dir / "ad_brief.json")
    scores = _scores_for_run(tenant, run_dir)
    pages = sorted(state.get("pages", {}))

    review_path = run_dir / "REVIEW.md"
    _cost, not_repeated = _parse_review_md(review_path.read_text() if review_path.exists() else "")
    not_repeated_html = "".join(f"<li>{html.escape(item)}</li>" for item in not_repeated)

    blocks = []
    for page in pages:
        review_url = url_for("page_review", run_id=run_dir.name, page=page)
        iframe_id = f"frame-{page}"
        page_state = state["pages"].get(page)
        history_html = "".join(f"<li>{html.escape(h)}</li>" for h in _history_for_page(state, page)) or "<li>none yet</li>"
        blocks.append(
            f"<div class=\"page-block\"><h2>{html.escape(page)} "
            f"<span class=\"state-{html.escape(page_state or '')}\">[{html.escape(page_state or '')}]</span></h2>"
            f"<p><button class=\"toggle-btn\" onclick=\"pkToggleWidth('{iframe_id}','desktop')\">Desktop</button> "
            f"<button class=\"toggle-btn\" onclick=\"pkToggleWidth('{iframe_id}','mobile')\">Mobile</button></p>"
            f"<iframe id=\"{iframe_id}\" src=\"{review_url}\"></iframe>"
            f"<h3>Feedback history</h3><ul>{history_html}</ul>"
            f"{_feedback_form(run_dir.name, page, scores.get(page, []))}"
            "</div>"
        )

    every_page_scored = pages and all(scores.get(p) for p in pages)
    approve_all = (
        f"<form method=\"post\" action=\"{url_for('approve_all', run_id=run_dir.name)}\">"
        "<button type=\"submit\">Approve all</button></form>"
        if every_page_scored else ""
    )

    body = (
        f"<p><a href=\"{url_for('run_list')}\">&laquo; all runs</a></p>"
        f"<h1>{html.escape(run_dir.name)}</h1>"
        f"<p>State: <span class=\"state-{html.escape(state.get('state') or '')}\">{html.escape(state.get('state') or '')}</span></p>"
        f"<h2>Source ad</h2>{_render_source_ad(tenant, run_dir.name, ad_brief)}"
        "<h2>Ad brief</h2>"
        f"<p><b>Hook:</b> {html.escape(ad_brief.get('hook') or '')}</p>"
        f"<p><b>Angle:</b> {html.escape(ad_brief.get('angle') or '')}</p>"
        f"<p><b>Claims not repeated:</b></p><ul>{not_repeated_html or '<li>none</li>'}</ul>"
        f"{approve_all}"
        "<h2>Pages</h2>" + "".join(blocks)
    )
    return _page_shell(f"{run_dir.name} -- review", body)


# ---------------------------------------------------------------------------
# Background revise subprocess
# ---------------------------------------------------------------------------

def _launch_revise_background(tenant, run_dir, page, by):
    runstate.set_revise_status(run_dir, page=page, status="running")
    tenant.runs_dir.mkdir(parents=True, exist_ok=True)
    log_path = tenant.runs_dir / f"{run_dir.name}-revise-{page}.background.log"
    with open(log_path, "ab") as log_fh:
        subprocess.Popen(
            [sys.executable, "-m", "harness.cli", "revise", str(run_dir),
             "--page", page, "--by", by, "--tenant", tenant.name],
            stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(REPO_ROOT),
        )


# ---------------------------------------------------------------------------
# Image library (cycle 36): a reviewer looks at every image a product could
# ever place on a page (ground.full_asset_pool -- uncapped, unlike
# facts_for()'s own `assets`), writes alt text or excludes one, and that
# flows into tenants/<t>/brand/asset-review.json (harness/asset_review.py),
# which ground.facts_for() applies to the pool the writer actually sees.
# ---------------------------------------------------------------------------

IMAGES_PER_PAGE = 48
# Display order for this page only -- distinct from ground.DRIVE_ASSET_TIERS,
# which orders Drive kinds for hero/section *selection*, not for how a
# reviewer browses them page by page.
_DRIVE_KIND_DISPLAY_ORDER = {"lifestyle": 0, "interior": 0, "installation": 1, "render": 2}
_SOURCE_DISPLAY_ORDER = {"shopify": 0, "drive": 1, "listicle": 2}

_THUMB_PLACEHOLDER_SVG = (
    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"480\" height=\"360\">"
    "<rect width=\"100%\" height=\"100%\" fill=\"#eee\"/>"
    "<text x=\"50%\" y=\"50%\" text-anchor=\"middle\" fill=\"#999\" font-family=\"sans-serif\" "
    "font-size=\"16\">could not load</text></svg>"
)


def _asset_source(tenant):
    return ground_mod.LocalFactsSource(tenant.claims_dir)


def _product_or_404(source, product_slug):
    product = next((p for p in source.active_products() if p["slug"] == product_slug), None)
    if product is None:
        abort(404)
    return product


def _image_sort_key(asset):
    """Shopify first, then Drive ordered lifestyle/interior, installation,
    render, other, then listicle-pack -- the reviewer brief's own order,
    stable within each group so assets keep the manifest's order."""
    if asset["source"] == "drive":
        kind_rank = _DRIVE_KIND_DISPLAY_ORDER.get(asset.get("kind"), 3)
    else:
        kind_rank = 0
    return (_SOURCE_DISPLAY_ORDER.get(asset["source"], 9), kind_rank)


def _render_image_library(tenant):
    source = _asset_source(tenant)
    config = tenant.claims_config
    overrides = asset_review.load_asset_review(tenant.brand_dir).get("assets") or {}
    rows = []
    for product in source.active_products():
        pool = ground_mod.full_asset_pool(source, product, config)
        reviewed = sum(1 for a in pool if a["id"] in overrides)
        excluded = sum(1 for a in pool if overrides.get(a["id"], {}).get("excluded"))
        product_url = url_for("image_library_product", product_slug=product["slug"])
        rows.append(
            f"<tr><td><a href=\"{product_url}\">{html.escape(product['name'])}</a></td>"
            f"<td>{len(pool)}</td><td>{reviewed}</td><td>{excluded}</td></tr>"
        )
    body = (
        f"<h1>{html.escape(tenant.display_name)} -- image library</h1>"
        "<table><thead><tr><th>Product</th><th>Total</th><th>Reviewed</th><th>Excluded</th></tr></thead>"
        "<tbody>" + ("".join(rows) or "<tr><td colspan=4>No active products.</td></tr>") + "</tbody></table>"
    )
    return _page_shell(f"{tenant.display_name} image library", body)


def _image_card(asset, override):
    excluded = bool(override and override.get("excluded"))
    asset_id = html.escape(asset["id"])
    alt_value = html.escape((override or {}).get("alt") or "")
    note_value = html.escape((override or {}).get("note") or "")
    default_alt = html.escape(asset.get("alt") or "")
    title = html.escape(asset.get("title") or "")
    tag = "<div class=\"excluded-tag\">EXCLUDED</div>" if excluded else ""
    ai_badge = " <b title=\"AI-generated render\">[AI]</b>" if asset.get("ai_generated") else ""
    return (
        f"<div class=\"image-card{' excluded' if excluded else ''}\">"
        f"{tag}"
        f"<img loading=\"lazy\" src=\"{url_for('image_thumb', asset_id=asset['id'])}\" alt=\"\">"
        f"<div class=\"asset-id\">{asset_id}</div>"
        f"<div>{html.escape(asset['source'])} / {html.escape(asset.get('kind') or '')} / "
        f"{html.escape(asset.get('model') or '')}{ai_badge}</div>"
        + (f"<div>{title}</div>" if title else "")
        + f"<textarea name=\"alt:{asset_id}\" placeholder=\"{default_alt}\">{alt_value}</textarea>"
        f"<select name=\"status:{asset_id}\">"
        f"<option value=\"keep\"{'' if excluded else ' selected'}>keep</option>"
        f"<option value=\"exclude\"{' selected' if excluded else ''}>exclude</option>"
        "</select>"
        f"<input type=\"text\" name=\"note:{asset_id}\" value=\"{note_value}\" placeholder=\"cropped, wrong product, ...\">"
        "</div>"
    )


def _render_image_library_product(tenant, product_slug, *, page, source_filter, saved=None):
    source = _asset_source(tenant)
    product = _product_or_404(source, product_slug)
    pool = ground_mod.full_asset_pool(source, product, tenant.claims_config)
    if source_filter in ("shopify", "drive", "listicle"):
        pool = [a for a in pool if a["source"] == source_filter]
    pool.sort(key=_image_sort_key)

    overrides = asset_review.load_asset_review(tenant.brand_dir).get("assets") or {}

    total = len(pool)
    page_count = max(1, -(-total // IMAGES_PER_PAGE))
    page = max(1, min(page, page_count))
    start = (page - 1) * IMAGES_PER_PAGE
    page_assets = pool[start:start + IMAGES_PER_PAGE]

    ids_field = html.escape(",".join(a["id"] for a in page_assets))
    cards = "".join(_image_card(a, overrides.get(a["id"])) for a in page_assets)

    filters = " ".join(
        f"<a href=\"{url_for('image_library_product', product_slug=product_slug, source=s)}\">{s}</a>"
        for s in ("all", "shopify", "drive", "listicle")
    )
    pager = " ".join(
        f"<a href=\"{url_for('image_library_product', product_slug=product_slug, page=p, source=source_filter)}\">{p}</a>"
        for p in range(1, page_count + 1)
    )
    saved_note = f"<p>Saved {saved} image(s).</p>" if saved else ""
    action_url = url_for("image_library_save", product_slug=product_slug, page=page, source=source_filter)
    submit = "<button type=\"submit\">Save this page</button>"
    body = (
        f"<h1>{html.escape(product['name'])} -- images</h1>"
        f"<p class=\"pool-counts\">{total} image(s) in this view (page {page} of {page_count})</p>"
        f"<p>{filters}</p><p>{pager}</p>"
        + saved_note
        + f"<form method=\"post\" action=\"{action_url}\">"
        f"<input type=\"hidden\" name=\"ids\" value=\"{ids_field}\">"
        + submit
        + f"<div class=\"image-grid\">{cards}</div>"
        + submit
        + "</form>"
    )
    return _page_shell(f"{product['name']} images", body)


def _save_image_library(tenant, product_slug):
    """Only ids named in the posted `ids` field are touched -- everything
    else already in asset-review.json (other pages, other products) is left
    alone. An id with nothing to say (empty alt, status=keep, empty note)
    and no pre-existing override writes nothing."""
    source = _asset_source(tenant)
    product = _product_or_404(source, product_slug)
    pool = {a["id"]: a for a in ground_mod.full_asset_pool(source, product, tenant.claims_config)}

    ids = [i for i in (request.form.get("ids") or "").split(",") if i]
    overrides = dict(asset_review.load_asset_review(tenant.brand_dir).get("assets") or {})
    now = asset_review.now_iso()
    saved = 0
    for asset_id in ids:
        asset = pool.get(asset_id)
        if asset is None:
            continue  # no longer in this product's pool -- nothing sane to write
        alt = (request.form.get(f"alt:{asset_id}") or "").strip()
        note = (request.form.get(f"note:{asset_id}") or "").strip()
        excluded = request.form.get(f"status:{asset_id}") == "exclude"
        if not (alt or excluded or note or asset_id in overrides):
            continue
        entry = {"alt": alt, "excluded": excluded, "note": note, "by": g.reviewer_email, "at": now}
        # Cycle 65: keep `harness images brandcheck`'s old-brand flag -- a
        # reviewer saving the page must not silently put the graphic back.
        previous = overrides.get(asset_id) or {}
        entry.update({k: previous[k] for k in ("old_brand", "old_brand_scores") if k in previous})
        if asset.get("source") == "shopify":
            entry["url"] = asset.get("url")
        overrides[asset_id] = entry
        saved += 1
    asset_review.save_asset_review(tenant.brand_dir, {"version": 1, "assets": overrides})
    return saved


def _thumb_placeholder():
    return Response(_THUMB_PLACEHOLDER_SVG, mimetype="image/svg+xml")


def _asset_pool_index(tenant):
    """{asset id: asset} across every active product's full_asset_pool.
    Recomputed per request -- this is low-traffic internal tooling, and the
    tenant's asset manifests are the source of truth, not worth caching
    across requests and risking staleness for."""
    source = _asset_source(tenant)
    config = tenant.claims_config
    index = {}
    for product in source.active_products():
        for asset in ground_mod.full_asset_pool(source, product, config):
            index.setdefault(asset["id"], asset)
    return index


def _image_thumb_response(tenant, asset_id):
    asset = _asset_pool_index(tenant).get(asset_id)
    if asset is None:
        abort(404)
    try:
        cache_dir = tenant.runs_dir / "asset-cache"
        thumb_path = cache_dir / f"{textutil.safe_filename(asset_id)}.thumb.jpg"
        if not thumb_path.exists():
            # render.download_asset's default fetch_url/drive_downloader
            # (http_fetch_bytes/ingest.download_drive_file) each carry their
            # own timeout (30s/60s) -- a dead Drive link fails that request,
            # it never hangs this route.
            downloaded = render_mod.download_asset(asset, cache_dir)
            if downloaded is None:
                return _thumb_placeholder()
            with Image.open(downloaded["path"]) as im:
                im = im.convert("RGB")
                im.thumbnail((480, 480), Image.LANCZOS)
                cache_dir.mkdir(parents=True, exist_ok=True)
                im.save(thumb_path, format="JPEG", quality=82)
        resp = send_file(thumb_path, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "max-age=86400"
        return resp
    except Exception:
        return _thumb_placeholder()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _unauthorized_response():
    resp = Response("Authentication required.\n", status=401)
    resp.headers["WWW-Authenticate"] = 'Basic realm="harness review"'
    return resp


def _authenticate(tenant):
    """Returns (email, None) on success, (None, Response) to short-circuit
    the request. Never puts REVIEW_PASSWORD (or any other env value) into a
    response body, a log line, or an exception message.

    Cycle 34: when REVIEW_TRUST_CF_ACCESS is on, the email header alone is
    not enough -- Cloudflare also sends Cf-Access-Jwt-Assertion on every
    authenticated request. Requiring that header to be present stops a
    trivial spoof of the email header against a process that is somehow
    reachable without Access in front.

    This is a presence check ONLY -- is Cf-Access-Jwt-Assertion non-empty --
    not a "JWT gate" in any cryptographic sense. It does not base64-decode
    the token, does not check issuer/audience/expiry, does not verify a
    signature, and makes no JWKS fetch or other network call. A guessed or
    replayed non-empty string in that header satisfies this check. Full CF
    Access JWT signature verification (JWKS) is backlog item #1 in
    docs/SWARM-2026-09-14-cycle34.md; until that lands, operators who need
    cryptographic verification should terminate TLS at Access and keep this
    process off the public internet.
    """
    trust_cf = (os.environ.get("REVIEW_TRUST_CF_ACCESS") or "").strip().lower() == "true"
    if trust_cf:
        cf_email = request.headers.get("Cf-Access-Authenticated-User-Email")
        cf_jwt = (request.headers.get("Cf-Access-Jwt-Assertion") or "").strip()
        if cf_email:
            if not cf_jwt:
                return None, Response(
                    "Cloudflare Access JWT assertion missing.\n", status=401
                )
            if runstate.find_reviewer(tenant, cf_email) is None:
                return None, Response("Not a listed reviewer for this tenant.\n", status=403)
            return cf_email, None
        # No CF header on this request even though CF trust is on -- fall
        # through to basic auth rather than silently granting access.

    password = os.environ.get("REVIEW_PASSWORD")
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        return None, _unauthorized_response()
    if runstate.find_reviewer(tenant, auth.username) is None:
        return None, Response("Not a listed reviewer for this tenant.\n", status=403)
    if not hmac.compare_digest(auth.password, password):
        return None, _unauthorized_response()
    return auth.username, None


def _same_origin(value, root):
    """True when `value` (an Origin or Referer) shares scheme+host+port with
    this app's url_root. Empty/missing values are not same-origin."""
    if not value or not root:
        return False
    value = value.strip()
    root = root.rstrip("/") + "/"
    # Origin has no path; Referer may. Compare on the absolute-prefix form.
    if value.rstrip("/") == root.rstrip("/"):
        return True
    return value.startswith(root)


def _check_post_origin():
    """Cycle 34 CSRF defense for cookie/Basic-auth browser POSTs: when the
    browser sends Origin or Referer, it must match this app. Requests with
    neither header (curl, scripts) are allowed -- operators use those; a
    cross-site form POST always includes Origin in modern browsers."""
    if request.method != "POST":
        return None
    root = request.url_root
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if not _same_origin(origin, root):
            return Response("Cross-origin POST refused.\n", status=403)
        return None
    if referer and not _same_origin(referer, root):
        return Response("Cross-origin POST refused.\n", status=403)
    return None


# ---------------------------------------------------------------------------
# Cycle 67: the A/B/C test beacon receiver (POST /e) -- the one public route
# ---------------------------------------------------------------------------

_LOOPBACK = ("127.0.0.1", "::1")


def _client_ip():
    """The visitor's IP. Behind a local reverse proxy / Cloudflare tunnel the
    socket peer is loopback, and only then is a forwarding header trusted."""
    peer = request.remote_addr or ""
    if peer in _LOOPBACK:
        forwarded = request.headers.get("CF-Connecting-IP") or (
            request.headers.get("X-Forwarded-For") or "").split(",")[0]
        if forwarded.strip():
            return forwarded.strip()
    return peer


def _handle_beacon(tenant, limiter, limiter_salt):
    """204 for a stored, duplicate or bot event (a client learns nothing
    from which); 400 malformed or unknown test/variant; 413 over
    MAX_BODY_BYTES; 429 over the per-IP rate. The IP itself is never stored
    or logged -- only salted, truncated hashes of it."""
    empty = lambda status: Response(status=status)  # noqa: E731
    if (request.content_length or 0) > abevents.MAX_BODY_BYTES:
        return empty(413)
    raw = request.stream.read(abevents.MAX_BODY_BYTES + 1)
    if len(raw) > abevents.MAX_BODY_BYTES:
        return empty(413)
    if abevents.is_bot(request.headers.get("User-Agent", "")):
        return empty(204)
    ip = _client_ip()
    if not limiter.allow(abevents.hash_ip(ip, limiter_salt)):
        return empty(429)
    event, _reason = abevents.parse_event(raw)
    if event is None:
        return empty(400)
    rec = abtest.find_test(tenant, event["t"])
    if not rec or rec.get("status") not in abtest.POOLED_STATUSES:
        return empty(400)
    if event["v"] not in {v.get("key") for v in rec.get("variants") or []}:
        return empty(400)
    conn = abevents.connect(abevents.db_path(tenant))
    try:
        iph = abevents.hash_ip(ip, abevents.ip_salt(conn))
        abevents.record_event(conn, test_id=event["t"], key=event["v"], event=event["e"],
                              vid=event["vid"], iph=iph)
    finally:
        conn.close()
    return empty(204)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def build_app(tenant):
    """Builds the Flask app for `tenant`. Refuses to build (raises
    ReviewServerNotConfigured) when REVIEW_PASSWORD is not set in the
    tenant's env -- basic auth is always the fallback path (even with
    REVIEW_TRUST_CF_ACCESS on, a request missing the CF header falls back to
    it), so a working password is required either way."""
    if not os.environ.get("REVIEW_PASSWORD"):
        raise ReviewServerNotConfigured(
            f"REVIEW_PASSWORD is not set in {tenant.env_path} -- the review site refuses to start "
            "without it. Add a line REVIEW_PASSWORD=<value> to that file (never print the value)."
        )

    app = Flask(__name__)
    beacon_limiter = abevents.RateLimiter()
    beacon_limiter_salt = os.urandom(16).hex()

    @app.before_request
    def _require_auth():
        # Cycle 67: storefront visitors' view/CTA events -- public by design,
        # checked and rate limited in _handle_beacon instead.
        if request.endpoint == "beacon":
            return None
        email, error = _authenticate(tenant)
        if error is not None:
            return error
        g.reviewer_email = email
        origin_error = _check_post_origin()
        if origin_error is not None:
            return origin_error

    @app.after_request
    def _security_headers(resp):
        # Cycle 34: deny framing so a cross-origin page cannot clickjack the
        # approve / reject / regenerate buttons (pairs with the Origin check).
        #
        # Cycle 34 fix: run_detail's own <iframe src="{review_url}"> embeds
        # page_review (see the route below) to show the reviewer a live
        # preview -- that embed is same-origin (this app framing its own
        # route), but DENY/frame-ancestors 'none' block same-origin framing
        # too, so the preview never rendered. Relax only this one route to
        # SAMEORIGIN / frame-ancestors 'self': still refuses any third-party
        # framing, just not the app's own.
        if request.endpoint == "page_review":
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
        else:
            resp.headers.setdefault("X-Frame-Options", "DENY")
            resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return resp

    @app.route("/")
    def run_list():
        show_test = request.args.get("show_test") == "1"
        runs = []
        hidden_count = 0
        if tenant.out_dir.is_dir():
            for run_dir in tenant.out_dir.iterdir():
                if not _is_run_dir(run_dir):
                    continue
                # A run dir always gets state.json first (pipeline.prepare_run) --
                # the ad_brief.json-only case in _is_run_dir is a defensive
                # fallback that in practice never happens; there is nothing
                # to render without state.json, so skip it outright.
                if not runstate.state_path(run_dir).exists():
                    continue
                state = runstate.load_state(run_dir)
                if _looks_like_test_run(tenant, run_dir, state):
                    hidden_count += 1
                    if not show_test:
                        continue
                runs.append(_run_summary(tenant, run_dir))
        runs.sort(key=lambda r: r["mtime"], reverse=True)
        return _render_run_list(
            tenant, runs, show_test=show_test, hidden_count=0 if show_test else hidden_count,
        )

    @app.route("/run/<run_id>")
    def run_detail(run_id):
        run_dir = _run_dir_or_404(tenant, run_id)
        return _render_run_detail(tenant, run_dir)

    @app.route("/run/<run_id>/review/<page>")
    def page_review(run_id, page):
        # Cycle 26b (bug 1): `<page>-review.html` only exists once `harness
        # review` has run for this page. A run made with `harness run` alone
        # (now also auto-built at the end of a successful run -- see
        # pipeline.execute -- but older runs predate that) has a rendered
        # `<page>/index.html` with no review file yet: build it on demand,
        # with the SAME function `harness review` uses (review.build_review_for_page),
        # so the two never drift apart. Neither file existing means the page
        # was never rendered (a STOP, or a page not yet written) -- serve a
        # 200 placeholder so the run detail page's iframe shows something
        # readable instead of Flask's default 404.
        run_dir = _run_dir_or_404(tenant, run_id)
        safe_page = textutil.safe_filename(page)
        path = _safe_path(run_dir, f"{safe_page}-review.html")
        # Cycle 35b: a review file built before the inliner learned to drop
        # <picture> <source>/srcset candidates renders broken images in the
        # iframe. Treat such a file as stale and rebuild it the same way a
        # missing one is built.
        if path is not None and "<source" in path.read_text(errors="ignore"):
            path = None
        if path is None:
            index_path = _safe_path(run_dir, f"{safe_page}/index.html")
            if index_path is not None:
                from .review import build_review_for_page

                path = build_review_for_page(run_dir, safe_page)
        if path is None:
            return Response(
                _page_shell("Not rendered yet", "<p>Page not rendered yet.</p>"),
                mimetype="text/html",
            )
        return send_file(path, mimetype="text/html")

    @app.route("/run/<run_id>/source-image")
    def source_image(run_id):
        run_dir = _run_dir_or_404(tenant, run_id)
        ad_brief = _load_json(run_dir / "ad_brief.json")
        source_file = ad_brief.get("source_file") or ""
        path = _safe_path(run_dir, source_file) or _safe_path(tenant.fixtures_dir, source_file)
        if path is None:
            abort(404)
        return send_file(path)

    @app.route("/run/<run_id>/page/<page>/action", methods=["POST"])
    def page_action(run_id, page):
        run_dir = _run_dir_or_404(tenant, run_id)
        state = runstate.load_state(run_dir)
        if page not in state.get("pages", {}):
            abort(404)
        reviewer_email = g.reviewer_email
        action = request.form.get("action")
        raw_notes = request.form.get("notes") or ""

        scores = {}
        for field in SCORE_FIELDS:
            value = request.form.get(field)
            if value:
                scores[field] = int(value)
        if scores:
            record_score(
                tenant, run_dir=str(run_dir), angle=scores.get("angle"), brand=scores.get("brand"),
                claims=scores.get("claims"), publish=scores.get("publish"), by=reviewer_email,
                note=raw_notes, page=page,
            )

        if action == "approve":
            runstate.approve(run_dir, tenant, by=reviewer_email, pages=[page], note=raw_notes)
            notify.notify_approved(tenant, run_id=run_id, by=reviewer_email, pages=[page], run_dir=str(run_dir))
        elif action == "reject":
            runstate.reject(run_dir, tenant, by=reviewer_email, note=raw_notes)
        elif action == "changes":
            from . import revise as revise_mod

            cuts, notes = revise_mod.parse_cuts_and_notes(raw_notes)
            runstate.request_changes(
                run_dir, tenant, page=page, by=reviewer_email, scores=scores, notes=notes, cuts=cuts,
            )
            if request.form.get("regenerate"):
                _launch_revise_background(tenant, run_dir, page, reviewer_email)
        else:
            abort(400)
        return redirect(url_for("run_detail", run_id=run_id))

    @app.route("/run/<run_id>/approve-all", methods=["POST"])
    def approve_all(run_id):
        run_dir = _run_dir_or_404(tenant, run_id)
        runstate.approve(run_dir, tenant, by=g.reviewer_email, note="approve all")
        notify.notify_approved(
            tenant, run_id=run_id, by=g.reviewer_email,
            pages=list(runstate.load_state(run_dir)["pages"]), run_dir=str(run_dir),
        )
        return redirect(url_for("run_detail", run_id=run_id))

    @app.route("/images")
    def image_library():
        return _render_image_library(tenant)

    @app.route("/images/<product_slug>")
    def image_library_product(product_slug):
        page = request.args.get("page", "1")
        page = int(page) if page.isdigit() else 1
        source_filter = request.args.get("source", "all")
        saved = request.args.get("saved")
        saved = int(saved) if saved and saved.isdigit() else None
        return _render_image_library_product(tenant, product_slug, page=page, source_filter=source_filter, saved=saved)

    @app.route("/images/<product_slug>/save", methods=["POST"])
    def image_library_save(product_slug):
        saved = _save_image_library(tenant, product_slug)
        page = request.args.get("page", "1")
        source_filter = request.args.get("source", "all")
        return redirect(
            url_for("image_library_product", product_slug=product_slug, page=page, source=source_filter, saved=saved)
        )

    @app.route("/images/thumb/<asset_id>")
    def image_thumb(asset_id):
        return _image_thumb_response(tenant, asset_id)

    @app.route("/e", methods=["POST"])
    def beacon():
        return _handle_beacon(tenant, beacon_limiter, beacon_limiter_salt)

    return app
