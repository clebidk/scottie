"""Cycle 67: the A/B/C test event store and the beacon receiver's rules.

Every variant page sends {t: test_id, v: key, e: "view"|"cta", vid} to the
tenant's `abtest.beacon_url` (harness/abtest.py's beacon_script). harness/
serve.py's public `POST /e` route checks it here and appends one row to
`tenants/<t>/abtests/events.sqlite` (WAL mode, so the CLI can read while the
server writes).

One row per (test, variant, visitor, event): the UNIQUE constraint makes a
repeat view or a second CTA click from the same visitor a no-op, so a count of
rows is a count of unique visitors. The client IP is never stored or logged:
only a 16-hex-char salted SHA-256 of it (the salt is random per database and
lives in its `meta` table), used for nothing but rate limiting.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time

MAX_BODY_BYTES = 1024
RATE_LIMIT_PER_MIN = 60
# Cycle 88: "assign" comes from the split page itself, once per visitor and
# arm, before it redirects -- the only count for an external arm (the PDP or
# home page carries no beacon). Same receiver, same rate limit.
# Cycle 110: "sms_tap" (a tap on an sms: link -- not a CTA, its own metric)
# and "leave" (sent on pagehide / hide: the deepest scroll, the seconds on
# the page and the load timings). Optional fields, all checked and bounded
# (wire name -> column): el -> el, y -> y (scroll depth % at the click),
# m -> max_scroll (%), s -> secs, lcp/fcp/dcl -> lcp_ms/fcp_ms/dcl_ms,
# hid -> hidden_ms (ms to the first interaction or the first hide), vh, vw
# (viewport px), cn -> conn (effective connection type). Old rows have NULLs.
EVENTS = ("view", "cta", "assign", "sms_tap", "leave")
_COUNT_FIELD = {"view": "views", "cta": "clicks", "assign": "assigns", "sms_tap": "sms_taps"}
ELEMENTS = ("hero", "mid", "cards", "reviews", "closing", "sticky", "sms", "other")
CONNECTIONS = ("slow-2g", "2g", "3g", "4g")
_INT_FIELDS = {  # wire name: (column, max)
    "y": ("y", 100), "m": ("max_scroll", 100), "s": ("secs", 86400), "lcp": ("lcp_ms", 600000),
    "fcp": ("fcp_ms", 600000), "dcl": ("dcl_ms", 600000), "hid": ("hidden_ms", 86400000),
    "vh": ("vh", 20000), "vw": ("vw", 20000),
}
EXTRA_COLUMNS = (("el", "TEXT"), ("conn", "TEXT")) + tuple((col, "INTEGER") for col, _ in _INT_FIELDS.values())

VID_RE = re.compile(r"^[a-z0-9]{8,40}$")
# Crawlers, link previewers (Meta fetches every ad destination with
# facebookexternalhit), headless browsers and scripted clients. An empty user
# agent is treated as a bot too.
BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|facebookcatalog|meta-externalagent|"
    r"headless|phantom|lighthouse|pagespeed|preview|curl|wget|python-|httpclient|"
    r"java/|go-http|okhttp|axios|node-fetch|scrapy|monitor|uptime",
    re.IGNORECASE,
)


def db_path(tenant):
    return tenant.abtests_dir / "events.sqlite"


def connect(path):
    """Open (creating when needed) the events database in WAL mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        " id INTEGER PRIMARY KEY, ts REAL NOT NULL, test_id TEXT NOT NULL, variant TEXT NOT NULL,"
        " event TEXT NOT NULL, vid TEXT NOT NULL, iph TEXT NOT NULL,"
        " UNIQUE (test_id, variant, vid, event))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    have = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for col, kind in EXTRA_COLUMNS:  # cycle 110: added in place; old rows keep NULL
        if col not in have:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {kind}")
    conn.commit()
    return conn


def ip_salt(conn):
    row = conn.execute("SELECT v FROM meta WHERE k = 'ip_salt'").fetchone()
    if row:
        return row[0]
    conn.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('ip_salt', ?)", (secrets.token_hex(16),))
    conn.commit()
    return conn.execute("SELECT v FROM meta WHERE k = 'ip_salt'").fetchone()[0]


def hash_ip(ip, salt):
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


def record_event(conn, *, test_id, key, event, vid, iph, ts=None, extra=None):
    """True when the row is new; False for a duplicate. Cycle 110: `extra`
    ({column: value}, from parse_event) is stored with the row. A repeat
    "leave" from the same visitor updates its row instead: the deepest
    scroll and the longest stay win, and the first load timings are kept."""
    extra = {k: v for k, v in (extra or {}).items() if v is not None}
    cols = ["ts", "test_id", "variant", "event", "vid", "iph"] + list(extra)
    vals = [ts if ts is not None else time.time(), test_id, key, event, vid, iph] + list(extra.values())
    sql = f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    if event == "leave" and extra:
        sets = ", ".join(
            f"{c} = MAX(COALESCE({c}, 0), excluded.{c})" if c in ("max_scroll", "secs")
            else f"{c} = COALESCE({c}, excluded.{c})" for c in extra)
        sql += f" ON CONFLICT (test_id, variant, vid, event) DO UPDATE SET {sets}"
    else:
        sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
    cur = conn.execute(sql, vals)
    conn.commit()
    return cur.rowcount == 1


def counts(path, test_id):
    """{key: {"views": n, "clicks": n}} of unique visitors per variant, or {}
    when there is no database yet. Cycle 88: plus "assigns": n for a key
    that has split-page assign events (absent otherwise)."""
    if not path.exists():
        return {}
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        rows = conn.execute(
            "SELECT variant, event, COUNT(DISTINCT vid) FROM events WHERE test_id = ? GROUP BY variant, event",
            (test_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    out = {}
    for variant, event, n in rows:
        field = _COUNT_FIELD.get(event)
        if field is None:
            continue
        slot = out.setdefault(variant, {"views": 0, "clicks": 0})
        slot[field] = n
    return out


def archive_variant(path, test_id, key, archived_key):
    """Cycle 69 ("Replace live variant"): move every event of (test_id, key)
    to the key `archived_key` (e.g. "A@1"). The variant's counts start again
    at zero, the old rows are kept, and a returning visitor's first event on
    the new page is not a duplicate. The beacon receiver never accepts an
    archived key (it is not a variant of the test). Returns the rows moved."""
    conn = connect(path)
    try:
        cur = conn.execute("UPDATE events SET variant = ? WHERE test_id = ? AND variant = ?",
                           (archived_key, test_id, key))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def parse_event(raw):
    """(event dict, None) or (None, reason). Checks shape only; whether the
    test and key exist is the caller's check."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "not json"
    if not isinstance(data, dict):
        return None, "not an object"
    t, v, e, vid = (data.get(k) for k in ("t", "v", "e", "vid"))
    if not all(isinstance(x, str) for x in (t, v, e, vid)):
        return None, "missing field"
    if e not in EVENTS:
        return None, "unknown event"
    if not VID_RE.match(vid):
        return None, "bad vid"
    event = {"t": t, "v": v, "e": e, "vid": vid}
    extra = _extra_fields(data, e)
    if extra:
        event["extra"] = extra
    return event, None


def _extra_fields(data, event):
    """Cycle 110: the optional fields, bounded; anything malformed is dropped
    (never the whole event)."""
    extra = {}
    if event in ("cta", "sms_tap"):
        el = data.get("el")
        extra["el"] = el if el in ELEMENTS else ("sms" if event == "sms_tap" else "other")
    for wire, (col, top) in _INT_FIELDS.items():
        value = data.get(wire)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if 0 <= value <= top:
            extra[col] = int(value)
    if data.get("cn") in CONNECTIONS:
        extra["conn"] = data["cn"]
    return extra


def engagement(path, test_id):
    """{key: {...}} per variant, from the cycle 110 fields: clicks by
    element (each visitor's first CTA click), the scroll depth at that click,
    sms taps, the deepest-scroll distribution and the seconds on the page of
    visitors who left, the LCP figures and the share of small screens. {}
    when there is no database yet."""
    if not path.exists():
        return {}
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "el" not in cols:
            return {}
        rows = conn.execute(
            "SELECT variant, event, el, y, max_scroll, secs, lcp_ms, vh FROM events WHERE test_id = ? "
            "AND event IN ('cta', 'sms_tap', 'leave', 'view')", (test_id,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    out = {}
    for variant, event, el, y, max_scroll, secs, lcp, vh in rows:
        slot = out.setdefault(variant, {"by_el": {}, "click_depth": [], "sms_taps": 0, "max_scroll": [],
                                        "secs": [], "lcp_ms": [], "vh": []})
        if event == "cta":
            slot["by_el"][el or "unknown"] = slot["by_el"].get(el or "unknown", 0) + 1
            if y is not None:
                slot["click_depth"].append(y)
        elif event == "sms_tap":
            slot["sms_taps"] += 1
        elif event == "leave":
            for field, value in (("max_scroll", max_scroll), ("secs", secs), ("lcp_ms", lcp)):
                if value is not None:
                    slot[field].append(value)
        elif event == "view" and vh is not None:
            slot["vh"].append(vh)
    return out


def is_bot(user_agent):
    return not user_agent or bool(BOT_UA_RE.search(user_agent))


class RateLimiter:
    """At most `limit` accepted events per key per 60 s (a sliding window)."""

    def __init__(self, limit=None, window_s=60.0, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        limit = self.limit if self.limit is not None else RATE_LIMIT_PER_MIN
        now = self.clock()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window_s]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 10000:  # keep memory bounded
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window_s}
            return True
