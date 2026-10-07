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
- order attribution and the results/finish rules;
- cycle 88: the destination PLAN -- a variant may be an EXTERNAL arm (the
  product's PDP, the store home page, or a given URL) instead of a built
  page, or the only arm (docs/ABTEST.md "Control arms (cycle 88)").
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

from . import abevents, abstats, exits, first_screen, listicle, looks, pipeline
from .errors import HarnessError

# The builds every tenant gets when tenant.yaml names none. Cycle 79: the
# listicle has one look ("open"), so a listicle build is its copy style plus
# its FIRST-SCREEN style (harness/first_screen.py: face | story | display) --
# "listicle:<style>:<look>:<hero>". Each style is paired with each hero style
# once over the library (5 builds), so every hero style is in play and
# `harness abtest library --by hero` can pool the results per hero style;
# plus the comparison and quiz pages.
DEFAULT_LIBRARY = (
    "listicle:reasons:open:story",
    "listicle:mistakes:open:face",
    "listicle:questions:open:display",
    "listicle:myths:open:story",
    "listicle:tested:open:face",
    "listicle:reasons:open:display",
    "listicle:questions:open:face",
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
    # cycle 79: the listicle's open look
    "op-btn", "op-link",
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
    hero_style: str = None


def parse_arm(text, tenant=None):
    """"cartridge", "cartridge:look" or "listicle:style[:look[:hero]]" -> Arm.
    A listicle entry with no look gets the tenant's own style -> look
    pairing, so the id always names the build that actually renders; a
    retired look name resolves to the look it renders in now (cycle 79).
    Cycle 79: the optional 4th part is the first-screen style (face | story
    | display); without it the run seeds one."""
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
        look = listicle.canonical_look(rest[1]) if len(rest) > 1 else listicle.resolve_look(None, style=style,
                                                                                            tenant=tenant)
        if (look not in listicle.LOOKS and not looks.is_look_of("listicle", look, tenant)) or len(rest) > 3:
            raise AbtestError(f"abtest library entry {text!r}: unknown listicle look")
        hero = rest[2] if len(rest) > 2 else None
        if hero is not None and hero not in first_screen.HERO_STYLES:
            raise AbtestError(f"abtest library entry {text!r}: unknown hero style {hero!r}, one of "
                              f"{list(first_screen.HERO_STYLES)}")
        arm_id = f"listicle:{style}:{look}" + (f":{hero}" if hero else "")
        return Arm(arm_id, "listicle", style, look, hero)
    # Cycle 87: a tenant's imported look ("Import a page") is a look too.
    if len(rest) > 1 or (rest and not looks.is_look_of(cartridge, rest[0], tenant)):
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


# ---------------------------------------------------------------------------
# destination plans (cycle 88)
# ---------------------------------------------------------------------------

# A variant is a page the harness builds ("generated") or an EXTERNAL arm:
# the run's product page, the store home page, or a given URL. An external
# page carries no beacon, so the split page's own "assign" ping is the only
# count the harness has for it.
EXTERNAL_TARGETS = ("pdp", "home", "url")
TARGET_LABELS = {"pdp": "PDP", "home": "Home", "url": "URL"}
# preset id -> (label, arm shape); the site's "Destinations" choice and
# `--destinations` on `harness abtest create` / `from-inbox`.
PLANS = {
    "generated": ("3 generated", ("gen", "gen", "gen")),
    "gen2-pdp": ("2 generated + PDP", ("gen", "gen", "pdp")),
    "gen2-home": ("2 generated + home", ("gen", "gen", "home")),
    "gen1-pdp-home": ("1 generated + PDP + home", ("gen", "pdp", "home")),
    "pdp-only": ("PDP only", ("pdp",)),
    "home-only": ("Home only", ("home",)),
}
DEFAULT_PLAN = "generated"
# The words ground.LocalFactsSource.pick_product_with_warning uses when the ad
# names no model (the run then falls back to the default product).
PRODUCT_NOT_NAMED = "product not named in ad"
NO_BEACON_NOTE = "measure in GA4/Shopify by utm_content"
# The split page adds these when the visitor's query has none (the ad's own
# URL parameters win); utm_content is always <test_id>_<key>.
UTM_DEFAULTS = (("utm_source", "meta"), ("utm_medium", "paid"))


def is_external(variant):
    return (variant or {}).get("kind") == "external"


def variant_label(variant):
    """"PDP" / "Home" / "URL" for an external arm, else "generated"."""
    return TARGET_LABELS.get(variant.get("target"), "URL") if is_external(variant) else "generated"


def destinations_label(rec, *, this_key=None):
    """"A = generated, B = generated, C = PDP" (this_key's arm reads "this page")."""
    return ", ".join(f"{v['key']} = {'this page' if v['key'] == this_key else variant_label(v)}"
                     for v in rec.get("variants") or [])


def utm_content(test_id, key):
    return f"{test_id}_{key}"


def plan_specs(name):
    return [{"kind": "generated"} if part == "gen" else {"kind": "external", "target": part}
            for part in PLANS[name][1]]


def validate_plan(specs):
    """The plan's arm specs, normalised, or AbtestError. A spec is
    {"kind": "generated"} or {"kind": "external", "target": "pdp"|"home"|"url"}
    (target url also needs "url": an absolute http(s) URL). Allowed: 3
    generated; 2 generated + 1 external; 1 generated + PDP + home; one
    external arm alone (100% of the traffic)."""
    if not isinstance(specs, list) or not specs:
        raise AbtestError("a destination plan is a non-empty list of arms")
    out = []
    for spec in specs:
        kind = spec.get("kind") if isinstance(spec, dict) else None
        if kind == "generated":
            out.append({"kind": "generated"})
            continue
        if kind != "external" or spec.get("target") not in EXTERNAL_TARGETS:
            raise AbtestError(f"destination arm {spec!r}: kind must be generated, or external with a target "
                              f"in {list(EXTERNAL_TARGETS)}")
        norm = {"kind": "external", "target": spec["target"]}
        if spec["target"] == "url":
            url = str(spec.get("url") or "").strip()
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise AbtestError(f"destination arm {spec!r}: target url needs an absolute http(s) url")
            norm["url"] = url
        out.append(norm)
    shape = tuple("gen" if s["kind"] == "generated" else s["target"] for s in out)
    if not (shape == ("gen", "gen", "gen")
            or (len(shape) == 3 and shape[:2] == ("gen", "gen"))
            or shape == ("gen", "pdp", "home")
            or (len(shape) == 1 and shape[0] != "gen")):
        raise AbtestError(f"destination plan {'|'.join(shape)} is not allowed; use 3 generated, 2 generated + 1 "
                          "external, 1 generated + PDP + home, or one external arm alone")
    return out


def parse_plan(value=None):
    """(plan name, arm specs) from a preset id (PLANS), a JSON list of arm
    specs (CLI only; name "custom" unless it matches a preset), or None
    (DEFAULT_PLAN)."""
    if value is None or value == "":
        value = DEFAULT_PLAN
    if isinstance(value, str):
        if value in PLANS:
            return value, plan_specs(value)
        try:
            value = json.loads(value)
        except ValueError:
            raise AbtestError(f"unknown destinations {value!r}; one of {list(PLANS)} or a JSON list of arms") from None
    specs = validate_plan(value)
    name = next((n for n in PLANS if plan_specs(n) == specs), "custom")
    return name, specs


def home_url(tenant):
    """The store's home page: tenant.yaml site_url (else https://site_host) + "/"."""
    site = (tenant.get("site_url") or "").strip().rstrip("/")
    if not site and tenant.get("site_host"):
        site = f"https://{tenant.get('site_host')}"
    if not site:
        raise AbtestError("tenant.yaml site_url is empty: there is no home page to send the ad to")
    return site + "/"


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pdp_url(tenant, run_dir):
    """The product page of the product the ad names: the run's
    facts_pack.product.url (what the generated page's cta_url links to),
    else the tenant product map's (claims/products.json) pick from the
    run's ad_brief.json. Refuses (AbtestError) when the ad names no product:
    the run's fallback to the default product is fine for a written page
    but would send the ad to a product it never mentions."""
    from . import ground

    run_dir = Path(run_dir)
    brief = _read_json(run_dir / "ad_brief.json")
    product, warning = ground.LocalFactsSource(tenant.claims_dir).pick_product_with_warning(None, brief)
    if warning and warning.startswith(PRODUCT_NOT_NAMED):
        raise AbtestError(f"PDP arm refused: {warning}. There is no product page to send this ad to; "
                          "choose generated pages or Home")
    url = (_read_json(run_dir / "facts_pack.json").get("product") or {}).get("url") or product.get("url")
    if not url:
        raise AbtestError(f"PDP arm refused: product {product.get('name')!r} has no url")
    return url


def split_path(tenant, url):
    """A URL on the tenant's own storefront as a path (the split page
    redirects within the store, like it does to /pages/<handle>); any other
    URL unchanged."""
    parsed = urllib.parse.urlparse(url)
    host = (tenant.get("site_host") or "").lower()
    if host and parsed.netloc.lower() in (host, f"www.{host}"):
        return (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return url


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

def pooled_arm_stats(tenant, by="arm"):
    """{arm_id: {"views", "clicks", "tests"}} summed over every live or
    finished test's variants (unique visitors, from events.sqlite).
    Cycle 70: by="headline" groups by each listicle variant's
    headline_template_id instead (variants with none are left out) -- what
    `harness abtest library --by headline` shows, and what a later cycle can
    feed to headlines.resolve_plan's `weights`."""
    db = abevents.db_path(tenant)
    stats = {}
    for rec in list_tests(tenant):
        if rec.get("status") not in POOLED_STATUSES:
            continue
        counts = abevents.counts(db, rec["test_id"])
        for v in rec.get("variants") or []:
            if is_external(v):  # cycle 88: not a build; no views or CTA clicks
                continue
            group = {"arm": v["arm"], "headline": v.get("headline_template_id"),
                     "hero": v.get("hero_style")}.get(by)
            if not group:
                continue
            slot = stats.setdefault(group, {"views": 0, "clicks": 0, "tests": 0})
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
    from . import batch as batch_mod
    from . import config as harness_config
    from .anthropic_client import make_client

    args = argparse.Namespace(
        input=str(input_path), cartridges=arm.cartridge, seed=seed, style=arm.style, look=arm.look,
        hero_style=arm.hero_style,
        # Cycle 76: an A/B/C build is not interactive -- its first writes go
        # through the Message Batches API when the tenant says so.
        product=None, batch=batch_mod.settings(tenant)["non_interactive"], tenant=tenant.name,
        ffmpeg_bin=harness_config.FFMPEG_BIN, whisper_bin=harness_config.whisper_bin(),
        whisper_model=harness_config.whisper_model(),
    )
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    rc = pipeline.execute(state, pipeline.DEFAULT_STAGES)
    return rc, state.run_dir


def default_brief_runner(tenant, input_path, seed):
    """Cycle 88: a PDP arm with no generated page to read the product from
    ("PDP only") needs the ad brief alone -- `harness ingest` (one model
    call, spend recorded in the daily ledger). Returns (exit code, run_dir
    or None)."""
    from . import budget as budget_mod
    from . import config as harness_config
    from .anthropic_client import make_client
    from .budget import Budget, BudgetExceeded
    from .ingest import run_ingest
    from .log import RunLog

    budget = Budget()
    run_id, run_dir = pipeline.make_run_dir(tenant.out_dir, pipeline.slugify(input_path) + "-brief")
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}.log")
    try:
        try:
            brief = run_ingest(
                input_arg=str(input_path), workdir=run_dir, client=make_client(),
                model=tenant.model_for("ingest"), budget=budget, log=log,
                ffmpeg_bin=harness_config.FFMPEG_BIN, whisper_bin=harness_config.whisper_bin(),
                whisper_model=harness_config.whisper_model(),
            )
        except BudgetExceeded as e:
            pipeline.abort_budget_run(log, budget, e, tenant=tenant, run_id=run_id, stage="ingest")
            return exits.BUDGET, None
        (run_dir / "ad_brief.json").write_text(json.dumps(brief, indent=2))
        budget_mod.record_spend(tenant, run_id=run_id, cost=log.cost_estimate(),
                                today_iso=datetime.date.today().isoformat(), log=log)
        return exits.OK, run_dir
    finally:
        log.close()


def _headline_template_id(run_dir):
    """Cycle 70: the headline template a listicle build used (its page.json's
    headline_template_id), so results can be grouped by template."""
    try:
        return json.loads((Path(run_dir) / "listicle" / "page.json").read_text()).get("headline_template_id")
    except (OSError, ValueError, AttributeError):
        return None


def _page_field(run_dir, field, cartridge="listicle"):
    try:
        return json.loads((Path(run_dir) / cartridge / "page.json").read_text()).get(field)
    except (OSError, ValueError, AttributeError):
        return None


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
            if arm.cartridge == "listicle":
                variant["headline_template_id"] = _headline_template_id(run_dir)
                # Cycle 79: the first screen it actually rendered (face falls
                # back when the ad has no usable still).
                variant["hero_style"] = _page_field(run_dir, "hero_style")
            if looks.has_looks(arm.cartridge):
                # Cycle 85: the look the page was actually stamped with (a
                # landing test may pit `open` against `proof`).
                variant["look"] = _page_field(run_dir, "look", arm.cartridge)
            save_test(tenant, rec)
            return "built"
        tries.append(attempt)
    return "failed"


def _resolve_externals(tenant, rec, run_dir=None):
    """Cycle 88: sets "url" on each external arm that has none: home now,
    pdp from `run_dir` (a built variant's run, or the brief run) when given.
    Raises AbtestError when the PDP cannot be named."""
    for v in rec["variants"]:
        if not is_external(v) or v.get("url"):
            continue
        if v["target"] == "home":
            v["url"] = home_url(tenant)
        elif v["target"] == "pdp" and run_dir:
            v["url"] = pdp_url(tenant, run_dir)
            v["product_from"] = Path(run_dir).name
        if v.get("url"):
            v["status"] = "ready"
    save_test(tenant, rec)


def _stop(tenant, rec, status, reason, rc):
    rec["status"], rec["reason"] = status, reason
    save_test(tenant, rec)
    return rc, rec


def build_variants(tenant, rec, runner, brief_runner=None):
    """Builds every variant that has no run yet. Returns (exit code, rec):
    0 and status "built"; 3 and "queued" when the daily budget cap stops a
    run (resumable); 1 and "failed" when the library runs out of arms.
    Cycle 88: external arms get their URL here -- home at once, PDP from the
    first built variant's run (so a refusal comes after one build, not
    three) or, with no generated arm, from an ingest-only brief run; a PDP
    the ad does not name fails the test (exit 1)."""
    rec["status"], rec["reason"] = "building", ""
    save_test(tenant, rec)
    try:
        _resolve_externals(tenant, rec)
    except AbtestError as e:
        return _stop(tenant, rec, "failed", str(e), exits.USAGE)
    for variant in rec["variants"]:
        if is_external(variant):
            continue
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
        try:
            _resolve_externals(tenant, rec, run_dir=variant["run_dir"])
        except AbtestError as e:
            return _stop(tenant, rec, "failed", str(e), exits.USAGE)
    if any(is_external(v) and not v.get("url") for v in rec["variants"]):
        brief = rec.get("brief") or {}
        if not brief.get("run_dir"):
            try:
                rc, run_dir = (brief_runner or default_brief_runner)(tenant, rec["input"], rec["seed"])
            except HarnessError as e:
                rc, run_dir = e.exit_code, None
            brief = rec["brief"] = {"rc": rc, "run_dir": str(run_dir) if run_dir else None, "at": _now()}
            save_test(tenant, rec)
            if rc == exits.BUDGET:
                return _stop(tenant, rec, "queued",
                             "daily budget cap reached while reading the ad for its product; run "
                             f"`harness abtest resume {rec['test_id']}` once the cap allows more runs", exits.BUDGET)
            if rc != exits.OK or not run_dir:
                rec["brief"] = None
                return _stop(tenant, rec, "failed", f"could not read the ad to find its product (exit {rc})",
                             exits.USAGE)
        try:
            _resolve_externals(tenant, rec, run_dir=brief["run_dir"])
        except AbtestError as e:
            return _stop(tenant, rec, "failed", str(e), exits.USAGE)
    rec["status"] = "built"
    save_test(tenant, rec)
    return exits.OK, rec


def _new_variants(specs, ranking):
    builds = iter(ranking)
    variants = []
    for key, spec in zip(KEYS, specs):
        if spec["kind"] == "generated":
            variants.append({"key": key, "arm": next(builds), "status": "pending", "attempts": []})
        else:
            variants.append({"key": key, "kind": "external", "target": spec["target"],
                             "arm": f"external:{spec['target']}", "url": spec.get("url"),
                             "status": "ready" if spec.get("url") else "pending"})
    return variants


def create_test(tenant, *, input_path, name, source=None, seed=None, runner=None, on_record=None, plan=None,
                brief_runner=None, pinned_arm=None):
    """Chooses three builds, records the test, and builds them. `on_record`
    (cycle 69) is called with the record once it is saved, before the first
    build, so a caller can link it (an inbox item keeps the test id).
    Cycle 88: `plan` (parse_plan: a PLANS id or a list of arm specs; default
    3 generated) makes some arms external; they take the last keys.
    Cycle 86: `pinned_arm` (a build string, e.g. "landing:proof" -- the
    Library's "Generate with this") is the first generated arm, so variant
    A; the other generated arms are the usual ranked picks, and a failed A
    is replaced from the ranking as usual. A plan with no generated arm
    cannot take a pin (AbtestError)."""
    plan_name, specs = parse_plan(plan)
    if pinned_arm and not any(s["kind"] == "generated" for s in specs):
        raise AbtestError(f"destinations {plan_name!r} build no page, so a pinned page version cannot be used")
    seed = seed if seed is not None else random.randrange(1_000_000)
    ranking = rank_arms(tenant, random.Random(seed))
    pinned = parse_arm(pinned_arm, tenant).id if pinned_arm else None
    if pinned:
        ranking = [pinned] + [a for a in ranking if a != pinned]
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
        "plan": plan_name,
        "variants": _new_variants(specs, ranking),
        "failed_arms": [],
        "split": None,
        "winner": None,
    }
    if pinned:
        rec["pinned_arm"] = pinned
    save_test(tenant, rec)
    if on_record is not None:
        on_record(rec)
    return build_variants(tenant, rec, runner or default_runner, brief_runner)


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


def split_body(test_id, variant_paths, title=None, *, beacon_url=""):
    """The split page's whole body: an inline script first, then a
    <noscript> link list. The script keeps a visitor on one variant with
    the cookie pk_ab_<test_id> (30 days), else picks a variant at random, then
    location.replace()s to that variant with the visitor's whole query string
    (utm_*, fbclid, ...) plus pk_t=<test_id>&pk_v=<key>. It does nothing in
    the theme editor. `variant_paths` is {key: "/pages/<handle>" | a PDP /
    home path | an absolute URL}.
    Cycle 88: every arm gets utm_content=<test_id>_<key> (an incoming
    utm_content is replaced) and, when the query has none, utm_source=meta,
    utm_medium=paid, utm_campaign=<test_id> -- so GA4/Shopify can tell the
    arms apart, an external one included. With `beacon_url`, the script also
    sends an "assign" event (same /e receiver, same pk_vid visitor id and
    cookie as the variant beacon) before it redirects: the only count the
    harness has for an arm that carries no beacon."""
    if not TEST_ID_RE.match(test_id):
        raise AbtestError(f"not a test id: {test_id!r}")
    paths = json.dumps(dict(variant_paths), separators=(",", ":")).replace("<", "\\u003c")
    defaults = "".join(f"h('{k}','{val}');" for k, val in UTM_DEFAULTS)
    script = (
        "(function(){if(typeof Shopify!='undefined'&&Shopify.designMode)return;"
        "var T=" + _js_str(test_id) + ",P=" + paths + ",U=" + _js_str(beacon_url or "") + ","
        "K=Object.keys(P),c='pk_ab_'+T,d=document,"
        "m=d.cookie.match(new RegExp('(?:^|; )'+c+'=([A-Z])')),v=m&&P[m[1]]?m[1]:K[Math.floor(Math.random()*K.length)];"
        "if(!(m&&P[m[1]]))d.cookie=c+'='+v+';path=/;max-age=2592000;SameSite=Lax';"
        "if(U){var n=d.cookie.match(/(?:^|; )pk_vid=([a-z0-9]{8,40})/),id='';if(n)id=n[1];"
        "else{var r=crypto.getRandomValues(new Uint32Array(4));"
        "for(var i=0;i<4;i++)id+=('000000'+r[i].toString(36)).slice(-7);"
        "d.cookie='pk_vid='+id+';path=/;max-age=31536000;SameSite=Lax'}"
        "try{navigator.sendBeacon(U,JSON.stringify({t:T,v:v,e:'assign',vid:id}))}catch(x){}}"
        "var q=location.search.replace(/^\\?/,'').split('&').filter(function(s){return s&&!/^(pk_[tv]|utm_content)=/.test(s)}),"
        "h=function(k,x){if(!q.some(function(s){return s.indexOf(k+'=')==0}))q.push(k+'='+x)};"
        + defaults + "h('utm_campaign',T);q.push('utm_content='+T+'_'+v,'pk_t='+T,'pk_v='+v);"
        "location.replace(P[v]+(P[v].indexOf('?')<0?'?':'&')+q.join('&')+location.hash)})();"
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
    """One row per variant: key, arm, kind, assigns (split page "assign"
    events), views, clicks, ctr, p_best, and orders/revenue when `orders`
    (attribute_orders output) is given.
    Cycle 88: an external arm has no beacon, so its views, clicks, ctr,
    p_best and orders are None (the report shows "-"; never a zero that
    reads as data) and its row carries the utm_content to look up in
    GA4/Shopify. P(best) compares generated arms only, and only when there
    are at least two."""
    counts = abevents.counts(abevents.db_path(tenant), rec["test_id"])
    data = {}
    for v in rec["variants"]:
        if is_external(v):
            continue
        c = counts.get(v["key"], {"views": 0, "clicks": 0})
        data[v["key"]] = (min(c["clicks"], c["views"]), c["views"])
    pb = abstats.p_best(data) if len(data) >= 2 else {}
    rows = []
    for v in rec["variants"]:
        assigns = counts.get(v["key"], {}).get("assigns", 0)
        if is_external(v):
            rows.append({"key": v["key"], "arm": v["arm"], "kind": "external", "target": v.get("target"),
                         "url": v.get("url"), "assigns": assigns, "views": None, "clicks": None, "ctr": None,
                         "p_best": None, "utm_content": utm_content(rec["test_id"], v["key"])})
            continue
        clicks, views = data[v["key"]]
        row = {"key": v["key"], "arm": v["arm"], "kind": "generated", "assigns": assigns, "views": views,
               "clicks": clicks, "ctr": abstats.ctr(clicks, views), "p_best": pb.get(v["key"])}
        if orders is not None:
            row.update(orders.get(v["key"]) or {"orders": 0, "revenue": "0.00"})
        rows.append(row)
    return rows


def format_results(rec, rows):
    def cell(value, width, fmt=""):
        return f"{'-':>{width}}" if value is None else f"{value:>{width}{fmt}}"

    lines = [f"{rec['test_id']} ({rec['status']})  split: {(rec.get('split') or {}).get('url') or '-'}",
             f"{'':3}{'build':32} {'assigned':>8} {'views':>7} {'CTA':>6} {'CTR':>7} {'P(best)':>8} "
             f"{'orders':>7} {'revenue':>11}"]
    for r in rows:
        orders = f"{r['orders']:>7} {r['revenue']:>11}" if "orders" in r else f"{'-':>7} {'-':>11}"
        lines.append(f"{r['key']:3}{r['arm']:32} {r.get('assigns', 0):>8} {cell(r['views'], 7)} "
                     f"{cell(r['clicks'], 6)} {cell(r['ctr'], 7, '.1%')} {cell(r['p_best'], 8, '.1%')} {orders}")
    externals = [r for r in rows if r.get("kind") == "external"]
    for r in externals:
        lines.append(f"{r['key']} = {TARGET_LABELS.get(r['target'], 'URL')} ({r['url'] or 'not resolved'}): no beacon "
                     f"on that page -- {NO_BEACON_NOTE}={r['utm_content']}. 'assigned' is the visitors the split "
                     "page sent there.")
    if externals and len(externals) == len(rows):
        lines.append("This test has no generated page: the harness counts assignments only; it has no CTA or "
                     "order data for it.")
    elif sum(1 for r in rows if r["p_best"] is not None) < 2 and len(rows) > 1:
        lines.append("P(best) needs two generated pages; compare the arms in GA4/Shopify by utm_content.")
    return "\n".join(lines)


def pick_winner(rows):
    """The row with the highest P(best). When the runner-up is within
    TIE_MARGIN and rows carry orders, more orders (then more revenue) wins.
    Cycle 88: only rows with a P(best) (generated arms) can win."""
    rows = [r for r in rows if r.get("p_best") is not None]
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
    comparable = [r for r in rows if r["p_best"] is not None]
    if len(comparable) < 2:
        # Cycle 88: one or no generated page -- the beacon cannot compare it
        # with an external arm, so there is no winner to name.
        if not force:
            raise AbtestError(
                f"test {rec['test_id']} has {len(comparable) or 'no'} generated page(s) to compare; the beacon "
                f"cannot see CTA clicks or orders on an external arm ({NO_BEACON_NOTE}). "
                "Use --force to finish it with no winner"
            )
        rec["winner"] = None
        rec["final"] = rows
        rec["status"] = "finished"
        rec["finished_at"] = _now()
        save_test(tenant, rec)
        return rec
    if not force:
        short = [f"{r['key']}={r['views']}" for r in comparable if r["views"] < min_views]
        if short:
            raise AbtestError(
                f"not enough data: variant(s) {', '.join(short)} below min_views {min_views} unique views "
                f"(use --force to finish anyway)"
            )
        leader = max(comparable, key=lambda r: r["p_best"])
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
