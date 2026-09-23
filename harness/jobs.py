"""Cycle 69: the listicle site's job queue, worker, and audit log.

Long work (a regenerate, an A/B/C test build, a Shopify publish) never runs
inside a web request. The site puts a job in `tenants/<t>/jobs/jobs.sqlite`
and `harness worker --tenant <t>` (systemd user unit
`harness-worker@<t>.service`, crons/worker.service) runs the jobs, oldest
first, one at a time.

Job states: queued -> running -> done | failed (failed always has a reason).
Crash safety: only one worker runs per tenant (an flock on
jobs/worker.lock). When a worker starts, a job still in "running" can only
be one a previous worker did not finish (killed, crashed, host restart), so
it is marked failed with a reason. The worker stops cleanly on SIGTERM: it
finishes the job in hand and takes no new one.

The same database holds the audit log: who gave feedback, uploaded an ad,
published or replaced a page, and when.

Job types and handlers (each reuses the CLI's own code path):
  regenerate       revise.revise_page (= `harness revise`) for one page
  create_test      abtest_inbox.build_test_for_item (= `harness abtest create`,
                   then `harness abtest publish` when abtest.auto_publish)
  publish_page     approve + packet ship + `harness publish --update`
  replace_variant  the same, for a live A/B/C variant, then its stats reset
"""
import argparse
import contextlib
import datetime
import fcntl
import io
import json
import os
import sqlite3
import threading
import traceback
from pathlib import Path

from . import exits
from .errors import HarnessError

STATES = ("queued", "running", "done", "failed")
JOB_TYPES = ("regenerate", "create_test", "publish_page", "replace_variant")


class JobError(HarnessError):
    """The job queue cannot go on (e.g. a second worker for one tenant)."""


class JobFailed(Exception):
    """A handler's expected failure: the job is marked failed with this reason."""


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def db_path(tenant):
    return Path(tenant.jobs_dir) / "jobs.sqlite"


def connect(tenant):
    path = db_path(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs ("
        " id INTEGER PRIMARY KEY, type TEXT NOT NULL, state TEXT NOT NULL, target TEXT NOT NULL,"
        " payload TEXT NOT NULL, result TEXT, reason TEXT NOT NULL DEFAULT '', by TEXT NOT NULL,"
        " created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, pid INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        " id INTEGER PRIMARY KEY, at TEXT NOT NULL, by TEXT NOT NULL, action TEXT NOT NULL,"
        " target TEXT NOT NULL, detail TEXT NOT NULL)"
    )
    conn.commit()
    return conn


@contextlib.contextmanager
def _db(tenant):
    conn = connect(tenant)
    try:
        yield conn
    finally:
        conn.close()


def _job(row):
    if row is None:
        return None
    job = dict(row)
    job["payload"] = json.loads(job["payload"] or "{}")
    job["result"] = json.loads(job["result"]) if job["result"] else None
    return job


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------

def enqueue(tenant, job_type, payload, *, by, target):
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type {job_type!r}")
    with _db(tenant) as conn:
        cur = conn.execute(
            "INSERT INTO jobs (type, state, target, payload, by, created_at) VALUES (?, 'queued', ?, ?, ?, ?)",
            (job_type, target, json.dumps(payload), by, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_job(tenant, job_id):
    if not db_path(tenant).exists():
        return None
    with _db(tenant) as conn:
        return _job(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def list_jobs(tenant, limit=50):
    """Newest first."""
    if not db_path(tenant).exists():
        return []
    with _db(tenant) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_job(r) for r in rows]


def latest_job(tenant, *, job_type, target):
    if not db_path(tenant).exists():
        return None
    with _db(tenant) as conn:
        return _job(conn.execute(
            "SELECT * FROM jobs WHERE type = ? AND target = ? ORDER BY id DESC LIMIT 1", (job_type, target),
        ).fetchone())


def active_job(tenant, *, job_type, target):
    """The queued or running job of this type for this target, or None."""
    job = latest_job(tenant, job_type=job_type, target=target)
    return job if job and job["state"] in ("queued", "running") else None


def claim_next(tenant):
    """The oldest queued job, now marked running (atomically), or None."""
    with _db(tenant) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            conn.rollback()
            return None
        conn.execute("UPDATE jobs SET state = 'running', started_at = ?, pid = ? WHERE id = ?",
                     (_now(), os.getpid(), row["id"]))
        conn.commit()
        return _job(conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())


def mark_done(tenant, job_id, result):
    with _db(tenant) as conn:
        conn.execute("UPDATE jobs SET state = 'done', result = ?, reason = '', finished_at = ? WHERE id = ?",
                     (json.dumps(result or {}), _now(), job_id))
        conn.commit()


def mark_failed(tenant, job_id, reason):
    with _db(tenant) as conn:
        conn.execute("UPDATE jobs SET state = 'failed', reason = ?, finished_at = ? WHERE id = ?",
                     (str(reason)[:2000] or "failed", _now(), job_id))
        conn.commit()


def recover_stale(tenant):
    """Marks every "running" job failed. Call only while holding the worker
    lock: then no other worker can be running it. Returns the job ids."""
    if not db_path(tenant).exists():
        return []
    with _db(tenant) as conn:
        rows = conn.execute("SELECT id, pid FROM jobs WHERE state = 'running'").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE jobs SET state = 'failed', finished_at = ?, reason = ? WHERE id = ?",
                (_now(), f"the worker stopped while this job was running (pid {row['pid']}); it was "
                         f"marked failed at the next worker restart. Submit it again.", row["id"]),
            )
        conn.commit()
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------

def audit(tenant, *, by, action, target, detail=None):
    with _db(tenant) as conn:
        conn.execute("INSERT INTO audit (at, by, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                     (_now(), by or "", action, target, json.dumps(detail or {}, ensure_ascii=False)))
        conn.commit()


def audit_rows(tenant, limit=200):
    """Newest first."""
    if not db_path(tenant).exists():
        return []
    with _db(tenant) as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        row["detail"] = json.loads(row["detail"] or "{}")
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _flock(path, *, blocking, busy_message):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise JobError(busy_message) from None
        yield
    finally:
        fh.close()


def worker_lock(tenant):
    """Held for a worker's whole life: one worker per tenant."""
    return _flock(Path(tenant.jobs_dir) / "worker.lock", blocking=False,
                  busy_message=f"another harness worker is already running for tenant {tenant.name}")


def build_lock(tenant, *, blocking=True):
    """Held around every job and around `harness abtest from-inbox`, so two
    builds for one tenant never run at the same time."""
    return _flock(Path(tenant.jobs_dir) / "build.lock", blocking=blocking,
                  busy_message=f"a job is running for tenant {tenant.name}; try again later")


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

def _run_dir(tenant, run_id):
    run_dir = (Path(tenant.out_dir) / run_id).resolve()
    if run_dir.parent != Path(tenant.out_dir).resolve() or not (run_dir / "state.json").exists():
        raise JobFailed(f"no run {run_id!r} under {tenant.out_dir}")
    return run_dir


def _call_cli(fn, namespace):
    """(exit code, last stderr line) of one cli.cmd_* call."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = fn(namespace)
    lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
    return rc, (lines[-1] if lines else "")


def _publish_update(tenant, run_dir, page, *, by, live, seo_hidden, note):
    """approve + packet ship + `harness publish --update` for a page this run
    already published. Raises JobFailed."""
    from . import cli, runstate
    from . import tenant as tenant_mod

    tenant_mod.activate(tenant)
    runstate.approve(run_dir, tenant, by=by, pages=[page], note=note)
    runstate.set_packet_stamp(run_dir, stamp="ship", by=by, note=note)
    args = argparse.Namespace(run_dir=str(run_dir), page=page, live=live, dry_run=False, redirect_from=None,
                              handle=None, update=True, tenant=tenant.name, seo_hidden=seo_hidden)
    try:
        rc, err = _call_cli(cli.cmd_publish, args)
    except HarnessError as e:
        raise JobFailed(f"publish failed: {e}") from e
    if rc != exits.OK:
        raise JobFailed(f"publish failed (exit {rc}): {err or 'see the worker log'}")
    return runstate.published_page_record(run_dir, page) or {}


def handle_regenerate(tenant, payload):
    from . import revise as revise_mod
    from . import runstate
    from .budget import BudgetExceeded

    run_dir = _run_dir(tenant, payload["run_id"])
    page = payload["page"]
    runstate.set_revise_status(run_dir, page=page, status="running")
    try:
        result = revise_mod.revise_page(run_dir, page, by=payload.get("by"), tenant=tenant)
    except (revise_mod.ReviseError, BudgetExceeded) as e:
        runstate.set_revise_status(run_dir, page=page, status="failed", detail=str(e))
        raise JobFailed(str(e)) from e
    gate = "PASS" if not result.get("gate_problems") else f"FAIL ({len(result['gate_problems'])} issue(s))"
    # revise_page's `version` is the number the superseded page was saved
    # under (page.vN.json), so the new current page is version N + 1.
    return {"old_version": result["version"], "new_version": result["version"] + 1, "gate": gate,
            "model_called": bool(result.get("model_called"))}


def handle_create_test(tenant, payload):
    from . import abtest_inbox

    outcome = abtest_inbox.build_test_for_item(tenant, payload["ad_id"], by=payload["by"])
    if outcome["outcome"] == "budget":
        raise JobFailed(f"budget cap: test {outcome['test_id']} is queued and the next "
                        f"`harness abtest from-inbox` run resumes it (daily cap)")
    if outcome["outcome"] == "failed":
        raise JobFailed(outcome["reason"] or "the test build failed")
    return outcome


def handle_publish_page(tenant, payload):
    run_dir = _run_dir(tenant, payload["run_id"])
    record = _publish_update(tenant, run_dir, payload["page"], by=payload["by"], live=payload["live"],
                             seo_hidden=False, note="listicle site: publish new version")
    audit(tenant, by=payload["by"], action="publish_done", target=f"run:{run_dir.name}/{payload['page']}",
          detail={"url": record.get("url"), "live": payload["live"]})
    return {"url": record.get("url"), "live": payload["live"]}


def handle_replace_variant(tenant, payload):
    from . import abtest, runstate

    run_dir = _run_dir(tenant, payload["run_id"])
    page, key = payload["page"], payload["key"]
    rec = abtest.load_test(tenant, payload["test_id"])
    variant = next((v for v in rec["variants"] if v["key"] == key), None)
    if rec["status"] != "live" or variant is None or Path(variant.get("run_dir") or "").name != run_dir.name:
        raise JobFailed(f"test {rec['test_id']} is {rec['status']!r} or has no variant {key} for this page")
    if runstate.abtest_record(run_dir, page) != {"test_id": rec["test_id"], "key": key}:
        raise JobFailed(f"page {page} of {run_dir.name} is not variant {key} of test {rec['test_id']}")
    record = _publish_update(tenant, run_dir, page, by=payload["by"], live=True, seo_hidden=True,
                             note=f"abtest {rec['test_id']} variant {key}: replace live variant")
    replacement = abtest.reset_variant_stats(tenant, rec, key, by=payload["by"],
                                             note=f"replaced with {run_dir.name}/{page}")
    audit(tenant, by=payload["by"], action="replace_done", target=f"run:{run_dir.name}/{page}",
          detail={"test_id": rec["test_id"], "key": key, "archived_key": replacement["archived_key"],
                  "rows_archived": replacement["rows"], "url": record.get("url")})
    return {"test_id": rec["test_id"], "key": key, "url": record.get("url"),
            "archived_key": replacement["archived_key"]}


HANDLERS = {
    "regenerate": handle_regenerate,
    "create_test": handle_create_test,
    "publish_page": handle_publish_page,
    "replace_variant": handle_replace_variant,
}


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

def run_one(tenant, handlers=None, log=print):
    """Claims and runs the oldest queued job. Returns the finished job, or
    None when the queue is empty or `harness abtest from-inbox` holds the
    build lock (the worker then tries again after its interval). A handler's
    exception fails the job; it never stops the worker."""
    handlers = {**HANDLERS, **(handlers or {})}
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(build_lock(tenant, blocking=False))
        except JobError:
            return None
        job = claim_next(tenant)
        if job is None:
            return None
        log(f"job {job['id']} {job['type']} {job['target']} started")
        try:
            handler = handlers.get(job["type"])
            if handler is None:
                raise JobFailed(f"no handler for job type {job['type']!r}")
            result = handler(tenant, job["payload"])
        except JobFailed as e:
            mark_failed(tenant, job["id"], str(e))
        except Exception as e:  # noqa: BLE001 -- a bug in one job must not stop the queue
            log(traceback.format_exc())
            mark_failed(tenant, job["id"], f"{type(e).__name__}: {e}")
        else:
            mark_done(tenant, job["id"], result)
    job = get_job(tenant, job["id"])
    log(f"job {job['id']} {job['state']}" + (f": {job['reason']}" if job["reason"] else ""))
    return job


def work(tenant, *, once=False, interval=5.0, stop=None, handlers=None, log=print):
    """The worker loop. `stop` (a threading.Event, set by the SIGTERM
    handler) ends the loop after the job in hand; `once` runs the queue
    until it is empty and returns."""
    stop = stop or threading.Event()
    with worker_lock(tenant):
        recovered = recover_stale(tenant)
        if recovered:
            log(f"marked {len(recovered)} job(s) left running by a previous worker as failed: {recovered}")
        log(f"harness worker for {tenant.name} started (pid {os.getpid()})")
        while not stop.is_set():
            job = run_one(tenant, handlers=handlers, log=log)
            if job is None:
                if once:
                    break
                stop.wait(interval)
        log(f"harness worker for {tenant.name} stopped")
