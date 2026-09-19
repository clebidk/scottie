"""The 200-generation dry run (a docs/KIMI-LONG-RUN.md follow-up): N
generations through the full pipeline with the test suite's fake client, in
batches of three cartridges per run -- offline, $0 spend, no model key.

After EVERY run the soak records a per-run summary (exit code, gate
attempts/repairs from REVIEW.md's table, the phase-2 deterministic check
results, the estimated cost line); after every batch it rewrites the running
aggregate report -- the after-every-run feedback artifact.

A fine-tune is deliberately NOT part of this loop. Correction 7: no
fine-tune before roughly two hundred HUMAN-scored pages (a dry run's canned
pages carry no signal to train on), and there is no fine-tune code path in
this harness. The dataset export (harness/evals.py, phase 5) is the record a
future fine-tune would consume; `harness score` is how the human-scored
corpus gets built.

Usage:
    python -m evals.soak [--runs 200] [--batch-size 3] [--tenant peak-saunas] \
        [--out evals/soak-report.json] [--keep-runs]
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

from harness import pagechecks
from harness import tenant as tenant_mod
from harness.evals import parse_gate_history

FIXTURES = [
    "founder-warranty-demo.txt",
    "hidden-costs-v2.transcript.txt",
    "price-comparison-v2.transcript.txt",
    "product-features-v2.transcript.txt",
    "transcript-small.txt",
]

_COST_RE = re.compile(r"estimated_cost_usd \(estimate\): \$([\d.]+)")


def _cartridge_set(run_index):
    """Batches of three cartridges per run: the default pool, with the two
    opt-in cartridges rotated in on their own runs (each is a one-page
    cartridge by design)."""
    if run_index % 10 == 4:
        return "listicle"
    if run_index % 10 == 9:
        return "comparison"
    return "article,product-page,longform"


def _listicle_page_for_real_store():
    """tests/test_listicle.py's canned page, adapted to the real tenant:
    the Fuji manifest asset id (the image-allowlist gate, phase 2) and the
    with-lender financing sentence (the tenant configures a lender)."""
    from tests.test_listicle import _listicle_page
    from tests.test_render import FUJI_MANIFEST_ASSET_ID

    page = _listicle_page(7)
    for item in page["reasons"]:
        item["image"] = {"asset_id": FUJI_MANIFEST_ASSET_ID}
    import harness.vocab as vocab_mod

    page["closing"]["financing_line"]["text"] = vocab_mod.allowed_financing_sentence(
        tenant_mod.active().claims_config.get("financing_lender")
    )
    return page


def _canned_pages_for(cartridges):
    from evals.fake_run import _canned_page

    pages = {}
    for c in cartridges:
        if c == "listicle":
            pages[c] = _listicle_page_for_real_store()
        else:
            pages[c] = _canned_page(c)
    return pages


def _run_once_with_pages(input_arg, *, tenant, cartridges, seed, product):
    """evals.fake_run.run_once, but with per-callable canned pages (the soak
    needs a listicle page adapted to the real store). `product` pins the
    grounding: the canned pages are Fuji pages, so every soak run grounds on
    the Fuji slug -- a claim-free brief would otherwise fall to the tenant's
    default product and the canned pages would (correctly) fail the gate."""
    import argparse as _argparse

    from harness import cli
    from evals.fake_run import _brief_for, fake_environment
    from tests.conftest import FakeClient, json_response

    brief = _brief_for(input_arg)
    responses = [json_response(brief)]
    if brief.get("claims_made"):
        responses.append(json_response({}))
    pages = _canned_pages_for([c.strip() for c in cartridges.split(",") if c.strip()])
    responses += [json_response(pages[c]) for c in [x.strip() for x in cartridges.split(",") if x.strip()]]
    client = FakeClient(responses)
    args = _argparse.Namespace(
        input=input_arg, cartridges=cartridges, seed=seed, product=product,
        ffmpeg_bin="/usr/bin/ffmpeg", whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin", tenant=tenant, batch=False,
    )
    with fake_environment(client):
        exit_code = cli.cmd_run(args)
    return exit_code


def _summarize_run(run_dir, tenant):
    """The per-run record: gate history from REVIEW.md, the phase-2 checks
    re-run over each stored page, the cost line."""
    review = (run_dir / "REVIEW.md").read_text()
    gate_history = parse_gate_history(review)
    cost_match = _COST_RE.search(review)
    pages = {}
    for cartridge_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        page_path = cartridge_dir / "page.json"
        if not page_path.exists():
            continue
        page = json.loads(page_path.read_text())
        facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
        checks = {
            "image_allowlist": len(pagechecks.find_image_allowlist_violations(page, facts_pack)),
            "internal_links": len(pagechecks.find_internal_link_violations(page, tenant=tenant)),
        }
        index_path = cartridge_dir / "index.html"
        if index_path.exists():
            html = index_path.read_text()
            checks["html_validity"] = len(pagechecks.find_html_validity_violations(html))
            checks["json_ld"] = len(pagechecks.find_rendered_json_ld_violations(html, cartridge_dir.name))
        pages[cartridge_dir.name] = checks
    return {
        "run_id": run_dir.name,
        "gate_history": gate_history,
        "attempts": sum(len(g["attempts"]) for g in gate_history.values()),
        "repairs": sum(max(0, len(g["attempts"]) - 1) for g in gate_history.values()),
        "check_failures": sum(sum(c.values()) for c in pages.values()),
        "pages": pages,
        "cost_estimate": float(cost_match.group(1)) if cost_match else None,
    }


def _aggregate(runs, batch_size):
    by_exit = {}
    by_fixture = {}
    for r in runs:
        by_exit[r["exit_code"]] = by_exit.get(r["exit_code"], 0) + 1
        by_fixture.setdefault(r["fixture"], {"runs": 0, "pass": 0, "stop": 0, "pages": 0})
        by_fixture[r["fixture"]]["runs"] += 1
        by_fixture[r["fixture"]]["pages"] += len(r["pages"])
        if r["exit_code"] == 0:
            by_fixture[r["fixture"]]["pass"] += 1
        elif r["exit_code"] == 2:
            by_fixture[r["fixture"]]["stop"] += 1
    return {
        "runs": len(runs),
        "pages": sum(len(r["pages"]) for r in runs),
        "batches": -(-len(runs) // batch_size) if runs else 0,
        "batch_size": batch_size,
        "by_exit_code": by_exit,
        "by_fixture": by_fixture,
        "total_attempts": sum(r["attempts"] for r in runs),
        "total_repairs": sum(r["repairs"] for r in runs),
        "total_check_failures": sum(r["check_failures"] for r in runs),
        "total_cost_estimate": round(sum(r["cost_estimate"] or 0 for r in runs), 4),
    }


# A run directory the harness itself creates: <YYYYMMDD-HHMMSS>-<slug>-<suffix>.
_RUN_DIR_RE = re.compile(r"^\d{8}-\d{4,6}-")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--keep-runs", action="store_true", help="keep run dirs (default: delete after capture)")
    ns = parser.parse_args(argv)

    tenant = tenant_mod.load_tenant(ns.tenant, require=True)
    tenant_mod.activate(tenant)
    fixtures = [tenant.fixtures_dir / f for f in FIXTURES if (tenant.fixtures_dir / f).exists()]
    if not fixtures:
        print("no text fixtures found", file=sys.stderr)
        return 1
    out = Path(ns.out) if ns.out else Path("evals") / "soak-report.json"

    runs = []
    started = datetime.datetime.now()
    for i in range(ns.runs):
        fixture = fixtures[i % len(fixtures)]
        cartridges = _cartridge_set(i)
        record = {"i": i, "fixture": fixture.name, "cartridges": cartridges,
                  "attempts": 0, "repairs": 0, "check_failures": 0, "pages": {}, "cost_estimate": None}
        # Only the run directory THIS iteration creates may ever be deleted:
        # snapshot out/ before the run and diff afterwards. The previous
        # `max(out_dir.iterdir(), key=mtime)` picked whatever entry had been
        # touched most recently -- including a reviewer's `_archive-*` folder
        # that had just received hundreds of moved run dirs -- and rmtree'd
        # it (2026-09-19 post-mortem: two archive folders lost this way while
        # the suite still ran against the real tenant paths).
        before = {p for p in tenant.out_dir.iterdir()} if tenant.out_dir.is_dir() else set()
        try:
            exit_code = _run_once_with_pages(
                str(fixture), tenant=ns.tenant, cartridges=cartridges, seed=i,
                product="peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy",
            )
        except Exception as e:
            # a soak of hundreds must complete and REPORT a broken run, not
            # die on it -- the aggregate's exit-code bucket carries it
            record["exit_code"] = "error"
            record["error"] = f"{type(e).__name__}: {e}"
        else:
            record["exit_code"] = exit_code
            if exit_code == 0:
                created = sorted(
                    (p for p in tenant.out_dir.iterdir() if p not in before and p.is_dir() and _RUN_DIR_RE.match(p.name)),
                    key=lambda p: p.stat().st_mtime,
                )
                if not created:
                    record["exit_code"] = "error"
                    record["error"] = "run reported success but no new run directory was found"
                else:
                    run_dir = created[-1]
                    record.update(_summarize_run(run_dir, tenant))
                    if not ns.keep_runs and run_dir.parent == tenant.out_dir and not run_dir.name.startswith("_"):
                        shutil.rmtree(run_dir)
        runs.append(record)
        # the after-every-batch feedback artifact: rewrite the running report
        if (i + 1) % ns.batch_size == 0 or i == ns.runs - 1:
            report = {
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "tenant": tenant.name,
                "aggregate": _aggregate(runs, ns.batch_size),
                "runs": runs,
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2) + "\n")
        if (i + 1) % 25 == 0:
            agg = _aggregate(runs, ns.batch_size)
            print(f"[{i + 1}/{ns.runs}] exit codes {agg['by_exit_code']}, "
                  f"repairs {agg['total_repairs']}, check failures {agg['total_check_failures']}")

    agg = _aggregate(runs, ns.batch_size)
    elapsed = (datetime.datetime.now() - started).total_seconds()
    print(f"\nsoak complete: {agg['runs']} runs, {agg['pages']} pages, "
      f"{agg['batches']} batches of {agg['batch_size']}, {elapsed:.0f}s")
    print(f"exit codes: {agg['by_exit_code']}; repairs: {agg['total_repairs']}; "
          f"check failures: {agg['total_check_failures']}; est. cost: ${agg['total_cost_estimate']:.4f} (fake client)")
    print(f"report: {out}")
    return 0 if agg["by_exit_code"].keys() <= {0} else 1


if __name__ == "__main__":
    sys.exit(main())
