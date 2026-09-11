"""Eval dataset export and the score report (docs/KIMI-LONG-RUN.md phase 5;
landscape borrowing #4 -- the dataset/solver/scorer split applied to the
score sheet; file shapes borrowed, no libraries).

`record_score` owns scores.jsonl writes (moved here from cli.py -- the last
piece of the R2 split; harness/serve.py imports it from here now) and
`load_scores` owns reads. `dataset_records` rebuilds one record per
(run, cartridge) from the artifacts a run already writes -- ad_brief.json,
facts_pack.json, page.json, REVIEW.md's gate table, state.json -- plus the
phase-2 deterministic checks re-run over the stored page.json/index.html,
plus human scores joined from scores.jsonl. No new run artifact is created
(review R26 stands); cartridge and block "versions" are content hashes,
which is the honest version a file-only registry has.
"""
import hashlib
import json
import re
from pathlib import Path

from . import pagechecks
from . import blocks as blocks_mod
from .pipeline import CARTRIDGES_DIR

AXES = ("angle", "brand", "claims", "publish")
PUBLISH_BAR = 4.0  # evals/rubric.md: shippable when mean "would publish" >= 4


# ---------------------------------------------------------------------------
# scores.jsonl -- write path (harness score, the review site) and read path.
# ---------------------------------------------------------------------------

def record_score(tenant, *, run_dir, angle, brand, claims, publish, by=None, note="", page=None):
    """Appends one line to tenants/<t>/evals/scores.jsonl. Shared by
    `harness score` and harness/serve.py's reviewer web app feedback form, so
    both write the exact same schema -- `page` is only added to the entry
    when given, so every score ever written keeps the same shape."""
    import datetime

    scores_path = tenant.evals_path
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "tenant": tenant.name,
        "run_dir": str(run_dir),
        "angle": angle,
        "brand": brand,
        "claims": claims,
        "publish": publish,
        "by": by or (tenant.author("contributor") or {}).get("name") or "operator",
        "note": note or "",
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if page is not None:
        entry["page"] = page
    with open(scores_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def load_scores(tenant):
    """Every scores.jsonl line as a dict. A missing file is an empty history,
    not an error; a malformed line is skipped, not fatal (the file is
    append-only and hand-inspectable -- never edited)."""
    path = tenant.evals_path
    if not path.exists():
        return []
    scores = []
    for line in path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            scores.append(entry)
    return scores


# ---------------------------------------------------------------------------
# Content-hash versions for cartridges and blocks.
# ---------------------------------------------------------------------------

def _content_version(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()[:12]


def cartridge_version(cartridge_name, cartridges_dir=None):
    """A short content hash over the cartridge's four files -- the version a
    page was written against, without a versioning system."""
    cdir = Path(cartridges_dir or CARTRIDGES_DIR) / cartridge_name
    files = [cdir / name for name in ("cartridge.md", "schema.json", "template.html", "rubric.md") if (cdir / name).exists()]
    return _content_version(files)


def block_version(block_name):
    entry, block_dir = blocks_mod.get_block(block_name)
    if block_dir is None:
        return None
    return _content_version(sorted(p for p in block_dir.iterdir() if p.is_file()))


# ---------------------------------------------------------------------------
# dataset export
# ---------------------------------------------------------------------------

_GATE_ROW_RE = re.compile(r"^\| (\S[^|]*) \| (\d+) \| ([^|]*) \| ([^|]*) \| (\w+) \|$")
_ATTEMPT_COUNT_RE = re.compile(r"attempt \d+: (\d+)")


def parse_gate_history(review_md_text):
    """REVIEW.md's gate-history table -> {cartridge: {"attempts": [failures
    per attempt], "deterministic_fixes": [...], "result": "PASS"}}. The table
    is the only place per-attempt gate history is recorded (review R26
    declined a machine-readable run manifest), so the export parses it back
    out of the artifact that already exists."""
    history = {}
    for line in review_md_text.splitlines():
        m = _GATE_ROW_RE.match(line.strip())
        if not m:
            continue
        name, _n, failures_cell, fixes_cell, result = (m.group(1).strip(), m.group(2),
                                                       m.group(3).strip(), m.group(4).strip(), m.group(5).strip())
        if name.lower() == "cartridge":
            continue
        history[name] = {
            "attempts": [int(n) for n in _ATTEMPT_COUNT_RE.findall(failures_cell)],
            "deterministic_fixes": [int(n) for n in _ATTEMPT_COUNT_RE.findall(fixes_cell)],
            "result": result,
        }
    return history


def _facts_pack_summary(facts_pack):
    return {
        "product": (facts_pack.get("product") or {}).get("slug"),
        "verified_claims": len(facts_pack.get("verified_claims") or []),
        "assets": len(facts_pack.get("assets") or []),
        "reviews_claim": bool(facts_pack.get("reviews_summary")),
    }


def _effective_blocks(page, schema):
    """The slot -> block-id map a page actually rendered with: the writer's
    recorded page.json "blocks" picks, filled out with each declared slot's
    default."""
    effective = {}
    for slot, spec in (schema.get("block_slots") or {}).items():
        effective[slot] = spec.get("default")
    for slot, block_id in (page.get("blocks") or {}).items():
        effective[slot] = block_id
    return {slot: bid for slot, bid in effective.items() if bid}


def dataset_records(tenant, *, cartridges_dir=None):
    """One record per (run, cartridge) for every run dir under the tenant's
    out/ that has a page.json. Deterministic check results are re-run over
    the stored artifacts, so old runs export with the same fields as new
    ones."""
    cartridges_dir = cartridges_dir or CARTRIDGES_DIR
    scores = load_scores(tenant)
    records = []
    if not tenant.out_dir.is_dir():
        return records
    for run_dir in sorted(p for p in tenant.out_dir.iterdir() if p.is_dir()):
        ad_brief_path = run_dir / "ad_brief.json"
        facts_pack_path = run_dir / "facts_pack.json"
        if not ad_brief_path.exists() or not facts_pack_path.exists():
            continue
        ad_brief = json.loads(ad_brief_path.read_text())
        facts_pack = json.loads(facts_pack_path.read_text())
        review_path = run_dir / "REVIEW.md"
        gate_history = parse_gate_history(review_path.read_text()) if review_path.exists() else {}
        state = {}
        state_path = run_dir / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())

        for cartridge_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            page_path = cartridge_dir / "page.json"
            if not page_path.exists():
                continue
            cartridge = cartridge_dir.name
            page = json.loads(page_path.read_text())
            schema_path = Path(cartridges_dir) / cartridge / "schema.json"
            schema = json.loads(tenant.render(schema_path.read_text())) if schema_path.exists() else {}
            effective_blocks = _effective_blocks(page, schema)

            check_results = {
                "image_allowlist": pagechecks.find_image_allowlist_violations(page, facts_pack),
                "internal_links": pagechecks.find_internal_link_violations(page, tenant=tenant),
                "blocks": pagechecks.find_block_violations(page, cartridge, schema.get("block_slots")),
            }
            index_path = cartridge_dir / "index.html"
            if index_path.exists():
                html = index_path.read_text()
                check_results["html_validity"] = pagechecks.find_html_validity_violations(html)
                check_results["json_ld"] = pagechecks.find_rendered_json_ld_violations(html, cartridge)
                check_results["rendered_links"] = pagechecks.find_rendered_internal_link_violations(html, tenant=tenant)

            page_scores = [
                {k: s.get(k) for k in ("by", *AXES, "note", "scored_at", "page")}
                for s in scores
                if Path(s.get("run_dir", "")).name == run_dir.name
                and (s.get("page") in (None, cartridge))
            ]
            records.append({
                "run_id": run_dir.name,
                "tenant": tenant.name,
                "cartridge": cartridge,
                "input": ad_brief.get("source_file"),
                "input_type": ad_brief.get("input_type"),
                "angle": ad_brief.get("angle"),
                "state": state.get("state"),
                "ad_brief": ad_brief,
                "facts_pack_summary": _facts_pack_summary(facts_pack),
                "cartridge_version": cartridge_version(cartridge, cartridges_dir),
                "blocks": effective_blocks,
                "block_versions": {b: block_version(b) for b in effective_blocks.values()},
                "page": page,
                "gate_history": gate_history.get(cartridge),
                "check_results": check_results,
                "scores": page_scores,
            })
    return records


def export_dataset(tenant, *, out_path=None, cartridges_dir=None):
    """Write the dataset as JSONL (one record per line) and return the record
    count. Default destination: the tenant's evals/dataset.jsonl."""
    records = dataset_records(tenant, cartridges_dir=cartridges_dir)
    out = Path(out_path) if out_path else tenant.evals_path.parent / "dataset.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return len(records), out


# ---------------------------------------------------------------------------
# eval report -- scores aggregated by cartridge, block, angle, reviewer.
# ---------------------------------------------------------------------------

def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 2) if values else None


def _bucket_report(rows):
    """rows: [(key, score)]. Returns {key: {axis: mean, n, publish_bar_met}}."""
    buckets = {}
    for key, score in rows:
        buckets.setdefault(key, []).append(score)
    out = {}
    for key, scores in sorted(buckets.items()):
        means = {axis: _mean([s.get(axis) for s in scores]) for axis in AXES}
        out[key] = {
            "n": len(scores),
            **means,
            "publish_bar_met": (means["publish"] is not None and means["publish"] >= PUBLISH_BAR),
        }
    return out


def build_report(tenant, *, cartridges_dir=None):
    """The aggregated score report: overall, then by cartridge, block, angle,
    and reviewer. Block and angle joins go through the run's page.json /
    ad_brief.json, so a score recorded against a run that no longer exists on
    disk still counts in the overall and per-reviewer buckets."""
    scores = load_scores(tenant)
    records = dataset_records(tenant, cartridges_dir=cartridges_dir)
    by_run_page = {(r["run_id"], r["cartridge"]): r for r in records}

    by_cartridge, by_block, by_angle, by_reviewer = [], [], [], []
    for s in scores:
        run_id = Path(s.get("run_dir", "")).name
        page = s.get("page")
        record = by_run_page.get((run_id, page)) if page else None
        if record is None and page is None:
            # a run-level score (no page key) counts once per cartridge the
            # run produced
            candidates = [r for (rid, _c), r in by_run_page.items() if rid == run_id]
        else:
            candidates = [record] if record else []

        by_reviewer.append((s.get("by") or "unknown", s))
        if page:
            by_cartridge.append((page, s))
        elif candidates:
            for r in candidates:
                by_cartridge.append((r["cartridge"], s))
        if record is None and not candidates:
            by_cartridge.append((page or "unknown", s))

        angle = (record or (candidates[0] if candidates else {}) or {}).get("angle")
        if angle:
            by_angle.append((angle, s))
        for r in candidates:
            for block_id in (r.get("blocks") or {}).values():
                by_block.append((block_id, s))

    return {
        "tenant": tenant.name,
        "scores_total": len(scores),
        "runs_on_disk": len({r["run_id"] for r in records}),
        "overall": _bucket_report([("all", s) for s in scores]).get("all"),
        "by_cartridge": _bucket_report(by_cartridge),
        "by_block": _bucket_report(by_block),
        "by_angle": _bucket_report(by_angle),
        "by_reviewer": _bucket_report(by_reviewer),
    }


def format_report(report):
    """The report as a plain-text table set. Handles an empty scores file
    cleanly -- there are no human scores in the repo today."""
    lines = [f"# Eval report: {report['tenant']}"]
    if report["scores_total"] == 0:
        lines.append("No human scores recorded yet (evals/scores.jsonl is empty or missing).")
        lines.append(f"Runs on disk exportable via `harness dataset export`: {report['runs_on_disk']}.")
        return "\n".join(lines)

    def _section(title, buckets):
        lines.append("")
        lines.append(f"## {title}")
        if not buckets:
            lines.append("(none)")
            return
        lines.append("| key | n | angle | brand | claims | publish | publish bar (>= 4) |")
        lines.append("|---|---|---|---|---|---|---|")
        for key, b in buckets.items():
            bar = "met" if b["publish_bar_met"] else "not met"
            lines.append(
                f"| {key} | {b['n']} | {b['angle']} | {b['brand']} | {b['claims']} | {b['publish']} | {bar} |"
            )

    overall = report["overall"] or {}
    lines.append(f"Scores: {report['scores_total']} across {report['runs_on_disk']} run(s) on disk.")
    if overall:
        bar = "met" if overall["publish_bar_met"] else "not met"
        lines.append(
            f"Overall: n={overall['n']} angle={overall['angle']} brand={overall['brand']} "
            f"claims={overall['claims']} publish={overall['publish']} (publish bar: {bar})"
        )
    _section("By cartridge", report["by_cartridge"])
    _section("By block", report["by_block"])
    _section("By angle", report["by_angle"])
    _section("By reviewer", report["by_reviewer"])
    return "\n".join(lines)
