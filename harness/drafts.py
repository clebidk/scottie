"""Cycle 76: best-of-N listicle drafts, judged by Jev (harness/jev.py).

A listicle run with tenant.yaml `jev.enabled: true` and `jev.drafts: N > 1`
writes N drafts at once (one thread each), and every gate runs on each draft
exactly as on a single page (repair.write_and_gate_page).

What makes draft k a different attempt, not a re-sample of the same prompt:
the same style, look, facts pack, verified claims, ad quotes and assets, but
  - another headline template: a seeded pick (run seed + draft number) from
    the templates the style and the evidence allow, minus those an earlier
    draft used (a pinned --headline-template stays pinned), and
  - another winner skeleton (item map): the next one in skeletons.ranked()
    for this ad that no earlier draft used (a pinned --skeleton stays
    pinned), and
  - when neither can differ (the questions, myths and tested styles allow
    one headline template and one skeleton each), the "objection-first"
    draft angle (skeletons.DRAFT_ANGLE_LINE): the dek, the headline's free
    slots and item order start from the reader's main doubt.
The hook and the item map are what change the reader's experience most, and
they change nothing the gates check against, so every draft faces the same
gates with the same inputs.

Which draft ships:
  - A draft that fails a gate never ships.
  - Two or more pass on attempt 1: Jev scores those; the higher composite
    ships, a tie goes to the lower draft number. Jev unavailable (no key,
    HTTP error, timeout, too little wall clock left): the first passing
    draft ships, and the reason is logged.
  - One passes: it ships (no Jev call -- there is nothing to choose).
  - None passes on attempt 1: exactly the single-draft run -- draft 1 goes
    through the patch-repair loop; the other drafts are dropped. Repairs are
    for draft 1 only, and only when no draft passed on attempt 1, so a
    best-of-N run never pays for a repair it does not need.

Prompt cache: everything before the last cache breakpoint is the same for
every draft (harness/write.py puts each draft's own headline and skeleton
lines after it). Drafts 2..n start when draft 1's response starts streaming
(its cache entry exists then; at most CACHE_WAIT_S), so they read the cache
draft 1 wrote instead of writing their own copy.

Message Batches (tenant.yaml `batch_api.non_interactive`, harness/batch.py):
the first write of every draft goes in one batch at half price; each
draft's page then runs the same gates and the same rules below.

Second draft only when needed (`jev.second_draft_below`, e.g. 0.70; unset =
always write every draft). In real time, draft 1 is written alone first.
Draft 2 is written only when draft 1 fails a gate on attempt 1 (before any
repair, as the backup), or passes but Jev scores it below the threshold.
Jev unavailable: draft 1 ships and draft 2 is not written (it could only
ship draft 1 anyway). A batch writes every draft (both at half price).
Measured on the fixed 10-input batch (docs/FIXLOG.md cycle 76): no run where
draft 1 scored >= 0.70 shipped draft 2, so the rule ships the same pages for
about 40% less model spend.

Minimum margin (`jev.min_margin`, default 0.02): a later draft ships only
when its composite beats the first passing draft by at least this much --
Jev's composite moves about 0.003 between identical calls, and two drafts a
hair apart are a coin toss, so the first draft keeps the page stable.

Recorded: the run log (every write line is tagged with its draft, e.g.
"write.listicle[draft 2]", plus "drafts:" and "jev:" lines), state.json's
"jev" entry (each draft's gate result, Jev scores, composite, which draft
shipped and why, Jev token usage), and every passing draft's page.json under
<run>/drafts/. harness/site.py shows the table on the generation page.
"""
import concurrent.futures
import json
import random
import threading
import time

from . import headlines
from . import jev
from . import repair
from . import runstate
from . import skeletons
from .budget import BudgetExceeded
from .claims import ClaimsGateFailure

# Drafts 2..n wait at most this long for draft 1's response to start.
CACHE_WAIT_S = 45
# Jev must answer inside the run's wall clock with this much to spare for
# render and review.
WALL_RESERVE_S = 30
MIN_JEV_TIMEOUT_S = 10


class _DraftLog:
    """The run log, with every stage tagged by draft number."""

    def __init__(self, log, number):
        self._log = log
        self._tag = f"[draft {number}]"

    def event(self, stage, message):
        self._log.event(f"{stage}{self._tag}", message)

    def call(self, stage, *args, **kwargs):
        self._log.call(f"{stage}{self._tag}", *args, **kwargs)

    def gate_result(self, result, detail=""):
        self._log.gate_result(result, f"{detail} {self._tag}".strip())

    def __getattr__(self, name):
        return getattr(self._log, name)


# ---------------------------------------------------------------------------
# the drafts' inputs
# ---------------------------------------------------------------------------

def variants(state, n, *, requested_skeleton=None, requested_headline=None):
    """[{"headline": plan, "skeleton": writer payload}] for drafts 1..n.
    Draft 1 is the run's own pick (state.listicle_headline /
    state.listicle_skeleton); see the module docstring for drafts 2..n."""
    out = [{"headline": state.listicle_headline, "skeleton": state.listicle_skeleton}]
    style = state.listicle_style
    used_headlines = {(state.listicle_headline or {}).get("id")}
    used_skeletons = {(state.listicle_skeleton or {}).get("id")}
    prefer = None
    if requested_skeleton:
        prefer = {headlines.canonical_id(t) for t in skeletons.load_skeleton(requested_skeleton)["headline_templates"]}
    ranked = [] if requested_skeleton else skeletons.ranked(style, state.ad_brief, tenant=state.tenant)
    for k in range(2, n + 1):
        plan = state.listicle_headline
        if not requested_headline:
            ids = headlines.eligible_templates(style, state.facts_pack, state.tenant)
            pool = [t for t in ids if t not in used_headlines and (not prefer or t in prefer)]
            pool = pool or [t for t in ids if t not in used_headlines]
            if pool:
                pick = random.Random(f"listicle-headline:{state.seed}:{style}:draft{k}").choice(pool)
                plan = headlines.build_plan(pick, style, state.facts_pack, tenant=state.tenant,
                                            today=state.today_iso)
        used_headlines.add((plan or {}).get("id"))
        skeleton = state.listicle_skeleton
        nxt = next((sk for sk in ranked if sk["id"] not in used_skeletons), None)
        if nxt is not None:
            skeleton = skeletons.for_writer(nxt, state.tenant)
        used_skeletons.add((skeleton or {}).get("id"))
        if (plan or {}).get("id") == (out[0]["headline"] or {}).get("id") and \
                (skeleton or {}).get("id") == (out[0]["skeleton"] or {}).get("id") and skeleton:
            # Nothing else differs (one template and one skeleton for the
            # style, or both pinned): another angle on the same page.
            skeleton = {**skeleton, "draft_angle": skeletons.DRAFT_ANGLE}
        out.append({"headline": plan, "skeleton": skeleton})
    return out


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _first_failure_keys(attempts):
    first = attempts[0] if attempts else []
    return [str(item.get("key") or item.get("path") or item.get("issue")) for item in first][:12]


def _write_one(state, kwargs, plan, k, on_first_failure):
    try:
        page, attempts, fixes = repair.write_and_gate_page(
            **{**kwargs, "initial_page": None, "initial_call_tokens": 0},
            log=_DraftLog(state.log, k + 1),
            listicle_headline=plan[k]["headline"],
            listicle_skeleton=plan[k]["skeleton"],
            on_first_failure=on_first_failure,
        )
        return {"page": page, "attempts": attempts, "fixes": fixes, "error": None}
    except Exception as err:
        return {"page": None, "attempts": getattr(err, "attempts", None),
                "fixes": getattr(err, "deterministic_fixes", None), "error": err}


def _write_conditional(state, kwargs, plan, threshold):
    """Real-time drafts, one after the other, each only when needed (see the
    module docstring). Returns (results, {draft index: jev result} already
    scored, a note on why later drafts were not written or None)."""
    n = len(plan)
    results = [None] * n
    scored = {}
    note = None

    def later_drafts_pass():
        # Draft 1 failed attempt 1: write the others as its backup first;
        # draft 1 repairs only when none of them passes.
        for k in range(1, n):
            results[k] = _write_one(state, kwargs, plan, k, lambda: False)
            if results[k]["error"] is None or isinstance(results[k]["error"], BudgetExceeded):
                return True
        return False

    results[0] = _write_one(state, kwargs, plan, 0, lambda: not later_drafts_pass())
    k = 0
    while results[0]["error"] is None and k + 1 < n and results[k + 1] is None:
        todo = [i for i in range(k + 1) if results[i]["error"] is None and i not in scored]
        try:
            scored.update(_score_drafts(state, todo, results) if todo else {})
        except Exception as e:  # JevUnavailable or anything else: never fail a run over the judge
            note = f"Jev unavailable ({e}); draft {k + 2} not written"
            break
        best = max(scored, key=lambda i: (scored[i]["composite"], -i))
        if scored[best]["composite"] >= threshold:
            note = (f"draft {best + 1} composite {scored[best]['composite']:.3f} >= second_draft_below "
                    f"{threshold}: draft {k + 2} not written")
            break
        results[k + 1] = _write_one(state, kwargs, plan, k + 1, lambda: False)
        k += 1
    for i in range(n):
        if results[i] is None:
            results[i] = {"page": None, "attempts": None, "fixes": None, "error": None, "skipped": True}
    return results, scored, note


def _write_all(state, kwargs, plan, initial=None):
    """Run write_and_gate_page for every draft at once. Returns one
    {"page", "attempts", "fixes", "error"} per draft. `initial`: per draft,
    (page, tokens) from a Message Batch, or None to write it here."""
    n = len(plan)
    initial = list(initial or []) + [None] * n
    futures = []
    submitted = threading.Event()
    cache_ready = threading.Event()

    def others_passed():
        submitted.wait()
        concurrent.futures.wait(futures[1:])
        return any(f.exception() is None for f in futures[1:])

    def task(k):
        page, tokens = initial[k] or (None, 0)
        if k and page is None and not cache_ready.wait(CACHE_WAIT_S):
            state.log.event(f"drafts[draft {k + 1}]", f"draft 1 had not started after {CACHE_WAIT_S}s; writing now")
        try:
            return repair.write_and_gate_page(
                **{**kwargs, "initial_page": page, "initial_call_tokens": tokens},
                on_write_started=cache_ready.set if k == 0 else None,
                log=_DraftLog(state.log, k + 1),
                listicle_headline=plan[k]["headline"],
                listicle_skeleton=plan[k]["skeleton"],
                # Draft 1 repairs only when no other draft passed attempt 1;
                # drafts 2..n never repair.
                on_first_failure=(lambda: not others_passed()) if k == 0 else (lambda: False),
            )
        finally:
            if k == 0:
                cache_ready.set()  # a batch page or an early error: nothing to wait for

    with concurrent.futures.ThreadPoolExecutor(max_workers=n, thread_name_prefix="draft") as pool:
        for k in range(n):
            futures.append(pool.submit(task, k))
        submitted.set()
        concurrent.futures.wait(futures)

    results = []
    for f in futures:
        err = f.exception()
        if err is None:
            page, attempts, fixes = f.result()
            results.append({"page": page, "attempts": attempts, "fixes": fixes, "error": None})
        else:
            results.append({"page": None, "attempts": getattr(err, "attempts", None), "fixes":
                            getattr(err, "deterministic_fixes", None), "error": err})
    return results


# ---------------------------------------------------------------------------
# judging
# ---------------------------------------------------------------------------

def _score_drafts(state, passed, results):
    """{draft index: jev result} for every passing draft, or raises
    JevUnavailable."""
    cfg = jev.settings(state.tenant)
    key = jev.api_key()
    if not key:
        raise jev.JevUnavailable(f"no {jev.KEY_ENV} in the environment")
    rubric = jev.load_rubric(state.tenant)
    left = state.budget.wall_s - state.budget.summary()["elapsed_s"] - WALL_RESERVE_S
    timeout_s = min(cfg["timeout_s"], left)
    if timeout_s < MIN_JEV_TIMEOUT_S:
        raise jev.JevUnavailable(f"only {max(left, 0):.0f}s of wall clock left for Jev")
    deadline = time.monotonic() + left
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(passed), thread_name_prefix="jev") as pool:
        futures = {
            k: pool.submit(jev.score_page, results[k]["page"], state.ad_brief, rubric,
                           key=key, timeout_s=timeout_s, deadline=deadline)
            for k in passed
        }
        return {k: f.result() for k, f in futures.items()}


def write_best_listicle(state, kwargs, n, *, requested_skeleton=None, requested_headline=None, plan=None,
                        initial=None):
    """The listicle's (page, attempts, deterministic_fixes) -- the same
    return as repair.write_and_gate_page -- after writing n drafts and
    picking one. Sets state.listicle_headline / state.listicle_skeleton to the
    shipped draft's, so render_pages records them. Raises exactly what a
    single-draft run raises when no draft can ship. `plan`/`initial`: the
    drafts' variants and their batch-written first pages (pipeline's batch
    path); else the variants are made here and every draft is written here."""
    log = state.log
    plan = plan or variants(state, n, requested_skeleton=requested_skeleton, requested_headline=requested_headline)
    for k, v in enumerate(plan, 1):
        log.event("drafts", f"draft {k}: headline template {(v['headline'] or {}).get('id')}, "
                            f"skeleton {(v['skeleton'] or {}).get('id')}"
                            + (f", angle {v['skeleton']['draft_angle']}" if (v["skeleton"] or {}).get("draft_angle") else "")
                            + (" (batch)" if initial and k <= len(initial) and initial[k - 1] else ""))
    threshold = jev.settings(state.tenant)["second_draft_below"]
    prescored, skip_note = {}, None
    if threshold is not None and not any(initial or []):
        results, prescored, skip_note = _write_conditional(state, kwargs, plan, threshold)
    else:
        results = _write_all(state, kwargs, plan, initial)
    state.drafts_written = sum(1 for r in results if not r.get("skipped"))

    rows = []
    for k, r in enumerate(results):
        if r.get("skipped"):
            gate = "SKIPPED"
        elif r["error"] is None:
            gate = "PASS"
        elif isinstance(r["error"], ClaimsGateFailure):
            gate = "FAIL"
        else:
            gate = "ERROR"
        rows.append({
            "draft": k + 1,
            "headline_template_id": (plan[k]["headline"] or {}).get("id"),
            "skeleton_id": (plan[k]["skeleton"] or {}).get("id"),
            **({"angle": plan[k]["skeleton"]["draft_angle"]} if (plan[k]["skeleton"] or {}).get("draft_angle") else {}),
            "gate": gate,
            "attempts": len(r["attempts"] or []),
            "failures": _first_failure_keys(r["attempts"]),
            **({"error": f"{type(r['error']).__name__}: {r['error']}"[:300]} if gate == "ERROR" else {}),
        })
        log.event("drafts", f"draft {k + 1}: gate {gate}"
                            + (f" after {len(r['attempts'])} attempt(s)" if r["attempts"] else "")
                            + (f" ({rows[-1].get('error') or ', '.join(rows[-1]['failures'])})" if gate != "PASS" else ""))

    record = {"drafts": rows, "shipped": None, "reason": "", "status": "", "usage": None}
    over = next((r["error"] for r in results if isinstance(r["error"], BudgetExceeded)), None)
    if over is not None:
        # A budget cap is a hard stop whichever draft hit it.
        record.update(status="budget", reason=str(over))
        _save(state, record)
        raise over
    passed = [k for k, r in enumerate(results) if r["error"] is None and not r.get("skipped")]
    if not passed:
        # Draft 1 went through the repair loop and still failed (or raised):
        # exactly the single-draft STOP.
        record.update(status="none_passed", reason="no draft passed the gates; draft 1 repairs failed")
        _save(state, record)
        raise results[0]["error"]

    winner = passed[0]
    scored = dict(prescored)
    if len(passed) == 1:
        if skip_note:
            record.update(status="first_strong" if scored else "jev_unavailable", reason=skip_note)
        elif results[winner]["attempts"] and len(results[winner]["attempts"]) > 1:
            record.update(status="repaired",
                          reason=f"no draft passed on attempt 1; draft 1 passed after "
                                 f"{len(results[winner]['attempts']) - 1} repair(s)")
        else:
            record.update(status="single_pass", reason=f"only draft {winner + 1} passed the gates")
    else:
        try:
            todo = [k for k in passed if k not in scored]
            scored.update(_score_drafts(state, todo, results) if todo else {})
        except jev.JevUnavailable as e:
            record.update(status="jev_unavailable",
                          reason=f"Jev unavailable ({e}); first passing draft {winner + 1} ships")
        except Exception as e:  # never fail a run over the judge
            record.update(status="jev_unavailable",
                          reason=f"Jev error ({type(e).__name__}: {e}); first passing draft {winner + 1} ships")
        else:
            min_margin = jev.settings(state.tenant)["min_margin"]
            first = passed[0]
            best = max(passed, key=lambda k: (scored[k]["composite"], -k))
            if best != first and scored[best]["composite"] - scored[first]["composite"] >= min_margin:
                winner, other, note = best, first, ""
            else:
                winner = first
                other = max((k for k in passed if k != first), key=lambda k: (scored[k]["composite"], -k))
                gap = scored[other]["composite"] - scored[first]["composite"]
                note = (" (tie: first passing draft ships)" if gap == 0 else
                        f" (gap {gap:.3f} < min_margin {min_margin}: first passing draft ships)" if gap > 0 else "")
            record.update(status="scored", min_margin=min_margin,
                          reason=(f"draft {winner + 1} composite {scored[winner]['composite']:.3f} vs draft "
                                  f"{other + 1} {scored[other]['composite']:.3f}" + note))
    if scored:
        usage = {"input_tokens": 0, "output_tokens": 0}
        for k, res in sorted(scored.items()):
            rows[k].update(scores=res["scores"], raw=res["raw"], composite=res["composite"],
                           overall=res["overall"], jev_model=res["model"])
            for u in usage:
                usage[u] += res["usage"][u]
            log.event("jev", f"draft {k + 1}: composite {res['composite']:.3f} overall "
                             f"{res['overall'] if res['overall'] is not None else '-'} "
                             + " ".join(f"{q}={v:.2f}" for q, v in res["scores"].items()))
        record["usage"] = usage
        log.event("jev", f"usage input_tokens={usage['input_tokens']} output_tokens={usage['output_tokens']} "
                         f"(billed by TypeSafe, not in estimated_cost_usd)")
    if record["status"] == "jev_unavailable":
        log.event("jev", record["reason"])

    record["shipped"] = winner + 1
    log.event("drafts", f"shipped draft {winner + 1}: {record['reason']}")
    state.listicle_headline = plan[winner]["headline"]
    state.listicle_skeleton = plan[winner]["skeleton"]
    _save(state, record, pages={k + 1: results[k]["page"] for k in passed})
    w = results[winner]
    return w["page"], w["attempts"], w["fixes"]


def _save(state, record, pages=None):
    runstate.record_jev(state.run_dir, record)
    if pages:
        out = state.run_dir / "drafts"
        out.mkdir(exist_ok=True)
        for number, page in pages.items():
            (out / f"listicle-draft-{number}.json").write_text(json.dumps(page, indent=2))
