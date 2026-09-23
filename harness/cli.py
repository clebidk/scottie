"""`harness` console entry point: run / ingest / claims / review / score /
shopify-body / tenant / workflow / design-skills / meta.

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

from . import asset_describe
from . import budget as budget_mod
from . import brand_import
from . import notify
from . import listicle
from . import looks
from . import meta_ingest
from . import pipeline
from . import repair
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
from .publishers import shopify as shopify_pub
from .publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher, rewrite_asset_srcs
from .render import render_page
from .review import build_review_for_page, build_reviews
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
# harness rerender (cycle 45)
# ---------------------------------------------------------------------------

def cmd_rerender(args):
    """`harness rerender <run-dir> --page <cartridge>`: re-renders one
    page's index.html from the run's OWN page.json + facts_pack.json,
    through the current template, image slot enforcement and matcher.

    There is no model call anywhere in this path -- the writer already ran,
    and its output (page.json) is the input here. That is the whole point:
    a template, CSS or image-slot fix reaches pages that are already live
    without paying for the copy a second time.

    Also rewrites <cartridge>-review.html, and refreshes shopify-body.html /
    shopify-body.assets.json when this page already has them (a page that
    was never exported does not gain an export it never asked for).

    Approval state is untouched -- see runstate.mark_rerendered. The run's
    published/updated dates are re-used from its own history, so a page
    that went live weeks ago is not silently re-dated to today.
    """
    tenant = _resolve_tenant_for_run(args)
    tenant_mod.activate(tenant)
    run_dir = Path(args.run_dir)
    cartridge_name = args.page
    cartridge_dir = run_dir / cartridge_name

    page_json = cartridge_dir / "page.json"
    facts_pack_json = run_dir / "facts_pack.json"
    for path in (page_json, facts_pack_json):
        if not path.exists():
            print(f"no {path.name} found at {path}; nothing to re-render", file=sys.stderr)
            return 1

    page = json.loads(page_json.read_text())
    facts_pack = json.loads(facts_pack_json.read_text())

    # Cycle 51: `--look` switches which template under
    # cartridges/listicle/looks/ renders this page. It is a template choice,
    # not a copy change -- there is still no model call in this path -- so it
    # is recorded on page.json (render_page writes the page back out at the
    # end) and in state.json next to the style.
    requested_look = getattr(args, "look", None)
    if requested_look:
        if not looks.has_looks(cartridge_name):
            print(f"--look applies to a cartridge with looks ({', '.join(looks.CARTRIDGE_LOOKS)}), "
                  f"not {cartridge_name}", file=sys.stderr)
            return 1
        if requested_look not in looks.cartridge_looks(cartridge_name):
            print(f"--look {requested_look!r} is not a {cartridge_name} look; choose one of "
                  f"{list(looks.cartridge_looks(cartridge_name))}", file=sys.stderr)
            return 1
        page["look"] = looks.resolve_look(cartridge_name, requested_look, tenant=tenant)
    ad_brief_json = run_dir / "ad_brief.json"
    ad_brief = json.loads(ad_brief_json.read_text()) if ad_brief_json.exists() else {}

    published = updated = (
        runstate.run_started_date(run_dir) or datetime.date.today().isoformat()
    )

    index_path = render_page(
        cartridge_name=cartridge_name,
        page=page,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        cartridges_dir=pipeline.CARTRIDGES_DIR,
        brand_dir=tenant.brand_dir,
        templates_dir=pipeline.TEMPLATES_DIR,
        out_dir=cartridge_dir,
        published=published,
        updated=updated,
        tenant=tenant,
    )
    print(f"Wrote {index_path}")

    review_path = build_review_for_page(run_dir, cartridge_name)
    if review_path:
        print(f"Wrote {review_path}")

    if (cartridge_dir / "shopify-body.html").exists():
        shopify_body_path, assets_manifest_path = write_shopify_body(cartridge_dir)
        # Cycle 52: the export carries the tenant's @font-face rules, whose
        # urls are repo-relative and would 404 on a storefront. Re-apply the
        # CDN urls this tenant has already uploaded (cache only -- rerender
        # never calls Shopify, and a tenant that has published no font is
        # left with its fallback stack).
        refreshed = shopify_pub.apply_cached_font_urls(shopify_body_path.read_text(), tenant)
        shopify_body_path.write_text(refreshed)
        print(f"Wrote {shopify_body_path}")
        print(f"Wrote {assets_manifest_path}")

    if requested_look:
        if cartridge_name == "listicle":
            runstate.record_listicle_choice(run_dir, look=page["look"])
        else:
            runstate.record_look(run_dir, cartridge_name, page["look"])
    runstate.mark_rerendered(run_dir, page=cartridge_name, note=args.note or "")
    return 0


def cmd_fixcopy(args):
    """`harness fixcopy <run-dir> --page <cartridge>`: applies only the
    deterministic copy fixes (currently: a retired brand name, tenant.yaml
    brand.retired_names -- harness/repair.py's apply_retired_name_fixes) to
    an existing page.json. No model call, no gate, no repair loop -- the
    writer already ran; this just corrects text the deterministic pre-repair
    pass would have caught had the gate flagged it for this run.

    Writes the fixed page.json back and records a state.json history note
    (approval untouched, same as `harness rerender` -- see
    runstate.mark_fixcopy). This command does not re-render index.html or
    republish; run `harness rerender <run-dir> --page <cartridge>` and then
    `harness publish <run-dir> --page <cartridge> --update` afterward."""
    tenant = _resolve_tenant_for_run(args)
    tenant_mod.activate(tenant)
    run_dir = Path(args.run_dir)
    cartridge_name = args.page
    cartridge_dir = run_dir / cartridge_name

    page_json_path = cartridge_dir / "page.json"
    facts_pack_json = run_dir / "facts_pack.json"
    for path in (page_json_path, facts_pack_json):
        if not path.exists():
            print(f"no {path.name} found at {path}; nothing to fix", file=sys.stderr)
            return 1

    page = json.loads(page_json_path.read_text())
    facts_pack = json.loads(facts_pack_json.read_text())

    changes = repair.apply_retired_name_fixes(page, facts_pack, tenant, cartridge_name=cartridge_name)
    if not changes:
        print(f"No copy fixes needed for {cartridge_dir}")
        return 0

    page_json_path.write_text(json.dumps(page, indent=2))
    for old, new, at_path in changes:
        print(f'fix: retired name "{old}" -> "{new}" at {at_path}')
    runstate.mark_fixcopy(run_dir, page=cartridge_name, note=f"{len(changes)} retired-name fix(es)")
    print(f"Wrote {page_json_path}")
    print(
        f"Now run: harness rerender {run_dir} --page {cartridge_name} "
        f"&& harness publish {run_dir} --page {cartridge_name} --update"
    )
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


def _publish_brand_fonts(tenant, publisher, body_html):
    """Point the export's @font-face rules at the storefront CDN.

    A tenant's self-hosted webfonts are brand data, not per-page assets:
    they are the same handful of files on every page, so they are uploaded
    once and remembered in brand/fonts/cdn-manifest.json. A file that is
    already in the manifest is never re-uploaded; a file Shopify refuses is
    reported here and its @font-face entry is dropped from the export, so
    the page renders in the tenant's fallback stack rather than waiting on
    a URL that will never resolve."""
    fonts = shopify_pub.font_files(tenant)
    if not fonts:
        return body_html
    known = shopify_pub.load_font_cdn_manifest(tenant)
    pending = [{"filename": f.name, "bytes": f.read_bytes()} for f in fonts if f.name not in known]
    if pending:
        urls, errors = publisher.upload_fonts(pending)
        for filename, reason in sorted(errors.items()):
            print(
                f"font {filename} could not be uploaded ({reason}); this page falls back "
                f"to the tenant's fallback font stack",
                file=sys.stderr,
            )
        if urls:
            known = {**known, **urls}
            manifest_path = shopify_pub.save_font_cdn_manifest(tenant, known)
            print(f"Uploaded {len(urls)} font file(s); cached in {manifest_path}")
    return shopify_pub.rewrite_font_face_urls(body_html, known)


def cmd_publish(args):
    """`harness publish <run-dir> --page <cartridge> [--live] [--dry-run]
    [--handle <slug>] [--redirect-from </path>] [--update]`: refuses unless
    state.json's `pages[<cartridge>]` is "approved" AND packet.json's stamp
    is "ship". Default publish is unpublished (a draft page) unless
    `--live`; `--live` also verifies the storefront cache with 8 pulls, 2s
    apart (the cache-epoch trap in the tenant's own storefront notes).
    `--dry-run` only validates credentials and the page body (a GET on the
    shop endpoint for the Shopify adapter) -- no approval or stamp
    required, and nothing is created. `--handle` sets the Shopify page
    handle explicitly. `--redirect-from` (requires `--live`) creates a
    Shopify URL redirect from a root path to the published page.

    `--update` (Cycle 40) updates the page this run already published --
    looked up via `runstate.published_page_record(run_dir, args.page)` --
    instead of creating a new one (which would otherwise create
    `<handle>-1` on a second publish of the same page). Refuses if this run
    has no stored page id for `--page` yet. `--handle` together with
    `--update` is ignored, with a warning: an update targets the page's
    existing handle, and changing a live page's handle is out of scope
    here."""
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

    update = getattr(args, "update", False)
    if update and handle:
        print(
            f"--handle {handle!r} is ignored with --update (handle changes are out of "
            f"scope) -- updating the existing page at its current handle instead.",
            file=sys.stderr,
        )
        handle = None

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

    page_id = None
    if update:
        published_record = runstate.published_page_record(run_dir, args.page)
        if not published_record or not published_record.get("page_id"):
            print(
                f"refusing --update: no stored page id for {args.page!r} in this run -- "
                f"publish it once without --update first "
                f"(`harness publish {run_dir} --page {args.page}`).",
                file=sys.stderr,
            )
            return 1
        page_id = published_record["page_id"]

    publisher = _make_publisher(tenant, export_dir=export_dir)
    if update and not isinstance(publisher, ShopifyPublisher):
        print("--update is only supported for the shopify publisher", file=sys.stderr)
        return 1
    manifest_with_bytes = _read_asset_manifest_bytes(cartridge_dir, assets_manifest)

    try:
        if isinstance(publisher, ShopifyPublisher):
            url_by_local_path = publisher.upload_assets(manifest_with_bytes)
            body_html = rewrite_asset_srcs(shopify_body_html, url_by_local_path)
            body_html = _publish_brand_fonts(tenant, publisher, body_html)
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
        if update:
            result = publisher.update_page(page_id, page_payload, unpublished=not args.live)
        else:
            result = publisher.publish(page_payload, unpublished=not args.live)
    except ShopifyCredentialsMissing as e:
        print(str(e), file=sys.stderr)
        return 1

    verb = "Updated" if update else "Published"
    print(f"{verb} {args.page} for {run_dir}: {json.dumps(result)}")

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
        page_id=result.get("id"), handle=result.get("handle"), url=result.get("url"),
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
# harness meta check / pull / inbox (cycle 68) -- read-only Meta ad ingest
# ---------------------------------------------------------------------------

def _meta_setup(tenant, *, need_token=True):
    """(config, client) for a meta command. The token is read from the
    environment after the tenant's .env is loaded, and never printed."""
    tenant.load_env()
    cfg = meta_ingest.meta_config(tenant)
    if not need_token:
        return cfg, None
    token = meta_ingest.token_from_env(cfg, tenant)
    return cfg, meta_ingest.make_client(token, cfg)


def cmd_meta_check(args):
    """`harness meta check`: is the token set, and can it read the ad
    account? The only command that calls Meta without pulling anything."""
    tenant = tenant_mod.load_tenant(args.tenant)
    cfg, client = _meta_setup(tenant)
    result = meta_ingest.check(client, cfg["ad_account_id"])
    me, account = result["me"], result["account"]
    status = account.get("account_status")
    print(f"token: OK, acting as {me.get('name') or '?'} (id {me.get('id') or '?'})")
    print(f"ad account {cfg['ad_account_id']}: {account.get('name') or '?'}, "
          f"status {meta_ingest.ACCOUNT_STATUS.get(status, status)}")
    print(f"graph api {cfg['graph_api_version']}; new ads counted from {cfg['ingest_since'].date().isoformat()}")
    return exits.OK


def cmd_meta_pull(args):
    """`harness meta pull [--since DATE] [--limit N] [--refresh] [--dry-run]`."""
    tenant = tenant_mod.load_tenant(args.tenant)
    since = None
    if args.since:
        since = meta_ingest.parse_since(args.since, what="--since")
    cfg, client = _meta_setup(tenant)
    summary = meta_ingest.pull(
        client, meta_ingest.Inbox(tenant.meta_inbox_dir),
        account_id=cfg["ad_account_id"], since=since or cfg["ingest_since"],
        limit=args.limit, refresh=args.refresh, dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"dry run: {len(summary['would_ingest'])} would be ingested, "
              f"{len(summary['existing'])} already in the inbox. Nothing written.")
    else:
        print(f"{len(summary['ingested'])} ingested, {len(summary['failed'])} failed, "
              f"{len(summary['skipped'])} skipped, {len(summary['existing'])} already in the inbox")
    return exits.OK


def cmd_meta_inbox(args):
    """`harness meta inbox`: one line per item, oldest first."""
    tenant = tenant_mod.load_tenant(args.tenant)
    items = meta_ingest.Inbox(tenant.meta_inbox_dir).items()
    if not items:
        print(f"{tenant.meta_inbox_dir} is empty.")
        return exits.OK
    print(f"{'ad_id':20s} {'state':9s} {'created':10s} {'media':6s} {'ad name':32s} reason")
    for item in items:
        print(f"{item.get('ad_id', ''):20s} {item.get('state', ''):9s} "
              f"{(item.get('created_time') or '')[:10]:10s} {item.get('media_type') or '-':6s} "
              f"{(item.get('ad_name') or '')[:32]:32s} {item.get('reason') or ''}")
    return exits.OK


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
# harness images describe / pool (cycle 42)
# ---------------------------------------------------------------------------

def cmd_images_pool(args):
    """`harness images pool --tenant <t>`: per-model total/reviewed/excluded/
    with-alt counts across every active product's uncapped asset pool -- the
    same numbers harness/serve.py's /images index shows per product."""
    tenant = tenant_mod.load_tenant(args.tenant)
    rows = asset_describe.pool_report(tenant)
    if not rows:
        print(f"No active products for {tenant.name}.")
        return 0
    print(f"{'model':30s} {'total':>6s} {'reviewed':>9s} {'excluded':>9s} {'with-alt':>9s}")
    for row in rows:
        print(f"{row['model']:30s} {row['total']:6d} {row['reviewed']:9d} {row['excluded']:9d} {row['with_alt']:9d}")
    return 0


def cmd_images_brandcheck(args):
    """`harness images brandcheck --tenant <t> [--model <slug>] [--dry-run]
    [--cache-dir DIR ...]`: cycle 65 -- scores every asset in the active
    products' pools (or one model's) for the retired brand look and stores
    old_brand on the flagged ones in asset-review.json, which keeps them off
    every page (harness/brandcheck.py). No model call, no file deleted."""
    from . import asset_review, brandcheck, ground as ground_mod

    tenant = tenant_mod.load_tenant(args.tenant)
    source = ground_mod.LocalFactsSource(tenant.claims_dir)
    if args.model:
        product = asset_describe.product_for_model(tenant, args.model)
        if product is None:
            available = ", ".join(asset_describe.available_model_slugs(tenant)) or "(none)"
            print(f"unknown --model: {args.model!r}; available: {available}", file=sys.stderr)
            return exits.USAGE
        products = [product]
    else:
        products = source.active_products()
    pool = {}
    for product in products:
        for asset in ground_mod.full_asset_pool(source, product, tenant.claims_config):
            pool.setdefault(asset["id"], asset)

    cache_dirs = [Path(d) for d in (args.cache_dir or [])] or [tenant.runs_dir / "asset-cache"]
    results, unreadable = brandcheck.check_assets(list(pool.values()), cache_dirs)
    review = asset_review.load_asset_review(tenant.brand_dir)
    new_review, flagged, cleared = brandcheck.apply_flags(review, results)
    for result in results:
        if result["old_brand"]:
            sc = result["scores"]
            print(f"old-brand  green={sc['green']:.3f} mint={sc['mint']:.3f} flat={sc['flat']:.3f}  {result['asset']['id']}")
    for asset_id in cleared:
        print(f"cleared    {asset_id}")
    print(f"{len(results)} checked, {len(flagged)} old-brand, {len(cleared)} cleared, {len(unreadable)} unreadable")
    if args.dry_run:
        print("--dry-run: asset-review.json not written")
    else:
        asset_review.save_asset_review(tenant.brand_dir, new_review)
    return 0


def cmd_images_describe(args):
    """`harness images describe --tenant <t> --model <slug> [--limit N]
    [--force] [--dry-run]`: vision-drafts alt text + tags for a model's
    uncapped, not-yet-reviewed asset pool -- see harness/asset_describe.py.

    Costed and capped exactly like `harness ingest`/`harness brand import`:
    the tenant's daily spend cap (harness/budget.py's reserve_spend) gates
    the whole invocation before any vision call, so a tenant already at/over
    its cap describes nothing and exits 3 -- never a partial, uncounted
    spend."""
    tenant = tenant_mod.load_tenant(args.tenant)
    product = asset_describe.product_for_model(tenant, args.model)
    if product is None:
        available = ", ".join(asset_describe.available_model_slugs(tenant)) or "(none)"
        print(f"unknown --model: {args.model!r}; available: {available}", file=sys.stderr)
        return exits.USAGE

    candidates = asset_describe.candidates_for(tenant, product, force=args.force)
    if args.limit is not None:
        candidates = candidates[:args.limit]

    if args.dry_run:
        estimate = len(candidates) * asset_describe.ESTIMATED_COST_PER_IMAGE_USD
        print(f"Would describe {len(candidates)} image(s) for model {args.model!r} (~${estimate:.3f} estimated, no calls made).")
        return 0

    if not candidates:
        print("described 0, skipped 0, cost $0.0000")
        return 0

    run_id = asset_describe.make_run_id(tenant.name, args.model)
    log = RunLog(run_id, tenant.runs_dir / f"{run_id}.log")
    budget = Budget(wall_s=1800, tokens=5_000_000, calls=len(candidates) + 5)
    today_iso = datetime.date.today().isoformat()

    try:
        try:
            # Reserved BEFORE any client/vision call is made, same as
            # pipeline.prepare_run does for `harness run` -- a tenant
            # already at/over its daily cap spends nothing on this
            # invocation at all, not even a wasted client construction.
            budget_mod.reserve_spend(tenant, run_id=run_id, today_iso=today_iso, log=log)
        except BudgetExceeded as e:
            pipeline.abort_budget_run(log, budget, e, tenant=tenant, run_id=run_id, today_iso=today_iso, stage="images_describe")
            print(f"budget exceeded: {e}", file=sys.stderr)
            return 3

        tenant.load_env()
        client = make_client()
        model = asset_describe.vision_model_for(tenant)
        try:
            result = asset_describe.describe_assets(
                tenant, candidates, product=product, client=client, model=model, budget=budget, log=log,
            )
        except BudgetExceeded as e:
            pipeline.abort_budget_run(log, budget, e, tenant=tenant, run_id=run_id, today_iso=today_iso, stage="images_describe")
            print(f"budget exceeded: {e}", file=sys.stderr)
            return 3

        cost = log.cost_estimate()
        budget_mod.record_spend(tenant, run_id=run_id, cost=cost, today_iso=today_iso, log=log)
        print(f"described {result['described']}, skipped {result['skipped']}, cost ${cost:.4f}")
        return 0
    finally:
        log.close()


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
    p_run.add_argument(
        "--look", choices=list(looks.all_looks()),
        help="page look -- which of cartridges/<cartridge>/looks/ lays the page out, for the "
             "selected cartridge that has this look (listicle: editorial|cards|pillars|"
             "scorecard|lander; product-page: pdp|classic); default: the listicle's style "
             "pairing, product-page's pdp, or the tenant's own pins",
    )
    p_run.add_argument(
        "--style", choices=list(listicle.STYLES),
        help="listicle style; default: deterministic from the run seed, so a batch of runs "
             "rotates through every style the tenant allows",
    )
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

    p_rerender = sub.add_parser(
        "rerender",
        help="re-render one page's index.html from its existing page.json -- no model call, no state change",
    )
    p_rerender.add_argument("run_dir", help="tenants/<t>/out/<run-id>")
    p_rerender.add_argument("--page", required=True, help="cartridge name, e.g. listicle")
    p_rerender.add_argument(
        "--look", choices=list(looks.all_looks()), default=None,
        help="re-render the page in a different look of its own cartridge (listicle or "
             "product-page); the copy is untouched",
    )
    p_rerender.add_argument("--note", default="", help="free text for the state.json history entry")
    _add_tenant_flag(p_rerender)
    p_rerender.set_defaults(func=cmd_rerender)

    p_fixcopy = sub.add_parser(
        "fixcopy",
        help="apply deterministic copy fixes (e.g. a retired brand name) to an existing page.json -- no model call",
    )
    p_fixcopy.add_argument("run_dir", help="tenants/<t>/out/<run-id>")
    p_fixcopy.add_argument("--page", required=True, help="cartridge name, e.g. article")
    _add_tenant_flag(p_fixcopy)
    p_fixcopy.set_defaults(func=cmd_fixcopy)

    p_doctor = sub.add_parser("doctor", help="check that a tenant can actually run")
    p_doctor.add_argument("--offline", action="store_true", help="skip the models-endpoint check")
    _add_tool_flags(p_doctor)
    _add_tenant_flag(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_images = sub.add_parser("images", help="vision-drafted alt text/tags for a product's asset pool")
    images_sub = p_images.add_subparsers(dest="images_command", required=True)
    p_images_describe = images_sub.add_parser(
        "describe", help="vision-describe a model's not-yet-reviewed asset pool into asset-review.json"
    )
    p_images_describe.add_argument("--model", required=True, help="model slug (a product's name, lowercased/hyphenated)")
    p_images_describe.add_argument("--limit", type=int, help="describe at most N assets")
    p_images_describe.add_argument("--force", action="store_true", help="also re-describe assets that already have alt text")
    p_images_describe.add_argument("--dry-run", action="store_true", help="list what would be described and the estimated cost; makes no calls")
    _add_tenant_flag(p_images_describe)
    p_images_describe.set_defaults(func=cmd_images_describe)
    p_images_brandcheck = images_sub.add_parser(
        "brandcheck", help="flag graphics in the retired brand look (old_brand in asset-review.json)"
    )
    p_images_brandcheck.add_argument("--model", help="check one model's pool only (default: every active product)")
    p_images_brandcheck.add_argument("--dry-run", action="store_true", help="print the result; do not write asset-review.json")
    p_images_brandcheck.add_argument("--cache-dir", action="append",
                                     help="where downloaded images are (repeatable); default <runs>/asset-cache")
    _add_tenant_flag(p_images_brandcheck)
    p_images_brandcheck.set_defaults(func=cmd_images_brandcheck)
    p_images_pool = images_sub.add_parser("pool", help="per-model total/reviewed/excluded/with-alt counts")
    _add_tenant_flag(p_images_pool)
    p_images_pool.set_defaults(func=cmd_images_pool)

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
    p_publish.add_argument(
        "--update",
        action="store_true",
        help="update the page this run already published instead of creating a new one",
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

    p_meta = sub.add_parser("meta", help="pull new ads from the tenant's Meta ad account (read only)")
    meta_sub = p_meta.add_subparsers(dest="meta_command", required=True)
    p_meta_check = meta_sub.add_parser("check", help="check the token and the ad account (one live call each)")
    _add_tenant_flag(p_meta_check)
    p_meta_check.set_defaults(func=cmd_meta_check)
    p_meta_pull = meta_sub.add_parser("pull", help="download new ads into tenants/<t>/meta_inbox/")
    p_meta_pull.add_argument("--since", help="ISO date; default: tenant.yaml meta.ingest_since")
    p_meta_pull.add_argument("--limit", type=int, help="ingest at most N ads this run")
    p_meta_pull.add_argument("--refresh", action="store_true", help="pull ads already in the inbox again")
    p_meta_pull.add_argument("--dry-run", action="store_true", help="list what would be ingested; write nothing")
    _add_tenant_flag(p_meta_pull)
    p_meta_pull.set_defaults(func=cmd_meta_pull)
    p_meta_inbox = meta_sub.add_parser("inbox", help="every inbox item and its state")
    _add_tenant_flag(p_meta_inbox)
    p_meta_inbox.set_defaults(func=cmd_meta_inbox)

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
