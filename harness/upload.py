"""Cycle 69: an ad uploaded on the listicle site becomes an inbox item.

The item has the same shape as a Meta inbox item (harness/meta_ingest.py,
docs/META-INGEST.md): tenants/<t>/meta_inbox/<ad_id>/ad.json plus one media
file, with ad_id "up-<8 random characters>" and source "upload". The next
step (a create-test job, harness/abtest_inbox.py) treats both kinds alike.

The media type comes from the file's first bytes, not from its name. The
extension must also be one this harness accepts and must agree with the
bytes. The uploaded file name is never used as a path: the file is saved as
"upload-<ad_id><ext>" with the extension of the detected type.
"""
import datetime
import os
import secrets

from . import meta_ingest

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 1024 * 1024
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

# extension on the uploaded name -> the detected extensions it may hold
ALLOWED_EXTENSIONS = {
    ".mp4": {".mp4", ".mov"},
    ".mov": {".mov", ".mp4"},
    ".jpg": {".jpg"},
    ".jpeg": {".jpg"},
    ".png": {".png"},
    ".webp": {".webp"},
}
CONTENT_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".jpg": "image/jpeg",
                 ".png": "image/png", ".webp": "image/webp"}
# QuickTime files that do not start with an ftyp box start with one of these.
_QT_ATOMS = (b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")
COPY_FIELDS = ("primary_text", "headline", "description", "cta")
MAX_NAME_CHARS = 200
MAX_COPY_CHARS = 2000


class UploadRejected(Exception):
    """A refused upload: `status` is the HTTP status (400 or 413)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def sniff(head):
    """(kind, ext, content_type) from a file's first bytes, or None."""
    head = bytes(head or b"")
    if head.startswith(b"\xff\xd8\xff"):
        ext = ".jpg"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = ".png"
    elif len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = ".webp"
    elif len(head) >= 12 and head[4:8] == b"ftyp":
        ext = ".mov" if head[8:12] == b"qt  " else ".mp4"
    elif len(head) >= 8 and head[4:8] in _QT_ATOMS:
        ext = ".mov"
    else:
        return None
    kind = "image" if ext in (".jpg", ".png", ".webp") else "video"
    return kind, ext, CONTENT_TYPES[ext]


def _extension(filename):
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return os.path.splitext(name)[1].lower()


def new_upload_id():
    return "up-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(8))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def save_upload(tenant, stream, filename, *, ad_name, copy=None, by, max_bytes=None):
    """Checks and saves one upload as an inbox item in state "new". Returns
    the item. Raises UploadRejected (nothing is left on disk)."""
    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    ad_name = " ".join(str(ad_name or "").split())
    if not ad_name:
        raise UploadRejected("Ad name is required.")
    if len(ad_name) > MAX_NAME_CHARS:
        raise UploadRejected(f"Ad name is longer than {MAX_NAME_CHARS} characters.")
    copy = {k: str(v or "").strip() for k, v in (copy or {}).items() if k in COPY_FIELDS}
    for key, value in copy.items():
        if len(value) > MAX_COPY_CHARS:
            raise UploadRejected(f"{key} is longer than {MAX_COPY_CHARS} characters.")
    if stream is None:
        raise UploadRejected("Choose a video or image file.")
    ext_given = _extension(filename)
    if ext_given not in ALLOWED_EXTENSIONS:
        raise UploadRejected("The file must be a video (mp4, mov) or an image (jpg, png, webp).")

    head = stream.read(64)
    detected = sniff(head)
    if detected is None or detected[1] not in ALLOWED_EXTENSIONS[ext_given]:
        raise UploadRejected("The file content is not a valid mp4, mov, jpg, png or webp file "
                             "(or does not match its extension).")
    kind, ext, content_type = detected

    inbox = meta_ingest.Inbox(tenant.meta_inbox_dir)
    ad_id = new_upload_id()
    directory = inbox.item_dir(ad_id)
    directory.mkdir(parents=True, exist_ok=False)
    media_name = f"upload-{ad_id}{ext}"
    part = directory / (media_name + ".part")
    total = 0
    try:
        with open(part, "wb") as fh:
            chunk = head
            while chunk:
                total += len(chunk)
                if total > max_bytes:
                    raise UploadRejected(f"The file is over the {max_bytes // (1024 * 1024)} MB limit.", 413)
                fh.write(chunk)
                chunk = stream.read(_CHUNK)
        os.replace(part, directory / media_name)
    except BaseException:
        if part.exists():
            part.unlink()
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        raise

    item = {
        "ad_id": ad_id, "ad_name": ad_name, "created_time": _now(), "source": "upload",
        "uploaded_by": by, "effective_status": "", "adset": {"id": "", "name": ""},
        "campaign": {"id": "", "name": ""}, "creative_id": "", "creative_name": "", "object_type": "",
        "primary_text": copy.get("primary_text", ""), "headline": copy.get("headline", ""),
        "description": copy.get("description", ""), "cta": copy.get("cta", ""),
        "destination_url": "", "url_tags": "", "thumbnail_url": "", "media_candidates": [],
        "media_type": kind, "media_source": "upload", "media_file": media_name,
        "media_bytes": total, "media_content_type": content_type,
        "state": "new", "reason": "", "history": [{"state": "new", "at": _now(), "reason": f"uploaded by {by}"}],
        "pulled_at": _now(), "updated_at": _now(),
    }
    inbox.write(item)
    return item
