"""Cycle 102d: "Revise this look" -- the owner's feedback on an imported or
hand-converted look, the way he gives feedback on a generated page.

One revise goes through the import's own revise round (harness/
look_import.py): the look's current template is rendered with the demo page
and screenshotted at 390 and 1280 (the before shots); the model
(claude-opus-5-5, streamed) gets the rules and the contract, the template,
the observation, the source screenshots the look was modelled on, the before
shots and the owner's note as a NAMED MISS ("fix exactly this, keep the
rest"); its answer goes through the same validator and render, with one
repair round when the validator refuses it. A refused revision changes
nothing. A valid one replaces template.html (the previous one is kept as
template.prev.html), review.html and the render shots, and adds one line to
library.json `history`. About $0.40 a revise ($0.80 with the repair round).
"""
import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import budget as budget_mod
from . import look_design, look_vision
from . import look_import as li
from .anthropic_client import make_client

PROGRESS_STEPS = ("rendering", "revising", "validating", "storing")
MAX_NOTE_CHARS = 2000
MAX_CALLS = 2
EST_REVISE_USD = 0.40
PREV_TEMPLATE = "template.prev.html"
BEFORE_SHOTS = ("revise-before-390.png", "revise-before-1280.png")
AFTER_SHOTS = (look_vision.RENDER_SHOT, "render-1280.png")
# the shots the review site serves from a look's folder
SHOT_NAMES = frozenset(BEFORE_SHOTS + AFTER_SHOTS)
MAX_HISTORY = 50


def can_revise(tenant, cartridge, slug):
    """True for a tenant look (imported or hand-converted) with its files."""
    folder = li.look_folder(tenant, cartridge, slug)
    return (folder / "template.html").is_file() and (folder / "library.json").is_file()


def _shooter(browser, tenant, work, prefix):
    """A validate_template `shot` hook writing <prefix>-390.png and
    <prefix>-1280.png into `work`; None without a browser."""
    if not browser:
        return None

    def shot(index_html):
        for size, width in ((look_vision.PHONE, 390), (look_vision.DESKTOP, 1280)):
            look_vision.screenshot_full(browser, index_html, work / f"{prefix}-{width}.png", size, tenant=tenant)
    return shot


def _shot_files(work, prefix):
    return [work / f"{prefix}-{w}.png" for w in (390, 1280)]


def _message(*, cartridge, template, observation, library, folder, work, note, before_errors):
    """The revise round's user message: the source screenshots, the before
    shots, then the template, the observation and the note as a named miss."""
    blocks = []
    sheets = [{"file": name, "label": f"The source page the look was modelled on ({name})"}
              for name in ((library.get("source") or {}).get("images") or []) if (folder / name).is_file()]
    if sheets:
        blocks += look_vision.image_blocks(sheets[:look_vision.MAX_IMAGES], folder, "The source page, as screenshots:")
    shots = [(look_vision.open_image(p), f"The current look with the demo page, {p.stem.rsplit('-', 1)[1]} px wide")
             for p in _shot_files(work, "before") if p.is_file()]
    if shots:
        before = look_vision.prepare(shots, work / "before-sheets", max_images=4, prefix="before-")
        blocks += look_vision.image_blocks(before, work / "before-sheets", "The current look as it renders now:")
    text = [f"Cartridge: {cartridge}. You REVISE an existing look: its complete template.html is below.", ""]
    if observation:
        text += ["The observation of the page it was modelled on:", "",
                 json.dumps(observation, separators=(",", ":")), ""]
    text += ["The current template.html:", "", template, ""]
    if before_errors:
        text += ["The harness refuses the current template as it is; fix these too:"]
        text += [f"- {e}" for e in before_errors[:li.MAX_ERRORS_SHOWN]] + [""]
    text += ["The owner looked at this look and named ONE miss. Treat it like a missing pattern: fix exactly "
             "this, keep everything else as it is (section order, photo and CTA cadence, grounds, markers):",
             f"- The owner's note: {note}", "",
             li.allowed_links_text(cartridge), "",
             "Return the complete corrected template.html only: no prose, no code fence."]
    blocks.append({"type": "text", "text": "\n".join(text), "cache_control": {"type": "ephemeral"}})
    return blocks


def _write(path, text):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def revise_look(tenant, *, cartridge, slug, note, by, client=None, model=None, progress=None, log=None):
    """One revise of a tenant look. Returns a JSON-ready result; raises
    LookImportError (the look unchanged) when the note is empty, the look
    is missing, or the revision fails validation after its repair round."""
    progress = progress or (lambda _s: None)
    note = " ".join(str(note or "").split())[:MAX_NOTE_CHARS]
    if not note:
        raise li.LookImportError("write what to change in the look")
    if cartridge not in li.CARTRIDGES or not li.looks.LOOK_SLUG_RE.match(slug or "") \
            or not can_revise(tenant, cartridge, slug):
        raise li.LookImportError(f"no tenant {cartridge} look {slug!r} to revise")
    folder = li.look_folder(tenant, cartridge, slug)
    template = (folder / "template.html").read_text()
    library = json.loads((folder / "library.json").read_text())
    try:
        observation = json.loads((folder / "observation.json").read_text())
    except (OSError, ValueError):
        observation = None
    demo = li.load_demo(tenant, cartridge)
    contract_ = li.contract(cartridge, tenant, demo)
    schema = li.load_schema(cartridge, tenant)
    model = model or look_vision.MODEL
    browser = look_vision.find_browser()
    client = client or make_client()
    today = datetime.date.today().isoformat()
    run_id = f"look-revise-{slug}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        budget_mod.reserve_spend(tenant, run_id=run_id, today_iso=today, log=log, extra_usd=EST_REVISE_USD)
    except budget_mod.BudgetExceeded as e:
        raise li.LookImportError(f"revise not started: {e}") from e
    cost, errors, best, attempt = 0.0, [], None, 0
    try:
        with tempfile.TemporaryDirectory(prefix="look-revise-", dir=li.work_root(tenant)) as tmp:
            work = Path(tmp)
            progress("rendering")
            # a revision may keep as many dark bands as the look has (the owner decides now)
            before_errors, _html = li.validate_template(template, cartridge, slug, tenant, demo, contract_=contract_,
                                                        schema=schema, max_dark=None)
            # cycle 102e: the shots are of the self-contained review document
            li.review_document(template, cartridge, slug, tenant, demo, shot=_shooter(browser, tenant, work, "before"))
            system = li.build_system_prompt(cartridge, contract_, schema, exemplar=None, reference=False)
            messages = [{"role": "user", "content": _message(
                cartridge=cartridge, template=template, observation=observation, library=library, folder=folder,
                work=work, note=note, before_errors=before_errors)}]
            from .budget import Budget, BudgetExceeded

            budget = Budget(wall_s=900, tokens=300_000, calls=MAX_CALLS)
            for attempt in range(1, MAX_CALLS + 1):
                progress("revising")
                try:
                    raw, call_cost = li._call(client, model, system, messages, budget, log)
                except BudgetExceeded as e:
                    raise li.LookImportError(f"revise stopped by its budget: {e}", errors) from e
                cost += call_cost
                text = li.extract_template(raw)
                progress("validating")
                errors, review_html = li.validate_template(text, cartridge, slug, tenant, demo, contract_=contract_,
                                                           schema=schema, max_dark=None)
                if not errors:
                    document = li.review_document(text, cartridge, slug, tenant, demo,
                                                  shot=_shooter(browser, tenant, work, f"after{attempt}"))
                    best = {"template": text, "review_html": review_html, "document": document or review_html,
                            "attempt": attempt}
                    break
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "The harness refused this template:\n"
                              + "\n".join(f"- {e}" for e in errors[:li.MAX_ERRORS_SHOWN])
                              + "\n\nReturn the corrected complete template.html only."}]
            if best is None:
                raise li.LookImportError(f"revise refused: the revised {cartridge} look failed validation after "
                                         f"{attempt - 1} repair round(s); the look is unchanged", errors)
            progress("storing")
            fid = look_design.fidelity(observation, best["review_html"], tenant) if observation else None
            shutil.copyfile(folder / "template.html", folder / PREV_TEMPLATE)
            _write(folder / "template.html", best["template"])
            _write(folder / "review.html", best["document"])
            spec = li.look_spec_for(best["template"])
            if spec:
                _write(folder / "look.json", json.dumps(spec, indent=2) + "\n")
            for name in BEFORE_SHOTS + AFTER_SHOTS:
                (folder / name).unlink(missing_ok=True)
            for src, name in zip(_shot_files(work, "before"), BEFORE_SHOTS, strict=False):
                if src.is_file():
                    shutil.copyfile(src, folder / name)
            for src, name in zip(_shot_files(work, f"after{best['attempt']}"), AFTER_SHOTS, strict=False):
                if src.is_file():
                    shutil.copyfile(src, folder / name)
            when = datetime.datetime.now().isoformat(timespec="seconds")
            line = (f"{when[:16].replace('T', ' ')} {by}: {note[:160]}"
                    + (" ..." if len(note) > 160 else "") + f" (${cost:.2f}"
                    + (f", fidelity {fid['score']:.2f}" if fid else "") + ")")
            library["history"] = (list(library.get("history") or []) + [line])[-MAX_HISTORY:]
            library["revised_at"] = when
            if fid:
                library["fidelity"] = fid["score"]
                library["missing_patterns"] = fid["missing"]
            if (folder / AFTER_SHOTS[0]).is_file():
                library["render_shot"] = AFTER_SHOTS[0]
            _write(folder / "library.json", json.dumps(library, indent=2) + "\n")
    except li.LookImportError as e:
        e.cost_usd = round(cost, 4)
        raise
    finally:
        budget_mod.record_spend(tenant, run_id=run_id, cost=cost, today_iso=today, log=log)
    return {"slug": slug, "cartridge": cartridge, "title": library.get("title") or slug, "note": note,
            "cost_usd": round(cost, 4), "attempts": best["attempt"], "fidelity": fid["score"] if fid else None,
            "history_line": line, "pending": library.get("library") == "pending",
            "before": [n for n in BEFORE_SHOTS if (folder / n).is_file()],
            "after": [n for n in AFTER_SHOTS if (folder / n).is_file()]}
