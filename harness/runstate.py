"""Cycle 20: the run state machine.

`state.json` (run-level state, per-page state, and a full history) and
`packet.json` (the ship-stamp gate `harness publish` checks) live in every run
directory next to `REVIEW.md`. Every writer of either file goes through this
module -- `harness/pipeline.py`'s `prepare_run`/`review_notify` stages and
`harness/cli.py`'s `approve`/`reject`/`packet`/`publish` commands never touch
the JSON directly -- so `state.json`'s `history` list is always complete and
in the order things actually happened.

State values: generated -> needs_review -> approved -> published, with
rejected reachable from needs_review. A run that STOPped at the claims gate
or a budget cap never leaves `generated` -- there is no page to review yet.
"""
import datetime
import json
from pathlib import Path

STATES = ("generated", "needs_review", "approved", "published", "rejected")

# Cycle 26: a per-page state a run-level state never takes -- set by
# request_changes below, cleared back to "needs_review" once harness revise
# writes a new version (mark_revised). Kept separate from STATES because
# nothing here ever sets the run-level state to it.
PAGE_CHANGES_REQUESTED = "changes_requested"

DEFAULT_PACKET_STAMP = "BOT DRAFT · NOT SENT"
VALID_STAMPS = ("ship", "redo", "kill")


class UnknownReviewer(Exception):
    """`by` does not match any tenants/<t>/tenant.yaml `reviewers` entry."""


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def state_path(run_dir):
    return Path(run_dir) / "state.json"


def packet_path(run_dir):
    return Path(run_dir) / "packet.json"


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------

def init_state(run_dir, *, pages, by="system", note="run started", dry_run=False):
    """Writes a fresh state.json: run-level state "generated", every page
    "generated". Called once, from pipeline.prepare_run. Refuses to
    overwrite an existing state.json (a run directory is created once).

    Cycle 26b (bug 2): `dry_run` is True when pipeline.prepare_run sees the
    fake Anthropic client tests inject (tests/conftest.py's FakeClient) --
    never true for a real `harness run`. The review site's run list reads
    this back to hide test-suite runs by default."""
    run_dir = Path(run_dir)
    path = state_path(run_dir)
    if path.exists():
        return json.loads(path.read_text())
    data = {
        "run_id": run_dir.name,
        "state": "generated",
        "pages": {p: "generated" for p in pages},
        "history": [{"state": "generated", "by": by, "at": _now(), "note": note}],
        "dry_run": bool(dry_run),
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def record_listicle_choice(run_dir, *, style=None, look=None):
    """Cycle 51: the style and the look this run's listicle page was built
    with, recorded together under state.json's "listicle" key. The style
    fixes the copy and is baked into page.json by the writer; the look only
    picks a template, so `harness rerender --look` can change it afterwards
    -- which is exactly why the resolved value has to be written down rather
    than re-derived later from a flag nobody kept."""
    data = load_state(run_dir)
    entry = dict(data.get("listicle") or {})
    if style:
        entry["style"] = style
    if look:
        entry["look"] = look
    data["listicle"] = entry
    save_state(run_dir, data)
    return data


def load_state(run_dir):
    path = state_path(run_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no state.json under {run_dir} -- not a run directory this harness produced"
        )
    return json.loads(path.read_text())


def save_state(run_dir, data):
    state_path(run_dir).write_text(json.dumps(data, indent=2) + "\n")


def mark_needs_review(run_dir, *, by="system", note=""):
    """Called once per run, right after render (pipeline.review_notify) --
    the run passed every gate, so every page still "generated" becomes
    "needs_review", and so does the run overall."""
    data = load_state(run_dir)
    data["state"] = "needs_review"
    for page in data["pages"]:
        if data["pages"][page] == "generated":
            data["pages"][page] = "needs_review"
    data["history"].append({"state": "needs_review", "by": by, "at": _now(), "note": note})
    save_state(run_dir, data)
    return data


def find_reviewer(tenant, email):
    """A reviewers entry {name, email, role} whose email matches
    (case-insensitive), or None."""
    email = (email or "").strip().lower()
    for reviewer in tenant.get("reviewers") or []:
        if (reviewer.get("email") or "").strip().lower() == email:
            return reviewer
    return None


def approve(run_dir, tenant, *, by, pages=None, note=""):
    """Approves `pages` (default: every page in this run) for reviewer `by`,
    who must match a tenant.yaml `reviewers` entry -- raises UnknownReviewer
    otherwise. The run-level state becomes "approved" once every page is;
    until then it stays "needs_review" (a partial approval). Appends one
    line to tenants/<t>/evals/approvals.jsonl. Returns the updated state
    dict."""
    reviewer = find_reviewer(tenant, by)
    if reviewer is None:
        raise UnknownReviewer(
            f"{by!r} is not a listed reviewer for tenant {tenant.name!r}; "
            f"see {tenant.name}/tenant.yaml's reviewers list."
        )
    data = load_state(run_dir)
    target_pages = pages or list(data["pages"])
    unknown = [p for p in target_pages if p not in data["pages"]]
    if unknown:
        raise KeyError(f"unknown page(s) for this run: {unknown}; run has: {list(data['pages'])}")

    for page in target_pages:
        data["pages"][page] = "approved"
    if all(v == "approved" for v in data["pages"].values()):
        data["state"] = "approved"
    note_full = f"pages={','.join(target_pages)}" + (f"; {note}" if note else "")
    data["history"].append({"state": "approved", "by": by, "at": _now(), "note": note_full})
    save_state(run_dir, data)

    approvals_path = tenant.evals_path.parent / "approvals.jsonl"
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    with open(approvals_path, "a") as f:
        f.write(json.dumps({
            "tenant": tenant.name,
            "run_dir": str(run_dir),
            "pages": target_pages,
            "by": by,
            "role": reviewer.get("role", ""),
            "note": note,
            "approved_at": _now(),
        }) + "\n")

    return data


def reject(run_dir, tenant, *, by, note=""):
    """Rejects the whole run (every page). `by` must be a listed reviewer,
    same rule as approve."""
    reviewer = find_reviewer(tenant, by)
    if reviewer is None:
        raise UnknownReviewer(
            f"{by!r} is not a listed reviewer for tenant {tenant.name!r}; "
            f"see {tenant.name}/tenant.yaml's reviewers list."
        )
    data = load_state(run_dir)
    for page in data["pages"]:
        data["pages"][page] = "rejected"
    data["state"] = "rejected"
    data["history"].append({"state": "rejected", "by": by, "at": _now(), "note": note})
    save_state(run_dir, data)
    return data


def request_changes(run_dir, tenant, *, page, by, scores=None, notes="", cuts=None, note=""):
    """Cycle 26: the review site's "Request changes" action. `by` must be a
    listed reviewer, same rule as approve/reject. Appends one feedback entry
    {page, by, at, scores, notes, cuts} to state.json's "feedback" list (kept
    forever, oldest first -- harness revise reads the LAST entry for `page`)
    and sets that page's state to "changes_requested". The run-level state is
    left alone (it can still be "needs_review" with other pages untouched)."""
    reviewer = find_reviewer(tenant, by)
    if reviewer is None:
        raise UnknownReviewer(
            f"{by!r} is not a listed reviewer for tenant {tenant.name!r}; "
            f"see {tenant.name}/tenant.yaml's reviewers list."
        )
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    entry = {
        "page": page, "by": by, "at": _now(),
        "scores": scores or {}, "notes": notes or "", "cuts": list(cuts or []),
    }
    data.setdefault("feedback", []).append(entry)
    data["pages"][page] = PAGE_CHANGES_REQUESTED
    data["history"].append({"state": PAGE_CHANGES_REQUESTED, "by": by, "at": _now(), "note": f"page={page}" + (f"; {note}" if note else "")})
    save_state(run_dir, data)
    return data


def latest_feedback(run_dir, page):
    """The last feedback entry recorded for `page` (request_changes above),
    or None if there is none yet."""
    data = load_state(run_dir)
    for entry in reversed(data.get("feedback", [])):
        if entry.get("page") == page:
            return entry
    return None


def mark_rerendered(run_dir, *, page, by="system", note=""):
    """Cycle 45: record that `page` was re-rendered from its own existing
    page.json through the current template (`harness rerender`), with no
    model call.

    Deliberately touches NEITHER data["state"] NOR data["pages"][page]: a
    re-render changes the HTML a template produces, never whether a
    reviewer approved the copy. An approved page stays approved and a
    published page stays published, so an operator can push a layout fix
    to ten live pages without sending them all back through review. The
    history entry repeats the page's current state for that reason -- the
    log still reads in order -- and carries `rerendered_at`, which is what
    tells a re-render apart from the state change it is not."""
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    at = _now()
    data["history"].append({
        "state": data["pages"][page],
        "by": by,
        "at": at,
        "rerendered_at": at,
        "note": f"rerendered page={page}" + (f"; {note}" if note else ""),
    })
    save_state(run_dir, data)
    return data


def mark_fixcopy(run_dir, *, page, by="system", note=""):
    """Cycle 53: record that `page`'s page.json had a deterministic copy fix
    applied (`harness fixcopy`) with no model call and no gate/repair loop --
    same non-approval-changing shape as mark_rerendered above: an approved or
    published page stays approved/published, and the history entry repeats
    the page's current state (carrying `fixcopy_at`, which is what tells a
    fixcopy apart from the state change it is not) rather than moving it."""
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    at = _now()
    data["history"].append({
        "state": data["pages"][page],
        "by": by,
        "at": at,
        "fixcopy_at": at,
        "note": f"fixcopy page={page}" + (f"; {note}" if note else ""),
    })
    save_state(run_dir, data)
    return data


def run_started_date(run_dir):
    """The ISO date (YYYY-MM-DD) this run was first generated, from its own
    earliest history entry -- what `harness rerender` re-uses as the
    published/updated dates, so re-rendering never silently re-dates a page
    that has been live for weeks. None when the history carries no usable
    timestamp."""
    data = load_state(run_dir)
    for entry in data.get("history", []):
        at = entry.get("at")
        if isinstance(at, str) and len(at) >= 10:
            return at[:10]
    return None


def mark_revised(run_dir, *, page, version, by="system", note=""):
    """Called by harness revise once a new version has been written: the
    page goes back to "needs_review" (a human needs to look at the new
    version) and history records which version this was."""
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    data["pages"][page] = "needs_review"
    data["history"].append({
        "state": "needs_review", "by": by, "at": _now(),
        "note": f"page={page}; revised to v{version}" + (f"; {note}" if note else ""),
    })
    save_state(run_dir, data)
    return data


def set_revise_status(run_dir, *, page, status, detail=""):
    """Cycle 26: the review site launches `harness revise` as a background
    subprocess and polls this on page reload -- status is one of "running",
    "done", "failed". Stored under state.json's "revise_status" key, keyed by
    page, so it survives the subprocess exiting and the process restarting."""
    data = load_state(run_dir)
    data.setdefault("revise_status", {})[page] = {"status": status, "detail": detail, "at": _now()}
    save_state(run_dir, data)
    return data


def get_revise_status(run_dir, page):
    data = load_state(run_dir)
    return (data.get("revise_status") or {}).get(page)


def mark_published(run_dir, *, page, by="operator", note="", page_id=None, handle=None, url=None):
    """Cycle 40: `page_id`/`handle`/`url`, when given (a real Shopify
    publish, not the export adapter, which has neither), are also stored as
    structured fields under `data["published_pages"][page]` -- not only in
    `note`'s free text -- so `harness publish --update` can look up the
    page id for a page this run already published without parsing history
    notes. `note` is unchanged and still carries the human-readable summary
    (see cli.cmd_publish)."""
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    data["pages"][page] = "published"
    if all(v == "published" for v in data["pages"].values()):
        data["state"] = "published"
    data["history"].append({"state": "published", "by": by, "at": _now(), "note": note})
    if page_id is not None:
        data.setdefault("published_pages", {})[page] = {
            "page_id": page_id, "handle": handle, "url": url, "at": _now(),
        }
    save_state(run_dir, data)
    return data


def published_page_record(run_dir, page):
    """The structured `{"page_id", "handle", "url", "at"}` record
    `mark_published` stored for `page` on its most recent real publish, or
    None if this run has never published `page` with a page id (an export
    publish, or a run from before cycle 40)."""
    data = load_state(run_dir)
    return (data.get("published_pages") or {}).get(page)


# ---------------------------------------------------------------------------
# packet.json -- the ship-stamp gate `harness publish` checks
# ---------------------------------------------------------------------------

def init_packet(run_dir, *, by="system"):
    """Writes packet.json with the default stamp if it doesn't exist yet.
    Never overwrites an existing packet -- a stamp is a human decision."""
    path = packet_path(run_dir)
    if path.exists():
        return json.loads(path.read_text())
    data = {"stamp": DEFAULT_PACKET_STAMP, "by": by, "at": _now(), "note": ""}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def load_packet(run_dir):
    path = packet_path(run_dir)
    if not path.exists():
        return {"stamp": DEFAULT_PACKET_STAMP, "by": None, "at": None, "note": ""}
    return json.loads(path.read_text())


def set_packet_stamp(run_dir, *, stamp, by, note=""):
    if stamp not in VALID_STAMPS:
        raise ValueError(f"stamp must be one of {VALID_STAMPS}, got {stamp!r}")
    data = {"stamp": stamp, "by": by, "at": _now(), "note": note}
    packet_path(run_dir).write_text(json.dumps(data, indent=2) + "\n")
    return data


# ---------------------------------------------------------------------------
# harness digest needs-review
# ---------------------------------------------------------------------------

def needs_review_runs(tenant, *, older_than_days=3):
    """[(run_dir, state_data, entered_needs_review_at)] for every run under
    tenant.out_dir whose state.json is currently "needs_review" and whose
    history shows it entered that state more than `older_than_days` days
    ago. Used by `harness digest needs-review`."""
    out = []
    if not tenant.out_dir.is_dir():
        return out
    cutoff = datetime.datetime.now() - datetime.timedelta(days=older_than_days)
    for run_dir in sorted(tenant.out_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        path = state_path(run_dir)
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if data.get("state") != "needs_review":
            continue
        entered_at = None
        for entry in data.get("history", []):
            if entry.get("state") == "needs_review":
                entered_at = entry.get("at")
        if entered_at is None:
            continue
        try:
            entered_dt = datetime.datetime.fromisoformat(entered_at)
        except ValueError:
            continue
        if entered_dt <= cutoff:
            out.append((run_dir, data, entered_dt))
    return out


def tenant_name_from_run_dir(run_dir):
    """tenants/<name>/out/<run-id> -> <name>, when the path has that shape;
    None otherwise, so the caller falls back to normal --tenant resolution.
    (Review R2: moved out of cli.py; revise.py needs it too.)"""
    parts = Path(run_dir).parts
    if "tenants" in parts:
        i = parts.index("tenants")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None
