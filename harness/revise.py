"""Cycle 26: `harness revise <run-dir> --page <cartridge> [--by <email>]`.

Reads the LAST feedback entry runstate.request_changes recorded for that page
(the reviewer web app -- harness/serve.py -- writes one; a CLI/test caller can
write one directly with runstate.request_changes). Two kinds of edit, applied
in order:

1. `cut:` lines -- an exact sentence, removed from page.json's text fields by
   a normalized-whitespace string match. No model call. Deterministic.
2. Free-text notes left after the cuts are pulled out -- one writer call with
   the original prompt plus a "REVISION REQUIRED"-style REVIEWER NOTES block,
   then the full page gate and the SAME bounded repair loop write_and_gate_page
   uses for a fresh run (harness/cli.py). Counts against a fresh budget: 60k
   tokens / 4 calls, separate from any run's own 220k/14 budget.

Either way, the current page.json/index.html/<page>-review.html are versioned
(page.vN.json etc., N starting at 1) before the new version is written, the
page's state goes back to "needs_review", and the tenant's reviewers are
notified -- exactly the same shape as a fresh run reaching review.
"""
import datetime
import json
import re
from pathlib import Path

from . import notify
from . import runstate
from . import tenant as tenant_mod
from .anthropic_client import make_client
from .budget import Budget, BudgetExceeded, record_spend, reserve_spend
from .claims import ClaimsGateFailure
from .log import RunLog
from .pipeline import CARTRIDGES_DIR, TEMPLATES_DIR
from .render import render_page
from .review import build_reviews
from .write import write_page

# Cycle 26: a revise call gets its own, smaller budget than a fresh run's
# 220k tokens / 14 calls (harness/budget.py) -- it is at most one writer call
# plus the same bounded repair loop, never a whole page from scratch.
REVISE_TOKEN_BUDGET = 60_000
REVISE_CALL_BUDGET = 4

_CUT_PREFIX_RE = re.compile(r"^\s*cut:\s*", re.IGNORECASE)


class ReviseError(Exception):
    """No feedback to revise from, or the run/page does not exist."""


# ---------------------------------------------------------------------------
# Parsing the feedback textarea: lines starting "cut:" become cuts[], every
# other non-blank line is free-text notes.
# ---------------------------------------------------------------------------

def parse_cuts_and_notes(raw_text):
    """`raw_text` is the reviewer's whole "Notes and cuts" textarea. Returns
    (cuts, notes): cuts is every "cut: <sentence>" line's sentence (order
    preserved, prefix stripped); notes is every remaining non-blank line,
    rejoined with newlines."""
    cuts = []
    note_lines = []
    for line in (raw_text or "").splitlines():
        if _CUT_PREFIX_RE.match(line):
            sentence = _CUT_PREFIX_RE.sub("", line).strip()
            if sentence:
                cuts.append(sentence)
        elif line.strip():
            note_lines.append(line.strip())
    return cuts, "\n".join(note_lines)


# ---------------------------------------------------------------------------
# Deterministic cut application -- no model call.
# ---------------------------------------------------------------------------

def _normalize_ws(text):
    return " ".join((text or "").split())


def _cut_pattern(sentence):
    words = _normalize_ws(sentence).split(" ")
    if not words:
        return None
    return re.compile(r"\s*" + r"\s+".join(re.escape(w) for w in words) + r"\s*")


def _remove_sentence(text, sentence):
    """(new_text, applied). `sentence` is matched against `text` on
    normalized whitespace only -- the sentence's own wording/case must be
    exact, matching the "paste the exact sentence" instruction shown in the
    review site's form."""
    if not isinstance(text, str):
        return text, False
    pattern = _cut_pattern(sentence)
    if pattern is None:
        return text, False
    new_text, count = pattern.subn(" ", text)
    if not count:
        return text, False
    return re.sub(r"\s+", " ", new_text).strip(), True


def _is_blank_text_item(node):
    """A dict shaped like page.json's paragraph/criterion items ({"text":
    ..., "claim_ids": [...]}) whose text a cut has emptied out."""
    return isinstance(node, dict) and "text" in node and not (node.get("text") or "").strip()


def _apply_cut(node, sentence):
    """Recursively removes `sentence` from every string leaf under `node`.
    Returns (new_node, applied). A list drops any dict item whose "text"
    field the cut left empty (the "if a cut leaves a field empty, drop the
    item" rule) -- everything else is kept, cut or not."""
    if isinstance(node, str):
        return _remove_sentence(node, sentence)
    if isinstance(node, list):
        applied = False
        new_list = []
        for item in node:
            new_item, item_applied = _apply_cut(item, sentence)
            applied = applied or item_applied
            if item_applied and _is_blank_text_item(new_item):
                continue
            new_list.append(new_item)
        return new_list, applied
    if isinstance(node, dict):
        applied = False
        new_dict = {}
        for key, value in node.items():
            new_value, value_applied = _apply_cut(value, sentence)
            applied = applied or value_applied
            new_dict[key] = new_value
        return new_dict, applied
    return node, False


def apply_cuts_to_page(page, cuts):
    """Applies every cut in `cuts`, in order. Returns (new_page,
    applied_cuts) -- applied_cuts is the subset that actually matched
    something, in the order given (a cut whose sentence is not found on the
    page, e.g. because an earlier cut already removed it, is silently
    skipped -- same "better a no-op than a crash" rule as inline_assets_as_
    data_uris in harness/review.py)."""
    new_page = page
    applied = []
    for sentence in cuts:
        new_page, did = _apply_cut(new_page, sentence)
        if did:
            applied.append(sentence)
    return new_page, applied


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

_PAGE_VERSION_RE = re.compile(r"^page\.v(\d+)\.json$")


def next_version(cartridge_dir):
    """1 if no page.vN.json exists yet under `cartridge_dir`, else the
    highest existing N + 1."""
    cartridge_dir = Path(cartridge_dir)
    highest = 0
    for path in cartridge_dir.glob("page.v*.json"):
        m = _PAGE_VERSION_RE.match(path.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _version_existing_files(run_dir, page_name, version):
    """Moves the CURRENT page.json/index.html/<page>-review.html to their
    .vN. names before the new version is written. A run's first revise call
    (version=1) is therefore the one that makes the original generation
    available as page.v1.json etc. -- exactly like git: version 1 is not
    "no version", it's the first one that got superseded."""
    run_dir = Path(run_dir)
    cartridge_dir = run_dir / page_name
    for src_name, dst_name in (
        ("page.json", f"page.v{version}.json"),
        ("index.html", f"index.v{version}.html"),
    ):
        src = cartridge_dir / src_name
        if src.exists():
            src.rename(cartridge_dir / dst_name)
    review_src = run_dir / f"{page_name}-review.html"
    if review_src.exists():
        review_src.rename(run_dir / f"{page_name}-review.v{version}.html")


# ---------------------------------------------------------------------------
# REVIEW.md -- append, never rewrite the original generation report.
# ---------------------------------------------------------------------------

def _append_revision_note(run_dir, *, page, version, by, applied_cuts, notes, model_called,
                           gate_problems, cost):
    review_path = Path(run_dir) / "REVIEW.md"
    lines = ["", f"## Revision: {page} v{version} ({datetime.datetime.now().isoformat(timespec='seconds')})"]
    lines.append(f"- By: {by}")
    if applied_cuts:
        lines.append(f"- Cuts applied ({len(applied_cuts)}):")
        for sentence in applied_cuts:
            lines.append(f"  - \"{sentence}\"")
    else:
        lines.append("- Cuts applied: none")
    lines.append(f"- Reviewer notes: {notes or '(none)'}")
    lines.append(f"- Writer called: {'yes' if model_called else 'no (cuts only)'}")
    if gate_problems:
        lines.append(f"- Gate result: FAIL ({len(gate_problems)} issue(s)) -- needs another look")
        for item in gate_problems:
            lines.append(f"  - {item.get('path')}: {item.get('issue')}")
    else:
        lines.append("- Gate result: PASS")
    if model_called:
        lines.append(f"- Estimated cost of this revision: ${cost:.4f}")
    with open(review_path, "a") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def revise_page(run_dir, page_name, *, by=None, tenant=None, make_client_fn=make_client):
    """Does the whole revise. Returns a result dict: {page, version,
    model_called, applied_cuts, gate_problems, cost}. Raises ReviseError if
    there is no feedback to revise from, or the page/run doesn't exist."""
    run_dir = Path(run_dir)
    if tenant is None:
        from .runstate import tenant_name_from_run_dir

        tenant = tenant_mod.load_tenant(tenant_name_from_run_dir(run_dir))
    tenant.load_env()
    tenant_mod.activate(tenant)

    state = runstate.load_state(run_dir)
    if page_name not in state.get("pages", {}):
        raise ReviseError(f"unknown page {page_name!r} for run {run_dir}; run has: {list(state.get('pages', {}))}")

    feedback = runstate.latest_feedback(run_dir, page_name)
    if feedback is None:
        raise ReviseError(f"no feedback recorded for page {page_name!r} of run {run_dir} -- nothing to revise")

    cartridge_dir = run_dir / page_name
    page = json.loads((cartridge_dir / "page.json").read_text())
    ad_brief = json.loads((run_dir / "ad_brief.json").read_text())
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())

    cuts = feedback.get("cuts") or []
    notes = feedback.get("notes") or ""

    page, applied_cuts = apply_cuts_to_page(page, cuts)

    version = next_version(cartridge_dir)
    run_id = run_dir.name
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}-revise-{page_name}-v{version}.log")
    log.event(f"revise.{page_name}", f"feedback from {feedback.get('by')}, {len(cuts)} cut(s), notes={'yes' if notes.strip() else 'no'}")

    model_called = False
    gate_problems = []
    cost = 0.0

    if notes.strip():
        model_called = True
        from .repair import cartridge_write_constraints, write_and_gate_page

        # Cycle 34: notes-driven revise spends tokens. Reserve against the
        # tenant daily ledger before the first model call and record actual
        # cost on the way out (success, gate failure, or mid-revise abort) --
        # same contract pipeline uses for harness run.
        spend_run_id = f"{run_dir.name}__revise__{page_name}__v{version}"
        today_iso = datetime.date.today().isoformat()
        reserve_spend(tenant, run_id=spend_run_id, today_iso=today_iso, log=log)
        try:
            client = make_client_fn()
            budget = Budget(wall_s=600, tokens=REVISE_TOKEN_BUDGET, calls=REVISE_CALL_BUDGET)
            _schema, word_range, allowed_cta_texts = cartridge_write_constraints(
                page_name, CARTRIDGES_DIR, facts_pack, ad_brief, tenant
            )
            revision_note = (
                "## REVIEWER NOTES — apply these changes and keep everything else\n\n"
                "The page below already went through one round of review. Apply ONLY the "
                "requested changes; every other sentence, section, and fact must stay as it "
                "is unless the requested change requires touching it.\n\n"
                f"Current page (JSON):\n{json.dumps(page)}\n\n"
                f"Requested changes:\n{notes.strip()}\n"
            )
            write_model = tenant.model_for("write")
            tokens_before = budget.tokens_used
            initial_page = write_page(
                cartridge_name=page_name, cartridges_dir=CARTRIDGES_DIR, ad_brief=ad_brief,
                facts_pack=facts_pack, client=client, model=write_model, budget=budget, log=log,
                word_range=word_range, allowed_cta_texts=allowed_cta_texts, revision_note=revision_note,
                tenant=tenant,
            )
            initial_call_tokens = budget.tokens_used - tokens_before
            try:
                page, attempts, _det_fixes = write_and_gate_page(
                    cartridge_name=page_name, cartridges_dir=CARTRIDGES_DIR, ad_brief=ad_brief,
                    facts_pack=facts_pack, client=client, model=write_model, budget=budget, log=log,
                    financing_lender=tenant.claims_config.get("financing_lender"),
                    speaker_pov=ad_brief.get("speaker_pov"), tenant=tenant,
                    repair_first_model=tenant.model_for("repair_first"),
                    repair_next_model=tenant.model_for("repair_next"),
                    initial_page=initial_page, initial_call_tokens=initial_call_tokens,
                )
            except (ClaimsGateFailure, BudgetExceeded) as e:
                # Same rule a fresh run follows: never write a page that failed
                # every repair attempt as though it were a success. Nothing on
                # disk changes -- the page stays at its current version, still
                # "changes_requested", so the reviewer's feedback is still there
                # to revise from again.
                runstate.set_revise_status(run_dir, page=page_name, status="failed", detail=str(e))
                raise ReviseError(f"revise for page {page_name!r} of run {run_dir} did not pass the gate: {e}") from e
            gate_problems = attempts[-1] if attempts else []
            cost = log.cost_estimate()
        finally:
            record_spend(
                tenant, run_id=spend_run_id, cost=log.cost_estimate(),
                today_iso=today_iso, log=log,
            )
    elif applied_cuts:
        # Cuts only: no model call allowed, so no repair loop either -- the
        # gate still runs so a broken cut (e.g. one that hollows out a
        # required section) is visible in REVIEW.md/state.json rather than
        # silently shipped, but nothing here can fix a failure automatically.
        from .repair import cartridge_write_constraints, check_page_gates

        _schema, word_range, allowed_cta_texts = cartridge_write_constraints(
            page_name, CARTRIDGES_DIR, facts_pack, ad_brief, tenant
        )
        gate_problems = check_page_gates(
            page, facts_pack, page_name,
            financing_lender=tenant.claims_config.get("financing_lender"),
            speaker_pov=ad_brief.get("speaker_pov"), word_range=word_range,
            allowed_cta_texts=allowed_cta_texts, ad_brief=ad_brief,
        )
        log.gate_result("PASS" if not gate_problems else "FAIL", f"page_json:{page_name} (cuts only)")
    else:
        raise ReviseError(f"feedback for page {page_name!r} has no cut: lines and no notes -- nothing to revise")

    _version_existing_files(run_dir, page_name, version)
    (cartridge_dir / "page.json").write_text(json.dumps(page, indent=2) + "\n")

    today_iso = datetime.date.today().isoformat()
    render_page(
        cartridge_name=page_name, page=page, ad_brief=ad_brief, facts_pack=facts_pack,
        cartridges_dir=CARTRIDGES_DIR, brand_dir=tenant.brand_dir, templates_dir=TEMPLATES_DIR,
        out_dir=cartridge_dir, published=today_iso, updated=today_iso, log=log, tenant=tenant,
    )
    build_reviews(run_dir)

    _append_revision_note(
        run_dir, page=page_name, version=version, by=feedback.get("by") or by or "operator",
        applied_cuts=applied_cuts, notes=notes, model_called=model_called,
        gate_problems=gate_problems, cost=cost,
    )

    runstate.mark_revised(run_dir, page=page_name, version=version, by=by or feedback.get("by") or "system",
                           note="gate PASS" if not gate_problems else f"gate FAIL ({len(gate_problems)} issue(s))")
    runstate.set_revise_status(run_dir, page=page_name, status="done",
                                detail="gate PASS" if not gate_problems else "gate FAIL")

    result = "PASS" if not gate_problems else "FAIL"
    notify.notify_revise_complete(
        tenant, run_id=run_id, page=page_name, version=version, result=result, run_dir=str(run_dir), log=log,
    )

    return {
        "page": page_name, "version": version, "model_called": model_called,
        "applied_cuts": applied_cuts, "gate_problems": gate_problems, "cost": cost,
    }
