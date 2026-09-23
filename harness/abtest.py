"""Cycle 67: A/B/C tests of page builds (`harness abtest`, docs/ABTEST.md).

Owner decision 2026-09-23: every new Meta ad gets three different page builds,
published as three hidden Shopify pages behind one SPLIT LINK that the team
pastes into the ad in Ads Manager (this harness never edits an ad). The winner
is the build with the highest CTA click-through: the share of unique visitors
on a variant who click a CTA to a product page. Orders are secondary.

The pieces:
- the LIBRARY of builds ("arms"), a tenant setting (`abtest.library`);
- arm selection: Thompson sampling over every live/finished test's pooled
  results, with an `abtest.explore` share of uniform random picks;
- the test RECORD, `tenants/<t>/abtests/<test_id>.json` (not under out/, so
  clearing old runs never deletes a test);
- the SPLIT script (the split page's whole body) and the tracking BEACON
  (added to a variant's Shopify export by harness/page_body.py);
- order attribution and the results/finish rules.
The CLI wiring, including the publish step that reuses `harness publish`,
is in harness/cli.py; the beacon receiver is harness/serve.py's POST /e.
"""
import argparse
import datetime
import json
import random
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import abevents, abstats, exits, listicle, looks, pipeline
from .errors import HarnessError

# The builds every tenant gets when tenant.yaml names none: the five listicle
# styles in the look the renderer pairs each with (harness/listicle.py
# LOOK_BY_STYLE), plus the comparison and quiz pages.
DEFAULT_LIBRARY = (
    "listicle:reasons:cards",
    "listicle:mistakes:editorial",
    "listicle:questions:scorecard",
    "listicle:myths:pillars",
    "listicle:tested:lander",
    "comparison",
    "quiz",
)
KEYS = ("A", "B", "C")
DEFAULT_EXPLORE = 0.2
DEFAULT_MIN_VIEWS = 300
FINISH_P_BEST = 0.95
# P(best) values closer than this are a tie that orders may break (finish --orders).
TIE_MARGIN = 0.05
MAX_ATTEMPTS = 3
POOLED_STATUSES = ("live", "finished")
TEST_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,70}[a-z0-9])?$")
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

# Keeps a test page out of the sitemap and search results. A page body cannot
# carry a <head> robots tag, so the page metafield is the storefront's own way.
SEO_HIDDEN_METAFIELD = {"namespace": "seo", "key": "hidden", "value": 1, "type": "number_integer"}

# The classes the arm templates put on an anchor that leads to a product page
# (every page.cta_url / model.url / card.url link). tests/
# test_abtest_cycle67.py checks every arm template against this list, so a new
# look with a new button class fails a test instead of silently not counting.
CTA_CLASSES = (
    "lst-btn", "ed-btn", "ed-link-cta", "ed-model-link", "ld-btn", "ld-link", "pil-btn",
    "sc-btn", "cmp-btn", "cmp-link", "qz-btn", "qz-link", "adv-cta", "pp-btn",
)


class AbtestError(HarnessError):
    """An A/B/C test command that cannot go on; one line, exit 1."""


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    id: str
    cartridge: str
    style: str = None
    look: str = None


def parse_arm(text, tenant=None):
    """"cartridge", "cartridge:look" or "listicle:style[:look]" -> Arm. A
    listicle entry with no look gets the tenant's own style -> look pairing,
    so the id always names the build that actually renders."""
    parts = [p.strip() for p in str(text).split(":")]
    cartridge, rest = parts[0], parts[1:]
    if cartridge not in pipeline.discover_cartridges():
        raise AbtestError(f"abtest library entry {text!r}: unknown cartridge {cartridge!r}")
    if cartridge == "listicle":
        if not rest or rest[0] not in listicle.STYLES:
            raise AbtestError(
                f"abtest library entry {text!r}: a listicle build needs a style, one of {list(listicle.STYLES)}"
            )
        style = rest[0]
        look = rest[1] if len(rest) > 1 else listicle.resolve_look(None, style=style, tenant=tenant)
        if look not in listicle.LOOKS or len(rest) > 2:
            raise AbtestError(f"abtest library entry {text!r}: unknown listicle look")
        return Arm(f"listicle:{style}:{look}", "listicle", style, look)
    if len(rest) > 1 or (rest and rest[0] not in looks.cartridge_looks(cartridge)):
        raise AbtestError(f"abtest library entry {text!r}: {cartridge} has no such look")
    look = rest[0] if rest else None
    return Arm(f"{cartridge}:{look}" if look else cartridge, cartridge, None, look)


def library(tenant):
    """The tenant's builds (tenant.yaml `abtest.library`, else
    DEFAULT_LIBRARY), in order, each once. At least three are needed."""
    entries = tenant.get("abtest.library") or DEFAULT_LIBRARY
    arms, seen = [], set()
    for entry in entries:
        arm = parse_arm(entry, tenant)
        if arm.id not in seen:
            seen.add(arm.id)
            arms.append(arm)
    if len(arms) < len(KEYS):
        raise AbtestError(f"abtest.library needs at least {len(KEYS)} different builds, has {len(arms)}")
    return arms


def settings(tenant):
    explore = tenant.get("abtest.explore")
    min_views = tenant.get("abtest.min_views")
    return {
        "explore": min(max(float(explore if explore is not None else DEFAULT_EXPLORE), 0.0), 1.0),
        "min_views": int(min_views if min_views is not None else DEFAULT_MIN_VIEWS),
        "beacon_url": (tenant.get("abtest.beacon_url") or "").strip(),
        # Cycle 69: publish a test as soon as its three builds exist (the
        # listicle site's upload and `harness abtest from-inbox`).
        "auto_publish": tenant.get("abtest.auto_publish") is True,
    }


# ---------------------------------------------------------------------------
# arm selection
# ---------------------------------------------------------------------------

def pooled_arm_stats(tenant):
    """{arm_id: {"views", "clicks", "tests"}} summed over every live or
    finished test's variants (unique visitors, from events.sqlite)."""
    db = abevents.db_path(tenant)
    stats = {}
    for rec in list_tests(tenant):
        if rec.get("status") not in POOLED_STATUSES:
            continue
        counts = abevents.counts(db, rec["test_id"])
        for v in rec.get("variants") or []:
            slot = stats.setdefault(v["arm"], {"views": 0, "clicks": 0, "tests": 0})
            # Cycle 69: a replaced variant's old events (archived key) are
            # still evidence for the same build.
            keys = [v["key"]] + [r["archived_key"] for r in v.get("replacements") or []]
            for key in keys:
                c = counts.get(key, {"views": 0, "clicks": 0})
                slot["views"] += c["views"]
                slot["clicks"] += min(c["clicks"], c["views"])
            slot["tests"] += 1
    return stats


def rank_arms(tenant, rng, *, stats=None):
    """Every library arm id, best first. With probability `abtest.explore`,
    or when no arm has a single view yet, a uniform random order; else one
    Thompson draw per arm from Beta(clicks + 1, views - clicks + 1)."""
    arms = [a.id for a in library(tenant)]
    stats = pooled_arm_stats(tenant) if stats is None else stats
    explore = settings(tenant)["explore"]
    roll = rng.random()
    has_data = any((stats.get(a) or {}).get("views", 0) > 0 for a in arms)
    if roll < explore or not has_data:
        return rng.sample(arms, len(arms))
    draws = {}
    for arm in arms:
        s = stats.get(arm) or {}
        draws[arm] = rng.betavariate(*abstats.beta_params(s.get("clicks", 0), s.get("views", 0)))
    return sorted(arms, key=lambda a: -draws[a])


def choose_arms(tenant, k=3, rng=None, *, stats=None):
    """The k distinct builds a new test runs (see rank_arms)."""
    return rank_arms(tenant, rng or random.Random(), stats=stats)[:k]


def selection_weights(tenant, *, stats=None, sims=2000):
    """{arm_id: share of simulated tests that would include it} -- what
    `harness abtest library` shows as the current sampling weight."""
    stats = pooled_arm_stats(tenant) if stats is None else stats
    hits = {a.id: 0 for a in library(tenant)}
    for i in range(sims):
        for arm in choose_arms(tenant, len(KEYS), random.Random(i), stats=stats):
            hits[arm] += 1
    return {a: n / sims for a, n in hits.items()}


# ---------------------------------------------------------------------------
# test record
# ---------------------------------------------------------------------------

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_test_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40].strip("-") or "test"
    return f"{slug}-{''.join(secrets.choice(_ID_ALPHABET) for _ in range(4))}"


def _test_path(tenant, test_id):
    if not TEST_ID_RE.match(test_id or ""):
        raise AbtestError(f"not a test id: {test_id!r}")
    return tenant.abtests_dir / f"{test_id}.json"


def save_test(tenant, rec):
    path = _test_path(tenant, rec["test_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    rec["updated_at"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n")
    tmp.replace(path)
    return path


def load_test(tenant, test_id):
    path = _test_path(tenant, test_id)
    if not path.exists():
        raise AbtestError(f"no A/B/C test {test_id!r} under {tenant.abtests_dir}")
    return json.loads(path.read_text())


def find_test(tenant, test_id):
    """The record, or None for an unknown or malformed id (never raises) --
    the beacon receiver's check."""
    try:
        return load_test(tenant, test_id)
    except (AbtestError, ValueError, OSError):
        return None


def list_tests(tenant):
    root = tenant.abtests_dir
    if not root.is_dir():
        return []
    recs = []
    for path in root.glob("*.json"):
        try:
            recs.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return sorted(recs, key=lambda r: (r.get("created_at") or "", r.get("test_id") or ""))


def variant_handle(test_id, key):
    return f"lp-{test_id}-{key.lower()}"


def split_handle(test_id):
    return f"lp-{test_id}"


# ---------------------------------------------------------------------------
# building the variants
# ---------------------------------------------------------------------------

def default_runner(tenant, input_path, arm, seed):
    """One `harness run --cartridges <c> [--style s] [--look l] --seed N`
    through the same pipeline stages cmd_run uses. Returns (exit code,
    run_dir or None)."""
    from . import config as harness_config
    from .anthropic_client import make_client

    args = argparse.Namespace(
        input=str(input_path), cartridges=arm.cartridge, seed=seed, style=arm.style, look=arm.look,
        product=None, batch=False, tenant=tenant.name,
        ffmpeg_bin=harness_config.FFMPEG_BIN, whisper_bin=harness_config.whisper_bin(),
        whisper_model=harness_config.whisper_model(),
    )
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    rc = pipeline.execute(state, pipeline.DEFAULT_STAGES)
    return rc, state.run_dir


def _build_variant(tenant, rec, variant, runner):
    """Up to MAX_ATTEMPTS runs of the variant's current arm, each with a new
    seed. Returns "built", "budget" or "failed"."""
    arm = parse_arm(variant["arm"], tenant)
    tries = [a for a in variant["attempts"] if a["arm"] == variant["arm"] and a["rc"] != exits.BUDGET]
    while len(tries) < MAX_ATTEMPTS:
        seed = rec["seed"] + 1000 * (KEYS.index(variant["key"]) + 1) + len(variant["attempts"]) + 1
        try:
            rc, run_dir = runner(tenant, rec["input"], arm, seed)
        except HarnessError as e:
            rc, run_dir = e.exit_code, None
        attempt = {"arm": variant["arm"], "seed": seed, "rc": rc,
                   "run_dir": str(run_dir) if run_dir else None, "at": _now()}
        variant["attempts"].append(attempt)
        save_test(tenant, rec)
        if rc == exits.BUDGET:
            return "budget"
        if rc == 0 and run_dir and (Path(run_dir) / arm.cartridge / "index.html").exists():
            variant.update(run_dir=str(run_dir), cartridge=arm.cartridge, status="built")
            save_test(tenant, rec)
            return "built"
        tries.append(attempt)
    return "failed"


def build_variants(tenant, rec, runner):
    """Builds every variant that has no run yet. Returns (exit code, rec):
    0 and status "built"; 3 and "queued" when the daily budget cap stops a
    run (resumable); 1 and "failed" when the library runs out of arms."""
    rec["status"], rec["reason"] = "building", ""
    save_test(tenant, rec)
    for variant in rec["variants"]:
        while not variant.get("run_dir"):
            result = _build_variant(tenant, rec, variant, runner)
            if result == "budget":
                rec["status"] = "queued"
                rec["reason"] = (
                    f"daily budget cap reached while building variant {variant['key']} ({variant['arm']}); "
                    f"run `harness abtest resume {rec['test_id']}` once the cap allows more runs"
                )
                save_test(tenant, rec)
                return exits.BUDGET, rec
            if result == "failed":
                rec["failed_arms"].append({"key": variant["key"], "arm": variant["arm"], "attempts": MAX_ATTEMPTS})
                used = {v["arm"] for v in rec["variants"]} | {f["arm"] for f in rec["failed_arms"]}
                substitute = next((a for a in rec["ranking"] if a not in used), None)
                if substitute is None:
                    rec["status"] = "failed"
                    rec["reason"] = f"no build left to try for variant {variant['key']}"
                    save_test(tenant, rec)
                    return exits.USAGE, rec
                variant["arm"], variant["status"] = substitute, "pending"
                save_test(tenant, rec)
    rec["status"] = "built"
    save_test(tenant, rec)
    return exits.OK, rec


def create_test(tenant, *, input_path, name, source=None, seed=None, runner=None, on_record=None):
    """Chooses three builds, records the test, and builds them. `on_record`
    (cycle 69) is called with the record once it is saved, before the first
    build, so a caller can link it (an inbox item keeps the test id)."""
    seed = seed if seed is not None else random.randrange(1_000_000)
    ranking = rank_arms(tenant, random.Random(seed))
    rec = {
        "test_id": new_test_id(name),
        "name": name,
        "status": "building",
        "reason": "",
        "created_at": _now(),
        "updated_at": _now(),
        "live_at": None,
        "finished_at": None,
        "source": source or {"kind": "upload"},
        "input": str(Path(input_path).resolve()),
        "seed": seed,
        "ranking": ranking,
        "variants": [{"key": k, "arm": ranking[i], "status": "pending", "attempts": []}
                     for i, k in enumerate(KEYS)],
        "failed_arms": [],
        "split": None,
        "winner": None,
    }
    save_test(tenant, rec)
    if on_record is not None:
        on_record(rec)
    return build_variants(tenant, rec, runner or default_runner)


def reset_variant_stats(tenant, rec, key, *, by, note=""):
    """Cycle 69 ("Replace live variant"): the variant's page was replaced
    with a new version, so its views and CTA clicks start again at zero.
    Its events move to the key "<key>@<n>" (abevents.archive_variant); the
    record keeps {archived_key, at, by, rows, note} under the variant's
    "replacements". results() counts only the current key; pooled_arm_stats
    still counts the archived rows for the build."""
    variant = next((v for v in rec["variants"] if v["key"] == key), None)
    if variant is None:
        raise AbtestError(f"test {rec['test_id']} has no variant {key!r}")
    replacements = variant.setdefault("replacements", [])
    archived_key = f"{key}@{len(replacements) + 1}"
    rows = abevents.archive_variant(abevents.db_path(tenant), rec["test_id"], key, archived_key)
    replacements.append({"archived_key": archived_key, "at": _utc_now(), "by": by, "rows": rows, "note": note})
    save_test(tenant, rec)
    return replacements[-1]


# ---------------------------------------------------------------------------
# split + beacon scripts
# ---------------------------------------------------------------------------

def _js_str(value):
    return json.dumps(value).replace("<", "\\u003c")


def split_body(test_id, variant_paths, title=None):
    """The split page's whole body: an inline script first, then a
    <noscript> link list. The script keeps a visitor on one variant with
    the cookie pk_ab_<test_id> (30 days), else picks A/B/C at random, then
    location.replace()s to that variant with the visitor's whole query string
    (utm_*, fbclid, ...) plus pk_t=<test_id>&pk_v=<key>. It does nothing in
    the theme editor. `variant_paths` is {key: "/pages/<handle>"}."""
    if not TEST_ID_RE.match(test_id):
        raise AbtestError(f"not a test id: {test_id!r}")
    paths = json.dumps(dict(variant_paths), separators=(",", ":")).replace("<", "\\u003c")
    script = (
        "(function(){if(typeof Shopify!='undefined'&&Shopify.designMode)return;"
        "var T=" + _js_str(test_id) + ",P=" + paths + ",K=Object.keys(P),c='pk_ab_'+T,d=document,"
        "m=d.cookie.match(new RegExp('(?:^|; )'+c+'=([A-Z])')),v=m&&P[m[1]]?m[1]:K[Math.floor(Math.random()*K.length)];"
        "if(!(m&&P[m[1]]))d.cookie=c+'='+v+';path=/;max-age=2592000;SameSite=Lax';"
        "var q=location.search.replace(/^\\?/,'').split('&').filter(function(s){return s&&!/^pk_[tv]=/.test(s)});"
        "q.push('pk_t='+T,'pk_v='+v);location.replace(P[v]+'?'+q.join('&')+location.hash)})();"
    )
    links = "".join(f'<li><a href="{path}">Continue ({key})</a></li>' for key, path in variant_paths.items())
    return f"<script>{script}</script>\n<noscript><ul>{links}</ul></noscript>\n"


def beacon_script(test_id, key, beacon_url):
    """The inline tracking beacon for a variant page: a first-party visitor
    id (cookie pk_vid, 1 year), a "view" event on load and a "cta" event on
    a click of any CTA anchor (CTA_CLASSES) whose path is /products/... or
    /collections/.... Events go by navigator.sendBeacon as text/plain, so the
    cross-origin POST needs no CORS preflight. A CTA click also tags the
    visitor's cart (attribute pk_ab=<test_id>:<key>) so an order that
    follows carries the variant (see attribute_orders)."""
    selector = ",".join(f"a.{c}" for c in CTA_CLASSES)
    script = (
        "(function(){var T=" + _js_str(test_id) + ",V=" + _js_str(key) + ",U=" + _js_str(beacon_url) + ","
        "d=document,m=d.cookie.match(/(?:^|; )pk_vid=([a-z0-9]{8,40})/),id='';"
        "if(m)id=m[1];else{var r=crypto.getRandomValues(new Uint32Array(4));"
        "for(var i=0;i<4;i++)id+=('000000'+r[i].toString(36)).slice(-7);"
        "d.cookie='pk_vid='+id+';path=/;max-age=31536000;SameSite=Lax'}"
        "function s(e){try{navigator.sendBeacon(U,JSON.stringify({t:T,v:V,e:e,vid:id}))}catch(x){}}"
        "s('view');d.addEventListener('click',function(ev){"
        "var a=ev.target&&ev.target.closest&&ev.target.closest(" + _js_str(selector) + ");if(!a)return;"
        "var p=a.pathname||'';if(p.indexOf('/products/')&&p.indexOf('/collections/'))return;"
        "s('cta');try{fetch('/cart/update.js',{method:'POST',keepalive:true,"
        "headers:{'Content-Type':'application/json'},body:JSON.stringify({attributes:{pk_ab:T+':'+V}})})}catch(x){}"
        "},true)})();"
    )
    return f"<script>{script}</script>"


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

def _money(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def order_variant(order, test_id, keys):
    """The variant key an order belongs to, or None: its landing_site query
    names pk_t=<test_id> and pk_v=<key>, or its cart attribute pk_ab is
    "<test_id>:<key>"."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(order.get("landing_site") or "").query)
    if query.get("pk_t") == [test_id]:
        key = (query.get("pk_v") or [None])[0]
        if key in keys:
            return key
    for attr in order.get("note_attributes") or []:
        if isinstance(attr, dict) and attr.get("name") == "pk_ab":
            t, _, key = str(attr.get("value") or "").partition(":")
            if t == test_id and key in keys:
                return key
    return None


def attribute_orders(orders, test_id, keys):
    """{key: {"orders": n, "revenue": "0.00"}}; cancelled orders do not count."""
    totals = {k: [0, Decimal("0")] for k in keys}
    for order in orders:
        if order.get("cancelled_at"):
            continue
        key = order_variant(order, test_id, keys)
        if key:
            totals[key][0] += 1
            totals[key][1] += _money(order.get("total_price"))
    return {k: {"orders": n, "revenue": f"{rev:.2f}"} for k, (n, rev) in totals.items()}


# ---------------------------------------------------------------------------
# results + finish
# ---------------------------------------------------------------------------

def results(tenant, rec, orders=None):
    """One row per variant: key, arm, views, clicks, ctr, p_best, and orders/
    revenue when `orders` (attribute_orders output) is given."""
    counts = abevents.counts(abevents.db_path(tenant), rec["test_id"])
    data = {}
    for v in rec["variants"]:
        c = counts.get(v["key"], {"views": 0, "clicks": 0})
        data[v["key"]] = (min(c["clicks"], c["views"]), c["views"])
    pb = abstats.p_best(data)
    rows = []
    for v in rec["variants"]:
        clicks, views = data[v["key"]]
        row = {"key": v["key"], "arm": v["arm"], "views": views, "clicks": clicks,
               "ctr": abstats.ctr(clicks, views), "p_best": pb[v["key"]]}
        if orders is not None:
            row.update(orders.get(v["key"]) or {"orders": 0, "revenue": "0.00"})
        rows.append(row)
    return rows


def format_results(rec, rows):
    lines = [f"{rec['test_id']} ({rec['status']})  split: {(rec.get('split') or {}).get('url') or '-'}",
             f"{'':3}{'build':32} {'views':>7} {'CTA':>6} {'CTR':>7} {'P(best)':>8} {'orders':>7} {'revenue':>11}"]
    for r in rows:
        orders = f"{r['orders']:>7} {r['revenue']:>11}" if "orders" in r else f"{'-':>7} {'-':>11}"
        lines.append(f"{r['key']:3}{r['arm']:32} {r['views']:>7} {r['clicks']:>6} "
                     f"{r['ctr']:>7.1%} {r['p_best']:>8.1%} {orders}")
    return "\n".join(lines)


def pick_winner(rows):
    """The row with the highest P(best). When the runner-up is within
    TIE_MARGIN and rows carry orders, more orders (then more revenue) wins."""
    ranked = sorted(rows, key=lambda r: -r["p_best"])
    best = ranked[0]
    if len(ranked) > 1 and "orders" in best and best["p_best"] - ranked[1]["p_best"] < TIE_MARGIN:
        close = [r for r in ranked if best["p_best"] - r["p_best"] < TIE_MARGIN]
        best = max(close, key=lambda r: (r["orders"], _money(r["revenue"]), r["p_best"]))
    return best


def finish(tenant, rec, *, force=False, orders=None):
    """Names the winner and marks the test finished. Refuses (AbtestError)
    unless every variant has at least `abtest.min_views` unique views and the
    leader's P(best) is at least FINISH_P_BEST -- `force` skips both."""
    if rec["status"] != "live":
        raise AbtestError(f"test {rec['test_id']} is {rec['status']!r}; only a live test can be finished")
    rows = results(tenant, rec, orders)
    min_views = settings(tenant)["min_views"]
    if not force:
        short = [f"{r['key']}={r['views']}" for r in rows if r["views"] < min_views]
        if short:
            raise AbtestError(
                f"not enough data: variant(s) {', '.join(short)} below min_views {min_views} unique views "
                f"(use --force to finish anyway)"
            )
        leader = max(rows, key=lambda r: r["p_best"])
        if leader["p_best"] < FINISH_P_BEST:
            raise AbtestError(
                f"no clear winner: best is {leader['key']} at P(best) {leader['p_best']:.3f}, "
                f"below {FINISH_P_BEST} (use --force to finish anyway)"
            )
    best = pick_winner(rows)
    rec["winner"] = {**best, "forced": bool(force), "at": _now()}
    rec["final"] = rows
    rec["status"] = "finished"
    rec["finished_at"] = _now()
    save_test(tenant, rec)
    return rec
