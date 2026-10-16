"""Cycle 114: the owner-approved design library goes live on the store.

design/library/approved/<entry>/<variant>/index.html + assets/ are
hand-built static pages the owner approved. A variant is a sub-folder of an
entry with its own index.html; an index.html at the entry root is not a
variant.

`harness library list`: entries, variants, status (live.json, the sweep).

`harness library publish <entry>/<variant> --tenant <t> [--dry-run] [--all]`:
  - the variant's assets go to the Shopify CDN through the run publisher's
    own calls (ShopifyPublisher.upload_assets for images, upload_fonts for
    webfonts), images named by page_body.build_asset_manifest, fonts by
    their file name. A file whose bytes were uploaded before (any live.json
    under approved/) is not uploaded again.
  - every `assets/...` reference in the HTML becomes its CDN URL.
  - the page body is the file's own head <style> blocks and preload links,
    then its <body> content. Nothing is added. One change is made: the
    tenant's page template (page.pk-harness.liquid) hides every <header>
    and <footer> element on the page (theme chrome), so the page's own
    header/footer/nav tags become <div data-lib-el="..."> and the page's CSS
    type selectors for them become div:where([data-lib-el="..."]), which
    has the same specificity.
  - a Shopify page, live, handle lp-lib-<entry>-<variant>, title = the
    file's <title>, seo.hidden = 1 (out of the sitemap and search), linked
    from nothing; the tenant's template suffix (shopify_publish), never the
    lean landing layout.
  - design/library/approved/<entry>/live.json: one record per variant (url,
    page_id, published_at, content_hash, assets, sale fields); the README's
    "Live" line lists them. The same content again is a no-op; changed
    content updates the same page.
  - publish_locked: true refuses, with LockedShopifyPublisher's message.

Sale variants (a folder name with the word "sale": sale, indoor-sale, ...)
carry offer_claim_id and offer_expires (the synced site deadline,
sale_sync.run_expiry) in live.json. sale_sync.live_offer_pages lists them
and the sweep's end action for them is hide only (sweep_action): the page
is set unpublished, no redirect, no rebuild, no twin. The sweep's state for
library pages is tenants/<t>/runs/library-sale.json (runtime data; the
repo's live.json is not written by the worker).
"""
import datetime
import hashlib
import html as html_mod
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import page_body
from .config import REPO_ROOT
from .errors import HarnessError

APPROVED_DIR = REPO_ROOT / "design" / "library" / "approved"
LIVE_FILE = "live.json"
SALE_STATE_FILE = "library-sale.json"
CLAIM_ID = "promo-community-savings-sale-2026-10"
HANDLE_PREFIX = "lp-lib-"
IMAGE_SUFFIXES = (".webp", ".jpg", ".jpeg", ".png", ".gif", ".avif")
FONT_SUFFIXES = (".woff2", ".woff", ".otf", ".ttf")

# a relative assets/ path: not part of a longer path or URL (".../t/1/assets/x" is the theme's)
ASSET_REF_RE = re.compile(r"(?<![\w/.:-])assets/[^\s\"'()<>,]+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_HEAD_KEEP_RE = re.compile(r"<style\b[^>]*>.*?</style>|<link\b[^>]*\brel=[\"']?preload\b[^>]*>",
                           re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.IGNORECASE | re.DOTALL)
_CHROME_TAG_RE = re.compile(r"<(/?)(header|footer|nav)\b([^>]*)>", re.IGNORECASE)
_PRELUDE_RE = re.compile(r"[^{}]+(?=\{)")
_TYPE_SELECTOR_RE = re.compile(r"(?<![\w.#\[=\"'-])(header|footer|nav)(?![\w-])", re.IGNORECASE)
# an assets/ path the browser would load (src, href, srcset, url()); a theme's CSS comment is not one
_RELATIVE_USE_RE = re.compile(r"""(?:\b(?:src|href)=["']?|\bsrcset=["'][^"']*?|url\(\s*["']?)(assets/[^\s"'()<>,]+)""",
                              re.IGNORECASE)


class LibraryError(HarnessError):
    """A library page cannot be published as asked."""


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# the folder
# ---------------------------------------------------------------------------

def entries(root=None):
    """[{"entry", "variants": [name, ...], "root_index": bool}] for every
    entry folder, sorted by name."""
    root = Path(root or APPROVED_DIR)
    out = []
    for path in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        variants = sorted(p.name for p in path.iterdir() if p.is_dir() and (p / "index.html").is_file())
        out.append({"entry": path.name, "variants": variants, "root_index": (path / "index.html").is_file()})
    return out


def is_sale(variant):
    """`sale`, `indoor-sale`, `outdoor-sale`; never `nosale` or `indoor`."""
    return "sale" in variant.lower().split("-")


def handle_for(entry, variant):
    return f"{HANDLE_PREFIX}{entry.lower()}-{variant.lower()}"


def target_of(entry, variant):
    return f"{entry}/{variant}"


def _visible(fragment):
    return " ".join(html_mod.unescape(_TAG_RE.sub("", fragment)).split())


def page_title(html):
    m = _TITLE_RE.search(html)
    return _visible(m.group(1)) if m else ""


def h1_texts(html):
    return [_visible(m.group(1)) for m in _H1_RE.finditer(html)]


def asset_refs(html):
    return sorted(set(ASSET_REF_RE.findall(html)))


def load_variant(root, entry, variant):
    """(html, {ref: bytes}) for one variant; every referenced asset must be
    a file inside the variant's own folder."""
    vdir = Path(root) / entry / variant
    index = vdir / "index.html"
    if not index.is_file():
        raise LibraryError(f"{target_of(entry, variant)}: no {index}")
    html = index.read_text()
    files = {}
    base = vdir.resolve()
    for ref in asset_refs(html):
        path = (vdir / ref).resolve()
        if base not in path.parents or not path.is_file():
            raise LibraryError(f"{target_of(entry, variant)}: {ref} is not a file in {vdir}")
        files[ref] = path.read_bytes()
    return html, files


def content_hash(html, files):
    h = hashlib.sha256(html.encode("utf-8"))
    for ref in sorted(files):
        h.update(b"\0" + ref.encode("utf-8") + b"\0" + hashlib.sha256(files[ref]).hexdigest().encode("ascii"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# the page body
# ---------------------------------------------------------------------------

def _css_selectors(css):
    """header/footer/nav type selectors -> div:where([data-lib-el=...]) in
    every rule prelude; declarations are not touched."""
    def prelude(m):
        if m.group(0).lstrip().startswith("@"):
            return m.group(0)
        return _TYPE_SELECTOR_RE.sub(lambda t: f'div:where([data-lib-el="{t.group(1).lower()}"])', m.group(0))
    return _PRELUDE_RE.sub(prelude, css)


def unhide_chrome(fragment):
    """The page's own header/footer/nav elements as <div data-lib-el=...>,
    and its CSS with them (see the module docstring)."""
    out = _STYLE_RE.sub(lambda m: m.group(1) + _css_selectors(m.group(2)) + m.group(3), fragment)

    def tag(m):
        if m.group(1):
            return "</div>"
        return f'<div data-lib-el="{m.group(2).lower()}"{m.group(3)}>'
    return _CHROME_TAG_RE.sub(tag, out)


def build_body(html):
    """index.html -> body_html: the head's <style> blocks and preload links,
    then the <body> content, with unhide_chrome. Asset paths are left as they
    are (rewrite_assets)."""
    head_end = html.lower().find("</head>")
    head = html[:head_end] if head_end != -1 else ""
    m = page_body._BODY_RE.search(html, head_end + 1 if head_end != -1 else 0)
    body = m.group(1) if m else html
    return unhide_chrome("\n".join(_HEAD_KEEP_RE.findall(head) + [body.strip()]) + "\n")


def rewrite_assets(body_html, url_by_ref):
    """Every assets/... reference -> its CDN URL. Raises when one is left."""
    out = ASSET_REF_RE.sub(lambda m: url_by_ref.get(m.group(0), m.group(0)), body_html)
    left = sorted(set(ASSET_REF_RE.findall(out)))
    if left:
        raise LibraryError(f"relative asset path(s) left after the rewrite: {', '.join(left[:5])}")
    return out


# ---------------------------------------------------------------------------
# live.json and the README
# ---------------------------------------------------------------------------

def live_path(root, entry):
    return Path(root) / entry / LIVE_FILE


def load_live(root, entry):
    return _read_json(live_path(root, entry))


def known_uploads(root):
    """{sha256: cdn_url} for every asset any live.json under `root` records."""
    known = {}
    for path in sorted(Path(root).glob(f"*/{LIVE_FILE}")):
        for rec in _read_json(path).values():
            for asset in (rec.get("assets") or {}).values() if isinstance(rec, dict) else []:
                if asset.get("sha256") and asset.get("url"):
                    known.setdefault(asset["sha256"], asset["url"])
    return known


def readme_live_line(live):
    parts = []
    for variant, rec in sorted(live.items()):
        if not rec.get("url"):
            continue
        extra = f", sale: hidden by the sale sweep after {rec['offer_expires']}" if rec.get("sale") else ""
        parts.append(f"{variant} {rec['url']} (page {rec['page_id']}, published {rec.get('published_at')}{extra})")
    return "Live (unlinked, hidden from search): " + " ; ".join(parts) if parts else ""


def update_readme(root, entry, live):
    """The README's "Live" line: replaced in place, else added at the end."""
    path = Path(root) / entry / "README.md"
    line = readme_live_line(live)
    if not line or not path.is_file():
        return
    lines = path.read_text().splitlines()
    for i, old in enumerate(lines):
        if old.startswith("Live (unlinked") or old.startswith("Live:"):
            lines[i] = line
            break
    else:
        lines += ["", line]
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# sale
# ---------------------------------------------------------------------------

def _claim(tenant):
    claims = _read_json(Path(tenant.claims_dir) / "verified.json")
    return next((c for c in claims if isinstance(c, dict) and c.get("id") == CLAIM_ID), None) \
        if isinstance(claims, list) else None


def sale_expiry(tenant, now=None):
    """The instant the sale ends for a library sale page: the synced site
    deadline (runs/sale-site.json when fresh, on and about the claim), else
    the claim's own deadline. None without the claim."""
    from . import sale_sync

    claim = _claim(tenant)
    if claim is None:
        return None
    expires, _source = sale_sync.run_expiry(tenant, {"offer": dict(claim, claim_id=CLAIM_ID)}, now)
    return expires


def promo_codes(tenant):
    from . import sale_sync

    claim = _claim(tenant) or {}
    return sorted(set(sale_sync._SITE_CODE_RE.findall(claim.get("text") or "")))


def sale_state_path(tenant):
    return Path(tenant.runs_dir) / SALE_STATE_FILE


def sale_state(tenant):
    return _read_json(sale_state_path(tenant))


def set_sale_state(tenant, target, **fields):
    path = sale_state_path(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _read_json(path)
    state[target] = dict(fields, at=_utcnow().isoformat(timespec="seconds"))
    _write_json(path, state)
    return state[target]


def live_sale_pages(tenant, root=None):
    """sale_sync.live_offer_pages: every library sale page this tenant
    published that the sweep has not hidden."""
    root = Path(root or APPROVED_DIR)
    state = sale_state(tenant)
    items = []
    for path in sorted(root.glob(f"*/{LIVE_FILE}")):
        for variant, rec in sorted(_read_json(path).items()):
            if not isinstance(rec, dict) or rec.get("tenant") != tenant.name or not rec.get("sale") \
                    or not rec.get("offer_expires") or not rec.get("page_id"):
                continue
            target = target_of(path.parent.name, variant)
            done = state.get(target) or {}
            if done.get("status") == "hidden":
                continue
            items.append({"kind": "library", "target": target, "run_id": None, "run_dir": None, "page": variant,
                          "url": rec.get("url"), "page_id": rec["page_id"], "handle": rec.get("handle"),
                          "offer_expires": rec["offer_expires"], "offer_claim_id": rec.get("offer_claim_id"),
                          "expires_from": "live.json", "sale_expiry": done})
    return items


def hide(tenant, item):
    """Sets the library page unpublished; nothing else."""
    from . import cli
    from .publishers.shopify import ShopifyPublisher

    tenant.load_env()
    publisher = cli._make_publisher(tenant, export_dir=Path(tenant.runs_dir) / "library-export")
    if not isinstance(publisher, ShopifyPublisher):
        raise LibraryError("hiding a library page needs the shopify publisher")
    publisher.update_page(item["page_id"], {"storefront_host": tenant.get("site_host")}, unpublished=True)


def sweep_action(tenant, item, decision, *, by=None, dry_run=False, log=print):
    """The sweep's decision for one library sale page. end -> hidden (and
    nothing else); extend -> a WARNING (the page is static); unknown -> no
    action. Returns "hidden" or None."""
    from . import jobs

    target, action = item["target"], decision["action"]
    label = f"sale sweep: library {target} ({item['url']})"
    if action == "end":
        if dry_run:
            log(f"{label}: sale ended; would hide the page (dry run)")
            return None
        try:
            hide(tenant, item)
        except Exception as e:  # noqa: BLE001 -- one page's failure never stops the sweep
            set_sale_state(tenant, target, status="failed", reason=str(e)[:300], end=item["offer_expires"])
            log(f"{label}: WARNING hide failed: {type(e).__name__}: {e}")
            return None
        set_sale_state(tenant, target, status="hidden", end=item["offer_expires"], page_id=item["page_id"])
        jobs.audit(tenant, by=by, action="library_sale_hidden", target=f"library:{target}",
                   detail={"url": item["url"], "page_id": item["page_id"]})
        log(f"sale sweep: library sale page hidden: {target} page {item['page_id']} {item['url']}")
        return "hidden"
    if action == "extend":
        if not dry_run:
            set_sale_state(tenant, target, status="extended", end=decision["end"])
        log(f"{label}: sale extended to {decision['end_local']}; WARNING the library page is static and still "
            f"prints its own deadline; not changed" + (" (dry run)" if dry_run else ""))
        return None
    log(f"{label}: {action}" + (f" ({decision['reason']})" if decision.get("reason") else "")
        + ("; WARNING no action" if action == "unknown" else ""))
    return None


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def _cdn_names(html, cartridge, refs):
    """{ref: (cdn_filename, alt)}: images as the run publisher names them
    (page_body.build_asset_manifest), fonts by their file name."""
    names = {i["local_path"]: (i["cdn_filename"], i["alt"])
             for i in page_body.build_asset_manifest(html, cartridge) if i["local_path"] in refs}
    for ref in refs:
        suffix = Path(ref).suffix.lower()
        if suffix in FONT_SUFFIXES:
            names[ref] = (Path(ref).name, "")
        elif ref not in names:
            names[ref] = (f"pk-{cartridge}-{page_body._slugify(Path(ref).stem)}{suffix}", "")
    return names


def plan_variant(tenant, entry, variant, *, root=None, now=None):
    """Everything a publish of one variant would do, with no store call."""
    root = Path(root or APPROVED_DIR)
    html, files = load_variant(root, entry, variant)
    target, handle = target_of(entry, variant), handle_for(entry, variant)
    from . import cli

    if not cli._HANDLE_RE.match(handle):
        raise LibraryError(f"{target}: handle {handle!r} is not a valid Shopify handle")
    title, h1s = page_title(html), h1_texts(html)
    if not title:
        raise LibraryError(f"{target}: index.html has no <title>")
    if not h1s or not h1s[0]:
        raise LibraryError(f"{target}: index.html has no <h1>")
    bad = [r for r in files if Path(r).suffix.lower() not in IMAGE_SUFFIXES + FONT_SUFFIXES]
    if bad:
        raise LibraryError(f"{target}: no upload path for {', '.join(bad)}")
    sale = is_sale(variant)
    text = _visible(html)
    codes = [c for c in promo_codes(tenant) if c in text]
    if not sale and codes:
        raise LibraryError(f"{target}: a no-sale variant shows the promo code(s) {', '.join(codes)}")
    banner = (_read_json(Path(tenant.runs_dir) / "sale-site.json").get("message") or "").strip()
    if banner and " ".join(banner.split()).lower() in text.lower():
        raise LibraryError(f"{target}: the page carries the theme's promo bar text")
    now = now or _utcnow()
    expires = None
    if sale:
        from . import offer as offer_mod

        expires = sale_expiry(tenant, now)
        end = offer_mod._instant(expires)
        if end is None:
            raise LibraryError(f"{target}: no deadline for the promo claim {CLAIM_ID}; a sale page is not published")
        if now >= end:
            raise LibraryError(f"{target}: the sale ended at {expires}; a sale page is not published")
    live = load_live(root, entry)
    rec = live.get(variant) or {}
    digest = content_hash(html, files)
    done = sale_state(tenant).get(target) or {}
    if done.get("status") == "hidden":
        action = "hidden"
    elif rec.get("page_id") and rec.get("content_hash") == digest:
        action = "unchanged"
    else:
        action = "update" if rec.get("page_id") else "create"
    known = known_uploads(root)
    shas = {ref: hashlib.sha256(data).hexdigest() for ref, data in files.items()}
    return {"entry": entry, "variant": variant, "target": target, "handle": handle, "title": title, "h1": h1s[0],
            "sale": sale, "offer_expires": expires, "action": action, "content_hash": digest, "record": rec,
            "html": html, "files": files, "sha": shas,
            "upload": sorted(r for r in files if shas[r] not in known),
            "cached": sorted(r for r in files if shas[r] in known), "known": known}


def _upload(publisher, plan):
    """{ref: cdn_url} for every asset of the plan, uploading only bytes no
    live.json has."""
    cartridge = handle_for(plan["entry"], plan["variant"])[len("lp-"):]
    names = _cdn_names(plan["html"], cartridge, set(plan["files"]))
    url_by_sha = dict(plan["known"])
    first = {}
    for ref in plan["upload"]:
        first.setdefault(plan["sha"][ref], ref)
    images = [{"local_path": ref, "alt": names[ref][1], "cdn_filename": names[ref][0], "bytes": plan["files"][ref]}
              for ref in first.values() if Path(ref).suffix.lower() in IMAGE_SUFFIXES]
    fonts = [{"filename": names[ref][0], "bytes": plan["files"][ref]}
             for ref in first.values() if Path(ref).suffix.lower() in FONT_SUFFIXES]
    if len({f["filename"] for f in fonts}) != len(fonts):
        raise LibraryError(f"{plan['target']}: two different font files share a name")
    if images:
        for ref, url in publisher.upload_assets(images).items():
            url_by_sha[plan["sha"][ref]] = url
    if fonts:
        urls, errors = publisher.upload_fonts(fonts)
        if errors:
            raise LibraryError(f"{plan['target']}: font upload failed: {errors}")
        by_name = {names[ref][0]: ref for ref in first.values()}
        for filename, url in urls.items():
            url_by_sha[plan["sha"][by_name[filename]]] = url
    missing = [ref for ref in plan["files"] if plan["sha"][ref] not in url_by_sha]
    if missing:
        raise LibraryError(f"{plan['target']}: no CDN URL for {', '.join(missing)}")
    return {ref: url_by_sha[plan["sha"][ref]] for ref in plan["files"]}


def publish_variant(tenant, entry, variant, *, publisher, root=None, now=None, log=print):
    """Publishes one variant (see the module docstring). Returns the plan
    with "result" ("unchanged", "hidden", "created", "updated") and, after a
    write, "record"."""
    from . import abtest, cli

    root = Path(root or APPROVED_DIR)
    if isinstance(publisher, cli.LockedShopifyPublisher):
        raise cli.PublishLocked(cli.LockedShopifyPublisher.MESSAGE)
    from .publishers.shopify import ShopifyPublisher

    if not isinstance(publisher, ShopifyPublisher):
        raise LibraryError("library publish needs the shopify publisher (tenant.yaml publisher: shopify)")
    plan = plan_variant(tenant, entry, variant, root=root, now=now)
    if plan["action"] in ("unchanged", "hidden"):
        log(f"{plan['target']}: {plan['action']}" + (" (the sale sweep hid it; not published again)"
                                                      if plan["action"] == "hidden" else "; no change")
            + (f" {plan['record'].get('url')}" if plan["record"].get("url") else ""))
        return dict(plan, result=plan["action"])
    if plan["action"] == "create":
        taken = publisher.find_page_by_handle(plan["handle"])
        if taken:
            raise LibraryError(f"{plan['target']}: the handle {plan['handle']} is already used by Shopify page "
                               f"{taken.get('id')} and live.json has no record of it")
    url_by_ref = _upload(publisher, plan)
    body = rewrite_assets(build_body(plan["html"]), url_by_ref)
    payload = {"title": plan["title"], "body_html": body, "storefront_host": tenant.get("site_host")}
    if plan["action"] == "create":
        payload["handle"] = plan["handle"]
        payload["metafields"] = [dict(abtest.SEO_HIDDEN_METAFIELD)]
        result = publisher.publish(payload, unpublished=False)
    else:
        result = publisher.update_page(plan["record"]["page_id"], payload, unpublished=False)
    if result.get("handle") != plan["handle"]:
        raise LibraryError(f"{plan['target']}: Shopify answered with handle {result.get('handle')!r}, "
                           f"not {plan['handle']!r}; page {result.get('id')}")
    page = publisher.get_page(result["id"]) or {}
    now_iso = _utcnow().isoformat(timespec="seconds")
    rec = {"tenant": tenant.name, "handle": result["handle"], "url": result["url"], "page_id": result["id"],
           "published_at": page.get("published_at"), "content_hash": plan["content_hash"], "title": plan["title"],
           "h1": plan["h1"], "sale": plan["sale"], "seo_hidden": True,
           "template_suffix": getattr(publisher, "template_suffix", None),
           "assets": {ref: {"sha256": plan["sha"][ref], "url": url} for ref, url in sorted(url_by_ref.items())}}
    if plan["sale"]:
        rec.update(offer_claim_id=CLAIM_ID, offer_expires=plan["offer_expires"])
    if plan["action"] == "update":
        rec.update(first_published_at=plan["record"].get("first_published_at") or plan["record"].get("published_at"),
                   updated_at=now_iso)
    else:
        rec["first_published_at"] = page.get("published_at")
    live = load_live(root, entry)
    live[variant] = rec
    _write_json(live_path(root, entry), live)
    update_readme(root, entry, live)
    verb = "created" if plan["action"] == "create" else "updated"
    log(f"{plan['target']}: {verb} {rec['url']} (page {rec['page_id']}, {len(plan['upload'])} asset(s) uploaded, "
        f"{len(plan['cached'])} already on the CDN)")
    return dict(plan, result=verb, record=rec)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _get(url, timeout=20):
    from .sale_sync import USER_AGENT

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (OSError, ValueError):
        return 0, b""


def verify_live(record, *, fetch=None, attempts=6, wait_s=5, sleep=time.sleep):
    """GET the live URL: status 200, our h1 present, every CDN asset URL of
    the record in the page and answering 200, no relative assets/ path (none
    of the page's own, none in a src/href/srcset/url(); the theme's own CSS
    comments name its assets/ files and are not a load).
    Retries while the storefront cache still serves an older copy."""
    fetch = fetch or _get
    status, text = 0, ""
    for i in range(attempts):
        if i:
            sleep(wait_s)
        status, raw = fetch(record["url"])
        text = raw.decode("utf-8", errors="replace")
        if status == 200 and record["h1"] in h1_texts(text):
            break
    urls = sorted({a["url"] for a in (record.get("assets") or {}).values()})
    asset_status = {u: (fetch(u)[0] if u in text else "not in page") for u in urls}
    relative = sorted(set(_RELATIVE_USE_RE.findall(text))
                      | {r for r in record.get("assets") or {} if re.search(rf"(?<![\w/.:-]){re.escape(r)}", text)})
    ok = status == 200 and record["h1"] in h1_texts(text) and all(s == 200 for s in asset_status.values()) \
        and not relative
    return {"ok": ok, "status": status, "h1": record["h1"] in h1_texts(text), "assets": asset_status,
            "relative": relative}
