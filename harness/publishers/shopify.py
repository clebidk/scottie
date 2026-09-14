"""Shopify Admin REST API (2024-10) publisher.

Credentials: SHOPIFY_STORE (the storefront's *.myshopify.com admin domain)
and SHOPIFY_TOKEN (an Admin API access token), read from the tenant's `.env`
by `Tenant.load_env` -- same convention as `ANTHROPIC_API_KEY`
(harness/anthropic_client.py). Required Admin API scopes: write_content,
write_files, read_content (see docs/PUBLISHING.md). Both env vars are
optional at construction time -- every method here fails closed with a
`ShopifyCredentialsMissing`, never a traceback and never a network call, when
either is missing.

Uses `urllib.request`, matching every other HTTP call in this codebase
(harness/prices.py, harness/sources/judgeme.py, harness/render.py) rather
than adding the `requests` dependency.

File upload: this adapter uploads a page's inline images with the REST
`files` endpoint (`POST /admin/api/2024-10/files.json`, base64
`file.attachment`) rather than GraphQL `fileCreate`/staged uploads -- for a
handful of page images (not a bulk/large-file job), one REST call per file is
simpler to reason about and simpler to fake in tests than the staged-upload
round trip GraphQL requires.
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..errors import PublishFailed
from .base import Publisher

API_VERSION = "2024-10"

# Cycle 34: Admin API hosts must be *.myshopify.com (no scheme/path/userinfo).
# Anything else would send X-Shopify-Access-Token to an attacker-controlled host
# (SSRF / credential exfil via a malicious SHOPIFY_STORE value).
_MYSHOPIFY_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$", re.IGNORECASE)
# Hard cap on response bodies for the default urllib transport and storefront
# probe -- Admin JSON and a storefront HTML check should never need more.
_MAX_RESPONSE_BYTES = 5_000_000


def normalize_shopify_store(store):
    """Return a bare `shop.myshopify.com` host or raise ValueError.

    Accepts a host alone or an https URL whose host is *.myshopify.com.
    Rejects empty values, other domains, http://, userinfo, and ports.
    """
    raw = (store or "").strip()
    if not raw:
        raise ValueError("SHOPIFY_STORE is empty")
    # Allow a full URL pasted from the admin bar; require https if scheme set.
    if "://" in raw:
        parts = urllib.parse.urlparse(raw)
        if parts.scheme.lower() != "https":
            raise ValueError(
                f"SHOPIFY_STORE URL must be https://*.myshopify.com, not {parts.scheme!r}"
            )
        if parts.username or parts.password or parts.port:
            raise ValueError("SHOPIFY_STORE must not include userinfo or a port")
        host = parts.hostname or ""
    else:
        # strip accidental path if someone pasted shop.myshopify.com/admin
        host = raw.split("/")[0].strip()
        if "@" in host or ":" in host:
            raise ValueError("SHOPIFY_STORE must be a bare *.myshopify.com host")
    host = host.lower().rstrip(".")
    if not _MYSHOPIFY_HOST_RE.match(host):
        raise ValueError(
            f"SHOPIFY_STORE must be a *.myshopify.com host (got {store!r})"
        )
    return host


def _read_capped(resp, limit=_MAX_RESPONSE_BYTES):
    """Read an HTTPResponse up to `limit` bytes; raise PublishFailed if larger."""
    chunks = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise PublishFailed(
                f"Shopify response exceeded {_MAX_RESPONSE_BYTES} byte cap; refusing to buffer"
            )
        chunks.append(chunk)
    return b"".join(chunks)


_SRC_ASSET_RE = re.compile(r'src="(assets/[^"]+)"')
_SRCSET_ATTR_RE = re.compile(r'srcset="([^"]*)"')


def _rewrite_srcset_value(value, url_by_local_path):
    """Rewrite each assets/... entry in a srcset attribute; keep width
    descriptors (e.g. `480w`). Unknown paths stay unchanged."""
    rewritten = []
    for chunk in value.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        path, sep, descriptor = piece.rpartition(" ")
        if not sep:
            path, descriptor = piece, ""
        path = path.strip()
        descriptor = descriptor.strip()
        mapped = url_by_local_path.get(path, path)
        rewritten.append(f"{mapped} {descriptor}".rstrip() if descriptor else mapped)
    return ", ".join(rewritten)


def rewrite_asset_srcs(body_html, url_by_local_path):
    """Every `src="assets/<file>"` and every matching `srcset` entry in
    `body_html` becomes the uploaded CDN URL from `url_by_local_path`
    (keyed by that same "assets/<file>" local path, see
    harness/page_body.py's build_asset_manifest). A path not in the mapping
    is left as-is. Srcset width descriptors are preserved.

    Cycle 34: storefront browsers that honor srcset were still requesting
    relative assets/ URLs after publish because only src= was rewritten."""

    def replace_src(match):
        local_path = match.group(1)
        return f'src="{url_by_local_path.get(local_path, local_path)}"'

    def replace_srcset(match):
        return f'srcset="{_rewrite_srcset_value(match.group(1), url_by_local_path)}"'

    out = _SRC_ASSET_RE.sub(replace_src, body_html)
    return _SRCSET_ATTR_RE.sub(replace_srcset, out)


class ShopifyCredentialsMissing(Exception):
    """SHOPIFY_STORE or SHOPIFY_TOKEN is not set for this tenant."""


class ShopifyPublisher(Publisher):
    def __init__(self, *, store=None, token=None, transport=None):
        """`store`/`token` default to reading SHOPIFY_STORE/SHOPIFY_TOKEN
        from the environment. `transport`, if given, replaces the real HTTP
        call for every request this adapter makes -- tests inject a fake
        transport so this class never makes a real network call outside a
        live server run."""
        raw_store = store if store is not None else os.environ.get("SHOPIFY_STORE")
        # Normalize when present; leave None/empty for _require_credentials to
        # report the usual "not configured" error (don't turn "missing" into
        # "invalid host").
        if raw_store and str(raw_store).strip():
            self.store = normalize_shopify_store(raw_store)
        else:
            self.store = raw_store if raw_store is not None else None
            if self.store is not None and not str(self.store).strip():
                self.store = None
        self.token = token if token is not None else os.environ.get("SHOPIFY_TOKEN")
        self._transport = transport or self._urllib_transport

    def _require_credentials(self):
        missing = []
        if not self.store:
            missing.append("SHOPIFY_STORE")
        if not self.token:
            missing.append("SHOPIFY_TOKEN")
        if missing:
            raise ShopifyCredentialsMissing(
                "no " + " / ".join(missing) + " configured for this tenant "
                "(set it in tenants/<tenant>/.env). No Shopify API call was made."
            )

    def _url(self, path):
        return f"https://{self.store}/admin/api/{API_VERSION}/{path}"

    @staticmethod
    def _urllib_transport(method, url, *, headers, body=None):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, _read_capped(resp)
        except urllib.error.HTTPError as e:
            # HTTPError is a file-like response body too; still cap it.
            try:
                return e.code, _read_capped(e)
            finally:
                e.close()

    def _request(self, method, path, payload=None):
        self._require_credentials()
        headers = {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, raw = self._transport(method, self._url(path), headers=headers, body=body)
        data = json.loads(raw) if raw else {}
        return status, data

    # -- Publisher interface --------------------------------------------

    def dry_run(self, page):
        """A GET on the shop endpoint: proves the token/store work without
        creating anything. Never sends `page` anywhere -- only reports its
        own size/asset count for the operator."""
        try:
            self._require_credentials()
        except ShopifyCredentialsMissing as e:
            return {"ok": False, "reason": str(e)}
        try:
            status, data = self._request("GET", "shop.json")
        except ShopifyCredentialsMissing as e:  # pragma: no cover - race with the check above
            return {"ok": False, "reason": str(e)}
        ok = status == 200 and "shop" in data
        return {
            "ok": ok,
            "reason": "" if ok else f"shop.json returned status {status}",
            "store": self.store,
            "body_bytes": len(page.get("body_html", "")) if isinstance(page, dict) else None,
            "asset_count": len(page.get("assets", []) or []) if isinstance(page, dict) else None,
        }

    def upload_assets(self, manifest):
        mapping = {}
        for item in manifest:
            attachment = base64.b64encode(item["bytes"]).decode("ascii")
            status, data = self._request("POST", "files.json", {
                "file": {
                    "attachment": attachment,
                    "filename": item["cdn_filename"],
                    "alt": item.get("alt", ""),
                }
            })
            if status not in (200, 201) or "file" not in data:
                raise PublishFailed(f"file upload failed for {item['cdn_filename']}: status={status} body={data}")
            file_data = data["file"]
            mapping[item["local_path"]] = file_data.get("url") or file_data.get("public_url") or ""
        return mapping

    def publish(self, page, *, unpublished=True):
        """`page`: {"title", "body_html", ["handle"], ["storefront_host"]}.
        Creates a new page via POST /pages.json; `published: not
        unpublished` -- a draft page unless the caller explicitly asked for
        `--live`. Returns {"id", "url", "admin_url"}."""
        payload = {
            "page": {
                "title": page["title"],
                "body_html": page["body_html"],
                "published": not unpublished,
            }
        }
        if page.get("handle"):
            payload["page"]["handle"] = page["handle"]
        status, data = self._request("POST", "pages.json", payload)
        if status not in (200, 201) or "page" not in data:
            raise PublishFailed(f"page create failed: status={status} body={data}")
        created = data["page"]
        page_id = created["id"]
        handle = created.get("handle", "")
        storefront_host = page.get("storefront_host") or self.store
        return {
            "id": page_id,
            "url": f"https://{storefront_host}/pages/{handle}",
            "admin_url": f"https://{self.store}/admin/pages/{page_id}",
        }

    def verify_cache(self, storefront_url, *, marker, pulls=8, delay_s=2, sleep=time.sleep, fetch=None):
        """Fetches `storefront_url` `pulls` times, `delay_s` apart, counting
        how many responses contain `marker` (a short, distinctive substring
        of the new body) -- the storefront cache-epoch trap documented in
        a tenant's own storefront notes ("verify with >= 8 pulls, not
        one"). Only ever called under `--live`."""
        fetch = fetch or self._default_fetch
        hits = 0
        for i in range(pulls):
            if i:
                sleep(delay_s)
            try:
                body = fetch(storefront_url)
            except Exception:
                body = b""
            if marker.encode("utf-8") in body:
                hits += 1
        return hits, pulls

    @staticmethod
    def _default_fetch(url):
        parts = urllib.parse.urlparse(url)
        if parts.scheme.lower() != "https" or not parts.netloc:
            raise PublishFailed(
                f"storefront probe URL must be https (got {parts.scheme!r})"
            )
        with urllib.request.urlopen(url, timeout=20) as resp:
            return _read_capped(resp)
