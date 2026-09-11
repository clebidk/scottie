"""Google Drive, read-only, by file id -- no OAuth.

An ad, or an image asset, is usually handed over as a Drive link rather than a
local path. This adapter turns either form of that link (or a bare id) into a
file on disk, following the HTML confirm-token interstitial Drive serves for
large files.
"""
import http.cookiejar
import re
import urllib.request
from pathlib import Path

from ..errors import HarnessError
from ..textutil import safe_filename


_FILE_D_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")
_ID_PARAM_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{15,}$")

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

FOLDER_NOT_PUBLIC_MESSAGE = (
    "Drive folder is not link-public; share it as Anyone with the link, or "
    "upload the files into tenants/<t>/brand/incoming/ and rerun with --local"
)


class DriveFolderNotPublic(HarnessError):
    """A `harness brand import --drive-folder` folder that Drive would not
    serve an anonymous listing for -- not shared "Anyone with the link"."""


def parse_drive_id(input_arg):
    """Extract a Google Drive file id from a /file/d/<id>/, ?id=<id>, or bare-id
    string. Returns None if none of the three patterns match."""
    m = _FILE_D_RE.search(input_arg)
    if m:
        return m.group(1)
    m = _ID_PARAM_RE.search(input_arg)
    if m:
        return m.group(1)
    if _BARE_ID_RE.match(input_arg):
        return input_arg
    return None


def download_drive_file(file_id, dest_dir):
    """Download a public Google Drive file by id, without OAuth, following the
    HTML confirm-token interstitial for large files."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = opener.open(url, timeout=60)
    content_type = resp.headers.get("Content-Type", "")
    data = resp.read()
    headers = resp.headers

    if "text/html" in content_type or data[:512].lstrip()[:15].lower().startswith(b"<!doctype html") or b"<html" in data[:2000].lower():
        html = data.decode("utf-8", errors="ignore")
        confirm_m = re.search(r'confirm=([0-9A-Za-z_-]+)', html)
        uuid_m = re.search(r'name="uuid"\s+value="([^"]+)"', html)
        token = confirm_m.group(1) if confirm_m else "t"
        uuid = uuid_m.group(1) if uuid_m else ""
        url2 = (
            "https://drive.usercontent.google.com/download"
            f"?id={file_id}&export=download&confirm={token}&uuid={uuid}"
        )
        resp = opener.open(url2, timeout=60)
        data = resp.read()
        headers = resp.headers

    cd = headers.get("Content-Disposition", "")
    fname_m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    # Cycle 22 finding R36: this filename comes from the REMOTE server, so it
    # is untrusted input. Unsanitised, a Content-Disposition of
    # `filename="../../../etc/x"` wrote outside the run directory.
    filename = safe_filename(fname_m.group(1) if fname_m else file_id, fallback=file_id)

    dest = dest_dir / filename
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------------------
# Folder listing (Cycle 27) -- still no OAuth: the public folder HTML itself,
# not the Drive API.
#
# Verified against a real folder (Cycle 27 server verification, see
# docs/FIXLOG.md): each row Drive renders is a `<div ... aria-label="<name>
# <type>? Shared|Limited access|..." ... ssk='<n>:<code>:<file id>-<n>-<n>'>`
# -- the file id lives inside `ssk`, not in a `data-id` attribute (an earlier
# version of this parser assumed `data-id`, which never actually appears per
# item on a real folder page; Drive repeats the same id across several rows
# per item -- the name row, a "Modified ..." row, a "Size ..." row, a "More
# actions" row -- so only the FIRST aria-label seen for a given id is kept).
# A folder that is NOT shared "Anyone with the link" instead serves a
# sign-in page with none of this.
# ---------------------------------------------------------------------------

_ARIA_SSK_ENTRY_RE = re.compile(
    r'aria-label="([^"]+)"[^>]*?ssk=[\'"]\d+:[A-Za-z0-9_]+:([A-Za-z0-9_-]{15,})-\d+-\d+[\'"]'
)

# Google Drive's own name for a sign-in wall -- present when the folder is
# not link-public, absent on a real (even empty) public folder listing.
_SIGNIN_WALL_MARKERS = (
    "accounts.google.com/signin",
    "accounts.google.com/servicelogin",
    "sign in - google accounts",
    "you need access",
    "request access",
)

# Trailing words Drive's accessibility label appends after the real
# filename -- a sharing-status phrase (rightmost), then optionally a
# mimetype-derived type word right before it. Stripped in that order so
# "Acme BRAND GUIDE.pdf PDF Shared" -> "Acme BRAND GUIDE.pdf PDF" -> "Acme BRAND GUIDE.pdf".
_ARIA_STATUS_SUFFIXES = ("Shared folder", "Shared", "Limited access", "Owned by me", "Private")
_ARIA_TYPE_SUFFIXES = (
    "Image", "PDF", "PostScript", "Document", "Spreadsheet", "Presentation",
    "Video", "Audio", "Text", "Archive", "Folder",
)


def _clean_entry_name(raw):
    name = raw
    for status in _ARIA_STATUS_SUFFIXES:
        suffix = " " + status
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for type_word in _ARIA_TYPE_SUFFIXES:
        suffix = " " + type_word
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def default_fetch_folder_html(folder_id):
    """Real fetch of a Drive folder's public HTML listing, with a browser
    User-Agent (an unadorned urllib request gets a stripped-down page with no
    file entries)."""
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def list_public_folder(folder_id, fetch=None):
    """[{"id": ..., "name": ...}, ...] for every file Drive's own public HTML
    listing for `folder_id` shows, deduplicated by id (first name seen wins --
    Drive's page can repeat an entry for a grid vs. list view, and does
    repeat one item's id across several rows -- name, modified date, size,
    "more actions" -- of which only the first carries the real name).

    `fetch` is an injectable `folder_id -> html str` callable so tests never
    touch the network; defaults to `default_fetch_folder_html`.

    Raises DriveFolderNotPublic (with the exact operator-facing message the
    tenant-onboarding flow expects) when the fetched page is a sign-in wall
    instead of a listing."""
    fetch = fetch or default_fetch_folder_html
    html = fetch(folder_id)

    entries = []
    seen_ids = set()
    for raw_name, file_id in _ARIA_SSK_ENTRY_RE.findall(html):
        if file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        entries.append({"id": file_id, "name": _clean_entry_name(raw_name)})

    if not entries:
        lowered = html.lower()
        if any(marker in lowered for marker in _SIGNIN_WALL_MARKERS):
            raise DriveFolderNotPublic(FOLDER_NOT_PUBLIC_MESSAGE)
    return entries


