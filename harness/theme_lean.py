"""Cycle 110: the lean landing layout -- a theme layout + page template pair
that renders a landing page without the store theme's chrome (promo bar,
header, sales-notification popup, chat bar, review widgets, extra fonts).

Measured 2026-10-15 at 375 x 812 inside the live theme: the fixed chrome
covers y 0-116 and the bottom 212px, so a visitor sees about 485px of our
page on load, and the theme loads ~2.97 MB / 338 requests around a 49 KB
page. The files are deploy/shopify/lean/layout/landing.liquid and
deploy/shopify/lean/templates/page.landing.liquid; the publisher sets
template_suffix "landing" on landing pages when tenant.yaml has
`landing_lean_layout: true` (harness/cli.py _make_publisher).

  push(client, theme_ids, dry_run=True): for each theme, GET each key; an
      existing copy that differs is saved to a local backup file first; then
      PUT (layout before template: the template names the layout). Dry run:
      prints what it would write and changes nothing.
  verify(url, ...): fetches a published page and reports: no promo bar,
      sales-pop, assist/chat bar or review-widget markup; the transfer of
      the HTML plus the stylesheets, scripts and images it references is at
      most VERIFY_MAX_BYTES; the template keys are present in every theme
      named in tenant.yaml `shopify_publish.theme_ids`.

The harness never pushes on its own: both are operator commands
(`harness theme push-landing-layout`, `harness theme verify-landing-layout`).
"""
import datetime
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .config import REPO_ROOT
from .errors import HarnessError

LEAN_DIR = REPO_ROOT / "deploy" / "shopify" / "lean"
ASSET_KEYS = ("layout/landing.liquid", "templates/page.landing.liquid")   # push order
VERIFY_MAX_BYTES = 800_000
USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 harness-verify"

# Markup the lean layout must not carry (the theme's chrome and widgets).
FORBIDDEN_MARKUP = {
    "promo bar": re.compile(r"announcement[-_]bar|promo[-_]bar|pk_promo|shopify-section-announcement", re.I),
    "sales popup": re.compile(r"sales?[-_ ]?pop|sales[-_]notification|recent[-_]sales|fomo", re.I),
    "assist / chat bar": re.compile(r"[-_]assist[-_]|assist[-_]bar|chat[-_]with|chat-bar", re.I),
    "review widget": re.compile(r"cdn\.judge\.me|judgeme[-_.]|jdgm[-_]", re.I),
    "theme header": re.compile(r"shopify-section-header|shopify-section-group-header", re.I),
}


class ThemeError(HarnessError):
    """A lean-layout command that cannot go on; one line, exit 1."""


def local_text(key):
    return (LEAN_DIR / key).read_text()


def _asset_path(theme_id, key=None):
    path = f"themes/{theme_id}/assets.json"
    if key:
        path += "?" + urllib.parse.urlencode({"asset[key]": key})
    return path


def get_asset(client, theme_id, key):
    status, data = client._request("GET", _asset_path(theme_id, key))
    if status == 404:
        return None
    if status != 200 or "asset" not in data:
        raise ThemeError(f"theme {theme_id}: {key}: read failed (status {status})")
    return data["asset"]


def push(client, theme_ids, *, dry_run=True, backup_dir, out=print, now=None):
    """Returns the number of assets written (0 on a dry run)."""
    now = now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    written = 0
    for theme_id in theme_ids:
        out(f"theme {theme_id}:")
        for key in ASSET_KEYS:
            text = local_text(key)
            asset = get_asset(client, theme_id, key)
            state = "absent" if asset is None else ("identical" if asset.get("value") == text else "different")
            if state == "identical":
                out(f"  {key}: identical, nothing to write")
                continue
            backup = None
            if state == "different":
                backup = Path(backup_dir) / str(theme_id) / f"{key.replace('/', '__')}.{now}.bak"
            if dry_run:
                out(f"  {key}: {state}; would " + (f"back up the theme copy to {backup}, then " if backup else "")
                    + f"write {len(text.encode())} bytes")
                continue
            if backup:
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(asset.get("value") or "")
                out(f"  {key}: backed up the theme copy to {backup}")
            status, data = client._request("PUT", _asset_path(theme_id), {"asset": {"key": key, "value": text}})
            if status not in (200, 201) or "asset" not in data:
                raise ThemeError(f"theme {theme_id}: {key}: write failed (status {status})")
            written += 1
            out(f"  {key}: written; updated_at {data['asset'].get('updated_at')}")
    return written


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_REF_RE = re.compile(r'<link\b[^>]*rel="?stylesheet"?[^>]*href="([^"]+)"|<script\b[^>]*\bsrc="([^"]+)"|'
                     r'<img\b[^>]*\bsrc="([^"]+)"', re.I)


def verify(url, *, client=None, theme_ids=(), fetch=_fetch, out=print):
    """Returns the list of failures ([] = pass); prints one line per check."""
    failures = []
    html_bytes = fetch(url)
    html = html_bytes.decode("utf-8", "ignore")
    for label, pattern in FORBIDDEN_MARKUP.items():
        m = pattern.search(html)
        if m:
            failures.append(f"{label} markup found ({m.group(0)!r})")
            out(f"FAIL {label}: {m.group(0)!r} in the page")
        else:
            out(f"ok   no {label} markup")
    total, seen = len(html_bytes), set()
    for m in _REF_RE.finditer(html):
        ref = next(g for g in m.groups() if g)
        ref = urllib.parse.urljoin(url, ref.replace("&amp;", "&"))
        if ref in seen or not ref.startswith("http"):
            continue
        seen.add(ref)
        try:
            total += len(fetch(ref))
        except Exception as e:  # noqa: BLE001 -- an unreadable asset is reported, not fatal
            out(f"     could not fetch {ref[:80]}: {e}")
    line = f"{total // 1000} KB for the HTML and {len(seen)} referenced stylesheets, scripts and images"
    if total > VERIFY_MAX_BYTES:
        failures.append(f"transfer {line} (budget {VERIFY_MAX_BYTES // 1000} KB)")
        out(f"FAIL {line} (budget {VERIFY_MAX_BYTES // 1000} KB)")
    else:
        out(f"ok   {line} (budget {VERIFY_MAX_BYTES // 1000} KB)")
    if client is not None:
        for theme_id in theme_ids:
            for key in ASSET_KEYS:
                if get_asset(client, theme_id, key) is None:
                    failures.append(f"theme {theme_id} has no {key}")
                    out(f"FAIL theme {theme_id}: {key} missing")
                else:
                    out(f"ok   theme {theme_id}: {key} present")
    elif theme_ids:
        out("     theme keys not checked (no Shopify credentials)")
    return failures
