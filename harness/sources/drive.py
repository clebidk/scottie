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


_FILE_D_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")
_ID_PARAM_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{15,}$")


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
    filename = fname_m.group(1) if fname_m else file_id

    dest = dest_dir / filename
    dest.write_bytes(data)
    return dest


