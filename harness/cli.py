"""`harness` console entry point: run / ingest / claims / review / score /
shopify-body / tenant / workflow / design-skills.

The CLI resolves which tenant a command is for (--tenant > HARNESS_TENANT >
tenants/default.txt), activates it, and hands the pipeline a Tenant object. No
company-specific value is read from anywhere else.
"""
import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

from . import budget as budget_mod
from . import brand_import
from . import notify
from . import pipeline
from . import runstate
from . import page_body as shopify_body_mod
from . import tenant as tenant_mod
from . import workflows
from .anthropic_client import make_client
from .budget import Budget, BudgetExceeded
from . import doctor as doctor_mod
from . import evals as evals_mod
from . import exits
from . import config as harness_config
from .errors import HarnessError
from .ingest import run_ingest
from .log import RunLog
from .publishers.export import ExportPublisher
from .publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher, rewrite_asset_srcs
from .review import build_reviews
from .runstate import UnknownReviewer
from .page_body import write_shopify_body

# Exit codes live in harness/exits.py. Kept as a name here because it was part
# of this module's surface before that module existed.
EXIT_TENANT_NOT_CONFIGURED = exits.TENANT_NOT_CONFIGURED

# Run-shaping helpers live in harness/pipeline.py, which owns the stage list.
# Re-exported here because they are part of the CLI's own surface.
discover_cartridges = pipeline.discover_cartridges
slugify = pipeline.slugify
make_run_id = pipeline.make_run_id
make_run_dir = pipeline.make_run_dir


def cmd_run(args):
    """ingest -> ground -> gate -> write -> render -> REVIEW.md, for one tenant.

    The stage list lives in harness/pipeline.py; `harness workflow run
    ad-to-pages` runs the same functions in the order workflows/ad-to-pages.yaml
    gives, so the two commands cannot drift apart."""
    tenant = tenant_mod.load_tenant(args.tenant, require=True)
    tenant_mod.activate(tenant)
    tenant.load_env()
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    return pipeline.execute(state, pipeline.DEFAULT_STAGES)


def cmd_workflow_run(args):
    """Run one workflow's pipeline stages, in the order its YAML gives."""
    tenant = tenant_mod.load_tenant(args.tenant, require=True)
    tenant_mod.activate(tenant)
    tenant.load_env()
    workflow = workflows.load_workflow(args.name)
    names = workflows.stage_names(workflow)
    if not names:
        print(f"workflow {args.name!r} runs no pipeline stages", file=sys.stderr)
        return 1
    state = pipeline.RunState(tenant=tenant, args=args, client=make_client())
    return pipeline.execute(state, names)


def cmd_workflow_list(args):
    for name in workflows.list_workflows():
        workflow = workflows.load_workflow(name)
        print(f"{name:16s} {(workflow.get('description') or '').strip().splitlines()[0] if workflow.get('description') else ''}")
    return 0


# ---------------------------------------------------------------------------
# harness ingest
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    tenant_mod.activate(tenant)
    tenant.load_env()
    client = make_client()
    budget = Budget()
    run_id, out_dir = pipeline.make_run_dir(tenant.out_dir, pipeline.slugify(args.input))
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}.log")

    # Cycle 34: close on every path (BudgetExceeded, ingest errors, success).
    # abort_budget_run also closes; RunLog.close is idempotent.
    try:
        try:
            ad_brief = run_ingest(
                input_arg=args.input,
                workdir=out_dir,
                client=client,
                model=tenant.model_for("ingest"),
                budget=budget,
                log=log,
                ffmpeg_bin=args.ffmpeg_bin,
                whisper_bin=args.whisper_bin,
                whisper_model=args.whisper_model,
            )
        except BudgetExceeded as e:
            # R20: the shared budget-STOP tail (pipeline.abort_budget_run) --
            # same event/summary/run_result/close shape as a pipeline run, plus
            # the phase-3 spend-ledger record.
            pipeline.abort_budget_run(log, budget, e, tenant=tenant, run_id=run_id, stage="ingest")
            print(f"budget exceeded: {e}", file=sys.stderr)
            return 3

        (out_dir / "ad_brief.json").write_text(json.dumps(ad_brief, indent=2))
        cost = log.cost_estimate()
        budget_mod.record_spend(tenant, run_id=run_id, cost=cost,
                                today_iso=datetime.date.today().isoformat(), log=log)
        print(json.dumps(ad_brief, indent=2))
        print(f"\nWrote {out_dir / 'ad_brief.json'}")
        return 0
    finally:
        log.close()


# ---------------------------------------------------------------------------
# harness tenant init / list
# ---------------------------------------------------------------------------

def cmd_tenant_init(args):
    root = tenant_mod.init_tenant(args.name)
    print(f"Created {root} from {tenant_mod.TEMPLATE_DIR.name}.")
    print(f"Next: work through {root / 'README.md'}. Until its claims store is")
    print("filled in, a run for this tenant exits 4 with a 'tenant not configured' message.")
    return 0


def cmd_tenant_list(args):
    active = tenant_mod.resolve_tenant_name(None) if tenant_mod.DEFAULT_FILE.exists() else None
    for name in tenant_mod.list_tenants():
        tenant = tenant_mod.load_tenant(name)
        missing = tenant.missing_pieces()
        status = "ready" if not missing else f"not configured ({', '.join(missing)})"
        marker = "*" if name == active else " "
        print(f"{marker} {name:20s} {status}")
    return 0


# ---------------------------------------------------------------------------
# harness brand import
# ---------------------------------------------------------------------------

def cmd_brand_import(args):
    """`harness brand import --tenant <t> (--drive-folder <url-or-id> | --local
    <dir>) [--dry-run] [--force]`. See harness/brand_import.py's module
    docstring for the full pipeline; this just wires the CLI plumbing every
    other command already uses (tenant load, RunLog, Budget, exit codes)."""
    tenant = tenant_mod.load_tenant(args.tenant)
    tenant.load_env()
    run_id = f"brand-import-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}.log")
    budget = Budget()
    # Cycle 34: shared budget-STOP tail + finally close (same shape as ingest).
    try:
        try:
            # A client is only actually called if a brand guide file needs its
            # vision-model pass (import_brand_kit notes the gap in
            # BRAND-IMPORT.md rather than failing when there isn't one) -- built
            # unconditionally here, same as every other model-using command,
            # so a real .env's key is validated up front rather than mid-run.
            client = make_client()
            result = brand_import.import_brand_kit(
                tenant,
                drive_folder=args.drive_folder,
                local_dir=args.local,
                dry_run=args.dry_run,
                force=args.force,
                client=client,
                model=tenant.model_for("ingest"),
                budget=budget,
                log=log,
            )
        except BudgetExceeded as e:
            pipeline.abort_budget_run(
                log, budget, e, tenant=tenant, run_id=run_id, stage="brand_import",
            )
            print(f"budget exceeded: {e}", file=sys.stderr)
            return 3
        log.cost_estimate()
        print(result.as_markdown(tenant_name=tenant.display_name))
        if args.dry_run:
            print("\n(--dry-run: nothing under tenants/ was written)")
        else:
            print(f"\nWrote {tenant.brand_dir / 'BRAND-IMPORT.md'}")
        return 0
    finally:
        log.close()


# ---------------------------------------------------------------------------
# harness shopify-body
# ---------------------------------------------------------------------------

def cmd_review(args):
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"no such run dir: {run_dir}", file=sys.stderr)
        return 1
    written = build_reviews(run_dir)
    if not written:
        print(f"no cartridge output (index.html) found under {run_dir}", file=sys.stderr)
        return 1
    for p in written:
        print(f"Wrote {p}")
    return 0


def cmd_shopify_body(args):
    """`harness shopify-body <run-dir>/<cartridge>`: writes shopify-body.html
    and shopify-body.assets.json next to that cartridge's index.html. No
    storefront API call anywhere in this path -- see `harness publish`
    (below) for the actual publish step, which refuses to run without an
    approved state and a packet stamped "ship"."""
    tenant_mod.activate(tenant_mod.load_tenant(args.tenant))
    cartridge_dir = Path(args.cartridge_dir)
    index_path = cartridge_dir / "index.html"
    if not index_path.exists():
        print(f"no index.html found under {cartridge_dir}", file=sys.stderr)
        return 1

    shopify_body_path, assets_manifest_path = write_shopify_body(cartridge_dir)
    print(f"Wrote {shopify_body_path}")
    print(f"Wrote {assets_manifest_path}")
    return 0


# ---------------------------------------------------------------------------
# harness approve / reject / packet / publish / digest (cycle 20)
# ---------------------------------------------------------------------------

def _resolve_tenant_for_run(args):
    name = args.tenant or runstate.tenant_name_from_run_dir(args.run_dir)
    return tenant_mod.load_tenant(name)


def cmd_approve(args):
    """`harness approve <run-dir> --by <email> [--pages a,b] [--note ...]`:
    approves the named pages (default: every page in the run) for a
    reviewer listed in the tenant's tenant.yaml `reviewers`. An unknown
    email is refused with a one-line message, exit 1, no traceback."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    pages = [p.strip() for p in args.pages.split(",") if p.strip()] if args.pages else None
    try:
        data = runstate.approve(run_dir, tenant, by=args.by, pages=pages, note=args.note or "")
    except (UnknownReviewer, FileNotFoundError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    approved_pages = pages or list(data["pages"])
    print(f"Approved {','.join(approved_pages)} for {run_dir} (state={data['state']})")
    notify.notify_approved(tenant, run_id=run_dir.name, by=args.by, pages=approved_pages, run_dir=str(run_dir))
    return 0


def cmd_reject(args):
    """`harness reject <run-dir> --by <email> --note ...`: rejects the whole
    run. Same reviewer check as approve."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    try:
        data = runstate.reject(run_dir, tenant, by=args.by, note=args.note)
    except (UnknownReviewer, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Rejected {run_dir} (state={data['state']})")
    return 0


def cmd_revise(args):
    """`harness revise <run-dir> --page <cartridge> [--by <email>]`: applies
    the latest reviewer feedback for that page (harness/revise.py) -- cut:
    lines deterministically, then a bounded writer+repair pass for any
    free-text notes -- and writes a new version. See harness/revise.py for
    the full behavior."""
    from . import revise as revise_mod

    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    try:
        result = revise_mod.revise_page(run_dir, args.page, by=args.by, tenant=tenant)
    except revise_mod.ReviseError as e:
        print(str(e), file=sys.stderr)
        return 1
    except BudgetExceeded as e:
        # Cycle 34: same cap-refusal contract as cmd_run/cmd_ingest/cmd_brand_import
        # -- reserve_spend already raised before any ledger write or model-client
        # construction (see harness/revise.py), so there is nothing left to unwind
        # here beyond printing the same clean message and exiting 3, no traceback.
        print(f"budget exceeded: {e}", file=sys.stderr)
        return 3
    status = "PASS" if not result["gate_problems"] else "FAIL"
    print(
        f"Revised {args.page} for {run_dir} -> v{result['version']} "
        f"({len(result['applied_cuts'])} cut(s), writer called={result['model_called']}, gate={status})"
    )
    return 0


def cmd_serve(args):
    """`harness serve --tenant <t> [--host 127.0.0.1] [--port 4870]`: the
    reviewer web app (harness/serve.py). Refuses to start with a clear
    message if the tenant env has no REVIEW_PASSWORD (see serve.py's
    build_app for the auth rule)."""
    from . import serve as serve_mod

    tenant = tenant_mod.load_tenant(args.tenant, require=True)
    tenant_mod.activate(tenant)
    tenant.load_env()
    app = serve_mod.build_app(tenant)
    app.run(host=args.host, port=args.port)
    return 0


def cmd_packet(args):
    """`harness packet <run-dir> --stamp ship|redo|kill --by <email> [--note
    ...]`: sets the packet stamp `harness publish` checks. A new run starts
    at "BOT DRAFT · NOT SENT" (harness/pipeline.py's prepare_run)."""
    run_dir = Path(args.run_dir)
    if not runstate.state_path(run_dir).exists():
        print(f"no state.json under {run_dir} -- not a run directory this harness produced", file=sys.stderr)
        return 1
    data = runstate.set_packet_stamp(run_dir, stamp=args.stamp, by=args.by, note=args.note or "")
    print(f"Packet for {run_dir} stamped {data['stamp']!r} by {data['by']}")
    return 0


def _make_publisher(tenant, *, export_dir):
    """tenant.yaml's `publisher` key picks the adapter; `export` (the
    default) needs no credentials at all."""
    kind = tenant.get("publisher") or "export"
    if kind == "shopify":
        return ShopifyPublisher()  # reads SHOPIFY_STORE/SHOPIFY_TOKEN from the tenant's .env
    return ExportPublisher(out_dir=export_dir)


def _read_asset_manifest_bytes(cartridge_dir, assets_manifest):
    out = []
    for item in assets_manifest:
        asset_path = cartridge_dir / item["local_path"]
        out.append({**item, "bytes": asset_path.read_bytes() if asset_path.exists() else b""})
    return out


_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


def cmd_publish(args):
    """`harness publish <run-dir> --page <cartridge> [--live] [--dry-run]
    [--handle <slug>] [--redirect-from </path>]`: refuses unless
    state.json's `pages[<cartridge>]` is "approved" AND packet.json's stamp
    is "ship". Default publish is unpublished (a draft page) unless
    `--live`; `--live` also verifies the storefront cache with 8 pulls, 2s
    apart (the cache-epoch trap in the tenant's own storefront notes).
    `--dry-run` only validates credentials and the page body (a GET on the
    shop endpoint for the Shopify adapter) -- no approval or stamp
    required, and nothing is created. `--handle` sets the Shopify page
    handle explicitly. `--redirect-from` (requires `--live`) creates a
    Shopify URL redirect from a root path to the published page."""
    tenant = _resolve_tenant_for_run(args)
    run_dir = Path(args.run_dir)
    cartridge_dir = run_dir / args.page
    if not (cartridge_dir / "index.html").exists():
        print(f"no index.html under {cartridge_dir}", file=sys.stderr)
        return 1

    # Cycle 38: cmd_publish is the one _resolve_tenant_for_run caller that
    # touches credentials (SHOPIFY_STORE/SHOPIFY_TOKEN via ShopifyPublisher).
    # approve/reject/revise don't need the tenant's .env, so they're left
    # alone -- see cmd_run/cmd_serve for the same load_env() convention.
    tenant.load_env()

    handle = getattr(args, "handle", None)
    if handle and not _HANDLE_RE.match(handle):
        print(
            f"--handle {handle!r} is invalid: must match {_HANDLE_RE.pattern}",
            file=sys.stderr,
        )
        return 1

    redirect_from = getattr(args, "redirect_from", None)
    if redirect_from and not args.live:
        print("--redirect-from requires --live", file=sys.stderr)
        return 1
    if redirect_from and not redirect_from.startswith("/"):
        print(f"--redirect-from {redirect_from!r} must start with '/'", file=sys.stderr)
        return 1

    shopify_body_html, assets_manifest = shopify_body_mod.build_shopify_body(cartridge_dir)
    export_dir = cartridge_dir / "export"

    if args.dry_run:
        publisher = _make_publisher(tenant, export_dir=export_dir)
        report = publisher.dry_run({"body_html": shopify_body_html, "assets": assets_manifest})
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    try:
        state_data = runstate.load_state(run_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    current_page_state = state_data["pages"].get(args.page)
    if current_page_state != "approved":
        print(
            f"refusing to publish {args.page!r}: state is {current_page_state!r}, not 'approved'. "
            f"Run `harness approve {run_dir} --by <email> --pages {args.page}` first.",
            file=sys.stderr,
        )
        return 1

    packet = runstate.load_packet(run_dir)
    if packet.get("stamp") != "ship":
        print(
            f"refusing to publish: packet stamp is {packet.get('stamp')!r}, not 'ship'. "
            f"Run `harness packet {run_dir} --stamp ship --by <email>` first.",
            file=sys.stderr,
        )
        return 1

    publisher = _make_publisher(tenant, export_dir=export_dir)
    manifest_with_bytes = _read_asset_manifest_bytes(cartridge_dir, assets_manifest)

    try:
        if isinstance(publisher, ShopifyPublisher):
            url_by_local_path = publisher.upload_assets(manifest_with_bytes)
            body_html = rewrite_asset_srcs(shopify_body_html, url_by_local_path)
        else:
            publisher.upload_assets(manifest_with_bytes)
            body_html = shopify_body_html
    except ShopifyCredentialsMissing as e:
        print(str(e), file=sys.stderr)
        return 1

    page_json = json.loads((cartridge_dir / "page.json").read_text())
    page_payload = {
        "title": page_json.get("headline") or f"{tenant.display_name} — {args.page}",
        "body_html": body_html,
        "storefront_host": tenant.get("site_host"),
    }
    if handle:
        page_payload["handle"] = handle

    try:
        result = publisher.publish(page_payload, unpublished=not args.live)
    except ShopifyCredentialsMissing as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Published {args.page} for {run_dir}: {json.dumps(result)}")

    redirect_target = None
    if args.live and redirect_from and isinstance(publisher, ShopifyPublisher):
        redirect_target = urllib.parse.urlparse(result.get("url", "")).path or "/"
        try:
            redirect_result = publisher.create_redirect(redirect_from, redirect_target)
        except ShopifyCredentialsMissing as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"Redirect {redirect_from} -> {redirect_target}: {json.dumps(redirect_result)}")

    note = f"url={result.get('url')} live={bool(args.live)}"
    if redirect_target is not None:
        note += f" redirect_from={redirect_from} redirect_target={redirect_target}"

    runstate.mark_published(
        run_dir, page=args.page, by="operator",
        note=note,
    )
    notify.notify_published(
        tenant, run_id=run_dir.name, page=args.page,
        url=result.get("url") or result.get("export_dir"),
    )

    if args.live and result.get("url") and isinstance(publisher, ShopifyPublisher):
        hits, pulls = publisher.verify_cache(result["url"], marker=run_dir.name)
        print(f"Cache verification: {hits}/{pulls} pulls returned the new body.")

    return 0


def cmd_digest_needs_review(args):
    """`harness digest needs-review --tenant <t> [--days 3]`: every run in
    needs_review whose history shows it entered that state more than
    `--days` days ago -- the weekly backlog digest (crons/needs-review-digest,
    workflows/weekly-digest.yaml)."""
    tenant = tenant_mod.load_tenant(args.tenant)
    rows = runstate.needs_review_runs(tenant, older_than_days=args.days)
    if not rows:
        print(f"No runs in needs_review older than {args.days} day(s) for {tenant.name}.")
        return 0
    print(f"{len(rows)} run(s) in needs_review older than {args.days} day(s) for {tenant.name}:")
    for run_dir, data, entered_dt in rows:
        pending_pages = [p for p, s in data["pages"].items() if s == "needs_review"]
        print(f"  - {run_dir.name}: pending={','.join(pending_pages)} since {entered_dt.isoformat()}")
    return 0


# ---------------------------------------------------------------------------
# harness spend -- K2 (docs/REVIEW-KIMI-LONG-RUN.md, Cycle 28)
# ---------------------------------------------------------------------------

def cmd_spend(args):
    """`harness spend --tenant <t>`: today's finalized spend, open
    reservations, and the daily cap, read straight from the ledger
    reserve_spend/record_spend write.

    `harness spend reconcile --tenant <t>`: drops reservations older than 2
    hours whose run already reached a final state but whose run never wrote
    its final ledger line (a crash, or record_spend's own write failing) --
    see harness/budget.py's reconcile_stale_reservations."""
    tenant = tenant_mod.load_tenant(args.tenant)
    if getattr(args, "spend_command", None) == "reconcile":
        dropped = budget_mod.reconcile_stale_reservations(tenant)
        if not dropped:
            print(f"No stale reservations to reconcile for {tenant.name}.")
            return 0
        print(f"Dropped {len(dropped)} stale reservation(s) for {tenant.name}:")
        for entry in dropped:
            print(f"  - {entry.get('run_id')}: ${float(entry.get('reserved_usd') or 0):.4f} reserved on {entry.get('date')}")
        return 0

    today_iso = datetime.date.today().isoformat()
    cap = budget_mod.daily_cap_usd(tenant)
    spent = budget_mod.daily_spend(tenant, today_iso)
    reserved = budget_mod.daily_reserved(tenant, today_iso)
    cap_str = f"${cap:.2f}" if cap is not None else "uncapped"
    print(f"{tenant.name} ({today_iso}): ${spent:.4f} spent, ${reserved:.4f} reserved, cap {cap_str}")
    return 0


# ---------------------------------------------------------------------------
# harness doctor
# ---------------------------------------------------------------------------

def cmd_doctor(args):
    """`harness doctor --tenant <t>`: can this tenant actually run?

    Files, tenant.yaml validity, credentials BY NAME (never a value), model
    reachability through the models endpoint (which spends no tokens),
    whisper/ffmpeg, and whether a run's directories are writable -- one
    pass/fail table. Exit 1 if anything failed."""
    tenant = tenant_mod.load_tenant(args.tenant)
    tenant_mod.activate(tenant)
    tenant.load_env()
    checks = doctor_mod.run_checks(
        tenant,
        ffmpeg_bin=args.ffmpeg_bin,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
        offline=args.offline,
    )
    print(doctor_mod.format_table(tenant, checks))
    failed = [c for c in checks if c.status == doctor_mod.FAIL]
    return exits.USAGE if failed else exits.OK


# ---------------------------------------------------------------------------
# harness claims add / list
# ---------------------------------------------------------------------------

def cmd_claims_add(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    verified_path = tenant.claims_dir / "verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else []

    base_id = re.sub(r"[^a-z0-9]+", "-", args.text.lower()).strip("-")[:40] or "claim"
    existing_ids = {c["id"] for c in verified}
    cid, n = base_id, 2
    while cid in existing_ids:
        cid = f"{base_id}-{n}"
        n += 1

    entry = {
        "id": cid,
        "text": args.text,
        "category": args.category,
        "source": args.source,
        "approved_by": args.approved_by or (tenant.author("contributor") or {}).get("name") or "operator",
        "date": datetime.date.today().isoformat(),
    }
    verified.append(entry)
    verified_path.write_text(json.dumps(verified, indent=2) + "\n")
    print(f"Added claim {cid!r} to {verified_path}")
    return 0


def cmd_claims_list(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    verified_path = tenant.claims_dir / "verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else []
    for c in verified:
        print(f"{c['id']:32s} [{c['category']:10s}] {c['text']}  ({c['source']})")
    return 0


# ---------------------------------------------------------------------------
# harness score -- see evals/rubric.md for what each axis means.
# ---------------------------------------------------------------------------

def cmd_score(args):
    tenant = tenant_mod.load_tenant(args.tenant)
    evals_mod.record_score(
        tenant, run_dir=args.run_dir, angle=args.angle, brand=args.brand,
        claims=args.claims, publish=args.publish, by=args.by, note=args.note,
    )
    print(f"Recorded score for {args.run_dir} -> {tenant.evals_path}")
    return 0


# ---------------------------------------------------------------------------
# harness dataset export / harness eval report (Kimi long-run phase 5)
# ---------------------------------------------------------------------------

def cmd_dataset_export(args):
    """`harness dataset export --tenant <t> [--out PATH]`: one JSONL record per
    (run, cartridge) -- ad_brief, facts_pack summary, cartridge/block content
    versions, page.json, gate history, deterministic check results, and the
    human scores joined from evals/scores.jsonl. The record a future
    fine-tune would be built from (correction 7: not before ~200
    human-scored pages)."""
    tenant = tenant_mod.load_tenant(args.tenant)
    count, out = evals_mod.export_dataset(tenant, out_path=args.out)
    print(f"Wrote {count} record(s) -> {out}")
    return 0


def cmd_eval_report(args):
    """`harness eval report --tenant <t>`: scores.jsonl aggregated by
    cartridge, block, angle, and reviewer, with the rubric's publish bar
    (mean would-publish >= 4) per bucket."""
    tenant = tenant_mod.load_tenant(args.tenant)
    print(evals_mod.format_report(evals_mod.build_report(tenant)))
    return 0


# ---------------------------------------------------------------------------
# harness design-skills (vendored elayadesign/ai-design-skills pack)
# ---------------------------------------------------------------------------

def cmd_design_skills_list(args):
    """`harness design-skills list [--tenant T]`: the vendored pack and its
    take/adapt/decline tally, no model call. With --tenant, also prints a
    "design_reference" group -- the structural rules derived from that
    tenant's tenant.yaml design_reference DESIGN.md packs (cycle 30), or a
    one-line note when the tenant sets none."""
    from .design_skills import adapter
    print(adapter.format_list(), end="")
    if getattr(args, "tenant", None):
        from .design_skills.design_md import design_reference_rules

        tenant = tenant_mod.load_tenant(args.tenant)
        rules = design_reference_rules(tenant)
        print("")
        print("design_reference")
        if not rules:
            print(f"  {tenant.display_name} sets no design_reference in tenant.yaml.")
        else:
            for rule in rules:
                check = f"  check={rule['check']}" if rule.get("check") else ""
                print(f"  {rule['id']}: {rule['title']} -- action={rule['action']} value={rule['value']!r}{check}")
    return 0


def cmd_design_skills_explain(args):
    """`harness design-skills explain [--action take|adapt|decline]`: the
    per-rule decision table. No tenant, no model call."""
    from .design_skills import adapter
    print(adapter.format_explain(action=args.action), end="")
    return 0


def cmd_design_skills_check(args):
    """`harness design-skills check <page.json> --cartridge NAME`: run the
    pack's hard and soft checks against one page.json. Exit 2 on a hard
    failure (same code as a claims-gate STOP); soft warnings print and
    still exit 0."""
    from .design_skills import gate as design_gate

    page = json.loads(Path(args.page).read_text())
    result = design_gate.check_page(page, cartridge_name=args.cartridge)
    for item in result["hard"]:
        print(f"FAIL  {item.get('path', '')}: {item['issue']}")
    for warning in result["soft"]:
        print(f"warn  {warning}")
    if not result["hard"] and not result["soft"]:
        print("ok")
    return 2 if result["hard"] else 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def _add_tenant_flag(parser):
    parser.add_argument(
        "--tenant",
        help="which company this command is for; default: HARNESS_TENANT, else tenants/default.txt",
    )


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which is the code this harness
    reserves for a claims-gate STOP -- so `harness run --carrtidges x` and a
    run that legitimately refused to write a page were indistinguishable to
    any script checking $?. A usage error is exits.USAGE here.

    add_subparsers() builds every subparser from the calling parser's own
    class, so `harness run` with a missing argument gets this too."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(exits.USAGE, f"{self.prog}: error: {message}\n")


def _add_tool_flags(p):
    """The ffmpeg/whisper flags every run-shaped command shares. Defaults
    resolve at parser-build time via harness/config.py's call-time functions
    (R24), so HARNESS_REPO_DIR set after import still wins."""
    p.add_argument("--ffmpeg-bin", default=harness_config.FFMPEG_BIN)
    p.add_argument("--whisper-bin", default=harness_config.whisper_bin())
    p.add_argument("--whisper-model", default=harness_config.whisper_model())


def build_parser():
    parser = _Parser(
        prog="harness",
        epilog=exits.HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="ingest -> ground -> gate -> write -> render")
    p_run.add_argument("input")
    p_run.add_argument("--cartridges", help="comma-separated cartridge names; default: 3 random from the tenant's pool")
    p_run.add_argument("--seed", type=int)
    p_run.add_argument("--product", help="product slug or name; default: inferred from the ad, else the tenant's default product")
    _add_tool_flags(p_run)
    p_run.add_argument(
        "--batch", action="store_true",
        help="submit the three cartridges' initial writes via the Message Batches API (50%% off) before repairs",
    )
    _add_tenant_flag(p_run)
    p_run.set_defaults(func=cmd_run)

    p_ingest = sub.add_parser("ingest", help="ad -> ad_brief.json only, for debugging")
    p_ingest.add_argument("input")
    _add_tool_flags(p_ingest)
    _add_tenant_flag(p_ingest)
    p_ingest.set_defaults(func=cmd_ingest)

    p_review = sub.add_parser("review", help="write a self-contained review.html per cartridge (images inlined)")
    p_review.add_argument("run_dir")
    _add_tenant_flag(p_review)
    p_review.set_defaults(func=cmd_review)

    p_shopify_body = sub.add_parser("shopify-body", help="write shopify-body.html + shopify-body.assets.json for one cartridge's output")
    p_shopify_body.add_argument("cartridge_dir", help="<run-dir>/<cartridge>, e.g. tenants/<t>/out/<run-id>/listicle")
    _add_tenant_flag(p_shopify_body)
    p_shopify_body.set_defaults(func=cmd_shopify_body)

    p_doctor = sub.add_parser("doctor", help="check that a tenant can actually run")
    p_doctor.add_argument("--offline", action="store_true", help="skip the models-endpoint check")
    _add_tool_flags(p_doctor)
    _add_tenant_flag(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_approve = sub.add_parser("approve", help="approve a run's page(s) for publish")
    p_approve.add_argument("run_dir")
    p_approve.add_argument("--by", required=True, help="reviewer email; must match a tenant.yaml reviewers entry")
    p_approve.add_argument("--pages", help="comma-separated cartridge names; default: every page in the run")
    p_approve.add_argument("--note")
    _add_tenant_flag(p_approve)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a run (every page)")
    p_reject.add_argument("run_dir")
    p_reject.add_argument("--by", required=True, help="reviewer email; must match a tenant.yaml reviewers entry")
    p_reject.add_argument("--note", required=True)
    _add_tenant_flag(p_reject)
    p_reject.set_defaults(func=cmd_reject)

    p_packet = sub.add_parser("packet", help="stamp a run's packet.json (the publish gate)")
    p_packet.add_argument("run_dir")
    p_packet.add_argument("--stamp", required=True, choices=["ship", "redo", "kill"])
    p_packet.add_argument("--by", required=True)
    p_packet.add_argument("--note")
    _add_tenant_flag(p_packet)
    p_packet.set_defaults(func=cmd_packet)

    p_revise = sub.add_parser("revise", help="apply the latest reviewer feedback for one page and write a new version")
    p_revise.add_argument("run_dir")
    p_revise.add_argument("--page", required=True, help="cartridge name, e.g. article")
    p_revise.add_argument("--by", help="who ran this revise (defaults to the feedback's own reviewer email)")
    _add_tenant_flag(p_revise)
    p_revise.set_defaults(func=cmd_revise)

    p_serve = sub.add_parser("serve", help="run the reviewer web app")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=4870)
    _add_tenant_flag(p_serve)
    p_serve.set_defaults(func=cmd_serve)

    p_publish = sub.add_parser("publish", help="publish one cartridge's page via the tenant's publisher adapter")
    p_publish.add_argument("run_dir")
    p_publish.add_argument("--page", required=True, help="cartridge name, e.g. article")
    p_publish.add_argument("--live", action="store_true", help="publish live (default: unpublished draft)")
    p_publish.add_argument("--dry-run", action="store_true", help="validate credentials and body only; creates nothing")
    p_publish.add_argument("--handle", help="explicit Shopify page handle, e.g. listicle-test-1")
    p_publish.add_argument(
        "--redirect-from",
        help="root path to redirect to the published page, e.g. /listicle-test-1 (requires --live)",
    )
    _add_tenant_flag(p_publish)
    p_publish.set_defaults(func=cmd_publish)

    p_digest = sub.add_parser("digest", help="reviewer-backlog and score digests")
    digest_sub = p_digest.add_subparsers(dest="digest_command", required=True)
    p_digest_nr = digest_sub.add_parser("needs-review", help="runs in needs_review older than N days")
    p_digest_nr.add_argument("--days", type=int, default=3)
    _add_tenant_flag(p_digest_nr)
    p_digest_nr.set_defaults(func=cmd_digest_needs_review)

    p_spend = sub.add_parser("spend", help="today's spend, reservations, and daily cap (K2)")
    _add_tenant_flag(p_spend)
    p_spend.set_defaults(func=cmd_spend, spend_command=None)
    spend_sub = p_spend.add_subparsers(dest="spend_command")
    p_spend_reconcile = spend_sub.add_parser(
        "reconcile", help="drop reservations older than 2h whose run already finished"
    )
    _add_tenant_flag(p_spend_reconcile)
    p_spend_reconcile.set_defaults(func=cmd_spend, spend_command="reconcile")

    p_claims = sub.add_parser("claims", help="manage a tenant's claims/verified.json")
    claims_sub = p_claims.add_subparsers(dest="claims_command", required=True)

    p_claims_add = claims_sub.add_parser("add")
    p_claims_add.add_argument("text")
    p_claims_add.add_argument("--category", required=True, choices=["spec", "price", "comparison", "health", "trust"])
    p_claims_add.add_argument("--source", required=True)
    p_claims_add.add_argument("--approved-by")
    _add_tenant_flag(p_claims_add)
    p_claims_add.set_defaults(func=cmd_claims_add)

    p_claims_list = claims_sub.add_parser("list")
    _add_tenant_flag(p_claims_list)
    p_claims_list.set_defaults(func=cmd_claims_list)

    p_score = sub.add_parser("score", help="record a human score for a run (see evals/rubric.md)")
    p_score.add_argument("run_dir")
    p_score.add_argument("--angle", type=int, required=True)
    p_score.add_argument("--brand", type=int, required=True)
    p_score.add_argument("--claims", type=int, required=True)
    p_score.add_argument("--publish", type=int, required=True)
    p_score.add_argument("--by")
    p_score.add_argument("--note")
    _add_tenant_flag(p_score)
    p_score.set_defaults(func=cmd_score)

    p_dataset = sub.add_parser("dataset", help="dataset export for eval/fine-tune records")
    dataset_sub = p_dataset.add_subparsers(dest="dataset_command", required=True)
    p_dataset_export = dataset_sub.add_parser("export", help="write one JSONL record per (run, cartridge)")
    p_dataset_export.add_argument("--out", help="default: tenants/<t>/evals/dataset.jsonl")
    _add_tenant_flag(p_dataset_export)
    p_dataset_export.set_defaults(func=cmd_dataset_export)

    p_eval = sub.add_parser("eval", help="eval reports over evals/scores.jsonl")
    eval_sub = p_eval.add_subparsers(dest="eval_command", required=True)
    p_eval_report = eval_sub.add_parser("report", help="aggregate scores by cartridge, block, angle, reviewer")
    _add_tenant_flag(p_eval_report)
    p_eval_report.set_defaults(func=cmd_eval_report)

    p_ds = sub.add_parser("design-skills", help="the vendored elayadesign/ai-design-skills pack")
    ds_sub = p_ds.add_subparsers(dest="design_skills_command", required=True)
    p_ds_list = ds_sub.add_parser("list", help="vendored skills and take/adapt/decline counts")
    _add_tenant_flag(p_ds_list)
    p_ds_list.set_defaults(func=cmd_design_skills_list)
    p_ds_explain = ds_sub.add_parser("explain", help="per-rule decision table")
    p_ds_explain.add_argument("--action", choices=["take", "adapt", "decline"])
    p_ds_explain.set_defaults(func=cmd_design_skills_explain)
    p_ds_check = ds_sub.add_parser("check", help="run the pack's gates against one page.json")
    p_ds_check.add_argument("page", help="path to a page.json")
    p_ds_check.add_argument("--cartridge", help="cartridge name (enables the landing-page soft checks)")
    p_ds_check.set_defaults(func=cmd_design_skills_check)

    p_tenant = sub.add_parser("tenant", help="create and inspect tenants")
    tenant_sub = p_tenant.add_subparsers(dest="tenant_command", required=True)
    p_tenant_init = tenant_sub.add_parser("init", help="copy tenants/_template to tenants/<name>")
    p_tenant_init.add_argument("name")
    p_tenant_init.set_defaults(func=cmd_tenant_init)
    p_tenant_list = tenant_sub.add_parser("list", help="every tenant and whether it is configured")
    p_tenant_list.set_defaults(func=cmd_tenant_list)

    p_brand = sub.add_parser("brand", help="import a brand kit from a Drive folder or a local directory")
    brand_sub = p_brand.add_subparsers(dest="brand_command", required=True)
    p_brand_import = brand_sub.add_parser(
        "import", help="logo/brand-guide/palette/fonts -> tenants/<t>/brand/* and tenant.yaml's brand: section"
    )
    p_brand_import.add_argument("--drive-folder", help="Drive folder URL or id (must be link-public)")
    p_brand_import.add_argument("--local", help="local directory to import from instead of Drive")
    p_brand_import.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    p_brand_import.add_argument(
        "--force", action="store_true",
        help="overwrite base.css and any existing tokens.json/tenant.yaml brand value this import also produces",
    )
    _add_tenant_flag(p_brand_import)
    p_brand_import.set_defaults(func=cmd_brand_import)

    p_workflow = sub.add_parser("workflow", help="run a named pipeline from workflows/")
    workflow_sub = p_workflow.add_subparsers(dest="workflow_command", required=True)
    p_workflow_run = workflow_sub.add_parser("run", help="run one workflow's stages, in its own order")
    p_workflow_run.add_argument("name")
    p_workflow_run.add_argument("--input", required=True)
    p_workflow_run.add_argument("--cartridges")
    p_workflow_run.add_argument("--seed", type=int)
    p_workflow_run.add_argument("--product")
    _add_tool_flags(p_workflow_run)
    p_workflow_run.add_argument("--batch", action="store_true")
    _add_tenant_flag(p_workflow_run)
    p_workflow_run.set_defaults(func=cmd_workflow_run)
    p_workflow_list = workflow_sub.add_parser("list", help="every workflow in workflows/")
    p_workflow_list.set_defaults(func=cmd_workflow_list)

    return parser


def main(argv=None):
    """One place turns an expected failure into a message and an exit code.

    Everything raised from harness/errors.py's hierarchy is a failure with an
    operator action behind it -- an unconfigured tenant, an unknown workflow or
    --product, a writer that never returned valid JSON, a batch that timed out,
    a publish the storefront rejected. Each prints one line and returns its own
    exit_code.

    Anything else still raises. A traceback from an unexpected exception is
    deliberate: that is a bug in this harness, and dressing it up as a friendly
    message would only make it harder to report."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except HarnessError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code


def main_adv_alias(argv=None):
    """The old `adv` entry point, kept for one release. Same CLI, one warning."""
    print("adv is deprecated and will be removed; use `harness` instead.", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
