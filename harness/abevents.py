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
EVENTS = ("view", "cta")

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


def record_event(conn, *, test_id, key, event, vid, iph, ts=None):
    """True when the row is new; False for a duplicate."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO events (ts, test_id, variant, event, vid, iph) VALUES (?, ?, ?, ?, ?, ?)",
        (ts if ts is not None else time.time(), test_id, key, event, vid, iph),
    )
    conn.commit()
    return cur.rowcount == 1


def counts(path, test_id):
    """{key: {"views": n, "clicks": n}} of unique visitors per variant, or {}
    when there is no database yet."""
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
        slot = out.setdefault(variant, {"views": 0, "clicks": 0})
        slot["views" if event == "view" else "clicks"] = n
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
    return {"t": t, "v": v, "e": e, "vid": vid}, None


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
