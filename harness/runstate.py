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

def init_state(run_dir, *, pages, by="system", note="run started"):
    """Writes a fresh state.json: run-level state "generated", every page
    "generated". Called once, from pipeline.prepare_run. Refuses to
    overwrite an existing state.json (a run directory is created once)."""
    run_dir = Path(run_dir)
    path = state_path(run_dir)
    if path.exists():
        return json.loads(path.read_text())
    data = {
        "run_id": run_dir.name,
        "state": "generated",
        "pages": {p: "generated" for p in pages},
        "history": [{"state": "generated", "by": by, "at": _now(), "note": note}],
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
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


def mark_published(run_dir, *, page, by="operator", note=""):
    data = load_state(run_dir)
    if page not in data["pages"]:
        raise KeyError(f"unknown page {page!r} for this run; run has: {list(data['pages'])}")
    data["pages"][page] = "published"
    if all(v == "published" for v in data["pages"].values()):
        data["state"] = "published"
    data["history"].append({"state": "published", "by": by, "at": _now(), "note": note})
    save_state(run_dir, data)
    return data


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
