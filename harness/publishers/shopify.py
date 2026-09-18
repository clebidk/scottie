"""Shopify Admin REST + GraphQL API (2024-10) publisher.

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

File upload: Shopify has no REST endpoint for uploading a file's bytes (the
old `POST /admin/api/2024-10/files.json` with a base64 `file.attachment`
does not exist and 406s) -- files only go in through the GraphQL Admin API's
staged-upload flow. `upload_assets` runs, per asset:

1. `stagedUploadsCreate` (GraphQL): asks Shopify for a one-time upload
   target (a signed GCS URL plus the form fields that must go with it).
2. A `multipart/form-data` POST of the raw bytes straight to that target --
   not an Admin API call, no `X-Shopify-Access-Token` header, built by hand
   since this is the one place the payload isn't JSON.
3. `fileCreate` (GraphQL): tells Shopify to adopt the just-uploaded object
   at its `resourceUrl` as a real file.
4. Polling `node(id: ...)` until Shopify finishes processing the file
   (`fileStatus == "READY"` and an `image.url`) -- processing is
   asynchronous, so the id `fileCreate` returns isn't usable yet.

Steps 1, 3, and 4 go over the same GraphQL endpoint as every other Admin API
call here (`_graphql`, built on the existing `_transport`/`_request`
machinery); step 2 is a plain POST to a non-Shopify host, so it goes through
a second injectable transport (`upload_transport`, defaulting to the same
urllib call `transport` does) instead of reusing `_transport`.
"""
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

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

# Cycle 39: staged-upload file processing is asynchronous on Shopify's side;
# these bound how long upload_assets waits for fileCreate's `node` to report
# fileStatus == READY before giving up.
_POLL_INTERVAL_S = 1
_POLL_MAX_ATTEMPTS = 30


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


_STAGED_UPLOADS_CREATE_QUERY = """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters {
        name
        value
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

_FILE_CREATE_QUERY = """
mutation fileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files {
      id
      fileStatus
      alt
      ... on MediaImage {
        image {
          url
        }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

_FILE_STATUS_QUERY = """
query fileStatus($id: ID!) {
  node(id: $id) {
    ... on MediaImage {
      fileStatus
      image {
        url
      }
    }
  }
}
"""


class ShopifyPublisher(Publisher):
    def __init__(self, *, store=None, token=None, transport=None, upload_transport=None, sleep=None):
        """`store`/`token` default to reading SHOPIFY_STORE/SHOPIFY_TOKEN
        from the environment. `transport`, if given, replaces the real HTTP
        call for every Admin API request this adapter makes (REST and
        GraphQL alike) -- tests inject a fake transport so this class never
        makes a real network call outside a live server run. `upload_transport`
        is the same shape but for the one non-Admin-API call this adapter
        makes: the raw multipart POST of a staged upload's bytes to its GCS
        target (see module docstring); it defaults to the same urllib call
        `transport` does. `sleep`, if given, replaces `time.sleep` for the
        staged-upload processing poll -- tests inject a no-op so a fake
        "never becomes ready" run doesn't actually wait 30 seconds."""
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
        self._upload_transport = upload_transport or self._urllib_transport
        self._sleep = sleep or time.sleep

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

    def _graphql(self, query, variables):
        """POST one GraphQL operation to `graphql.json`, over the same
        `_transport` (and the same token header) as every REST call. Raises
        PublishFailed on a non-200 status or a top-level `errors` entry
        (a malformed query/variables) -- NOT on a `userErrors` entry inside
        `data`, which is operation-specific and left for the caller to
        check, since a userErrors entry is a normal, expected-shape failure
        (e.g. a bad filename) rather than a broken request."""
        self._require_credentials()
        headers = {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"}
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        status, raw = self._transport("POST", self._url("graphql.json"), headers=headers, body=body)
        data = json.loads(raw) if raw else {}
        if status != 200:
            raise PublishFailed(f"Shopify GraphQL request failed: status={status} body={data}")
        if data.get("errors"):
            raise PublishFailed(f"Shopify GraphQL request failed: errors={data['errors']}")
        return data.get("data") or {}

    def _raw_post(self, url, headers, body):
        """POST `body` to `url` exactly as given -- no Admin API host, no
        token, no path prefix. Used only for the staged-upload target,
        which is a signed GCS URL, not `self.store`."""
        return self._upload_transport("POST", url, headers=headers, body=body)

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

    @staticmethod
    def _mime_type_for(item):
        """`item` never carries its own mime type (harness/page_body.py's
        build_asset_manifest only writes local_path/alt/cdn_filename) -- guess
        from the cdn_filename's extension. `mimetypes` already knows most
        image extensions, but .webp is missing from some stdlib mimetypes
        databases, so it's handled explicitly before falling back to
        `mimetypes.guess_type`, itself defaulting to image/jpeg."""
        filename = item.get("cdn_filename") or item.get("local_path") or ""
        if Path(filename).suffix.lower() == ".webp":
            return "image/webp"
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "image/jpeg"

    def _create_staged_target(self, item, mime_type):
        variables = {
            "input": [
                {
                    "resource": "IMAGE",
                    "filename": item["cdn_filename"],
                    "mimeType": mime_type,
                    "httpMethod": "POST",
                    "fileSize": str(len(item["bytes"])),
                }
            ]
        }
        data = self._graphql(_STAGED_UPLOADS_CREATE_QUERY, variables)
        result = data.get("stagedUploadsCreate") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise PublishFailed(
                f"stagedUploadsCreate failed for {item['cdn_filename']}: {user_errors}"
            )
        targets = result.get("stagedTargets") or []
        if not targets:
            raise PublishFailed(
                f"stagedUploadsCreate returned no stagedTargets for {item['cdn_filename']}"
            )
        return targets[0]

    @staticmethod
    def _build_multipart_body(parameters, filename, mime_type, file_bytes):
        """Hand-built multipart/form-data body: every staged-upload
        `parameters` entry as its own field, in the order Shopify returned
        them, then `file` LAST -- Google Cloud Storage (the actual upload
        target) rejects the request if `file` isn't the final field."""
        boundary = uuid.uuid4().hex
        lines = []
        for param in parameters:
            lines.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{param["name"]}"\r\n\r\n'
                f'{param["value"]}\r\n'.encode("utf-8")
            )
        lines.append(
            (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: {mime_type}\r\n\r\n'
            ).encode("utf-8")
        )
        lines.append(file_bytes)
        lines.append(f'\r\n--{boundary}--\r\n'.encode("utf-8"))
        return boundary, b"".join(lines)

    def _upload_to_staged_target(self, target, item, mime_type):
        boundary, body = self._build_multipart_body(
            target.get("parameters") or [], item["cdn_filename"], mime_type, item["bytes"]
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        status, _raw = self._raw_post(target["url"], headers, body)
        if not (200 <= status < 300):
            raise PublishFailed(
                f"staged upload failed for {item['cdn_filename']}: status={status}"
            )

    def _create_file(self, target, item):
        variables = {
            "files": [
                {
                    "originalSource": target["resourceUrl"],
                    "contentType": "IMAGE",
                    "alt": item.get("alt", ""),
                    "filename": item["cdn_filename"],
                }
            ]
        }
        data = self._graphql(_FILE_CREATE_QUERY, variables)
        result = data.get("fileCreate") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise PublishFailed(f"fileCreate failed for {item['cdn_filename']}: {user_errors}")
        files = result.get("files") or []
        if not files:
            raise PublishFailed(f"fileCreate returned no files for {item['cdn_filename']}")
        return files[0]

    def _poll_until_ready(self, file_id, cdn_filename):
        for attempt in range(_POLL_MAX_ATTEMPTS):
            data = self._graphql(_FILE_STATUS_QUERY, {"id": file_id})
            node = data.get("node") or {}
            status = node.get("fileStatus")
            image = node.get("image") or {}
            url = image.get("url") or ""
            if status == "READY" and url:
                return url
            if status == "FAILED":
                raise PublishFailed(
                    f"Shopify file processing failed for {cdn_filename} (id={file_id})"
                )
            if attempt < _POLL_MAX_ATTEMPTS - 1:
                self._sleep(_POLL_INTERVAL_S)
        raise PublishFailed(
            f"Shopify file {cdn_filename} (id={file_id}) never reached fileStatus=READY "
            f"after {_POLL_MAX_ATTEMPTS} polls"
        )

    def upload_assets(self, manifest):
        """Uploads every manifest item through the GraphQL staged-upload
        flow (see module docstring) and returns {local_path: cdn_url} --
        always Shopify's own returned URL, never one this adapter builds,
        since Shopify may rename a duplicate filename."""
        self._require_credentials()
        mapping = {}
        for item in manifest:
            mime_type = self._mime_type_for(item)
            target = self._create_staged_target(item, mime_type)
            self._upload_to_staged_target(target, item, mime_type)
            file_data = self._create_file(target, item)
            status = file_data.get("fileStatus")
            image = file_data.get("image") or {}
            url = image.get("url") or ""
            if status == "FAILED":
                raise PublishFailed(
                    f"Shopify file processing failed for {item['cdn_filename']} "
                    f"(id={file_data.get('id')})"
                )
            if not (status == "READY" and url):
                url = self._poll_until_ready(file_data["id"], item["cdn_filename"])
            mapping[item["local_path"]] = url
        return mapping

    def publish(self, page, *, unpublished=True):
        """`page`: {"title", "body_html", ["handle"], ["storefront_host"]}.
        Creates a new page via POST /pages.json; `published: not
        unpublished` -- a draft page unless the caller explicitly asked for
        `--live`. Returns {"id", "url", "admin_url", "handle"}."""
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
            "handle": handle,
        }

    def update_page(self, page_id, page, *, unpublished=True):
        """Same shape as `publish`, but updates the existing page `page_id`
        via `PUT /pages/<page_id>.json` instead of creating a new one --
        `harness publish --update` uses this so re-running a publish
        against an already-published handle doesn't create
        `<handle>-1`. `page.get("handle")` is intentionally never sent:
        `harness publish --update` ignores `--handle` (handle changes are
        out of scope for cycle 40), and PUT doesn't need it to identify the
        page anyway. Returns the same shape `publish` does: {"id", "url",
        "admin_url", "handle"}."""
        payload = {
            "page": {
                "id": page_id,
                "title": page["title"],
                "body_html": page["body_html"],
                "published": not unpublished,
            }
        }
        status, data = self._request("PUT", f"pages/{page_id}.json", payload)
        if status not in (200, 201) or "page" not in data:
            raise PublishFailed(f"page update failed: status={status} body={data}")
        updated = data["page"]
        handle = updated.get("handle", "")
        storefront_host = page.get("storefront_host") or self.store
        return {
            "id": updated["id"],
            "url": f"https://{storefront_host}/pages/{handle}",
            "admin_url": f"https://{self.store}/admin/pages/{updated['id']}",
            "handle": handle,
        }

    def create_redirect(self, path, target):
        """Creates a URL redirect from `path` (e.g. "/listicle-test-1") to
        `target` (e.g. "/pages/listicle-test-1") via POST /redirects.json.
        Idempotent across re-publishes: if Shopify refuses with a 422
        because `path` is already redirected, the existing redirect is
        looked up (GET /redirects.json?path=<path>) and retargeted with a
        PUT instead of failing. Both `path` and `target` must start with
        "/". Returns the redirect dict Shopify reports."""
        if not path.startswith("/"):
            raise ValueError(f"redirect path must start with '/' (got {path!r})")
        if not target.startswith("/"):
            raise ValueError(f"redirect target must start with '/' (got {target!r})")
        status, data = self._request(
            "POST", "redirects.json", {"redirect": {"path": path, "target": target}}
        )
        if status in (200, 201) and "redirect" in data:
            return data["redirect"]
        if status == 422 and "already been taken" in json.dumps(data).lower():
            existing = self._find_redirect_by_path(path)
            if existing is None:
                raise PublishFailed(
                    f"redirect create failed for {path!r} (422 'already taken') "
                    f"but no existing redirect was found: status={status} body={data}"
                )
            put_status, put_data = self._request(
                "PUT",
                f"redirects/{existing['id']}.json",
                {"redirect": {"id": existing["id"], "path": path, "target": target}},
            )
            if put_status not in (200, 201) or "redirect" not in put_data:
                raise PublishFailed(
                    f"redirect update failed for {path!r}: status={put_status} body={put_data}"
                )
            return put_data["redirect"]
        raise PublishFailed(f"redirect create failed for {path!r}: status={status} body={data}")

    def _find_redirect_by_path(self, path):
        query = urllib.parse.urlencode({"path": path})
        status, data = self._request("GET", f"redirects.json?{query}")
        redirects = data.get("redirects") if status == 200 else None
        return redirects[0] if redirects else None

    def verify_cache(self, storefront_url, *, marker, pulls=8, delay_s=2, sleep=time.sleep, fetch=None):
        """Fetches `storefront_url` `pulls` times, `delay_s` apart, counting
        how many responses contain `marker` (a short, distinctive substring
        of the new body) -- the storefront cache-epoch trap documented in
        a tenant's own storefront notes ("verify with >= 8 pulls, not
        one."). Only ever called under `--live`."""
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
