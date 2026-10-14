"""Cycle 109: `harness abtest rebuild-arm <test_id> <A|B|C> --by <email>`
(and `--all`): build a live test's generated arm again with the current code
and claims, and put the new page on the arm's EXISTING Shopify page.

Why (owner, 2026-10-14): five sale-ad tests went live with pages that denied
the sale; once the sale's promo claim is verified the pages must be built
again as offer-led pages, without touching the split link or the Meta ad
that points at it.

For one arm:
  - refused unless the test is live and the arm is a generated page that was
    published (its run has a Shopify page id);
  - one `harness run` of the arm's own build, pinned to the look and hero the
    arm rendered, with the next seed of the arm's seed family (the same
    formula abtest._build_variant uses), and the design gate in warn mode
    (HARNESS_DESIGN_GATE=warn for this run) so its score is recorded and a low
    score never stops the rebuild;
  - the new run takes over the old run's published page record
    (runstate.carry_published) and the test's beacon (runstate.record_abtest),
    then `harness publish --update` (approve + ship stamp first, like every
    A/B/C publish) updates that page: same page id, same handle, same split
    link, same ad;
  - the variant's run_dir becomes the new run; the old one is kept in the
    variant's `history` list with its design score and the time it was
    replaced. The arm's view and click counts are NOT reset (the cycle 69
    "Replace live variant" resets them; this command does not).
"""
import contextlib
import os
from pathlib import Path

from . import abtest, exits, runstate
from .errors import HarnessError

DESIGN_GATE_ENV = "HARNESS_DESIGN_GATE"


@contextlib.contextmanager
def _design_gate_warn():
    before = os.environ.get(DESIGN_GATE_ENV)
    os.environ[DESIGN_GATE_ENV] = "warn"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(DESIGN_GATE_ENV, None)
        else:
            os.environ[DESIGN_GATE_ENV] = before


def generated_keys(rec):
    return [v["key"] for v in rec["variants"] if not abtest.is_external(v)]


def _check(tenant, rec, key, by):
    if rec.get("status") != "live":
        raise abtest.AbtestError(f"test {rec['test_id']} is {rec.get('status')!r}; rebuild-arm rebuilds an arm of a "
                                 "live test only (publish it first)")
    if runstate.find_reviewer(tenant, by) is None:
        raise abtest.AbtestError(f"{by!r} is not a listed reviewer for tenant {tenant.name!r}")
    variant = next((v for v in rec["variants"] if v["key"] == key), None)
    if variant is None:
        raise abtest.AbtestError(f"test {rec['test_id']} has no arm {key!r}")
    if abtest.is_external(variant):
        raise abtest.AbtestError(f"arm {key} of test {rec['test_id']} is an external page "
                                 f"({abtest.variant_label(variant)}); there is no page to rebuild")
    if not variant.get("run_dir") or not variant.get("cartridge"):
        raise abtest.AbtestError(f"arm {key} of test {rec['test_id']} was never built")
    record = runstate.published_page_record(Path(variant["run_dir"]), variant["cartridge"])
    if not record or not record.get("page_id"):
        raise abtest.AbtestError(f"arm {key} of test {rec['test_id']} has no published Shopify page to update")
    return variant, record


def _arm(tenant, variant):
    """The arm's own build, pinned to the look and the hero it rendered."""
    base = abtest.parse_arm(variant["arm"], tenant)
    return abtest.Arm(base.id, base.cartridge, base.style, variant.get("look") or base.look,
                      variant.get("hero_style") or base.hero_style)


def rebuild_arm(tenant, test_id, key, *, by, runner=None, log=print):
    """Rebuilds and republishes one generated arm (module docstring). Returns
    {key, old_run_dir, run_dir, handle, url, page_id, design_score,
    design_status, look, hero_style}. Raises AbtestError; the live page is
    unchanged when it does."""
    from . import jobs

    key = str(key).upper()
    rec = abtest.load_test(tenant, test_id)
    variant, published = _check(tenant, rec, key, by)
    arm = _arm(tenant, variant)
    old_run = Path(variant["run_dir"])
    cartridge = variant["cartridge"]
    seed = rec["seed"] + 1000 * (abtest.KEYS.index(key) + 1) + len(variant.get("attempts") or []) + 1
    log(f"rebuilding arm {key} of {test_id}: {arm.id} (look {arm.look or '-'}, hero {arm.hero_style or '-'}), "
        f"seed {seed}")
    try:
        with _design_gate_warn():
            rc, run_dir = (runner or abtest.default_runner)(tenant, rec["input"], arm, seed)
    except HarnessError as e:
        rc, run_dir = e.exit_code, None
    attempt = {"arm": variant["arm"], "seed": seed, "rc": rc, "run_dir": str(run_dir) if run_dir else None,
               "at": abtest._now(), "rebuild": True, "by": by}
    variant.setdefault("attempts", []).append(attempt)
    abtest.save_test(tenant, rec)
    if rc != exits.OK or not run_dir or not (Path(run_dir) / cartridge / "index.html").is_file():
        raise abtest.AbtestError(f"the rebuild of arm {key} failed (exit {rc}); the live page is unchanged")
    run_dir = Path(run_dir)
    design = abtest._design_record(run_dir, cartridge) or {}
    runstate.carry_published(old_run, run_dir, cartridge, note=f"abtest {test_id} arm {key}: rebuild-arm")
    runstate.record_abtest(run_dir, page=cartridge, test_id=test_id, key=key)
    try:
        record = jobs._publish_update(tenant, run_dir, cartridge, by=by, live=True, seo_hidden=True,
                                      note=f"abtest {test_id} arm {key}: rebuild-arm")
    except jobs.JobFailed as e:
        raise abtest.AbtestError(f"arm {key} was rebuilt in {run_dir.name} but its page did not update: {e}; the "
                                 f"live page still shows {old_run.name}") from e
    if str(record.get("page_id")) != str(published.get("page_id")):
        raise abtest.AbtestError(f"arm {key}: the update went to page {record.get('page_id')}, not the arm's page "
                                 f"{published.get('page_id')}; check Shopify")
    rec = abtest.load_test(tenant, test_id)
    variant = next(v for v in rec["variants"] if v["key"] == key)
    variant.setdefault("history", []).append({
        "run_dir": str(old_run), "replaced_at": abtest._utc_now(), "replaced_by": by,
        "design_score": variant.get("design_score"), "design_status": variant.get("design_status"),
        "look": variant.get("look"), "hero_style": variant.get("hero_style"),
    })
    variant["run_dir"] = str(run_dir)
    variant["rebuilt_at"], variant["rebuilt_by"] = abtest._utc_now(), by
    variant["design_score"], variant["design_status"] = design.get("score"), design.get("status")
    variant["design_cost_usd"] = design.get("cost_usd")
    rendered_look = abtest._page_field(run_dir, "look", cartridge)
    rendered_hero = abtest._page_field(run_dir, "hero_style", cartridge)
    if rendered_look:
        variant["look"] = rendered_look
    if rendered_hero:
        variant["hero_style"] = rendered_hero
    variant["shopify"] = {k: record.get(k) for k in ("page_id", "handle", "url")}
    abtest.save_test(tenant, rec)
    jobs.audit(tenant, by=by, action="abtest_rebuild_arm", target=f"test:{test_id}",
               detail={"key": key, "old_run": old_run.name, "run": run_dir.name, "page_id": record.get("page_id"),
                       "handle": record.get("handle"), "design_score": design.get("score")})
    if (arm.look and rendered_look and rendered_look != arm.look) or (
            arm.hero_style and rendered_hero and rendered_hero != arm.hero_style):
        log(f"  note: arm {key} rendered {rendered_look}/{rendered_hero}, not {arm.look}/{arm.hero_style}")
    log(f"  arm {key}: {old_run.name} -> {run_dir.name}, page {record.get('handle')} updated"
        + (f", design {design.get('score')} ({design.get('status')}, warn mode)" if design.get("score") is not None
           else ""))
    return {"key": key, "old_run_dir": str(old_run), "run_dir": str(run_dir), "handle": record.get("handle"),
            "url": record.get("url"), "page_id": record.get("page_id"), "design_score": design.get("score"),
            "design_status": design.get("status"), "look": variant.get("look"), "hero_style": variant.get("hero_style")}


def rebuild_all(tenant, test_id, *, by, runner=None, log=print):
    """rebuild_arm for every generated arm of the test, in letter order. The
    checks run for every arm first, so a refusal changes nothing."""
    rec = abtest.load_test(tenant, test_id)
    keys = generated_keys(rec)
    if not keys:
        raise abtest.AbtestError(f"test {test_id} has no generated arm")
    for key in keys:
        _check(tenant, rec, key, by)
    return [rebuild_arm(tenant, test_id, key, by=by, runner=runner, log=log) for key in keys]
