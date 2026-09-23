"""Cycle 68: pull new ads from a tenant's Meta ad account into its inbox.

`harness meta pull` lists the ads in tenant.yaml's `meta.ad_account_id` that
were created on or after `meta.ingest_since`, downloads each one's video or
image, and writes it to `tenants/<t>/meta_inbox/<ad_id>/` as `ad.json` plus
the media file. The next stage (an A/B/C landing-page test) reads the inbox;
`harness run <item>/<media_file>` already accepts the media file, and
harness/ingest.py reads the item's ad.json for the ad copy.

READ ONLY. The token needs the `ads_read` permission and nothing else; this
module never creates, edits, or pauses an ad.

The token goes in the Authorization header only -- never in a URL, a log
line, an exception, or ad.json. Media downloads go to Meta's CDN with no
token at all.

Graph calls used (all GET, on https://graph.facebook.com/<version>/):
    me?fields=id,name                                   (harness meta check)
    act_<id>?fields=name,account_status                 (harness meta check)
    act_<id>/ads?fields=AD_FIELDS&effective_status=[..]&updated_since=<ts>&limit=100
    <video_id>?fields=source,length,title,picture
    act_<id>/adimages?hashes=[..]&fields=hash,url,width,height,name
"""
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import HarnessError

GRAPH_HOST = "graph.facebook.com"
# v24.0 (October 2025): the newest Graph/Marketing API version this harness
# was written against. Meta supports a version for about two years after its
# release, so v21.0 (October 2024) is close to its end. tenant.yaml's
# meta.graph_api_version overrides this.
DEFAULT_GRAPH_API_VERSION = "v24.0"
DEFAULT_TOKEN_ENV = "META_ACCESS_TOKEN"

# effective_status values that mean the ad exists and is (or will be) able to
# run. DELETED/ARCHIVED are never ingested; DISAPPROVED is left out because a
# landing page for an ad Meta refused has no traffic to test.
INGEST_STATUSES = (
    "ACTIVE", "PAUSED", "IN_PROCESS", "PENDING_REVIEW", "PREAPPROVED",
    "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES",
)

AD_FIELDS = (
    "id,name,created_time,effective_status,adset{id,name},campaign{id,name},"
    "creative{id,name,title,body,object_type,video_id,image_url,image_hash,"
    "thumbnail_url,object_story_spec,asset_feed_spec,url_tags,"
    "call_to_action_type,link_url}"
)
VIDEO_FIELDS = "source,length,title,picture"
ADIMAGE_FIELDS = "hash,url,width,height,name"
PAGE_SIZE = 100

# Graph error codes that mean "slow down": 4 app-level, 17 user-level, 32
# page-level, 613 custom rate limit; 80000-80014 are the Business Use Case
# (ads management / insights) limits.
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613} | set(range(80000, 80015)))
# 190 invalid/expired token, 102 session; 10 and 200-299 permission denied.
AUTH_CODES = frozenset({102, 190})
PERMISSION_CODES = frozenset({10, 294} | set(range(200, 300)))
MAX_RETRIES = 4
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0

MAX_MEDIA_BYTES = 500 * 1024 * 1024
_MAX_JSON_BYTES = 20_000_000
_CHUNK = 1024 * 1024

# Only types harness/ingest.py can read (VIDEO_EXT / STILL_EXT).
MEDIA_TYPES = {
    "video": {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-m4v": ".m4v", "video/webm": ".webm"},
    "image": {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"},
}

ACCOUNT_STATUS = {
    1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT", 9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE",
    101: "CLOSED", 201: "ANY_ACTIVE", 202: "ANY_CLOSED",
}

DOCS = "docs/META-INGEST.md"


class MetaError(HarnessError):
    """Base for every Meta ingest failure an operator is meant to see."""


class MetaConfigError(MetaError):
    """tenant.yaml's meta: block (or a --since value) is missing or wrong."""


class MetaTokenMissing(MetaError):
    """The token environment variable is not set."""


class MetaAuthError(MetaError):
    """Meta refused the token: invalid, expired, or missing ads_read."""


class MetaAPIError(MetaError):
    """Any other Graph API failure, after retries where retrying applies."""


class MediaRejected(MetaError):
    """A media download was too large, the wrong type, or failed."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def parse_since(value, *, what="meta.ingest_since"):
    """A timezone-aware UTC datetime from a date, a datetime, or an ISO
    string. A bare date means 00:00 UTC on that day."""
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, datetime.date):
        parsed = datetime.datetime(value.year, value.month, value.day)
    else:
        try:
            parsed = datetime.datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise MetaConfigError(
                f"{what} must be an ISO date such as 2026-09-23, got {value!r}"
            ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def meta_config(tenant):
    """tenant.yaml's meta: block, checked and normalized."""
    meta = tenant.get("meta") or {}
    where = f"{tenant.root / 'tenant.yaml'}"
    account = str(meta.get("ad_account_id") or "").strip()
    if not account:
        raise MetaConfigError(f"{where} has no meta.ad_account_id; see {DOCS}")
    digits = account[4:] if account.startswith("act_") else account
    if not digits.isdigit():
        raise MetaConfigError(f"meta.ad_account_id must be act_<digits>, got {account!r}")
    version = str(meta.get("graph_api_version") or DEFAULT_GRAPH_API_VERSION).strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise MetaConfigError(f"meta.graph_api_version must look like v24.0, got {version!r}")
    since = meta.get("ingest_since")
    if not since:
        # No cutoff would pull the account's whole history; refuse instead.
        raise MetaConfigError(f"{where} has no meta.ingest_since (the date new ads start counting)")
    return {
        "ad_account_id": f"act_{digits}",
        "graph_api_version": version,
        "ingest_since": parse_since(since),
        "token_env": str(meta.get("token_env") or DEFAULT_TOKEN_ENV),
    }


def token_from_env(cfg, tenant):
    """The token from the environment (Tenant.load_env reads the tenant's
    .env first). Raises MetaTokenMissing before any call is made."""
    name = cfg["token_env"]
    token = (os.environ.get(name) or "").strip()
    if not token:
        raise MetaTokenMissing(
            f"{name} is not set for tenant {tenant.name}: add it to {tenant.env_path} "
            f"(see {DOCS}). No Meta API call was made."
        )
    return token


def make_client(token, cfg):
    """The real client. Tests replace this function."""
    return GraphClient(token, version=cfg["graph_api_version"])


# ---------------------------------------------------------------------------
# Transports (the only code here that touches the network)
# ---------------------------------------------------------------------------

def _urllib_transport(method, url, *, headers, body=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(_MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(_MAX_JSON_BYTES + 1)
        finally:
            e.close()


def _urllib_download_transport(url, *, headers):
    """(status, headers, chunk iterator). The iterator closes the response
    when it is exhausted or closed."""
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        status, response_headers = e.code, dict(e.headers or {})
        e.close()
        return status, response_headers, iter(())

    def chunks():
        try:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    return
                yield chunk
        finally:
            resp.close()

    return resp.status, dict(resp.headers), chunks()


# ---------------------------------------------------------------------------
# Graph client
# ---------------------------------------------------------------------------

class GraphClient:
    """A small read-only Graph API client.

    `transport(method, url, *, headers, body=None) -> (status, bytes)` is the
    same shape as harness/publishers/shopify.py's; `download_transport(url, *,
    headers) -> (status, headers, chunks)` streams media. Tests inject both,
    and `sleep` so a retry does not wait."""

    def __init__(self, token, *, version=DEFAULT_GRAPH_API_VERSION, transport=None,
                 download_transport=None, sleep=None, max_retries=MAX_RETRIES):
        self._token = token
        self.version = version
        self._transport = transport or _urllib_transport
        self._download = download_transport or _urllib_download_transport
        self._sleep = sleep or time.sleep
        self.max_retries = max_retries

    def __repr__(self):
        return f"<GraphClient {self.version}>"

    def redact(self, text):
        text = str(text)
        return text.replace(self._token, "[redacted]") if self._token else text

    def _url(self, path, params=None):
        url = f"https://{GRAPH_HOST}/{self.version}/{path.lstrip('/')}"
        if params:
            encoded = {
                k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in params.items()
            }
            url += "?" + urllib.parse.urlencode(encoded)
        return url

    def get(self, path, params=None):
        return self._get_url(self._url(path, params))

    def _get_url(self, url):
        """One GET with retry/backoff. `url` never carries the token."""
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        where = urllib.parse.urlsplit(url).path
        attempt = 0
        while True:
            try:
                status, raw = self._transport("GET", url, headers=headers)
                failure = None
            except OSError as e:  # connection reset, timeout, DNS
                status, raw, failure = 0, b"", f"network error: {type(e).__name__}"
            data = {}
            if raw:
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = {}
            error = data.get("error") if isinstance(data, dict) else None
            if failure is None and status == 200 and not error:
                return data
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            detail = self.redact(
                failure or f"HTTP {status}, code {code}"
                + (f"/{error['error_subcode']}" if error.get("error_subcode") else "")
                + f": {error.get('message') or 'no message'}"
            )
            if code in AUTH_CODES:
                raise MetaAuthError(
                    f"Meta says the access token is invalid or expired ({detail}). Make a new "
                    f"system-user token with ads_read and put it in the tenant's .env; see {DOCS}."
                )
            if code in PERMISSION_CODES:
                raise MetaAuthError(
                    f"Meta refused {where}: the token has no permission for it ({detail}). The "
                    f"system user needs ads_read and access to the ad account; see {DOCS}."
                )
            retryable = (
                failure is not None or status == 429 or status >= 500
                or code in RATE_LIMIT_CODES or bool(error.get("is_transient"))
            )
            if not retryable or attempt >= self.max_retries:
                suffix = f" after {attempt} retries" if attempt else ""
                raise MetaAPIError(f"Meta API GET {where} failed{suffix}: {detail}")
            self._sleep(min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_MAX_S))
            attempt += 1

    def paged(self, path, params):
        """Every item of a list edge, following paging.next. The next URL
        must stay on graph.facebook.com; any access_token Meta echoes into
        it is removed, since the token travels in the header."""
        data = self.get(path, params)
        while True:
            yield from data.get("data") or []
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                return
            data = self._get_url(self._safe_next(next_url))

    def _safe_next(self, next_url):
        parts = urllib.parse.urlsplit(next_url)
        if parts.scheme != "https" or parts.hostname != GRAPH_HOST:
            raise MetaAPIError(f"refusing paging.next on another host: {parts.hostname!r}")
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "access_token"]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), "")
        )

    def download(self, url, dest, *, kind, max_bytes=MAX_MEDIA_BYTES):
        """Stream `url` to `dest` (no token sent). Refuses a non-https URL,
        a non-200 status, a content type ingest cannot read, and anything
        over `max_bytes`. A refused download leaves no file behind. Returns
        (dest, bytes, content_type)."""
        if urllib.parse.urlsplit(url).scheme != "https":
            raise MediaRejected(f"{kind} URL is not https")
        dest = Path(dest)
        status, headers, chunks = self._download(url, headers={"User-Agent": "harness-meta-ingest"})
        headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        try:
            if status != 200:
                raise MediaRejected(f"{kind} download failed: HTTP {status}")
            content_type = str(headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type not in MEDIA_TYPES[kind]:
                raise MediaRejected(f"{kind} download has content type {content_type or 'none'!r}, "
                                    f"expected one of {sorted(MEDIA_TYPES[kind])}")
            length = headers.get("content-length")
            if length and str(length).isdigit() and int(length) > max_bytes:
                raise MediaRejected(f"{kind} is {int(length)} bytes, over the {max_bytes}-byte cap")
            part = dest.with_name(dest.name + ".part")
            total = 0
            try:
                with open(part, "wb") as fh:
                    for chunk in chunks:
                        total += len(chunk)
                        if total > max_bytes:
                            raise MediaRejected(f"{kind} passed the {max_bytes}-byte cap while downloading")
                        fh.write(chunk)
                os.replace(part, dest)
            finally:
                if part.exists():
                    part.unlink()
            return dest, total, content_type
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()


# ---------------------------------------------------------------------------
# Listing and normalizing
# ---------------------------------------------------------------------------

def parse_meta_time(value):
    """Meta's "2026-09-24T09:15:02-0700" as an aware UTC datetime, or None."""
    try:
        parsed = datetime.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def list_new_ads(client, account_id, since):
    """Ads in the account created at or after `since`, oldest first.

    Meta filters by status and by `updated_since` (every ad created after
    the cutoff was also updated after it, so that is a safe superset); the
    created_time cutoff and the status list are then applied here."""
    params = {
        "fields": AD_FIELDS,
        "effective_status": list(INGEST_STATUSES),
        "updated_since": int(since.timestamp()),
        "limit": PAGE_SIZE,
    }
    ads = []
    for ad in client.paged(f"{account_id}/ads", params):
        if ad.get("effective_status") not in INGEST_STATUSES:
            continue
        created = parse_meta_time(ad.get("created_time"))
        if created is None or created < since:
            continue
        ads.append((created, ad))
    return [ad for _created, ad in sorted(ads, key=lambda pair: pair[0])]


def _first_text(items):
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else None
        if text:
            return text
    return ""


def media_candidates(creative):
    """Every video and image the creative carries, in the order Meta shows
    them: the creative's own media, then story-spec media, carousel cards,
    and asset_feed_spec (flexible / dynamic creative)."""
    spec = creative.get("object_story_spec") or {}
    video_data = spec.get("video_data") or {}
    link_data = spec.get("link_data") or {}
    photo_data = spec.get("photo_data") or {}
    feed = creative.get("asset_feed_spec") or {}
    out = []

    def video(video_id, source):
        if video_id:
            out.append({"kind": "video", "video_id": str(video_id), "source": source})

    def image(image_hash=None, url=None, source=""):
        if image_hash:
            out.append({"kind": "image", "image_hash": image_hash, "source": source})
        elif url:
            out.append({"kind": "image", "url": url, "source": source})

    video(creative.get("video_id"), "creative.video_id")
    video(video_data.get("video_id"), "object_story_spec.video_data")
    for i, card in enumerate(link_data.get("child_attachments") or []):
        video(card.get("video_id"), f"object_story_spec.link_data.child_attachments[{i}]")
        image(card.get("image_hash"), card.get("picture"),
              f"object_story_spec.link_data.child_attachments[{i}]")
    for i, item in enumerate(feed.get("videos") or []):
        video(item.get("video_id"), f"asset_feed_spec.videos[{i}]")
    image(creative.get("image_hash"), None, "creative.image_hash")
    image(link_data.get("image_hash"), link_data.get("picture"), "object_story_spec.link_data")
    image(photo_data.get("image_hash"), photo_data.get("url"), "object_story_spec.photo_data")
    for i, item in enumerate(feed.get("images") or []):
        image(item.get("hash"), item.get("url"), f"asset_feed_spec.images[{i}]")
    image(None, creative.get("image_url"), "creative.image_url")

    unique, seen = [], set()
    for c in out:
        key = (c["kind"], c.get("video_id") or c.get("image_hash") or c.get("url"))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def choose_media(candidates):
    """The first video if there is one, else the first image, else None."""
    for kind in ("video", "image"):
        for c in candidates:
            if c["kind"] == kind:
                return c
    return None


def normalize_ad(raw):
    """ad.json's fields from one raw /ads item."""
    creative = raw.get("creative") or {}
    spec = creative.get("object_story_spec") or {}
    video_data = spec.get("video_data") or {}
    link_data = spec.get("link_data") or {}
    template = spec.get("template_data") or {}
    feed = creative.get("asset_feed_spec") or {}
    cta_block = video_data.get("call_to_action") or link_data.get("call_to_action") or {}
    cta_types = feed.get("call_to_action_types") or []

    primary_text = (link_data.get("message") or video_data.get("message") or creative.get("body")
                    or _first_text(feed.get("bodies")) or template.get("message") or "")
    headline = (link_data.get("name") or video_data.get("title") or creative.get("title")
                or _first_text(feed.get("titles")) or "")
    description = (link_data.get("description") or video_data.get("link_description")
                   or _first_text(feed.get("descriptions")) or "")
    cta = cta_block.get("type") or creative.get("call_to_action_type") or (cta_types[0] if cta_types else "")
    link_urls = feed.get("link_urls") or []
    destination = (link_data.get("link") or (cta_block.get("value") or {}).get("link")
                   or creative.get("link_url")
                   or (link_urls[0].get("website_url") if link_urls and isinstance(link_urls[0], dict) else "")
                   or template.get("link") or "")

    def pair(node):
        node = node or {}
        return {"id": node.get("id", ""), "name": node.get("name", "")}

    return {
        "ad_id": str(raw.get("id") or ""),
        "ad_name": raw.get("name") or "",
        "created_time": raw.get("created_time") or "",
        "effective_status": raw.get("effective_status") or "",
        "adset": pair(raw.get("adset")),
        "campaign": pair(raw.get("campaign")),
        "creative_id": str(creative.get("id") or ""),
        "creative_name": creative.get("name") or "",
        "object_type": creative.get("object_type") or "",
        "primary_text": primary_text,
        "headline": headline,
        "description": description,
        "cta": cta,
        "destination_url": destination,
        "url_tags": creative.get("url_tags") or "",
        "thumbnail_url": creative.get("thumbnail_url") or "",
        "media_candidates": media_candidates(creative),
        "meta": raw,
    }


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

STATES = ("new", "queued", "building", "tested", "failed", "skipped")
# new: pulled, media on disk. queued: picked for a test. building: the test
# pages are being built. tested: done. failed / skipped carry a reason.
TRANSITIONS = {
    "new": {"queued", "skipped", "failed"},
    "queued": {"building", "skipped", "failed"},
    "building": {"tested", "failed"},
    "tested": set(),
    "failed": {"new", "queued", "skipped"},
    "skipped": {"new", "queued"},
}
# States the pull stage itself sets; a refresh may recompute these. A later
# state (queued/building/tested) belongs to the next stage and is kept.
PULL_STATES = {"new", "failed", "skipped"}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


# Cycle 69: an ad uploaded on the listicle site (harness/upload.py) gets an
# inbox item too, with the id "up-<8 lowercase letters/digits>".
UPLOAD_ID_RE = re.compile(r"up-[a-z0-9]{8}")


class Inbox:
    """tenants/<t>/meta_inbox/<ad_id>/ad.json plus one media file."""

    def __init__(self, root):
        self.root = Path(root)

    def item_dir(self, ad_id):
        ad_id = str(ad_id)
        if not (ad_id.isdigit() and ad_id.isascii()) and not UPLOAD_ID_RE.fullmatch(ad_id):
            raise ValueError(f"not a Meta ad id or an upload id: {ad_id!r}")
        return self.root / ad_id

    def read(self, ad_id):
        path = self.item_dir(ad_id) / "ad.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def write(self, item):
        directory = self.item_dir(item["ad_id"])
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "ad.json.tmp"
        tmp.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, directory / "ad.json")

    def items(self):
        if not self.root.is_dir():
            return []
        found = []
        for path in sorted(self.root.glob("*/ad.json")):
            try:
                found.append(json.loads(path.read_text()))
            except ValueError:
                found.append({"ad_id": path.parent.name, "state": "?", "reason": "ad.json is not valid JSON"})
        return sorted(found, key=lambda i: (i.get("created_time") or "", i.get("ad_id") or ""))

    def set_state(self, ad_id, state, reason=""):
        """Move an item along new -> queued -> building -> tested (or to
        failed / skipped), with a reason and a history entry."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}; one of {', '.join(STATES)}")
        item = self.read(ad_id)
        if item is None:
            raise ValueError(f"no inbox item for ad {ad_id}")
        current = item.get("state")
        if state not in TRANSITIONS.get(current, set()):
            raise ValueError(f"ad {ad_id}: {current} -> {state} is not allowed")
        _record(item, state, reason)
        self.write(item)
        return item

    def note(self, ad_id, reason, **fields):
        """Cycle 69: a new reason (and extra fields, e.g. test_id) on an
        item that keeps its state -- a "building" item the budget cap
        stopped, say. Adds a history entry like set_state does."""
        item = self.read(ad_id)
        if item is None:
            raise ValueError(f"no inbox item for ad {ad_id}")
        item.update(fields)
        _record(item, item.get("state"), reason)
        self.write(item)
        return item


def _record(item, state, reason):
    at = _now()
    item["state"] = state
    item["reason"] = reason
    item["updated_at"] = at
    item.setdefault("history", []).append({"state": state, "at": at, "reason": reason})


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def _resolve_and_download(client, account_id, directory, ad_id, chosen, max_bytes):
    """Download the chosen media into `directory`. Returns the ad.json media
    fields."""
    fields = {"media_type": chosen["kind"], "media_source": chosen["source"]}
    if chosen["kind"] == "video":
        video = client.get(chosen["video_id"], {"fields": VIDEO_FIELDS})
        fields["video"] = {
            "id": chosen["video_id"], "length": video.get("length"),
            "title": video.get("title") or "", "picture": video.get("picture") or "",
        }
        url = video.get("source")
        if not url:
            raise MediaRejected(
                f"video {chosen['video_id']} has no source URL in the Graph response -- "
                "the token may not have access to the Page that owns the video"
            )
    else:
        url = chosen.get("url")
        if chosen.get("image_hash"):
            found = client.get(f"{account_id}/adimages",
                               {"hashes": [chosen["image_hash"]], "fields": ADIMAGE_FIELDS})
            match = next((i for i in found.get("data") or [] if i.get("hash") == chosen["image_hash"]), None)
            if match:
                url = match.get("url") or url
                fields["image"] = {
                    "hash": chosen["image_hash"], "width": match.get("width"),
                    "height": match.get("height"), "name": match.get("name") or "",
                }
        if not url:
            raise MediaRejected(f"image {chosen.get('image_hash')} has no URL in the Graph response")
    stem = directory / f"meta-{ad_id}"
    part_name = stem.with_suffix(".download")
    _, size, content_type = client.download(url, part_name, kind=chosen["kind"], max_bytes=max_bytes)
    final = stem.with_suffix(MEDIA_TYPES[chosen["kind"]][content_type])
    os.replace(part_name, final)
    fields.update({"media_file": final.name, "media_bytes": size, "media_content_type": content_type})
    return fields


def ingest_one(client, inbox, account_id, raw, existing=None, *, max_bytes=MAX_MEDIA_BYTES):
    """Write one ad into the inbox. Returns the item. A media failure is
    recorded on the item (state failed), never raised; an auth failure is
    raised, since every later ad would fail the same way."""
    norm = normalize_ad(raw)
    ad_id = norm["ad_id"]
    directory = inbox.item_dir(ad_id)
    directory.mkdir(parents=True, exist_ok=True)
    item = dict(existing or {})
    for key in ("media_type", "media_source", "media_file", "media_bytes", "media_content_type",
                "video", "image", "pull_error"):
        item.pop(key, None)
    item.update(norm)
    item["media_type"], item["media_file"] = "", ""
    item["pulled_at"] = _now()

    chosen = choose_media(norm["media_candidates"])
    if chosen is None:
        state = "skipped"
        reason = f"creative has no video or image to build from (object_type={norm['object_type'] or 'none'})"
    else:
        try:
            item.update(_resolve_and_download(client, account_id, directory, ad_id, chosen, max_bytes))
            state, reason = "new", ""
        except MetaAuthError:
            raise
        except (MediaRejected, MetaAPIError) as e:
            state, reason = "failed", client.redact(str(e))
            item["pull_error"] = reason

    if existing and existing.get("state") not in PULL_STATES:
        # The next stage owns this item now; keep its state.
        _record(item, existing["state"], "refreshed from Meta" if state == "new" else f"refresh: {reason}")
        item["reason"] = existing.get("reason", "")
    else:
        _record(item, state, reason)
    inbox.write(item)
    return item


def pull(client, inbox, *, account_id, since, limit=None, refresh=False, dry_run=False,
         max_bytes=MAX_MEDIA_BYTES, log=print):
    """Ingest every ad created at or after `since` that is not in the inbox.

    An item already in the inbox is left alone, except one whose previous
    pull failed (retried) or with `refresh`. `limit` caps how many ads this
    call ingests. `dry_run` lists them and writes nothing."""
    summary = {"listed": 0, "ingested": [], "existing": [], "failed": [], "skipped": [], "would_ingest": []}
    ads = list_new_ads(client, account_id, since)
    summary["listed"] = len(ads)
    log(f"{len(ads)} ad(s) in {account_id} created since {since.date().isoformat()}")
    done = 0
    for raw in ads:
        ad_id = str(raw.get("id") or "")
        existing = inbox.read(ad_id)
        retry = existing is not None and existing.get("state") == "failed" and existing.get("pull_error")
        if existing is not None and not refresh and not retry:
            summary["existing"].append(ad_id)
            continue
        if limit is not None and done >= limit:
            break
        done += 1
        if dry_run:
            norm = normalize_ad(raw)
            chosen = choose_media(norm["media_candidates"])
            summary["would_ingest"].append(ad_id)
            log(f"would ingest {ad_id} ({chosen['kind'] if chosen else 'no media'}) "
                f"created {norm['created_time']}: {norm['ad_name']}")
            continue
        item = ingest_one(client, inbox, account_id, raw, existing, max_bytes=max_bytes)
        state = item["state"]
        if item.get("pull_error"):
            summary["failed"].append(ad_id)
            log(f"failed {ad_id}: {item['pull_error']}")
        elif state == "skipped":
            summary["skipped"].append(ad_id)
            log(f"skipped {ad_id}: {item['reason']}")
        else:
            summary["ingested"].append(ad_id)
            log(f"ingested {ad_id} ({item['media_type']}) -> "
                f"{inbox.item_dir(ad_id) / item['media_file']}")
    return summary


def check(client, account_id):
    """The two calls `harness meta check` makes."""
    me = client.get("me", {"fields": "id,name"})
    account = client.get(account_id, {"fields": "name,account_status"})
    return {"me": me, "account": account}
