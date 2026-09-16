"""The run pipeline, as named stages over one shared RunState.

`harness run` executes DEFAULT_STAGES in order. `harness workflow run
ad-to-pages` executes the stage list from workflows/ad-to-pages.yaml. Both go
through `execute` below and call the same functions, so a workflow cannot drift
away from what `harness run` does -- there is only one implementation.

Every stage takes the RunState, mutates it, and returns None. Stage order is
data; stage behaviour is code.
"""
import datetime
import json
import os
import random
import sys
from pathlib import Path

from . import budget as budget_mod
from . import pagechecks
from . import repair
from . import review_md
from .budget import Budget, BudgetExceeded
from .config import REPO_ROOT
from .claims import ClaimsGateFailure, gate_ad_brief_claims
from .ground import LocalFactsSource
from .ingest import download_drive_file, run_ingest
from .log import RunLog
from .pdp_claims import save_pdp_claims_cache, seed_pdp_claims
from .prices import refresh_price_data
from .render import http_fetch_bytes, render_page
from .semantic_match import semantic_match_claims
from .sources.judgeme import fetch_reviews_claim

CARTRIDGES_DIR = REPO_ROOT / "cartridges"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def slugify(input_arg):
    import re

    name = Path(input_arg).stem or input_arg
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "run"


# Fix cycle 23 (R29): minute-granularity run ids let two runs of the same
# input inside one wall-clock minute collide into the same run directory,
# silently overwriting one another. Seconds narrow the window; the 4-char
# random base32 suffix (lowercase a-z2-7, 32^4 ~= 1M combinations) makes an
# actual collision astronomically unlikely even within the same second --
# make_run_dir below still backstops it with a real filesystem check.
_RUN_ID_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_RUN_ID_SUFFIX_LEN = 4


def _run_id_suffix():
    return "".join(random.choices(_RUN_ID_SUFFIX_ALPHABET, k=_RUN_ID_SUFFIX_LEN))


def make_run_id(slug):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{slug}-{_run_id_suffix()}"


def make_run_dir(base_dir, slug):
    """Create a fresh run directory under `base_dir` and return
    (run_id, run_dir) for it. os.makedirs(..., exist_ok=False) is the actual
    uniqueness guarantee -- a genuine collision (same second, same slug, same
    random suffix) is retried once with a freshly generated id before
    raising. Older run dirs (any id shape, including the pre-cycle-23
    minute-granularity ones) are untouched and stay readable."""
    base_dir = Path(base_dir)
    last_error = None
    for _attempt in range(2):
        run_id = make_run_id(slug)
        run_dir = base_dir / run_id
        try:
            os.makedirs(run_dir, exist_ok=False)
        except FileExistsError as e:
            last_error = e
            continue
        return run_id, run_dir
    raise last_error


def discover_cartridges():
    if not CARTRIDGES_DIR.exists():
        return []
    return sorted(
        p.name for p in CARTRIDGES_DIR.iterdir() if p.is_dir() and (p / "cartridge.md").exists()
    )


class RunState:
    """Everything one run carries between stages."""

    def __init__(self, *, tenant, args, client):
        self.tenant = tenant
        self.args = args
        self.client = client
        self.budget = Budget()
        self.run_id = None
        self.run_dir = None
        self.log = None
        self.seed = None
        self.rng = None
        self.selected = []
        self.claims_config = {}
        self.facts_source = None
        self.merged_products = {}
        self.live_price_claims_by_slug = {}
        self.live_products = []
        self.pdp_claims = []
        self.today_iso = datetime.date.today().isoformat()
        self.ad_brief = None
        self.product = None
        self.product_warning = None
        self.reviews_claim = None
        self.facts_pack = None
        self.gate_matched = []
        self.ad_not_repeated = []
        self.ad_alternative_claims = []
        self.forbidden_urls = []
        self.pages = {}
        self.gate_log = {}
        self.outputs = []
        self.cost = 0.0


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def prepare_run(state):
    args = state.args
    tenant = state.tenant
    state.run_id, state.run_dir = make_run_dir(tenant.out_dir, slugify(args.input))
    state.log = RunLog(state.run_id, tenant.runs_dir / f"{state.run_id}.log")
    state.log.event("run", f"tenant: {tenant.name}")

    state.seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    state.log.seed(state.seed)
    state.rng = random.Random(state.seed)

    available = discover_cartridges()
    if getattr(args, "cartridges", None):
        selected = [c.strip() for c in args.cartridges.split(",") if c.strip()]
        unknown = [c for c in selected if c not in available]
        if unknown:
            raise UnknownCartridge(f"unknown cartridge(s): {unknown}; available: {available}")
    else:
        pool = tenant.get("default_cartridge_pool") or available
        default_pool = [c for c in available if c in pool] or available
        selected = state.rng.sample(default_pool, k=min(3, len(default_pool)))
    state.selected = selected
    state.log.cartridges(selected)
    state.claims_config = tenant.claims_config
    # R23: tenant.yaml and claims/config.json disagreeing on an overlapping
    # key is legal (config.json wins) but invisible without this line.
    for key, yaml_value, json_value in tenant.config_disagreements():
        state.log.event(
            "run",
            f"config disagreement: {key!r} is {yaml_value!r} in tenant.yaml but {json_value!r} "
            "in claims/config.json -- claims/config.json wins",
        )
    state.facts_source = LocalFactsSource(tenant.claims_dir)

    # Cycle 20: every run gets a state.json ("generated", one entry per
    # selected page) and a packet.json (default stamp "BOT DRAFT · NOT
    # SENT") the moment its run directory exists -- review_notify below
    # advances state.json to "needs_review" once the run actually passes
    # every gate; a STOP or a budget cap leaves both files at their initial
    # values, since there is no page to review yet.
    from . import runstate

    # Cycle 26b (bug 2): a run built against tests/conftest.py's FakeClient
    # (every test in this suite injects one instead of a real
    # anthropic.Anthropic()) is marked dry_run so the review site's run list
    # can hide it by default -- checked by class name rather than an isinstance
    # import, so this module never has to import the test double.
    dry_run = type(state.client).__name__ == "FakeClient"
    runstate.init_state(state.run_dir, pages=selected, dry_run=dry_run)
    runstate.init_packet(state.run_dir)

    # Kimi long-run phase 3, K2 fix (Cycle 28): the tenant's daily spend cap
    # gates starting a new run at all (the per-run Budget bounds a run
    # already in flight). reserve_spend both checks and, if the run is
    # allowed to start, reserves its estimated cost against the cap in the
    # same locked step -- see harness/budget.py's module docstring for why
    # (K2: the old check-then-record-later design let two runs starting at
    # once both bypass the cap). Uncapped when the tenant sets no
    # budget.daily_usd -- the default.
    budget_mod.reserve_spend(tenant, run_id=state.run_id, today_iso=state.today_iso, log=state.log)


def refresh_prices(state):
    """Live prices, then the product-page-derived claims, before ingest.

    Both are cached under the tenant's runs/ dir and merged into this run's
    claim universe only -- never written into claims/verified.json, which stays
    hand-curated."""
    tenant = state.tenant
    state.merged_products, state.live_price_claims_by_slug, state.live_products = refresh_price_data(
        products_path=tenant.claims_dir / "products.json",
        cache_path=tenant.runs_dir / "products-cache.json",
        show_compare_at_price=state.claims_config.get("show_compare_at_price", False),
        today_iso=state.today_iso,
        log=state.log,
    )
    live_by_handle = {p.get("handle"): p for p in state.live_products}
    state.pdp_claims = seed_pdp_claims(state.merged_products, live_by_handle, state.today_iso)
    save_pdp_claims_cache(tenant.runs_dir / "pdp-claims-cache.json", state.pdp_claims)


def ingest(state):
    args = state.args
    state.ad_brief = run_ingest(
        input_arg=args.input,
        workdir=state.run_dir,
        client=state.client,
        model=state.tenant.model_for("ingest"),
        budget=state.budget,
        log=state.log,
        ffmpeg_bin=args.ffmpeg_bin,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
    )
    (state.run_dir / "ad_brief.json").write_text(json.dumps(state.ad_brief, indent=2))


def ground(state):
    """Pick the product the ad is about, then build the facts_pack.

    Product-picking runs BEFORE the ad-claims gate so price-based product
    inference gets a chance; with the order reversed the gate stopped on a price
    claim every time."""
    state.budget.check()
    state.product, state.product_warning = state.facts_source.pick_product_with_warning(
        state.args.product, state.ad_brief
    )
    if state.product_warning:
        state.log.event("run", state.product_warning)
    live_price_claim = state.live_price_claims_by_slug.get(state.product["slug"])
    state.reviews_claim = (
        fetch_reviews_claim(state.product["url"], state.today_iso, log=state.log)
        if state.claims_config.get("reviews_source") not in (None, "none")
        else None
    )
    state.facts_pack = state.facts_source.facts_for(
        state.product["slug"],
        state.ad_brief,
        config=state.claims_config,
        live_price_claim=live_price_claim,
        reviews_claim=state.reviews_claim,
        pdp_claims=state.pdp_claims,
        # Kimi long-run phase 6: only a run with the comparison cartridge
        # selected gets comparison targets (and their backing claims) in its
        # facts_pack -- every other run's facts_pack is unchanged.
        include_comparison="comparison" in state.selected,
        log=state.log,
    )
    (state.run_dir / "facts_pack.json").write_text(json.dumps(state.facts_pack, indent=2))


def gate_ad_claims(state):
    """Gate against the FULL verified universe (with this run's live price and
    freshly-seeded page claims folded in) -- an ad claim can reference anything
    approved, not just the eventual product's curated facts_pack subset."""
    policy = state.claims_config.get("ad_overclaim_policy", "stop")
    all_verified = state.facts_source.all_verified_claims(
        state.live_price_claims_by_slug, extra_claims=state.pdp_claims
    )
    semantic_mapping = semantic_match_claims(
        state.ad_brief.get("claims_made", []),
        all_verified,
        client=state.client,
        model=state.tenant.model_for("matcher"),
        budget=state.budget,
        log=state.log,
    )
    state.gate_matched, state.ad_not_repeated, state.ad_alternative_claims = gate_ad_brief_claims(
        state.ad_brief,
        all_verified,
        product=state.product,
        reviews_claim=state.reviews_claim,
        financing_lender=state.claims_config.get("financing_lender"),
        policy=policy,
        log=state.log,
        semantic_mapping=semantic_mapping,
    )
    state.log.gate_result(
        "PASS",
        f"{len(state.gate_matched)} ad claim(s) matched"
        + (
            f", {len(state.ad_not_repeated)} ad claim(s) not repeated under 'warn' policy"
            if state.ad_not_repeated
            else ""
        )
        + (
            f", {len(state.ad_alternative_claims)} ad statement(s) about the alternative"
            if state.ad_alternative_claims
            else ""
        ),
    )

    state.forbidden_urls = pagechecks.find_forbidden_term_urls(state.facts_pack)
    for url in state.forbidden_urls:
        state.log.event("run", f"URL contains a forbidden term: {url}")


def _write_initial_pages_via_batch(state, write_model):
    """Fix cycle 17 item 5: submits every selected cartridge's initial write
    as one Message Batch (50% off), polls, and returns
    {cartridge_name: (page, tokens_spent)} for every cartridge whose result
    parsed and validated. A cartridge that isn't in the returned dict falls
    through to write_pages' normal synchronous attempt 1 below -- exactly as
    if --batch had never been passed for that one cartridge."""
    from . import batch as batch_mod

    requests, schemas = batch_mod.build_batch_requests(
        cartridge_names=state.selected,
        cartridges_dir=CARTRIDGES_DIR,
        ad_brief=state.ad_brief,
        facts_pack=state.facts_pack,
        model=write_model,
        tenant=state.tenant,
        ad_not_repeated=state.ad_not_repeated,
    )
    created = state.client.messages.batches.create(requests=requests)
    state.log.event("write_pages", f"batch {created.id} submitted for {len(requests)} cartridge(s)")
    batch_mod.poll_batch(state.client, created.id, log=state.log)
    results = batch_mod.collect_batch_results(state.client, created.id, schemas)

    initial_pages = {}
    for cartridge_name, (page, usage, error) in results.items():
        if page is None:
            state.log.event(
                f"write.{cartridge_name}",
                f"batch initial write unusable, falling back to a synchronous call: {error}",
            )
            continue
        tokens = 0
        if usage is not None:
            tokens = usage.input_tokens + usage.output_tokens
            state.budget.record_call(usage.input_tokens, usage.output_tokens)
            state.log.call(
                f"write.{cartridge_name}", write_model, usage.input_tokens, usage.output_tokens,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
                batch=True,
            )
        initial_pages[cartridge_name] = (page, tokens)
    return initial_pages


def write_pages(state):
    write_model = state.tenant.model_for("write")
    repair_first_model = state.tenant.model_for("repair_first")
    repair_next_model = state.tenant.model_for("repair_next")

    # Fix cycle 17 item 5: `harness run --batch` submits every selected
    # cartridge's initial write as one Message Batch before this loop runs,
    # instead of each cartridge making its own synchronous attempt-1 call
    # below. Repairs (attempt 2+) are unaffected either way -- each depends
    # on that cartridge's own gate result, so they stay synchronous.
    initial_pages = (
        _write_initial_pages_via_batch(state, write_model) if getattr(state.args, "batch", False) else {}
    )

    for cartridge_name in state.selected:
        state.budget.check()
        initial_page, initial_call_tokens = initial_pages.get(cartridge_name, (None, 0))
        try:
            page, attempts, deterministic_fixes = repair.write_and_gate_page(
                cartridge_name=cartridge_name,
                cartridges_dir=CARTRIDGES_DIR,
                ad_brief=state.ad_brief,
                facts_pack=state.facts_pack,
                client=state.client,
                model=write_model,
                repair_first_model=repair_first_model,
                repair_next_model=repair_next_model,
                budget=state.budget,
                log=state.log,
                financing_lender=state.claims_config.get("financing_lender"),
                speaker_pov=state.ad_brief.get("speaker_pov"),
                ad_not_repeated=state.ad_not_repeated,
                tenant=state.tenant,
                initial_page=initial_page,
                initial_call_tokens=initial_call_tokens,
            )
        except ClaimsGateFailure as e:
            attempts = getattr(e, "attempts", [e.items])
            state.gate_log[cartridge_name] = {
                "attempts": attempts,
                "deterministic_fixes": getattr(e, "deterministic_fixes", [0] * len(attempts)),
            }
            raise
        state.gate_log[cartridge_name] = {
            "attempts": attempts,
            "deterministic_fixes": deterministic_fixes,
        }
        state.pages[cartridge_name] = page


def render_pages(state):
    published = updated = state.today_iso
    for cartridge_name, page in state.pages.items():
        index_path = render_page(
            cartridge_name=cartridge_name,
            page=page,
            ad_brief=state.ad_brief,
            facts_pack=state.facts_pack,
            cartridges_dir=CARTRIDGES_DIR,
            brand_dir=state.tenant.brand_dir,
            templates_dir=TEMPLATES_DIR,
            out_dir=state.run_dir / cartridge_name,
            published=published,
            updated=updated,
            log=state.log,
            fetch_url=http_fetch_bytes,
            drive_downloader=download_drive_file,
            tenant=state.tenant,
        )
        state.outputs.append(index_path)


def review_notify(state):
    """Cycle 20: the run passed every gate (a STOP or a budget cap never
    reaches this stage) -- advance state.json to "needs_review" and notify
    the tenant's reviewers (Slack/email, both optional, both fail closed;
    see harness/notify.py). Runs right after render_pages, before
    write_review, so REVIEW.md's path can be referenced in the message even
    though the file itself is written by the next stage."""
    from . import notify, runstate

    runstate.mark_needs_review(state.run_dir, note="run passed the claims gate")
    notify.notify_needs_review(
        state.tenant,
        run_id=state.run_id,
        input_name=str(state.args.input),
        pages=state.selected,
        gate_log=state.gate_log,
        ad_not_repeated=state.ad_not_repeated,
        run_dir=str(state.run_dir),
        log=state.log,
    )


def write_review(state):
    review_md.write_review_md(
        state.run_dir,
        ad_brief=state.ad_brief,
        facts_pack=state.facts_pack,
        product_name=state.facts_pack["product"]["name"],
        selected=state.selected,
        pages=state.pages,
        budget=state.budget,
        cost=state.cost,
        gate_matched=state.gate_matched,
        forbidden_urls=state.forbidden_urls,
        gate_log=state.gate_log,
        product_warning=state.product_warning,
        ad_not_repeated=state.ad_not_repeated,
        ad_alternative_claims=state.ad_alternative_claims,
    )


STAGES = {
    "prepare_run": prepare_run,
    "refresh_prices": refresh_prices,
    "ingest": ingest,
    "ground": ground,
    "gate_ad_claims": gate_ad_claims,
    "write_pages": write_pages,
    "render_pages": render_pages,
    "review_notify": review_notify,
    "write_review": write_review,
}

DEFAULT_STAGES = (
    "prepare_run",
    "refresh_prices",
    "ingest",
    "ground",
    "gate_ad_claims",
    "write_pages",
    "render_pages",
    "review_notify",
    "write_review",
)


class UnknownCartridge(Exception):
    pass


class UnknownStage(Exception):
    pass


def abort_budget_run(log, budget, e, *, tenant=None, run_id=None, today_iso=None, gate_log=None, stage="run"):
    """The budget-STOP tail, shared by execute() and cli.cmd_ingest (review
    R20 -- cmd_ingest used to re-implement its own thinner version): log the
    event, record the spend into the tenant's daily ledger (phase 3), write
    the budget summary and the run_result line, close the log. The caller
    prints the one-line message and returns exit 3."""
    log.event(stage, f"budget exceeded: {e}")
    if tenant is not None:
        budget_mod.record_spend(tenant, run_id=run_id, cost=log.cost_estimate(),
                                today_iso=today_iso or datetime.date.today().isoformat(), log=log)
    log.budget_summary(budget.summary())
    review_md.log_run_result(log, "STOP", gate_log or {})
    log.close()


def execute(state, stage_names=DEFAULT_STAGES):
    """Run the named stages in order. Returns the process exit code.

    A gate STOP is exit 2 and never leaves a partial page; a budget overrun is
    exit 3, likewise. The cost estimate and REVIEW.md are written last, so a
    STOP still records what the run spent.

    Cycle 34: every exit path (typed STOP, PASS, or an unexpected stage
    exception) closes state.log. Named except blocks may also call close();
    RunLog.close is idempotent so a shared finally is safe."""

    stage_names = list(stage_names)
    unknown = [n for n in stage_names if n not in STAGES and n != "write_review"]
    if unknown:
        raise UnknownStage(f"unknown stage(s): {unknown}; known: {sorted(STAGES)}")

    try:
        try:
            for name in stage_names:
                if name == "write_review":
                    state.cost = state.log.cost_estimate()
                    state.log.budget_summary(state.budget.summary())
                    budget_mod.record_spend(state.tenant, run_id=state.run_id, cost=state.cost,
                                            today_iso=state.today_iso, log=state.log)
                STAGES[name](state)
        except UnknownCartridge as e:
            print(str(e), file=sys.stderr)
            return 1
        except ClaimsGateFailure as e:
            (state.run_dir / "unmatched_claims.json").write_text(
                json.dumps({"stage": e.stage, "items": e.items}, indent=2)
            )
            state.log.gate_result("STOP", f"stage={e.stage} unmatched={len(e.items)}")
            state.log.event("run", str(e))
            budget_mod.record_spend(state.tenant, run_id=state.run_id, cost=state.log.cost_estimate(),
                                    today_iso=state.today_iso, log=state.log)
            state.log.budget_summary(state.budget.summary())
            review_md.log_run_result(state.log, "STOP", state.gate_log)
            print(
                f"Claims gate STOPPED at stage {e.stage!r}: {len(e.items)} unmatched item(s).",
                file=sys.stderr,
            )
            print(json.dumps(e.items, indent=2), file=sys.stderr)
            print(f"See {state.run_dir / 'unmatched_claims.json'}", file=sys.stderr)
            return 2
        except BudgetExceeded as e:
            abort_budget_run(state.log, state.budget, e, tenant=state.tenant, run_id=state.run_id,
                             today_iso=state.today_iso, gate_log=state.gate_log)
            print(f"budget exceeded: {e}", file=sys.stderr)
            return 3

        review_md.log_run_result(state.log, "PASS", state.gate_log)
        # Cycle 26b (bug 1): build every page's <page>-review.html right away so
        # the review site's iframe never shows "Not Found" for a run that only
        # went through `harness run` -- same function `harness review` uses
        # (review.build_reviews), guarded so a build failure warns instead of
        # failing an otherwise-successful run.
        try:
            from .review import build_reviews

            build_reviews(state.run_dir)
        except Exception as e:
            state.log.event("run", f"warning: failed to build review html: {e}")
        print(f"Run complete: {state.run_dir}")
        for p in state.outputs:
            print(f" - {p}")
        return 0
    finally:
        if state.log:
            state.log.close()
