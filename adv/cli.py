"""`adv` console entry point: run / ingest / claims add|list / review / score."""
import argparse
import base64
import datetime
import json
import mimetypes
import random
import re
import sys
from pathlib import Path

from . import config
from .anthropic_client import make_client
from .budget import Budget, BudgetExceeded
from .claims import ClaimsGateFailure, collect_claim_ids, gate_ad_brief_claims, gate_page_json
from .ground import LocalFactsSource, load_claims_config
from .ingest import download_drive_file, run_ingest
from .log import RunLog
from .prices import refresh_price_data
from .render import http_fetch_bytes, render_page
from .reviews import fetch_reviews_claim
from .write import parse_word_range, resolve_allowed_cta_texts, write_page

REPO_ROOT = Path(__file__).resolve().parent.parent


def slugify(input_arg):
    name = Path(input_arg).stem or input_arg
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "run"


def make_run_id(slug):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    return f"{ts}-{slug}"


def discover_cartridges():
    cart_dir = REPO_ROOT / "cartridges"
    if not cart_dir.exists():
        return []
    return sorted(p.name for p in cart_dir.iterdir() if p.is_dir() and (p / "cartridge.md").exists())


# ---------------------------------------------------------------------------
# adv run
# ---------------------------------------------------------------------------

# Fix 10: page.json keys that hold structural/reference data, not prose --
# excluded from the main-content word count. cta_url (fix cycle 3 item 4's
# single top-level CTA field) is the flattened equivalent of the old nested
# cta.url -- excluded the same way.
_NON_PROSE_KEYS = {"url", "cta_url", "asset_id", "claim_ids", "claim_id", "id", "sku"}


def _collect_prose_strings(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _NON_PROSE_KEYS:
                continue
            _collect_prose_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_prose_strings(v, out)
    elif isinstance(node, str):
        out.append(node)


def find_emf_urls(facts_pack):
    """Fix cycle 2 item 8: the Shopify handle for Fuji (and other models)
    contains "near-zero-emf" -- pages link to the product URL as-is (that's
    Caleb's call on the Shopify side), but every such URL is logged per run
    so it stays visible in REVIEW.md."""
    urls = []
    product_url = facts_pack.get("product", {}).get("url")
    if product_url and "emf" in product_url.lower():
        urls.append(product_url)
    for claim in facts_pack.get("verified_claims", []):
        source = claim.get("source", "")
        if source.startswith("http") and "emf" in source.lower() and source not in urls:
            urls.append(source)
    return urls


def count_words(page_json):
    """Word count of a page's main content only: every prose string in
    page.json (the byline and disclosure blocks are renderer-injected and
    never appear in page.json, so they're excluded automatically)."""
    strings = []
    _collect_prose_strings(page_json, strings)
    return sum(len(s.split()) for s in strings)


# ---------------------------------------------------------------------------
# Fix cycle 4: writer repair loop. After write_page, run every page-level
# gate check (claims.gate_page_json's checks, plus the two new hard checks
# below); on failure, call the writer again with the original prompt plus a
# "REVISION REQUIRED" block listing each failure verbatim, up to
# MAX_REPAIR_ATTEMPTS times. Ad-claim gate failures (gate_ad_brief_claims, in
# cmd_run) are unaffected -- those still STOP immediately, no retry.
# ---------------------------------------------------------------------------

MAX_REPAIR_ATTEMPTS = 2


# article's CTA lives at page.cta.text (fix cycle 3 item 4 left article's
# single nested cta object alone); longform and product-page have a flat
# top-level cta_text.
def get_cta_text(page_json, cartridge_name):
    if cartridge_name == "article":
        return (page_json.get("cta") or {}).get("text")
    return page_json.get("cta_text")


def find_cta_violation(page_json, cartridge_name, allowed_cta_texts):
    """[] if allowed_cta_texts is empty/None (cartridge has no allowlist) or
    the page's CTA text matches one of the allowed, already-substituted
    options exactly; otherwise one problem dict in the same shape
    claims.gate_page_json's checks use."""
    if not allowed_cta_texts:
        return []
    cta_text = get_cta_text(page_json, cartridge_name)
    if cta_text in allowed_cta_texts:
        return []
    path = "$.cta.text" if cartridge_name == "article" else "$.cta_text"
    return [{
        "path": path,
        "issue": f"CTA text {cta_text!r} is not one of the allowed options: {allowed_cta_texts}",
    }]


def find_word_range_violation(page_json, word_range):
    """[] if word_range is None or the page's word count (count_words) falls
    inside it; otherwise one problem dict."""
    if not word_range:
        return []
    lo, hi = word_range
    wc = count_words(page_json)
    if lo <= wc <= hi:
        return []
    target = lo + (hi - lo) // 2
    if wc < lo:
        detail = f"Expand sections with real substance until you're near {target} words, not just barely over {lo}."
    else:
        detail = f"Trim sections down toward {target} words."
    return [{
        "path": "$.word_count",
        "issue": f"Body is {wc} words; required {lo}-{hi}. {detail}",
    }]


def check_page_gates(page, facts_pack, cartridge_name, *, financing_lender, speaker_pov, word_range, allowed_cta_texts):
    """Every page-level gate check, combined into one list of problem dicts
    (empty if the page passes everything). Never raises -- the repair loop
    decides what to do with the result."""
    problems = []
    try:
        gate_page_json(page, facts_pack, cartridge_name, financing_lender=financing_lender, speaker_pov=speaker_pov)
    except ClaimsGateFailure as e:
        problems += e.items
    problems += find_word_range_violation(page, word_range)
    problems += find_cta_violation(page, cartridge_name, allowed_cta_texts)
    return problems


def _format_gate_failure(item):
    detail = f" (text: {item['text']!r})" if "text" in item else ""
    return f"- {item.get('path', '')}: {item['issue']}{detail}"


def build_revision_note(attempt, failures):
    lines = [
        f"## REVISION REQUIRED (repair attempt {attempt} of {MAX_REPAIR_ATTEMPTS})",
        "Your previous page.json failed the gate checks below. Fix every one of them and "
        "return a complete, corrected page.json in the same schema -- the full page, not a "
        "diff or a patch. Fixing a flagged sentence by rewriting it often introduces a new, "
        "unflagged violation nearby (a different sentence using a forbidden word, or another "
        "unsourced number) -- re-read every sentence you touch, and every sentence next to it, "
        "against the system prompt's forbidden-term and claim_id rules before returning.",
        "",
    ]
    lines += [_format_gate_failure(item) for item in failures]
    return "\n".join(lines)


def write_and_gate_page(*, cartridge_name, cartridges_dir, ad_brief, facts_pack, client, model, budget, log,
                         financing_lender, speaker_pov):
    """write_page, then check_page_gates; on failure, retries write_page with
    a REVISION REQUIRED block up to MAX_REPAIR_ATTEMPTS times. Every attempt
    (initial + repairs) is one write_page call and counts against the run's
    budget like any other model call. Returns (page, attempts) on success,
    where attempts is a list of that attempt's failure list (empty for the
    winning attempt). Raises ClaimsGateFailure (stage page_json:<cartridge>,
    with .attempts set to the same list) if every attempt fails."""
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    cartridge_md = (cartridge_dir / "cartridge.md").read_text()
    schema = json.loads((cartridge_dir / "schema.json").read_text())
    word_range = parse_word_range(cartridge_md)
    allowed_cta_texts = resolve_allowed_cta_texts(schema, facts_pack["product"]["short_name"])

    revision_note = None
    attempts = []
    attempt = 0
    while True:
        attempt += 1
        budget.check()
        page = write_page(
            cartridge_name=cartridge_name,
            cartridges_dir=cartridges_dir,
            ad_brief=ad_brief,
            facts_pack=facts_pack,
            client=client,
            model=model,
            budget=budget,
            log=log,
            word_range=word_range,
            allowed_cta_texts=allowed_cta_texts,
            revision_note=revision_note,
        )
        problems = check_page_gates(
            page, facts_pack, cartridge_name,
            financing_lender=financing_lender, speaker_pov=speaker_pov,
            word_range=word_range, allowed_cta_texts=allowed_cta_texts,
        )
        attempts.append(problems)

        if not problems:
            log.event(f"write.{cartridge_name}", f"gate PASS on attempt {attempt}")
            log.gate_result("PASS", f"page_json:{cartridge_name} attempt={attempt} repairs={attempt - 1}")
            return page, attempts

        log.event(f"write.{cartridge_name}", f"gate FAIL on attempt {attempt}: {problems}")
        if attempt >= MAX_REPAIR_ATTEMPTS + 1:
            err = ClaimsGateFailure(f"page_json:{cartridge_name}", problems)
            err.attempts = attempts
            raise err
        revision_note = build_revision_note(attempt, problems)


def _log_run_result(log, result, gate_log):
    """Fix cycle 4 item 5: `run_result: PASS|STOP attempts=<n> repairs=<m>`,
    summed across every cartridge write_and_gate_page got to before the run
    ended -- attempts/repairs are 0 for a STOP that happened before any
    cartridge was written (e.g. the ad_claims gate)."""
    total_attempts = sum(len(v["attempts"]) for v in gate_log.values())
    total_repairs = sum(max(0, len(v["attempts"]) - 1) for v in gate_log.values())
    log.result(result, total_attempts, total_repairs)


def write_review_md(run_dir, *, ad_brief, facts_pack, product_name, selected, pages, budget, cost, gate_matched, emf_urls=None, gate_log=None):
    lines = [
        f"# REVIEW: {run_dir.name}",
        "",
        f"- Angle: {ad_brief.get('angle', '')}",
        f"- Product: {product_name}",
        f"- Cartridges: {', '.join(selected)}",
        "",
        "## Ad claims matched",
    ]
    for m in gate_matched:
        lines.append(f"- ad claim \"{m['claim']}\" -> verified `{m['matched_claim_id']}` (overlap {m['overlap']})")

    verified_by_id = {c["id"]: c for c in facts_pack["verified_claims"]}
    used_claim_ids = set()
    for page in pages.values():
        used_claim_ids |= collect_claim_ids(page)
    lines.append("")
    lines.append("## Claims used")
    if used_claim_ids:
        for cid in sorted(used_claim_ids):
            c = verified_by_id.get(cid)
            text = c["text"] if c else "(not found in facts_pack.verified_claims)"
            lines.append(f"- `{cid}`: {text}")
    else:
        lines.append("- (no claim_ids referenced by any page)")

    lines.append("")
    lines.append("## Sources")
    for c in facts_pack["verified_claims"]:
        lines.append(f"- `{c['id']}`: {c['text']} ({c['source']})")

    lines.append("")
    lines.append("## Assets used")
    for a in facts_pack.get("assets", []):
        lines.append(f"- `{a['id']}`: {a['url']}")

    lines.append("")
    lines.append("## Pages")
    for name in selected:
        lines.append(f"- {name}/index.html")

    lines.append("")
    lines.append("## Word counts (main content only, excludes disclosure and byline)")
    for name in selected:
        wc = count_words(pages[name])
        lines.append(f"- {name}: {wc} words")

    lines.append("")
    lines.append("## Review checklist")
    if ad_brief.get("speaker_pov") == "first_person":
        lines.append(
            "- First-person attribution: PASS -- the ad speaker's first-person story was "
            "attributed to a customer (or facts_pack.speaker_name), not written in the "
            "author's own first person (gate: claims.find_first_person_violations)."
        )
    else:
        lines.append("- First-person attribution: not applicable (ad_brief.speaker_pov is not first_person).")

    lines.append("")
    lines.append("## EMF handling")
    dropped = ad_brief.get("_dropped_emf_claims") or []
    if dropped:
        lines.append("Dropped from ad_brief during ingest (fix 7):")
        for text in dropped:
            lines.append(f"- dropped EMF claim: {text}")
    else:
        lines.append("- no EMF claims/features were dropped from ad_brief during ingest.")
    if emf_urls:
        lines.append("URLs that still contain \"emf\" (Shopify handle; pages link to it as-is -- fix 8):")
        for url in emf_urls:
            lines.append(f"- {url}")
    else:
        lines.append("- no URL used by this run contains \"emf\".")

    lines.append("")
    lines.append("## Gate history")
    lines.append("(fix cycle 4 item 5 -- attempts include the writer repair loop: attempt 1 is the")
    lines.append("initial write, attempts 2-3 are repairs made from a REVISION REQUIRED prompt.)")
    lines.append("")
    lines.append("| Cartridge | Attempts | Failures per attempt | Result |")
    lines.append("|---|---|---|---|")
    for name in selected:
        attempts = (gate_log or {}).get(name, {}).get("attempts", [[]])
        per_attempt = "; ".join(
            f"attempt {i + 1}: {len(failures)} failure(s)" for i, failures in enumerate(attempts)
        )
        lines.append(f"| {name} | {len(attempts)} | {per_attempt} | PASS |")

    lines.append("")
    lines.append("## Budget use")
    summary = budget.summary()
    for k, v in summary.items():
        lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append(f"## Token totals / estimated cost\n- estimated_cost_usd (estimate): ${cost:.4f}")

    (run_dir / "REVIEW.md").write_text("\n".join(lines) + "\n")


def cmd_run(args):
    client = make_client()
    budget = Budget()
    slug = slugify(args.input)
    run_id = make_run_id(slug)
    run_dir = REPO_ROOT / "out" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog(run_id, REPO_ROOT / "runs" / f"{run_id}.log")

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    log.seed(seed)
    rng = random.Random(seed)

    available = discover_cartridges()
    if args.cartridges:
        selected = [c.strip() for c in args.cartridges.split(",") if c.strip()]
        unknown = [c for c in selected if c not in available]
        if unknown:
            print(f"unknown cartridge(s): {unknown}; available: {available}", file=sys.stderr)
            log.close()
            return 1
    else:
        selected = rng.sample(available, k=min(3, len(available)))
    log.cartridges(selected)

    claims_dir = REPO_ROOT / "claims"
    claims_config = load_claims_config(claims_dir)

    # Fix cycle 4 item 5: cartridge_name -> {"attempts": [failures_per_attempt]}
    # from the writer repair loop below, kept outside the try block so a STOP
    # can still log a run_result line with real attempts/repairs counts for
    # whatever cartridges were processed before the STOP.
    gate_log = {}

    # Fix 2: live prices, at the start of the run, before ingest. Cached for
    # 60 minutes in runs/products-cache.json; falls back to the cache (with a
    # logged warning) on fetch failure.
    _, live_price_claims_by_slug = refresh_price_data(
        products_path=claims_dir / "products.json",
        cache_path=REPO_ROOT / "runs" / "products-cache.json",
        show_compare_at_price=claims_config.get("show_compare_at_price", False),
        today_iso=datetime.date.today().isoformat(),
        log=log,
    )

    try:
        ad_brief = run_ingest(
            input_arg=args.input,
            workdir=run_dir,
            client=client,
            model=config.DEFAULT_MODEL,
            budget=budget,
            log=log,
            ffmpeg_bin=args.ffmpeg_bin,
            whisper_bin=args.whisper_bin,
            whisper_model=args.whisper_model,
        )
        (run_dir / "ad_brief.json").write_text(json.dumps(ad_brief, indent=2))

        budget.check()
        facts_source = LocalFactsSource(claims_dir)

        # Gate against the FULL verified.json universe (with this run's live
        # price claims substituted in) -- an ad claim can reference anything
        # approved, not just the eventual product's curated facts_pack subset.
        gate_matched = gate_ad_brief_claims(
            ad_brief, facts_source.all_verified_claims(live_price_claims_by_slug)
        )
        log.gate_result("PASS", f"{len(gate_matched)} ad claim(s) matched")

        product = facts_source.pick_product(args.product, ad_brief)
        live_price_claim = live_price_claims_by_slug.get(product["slug"])
        reviews_claim = fetch_reviews_claim(product["url"], datetime.date.today().isoformat(), log=log)

        facts_pack = facts_source.facts_for(
            product["slug"],
            ad_brief,
            config=claims_config,
            live_price_claim=live_price_claim,
            reviews_claim=reviews_claim,
        )
        (run_dir / "facts_pack.json").write_text(json.dumps(facts_pack, indent=2))

        # Fix cycle 2 item 8: log a warning for every URL that still contains
        # "emf" (the Shopify handle), so it stays visible in REVIEW.md even
        # though the page is allowed to link to it as-is.
        emf_urls = find_emf_urls(facts_pack)
        for url in emf_urls:
            log.event("run", f"URL contains 'emf': {url}")

        pages = {}
        for cartridge_name in selected:
            budget.check()
            try:
                page, attempts = write_and_gate_page(
                    cartridge_name=cartridge_name,
                    cartridges_dir=REPO_ROOT / "cartridges",
                    ad_brief=ad_brief,
                    facts_pack=facts_pack,
                    client=client,
                    model=config.DEFAULT_MODEL,
                    budget=budget,
                    log=log,
                    financing_lender=claims_config.get("financing_lender"),
                    speaker_pov=ad_brief.get("speaker_pov"),
                )
            except ClaimsGateFailure as e:
                gate_log[cartridge_name] = {"attempts": getattr(e, "attempts", [e.items])}
                raise
            gate_log[cartridge_name] = {"attempts": attempts}
            pages[cartridge_name] = page

        published = updated = datetime.date.today().isoformat()
        outputs = []
        for cartridge_name, page in pages.items():
            index_path = render_page(
                cartridge_name=cartridge_name,
                page=page,
                ad_brief=ad_brief,
                facts_pack=facts_pack,
                cartridges_dir=REPO_ROOT / "cartridges",
                brand_dir=REPO_ROOT / "brand",
                templates_dir=REPO_ROOT / "adv" / "templates",
                out_dir=run_dir / cartridge_name,
                published=published,
                updated=updated,
                log=log,
                fetch_url=http_fetch_bytes,
                drive_downloader=download_drive_file,
            )
            outputs.append(index_path)

    except ClaimsGateFailure as e:
        (run_dir / "unmatched_claims.json").write_text(
            json.dumps({"stage": e.stage, "items": e.items}, indent=2)
        )
        log.gate_result("STOP", f"stage={e.stage} unmatched={len(e.items)}")
        log.event("run", str(e))
        cost = log.cost_estimate()
        log.budget_summary(budget.summary())
        _log_run_result(log, "STOP", gate_log)
        log.close()
        print(f"Claims gate STOPPED at stage {e.stage!r}: {len(e.items)} unmatched item(s).", file=sys.stderr)
        print(json.dumps(e.items, indent=2), file=sys.stderr)
        print(f"See {run_dir / 'unmatched_claims.json'}", file=sys.stderr)
        return 2

    except BudgetExceeded as e:
        log.event("run", f"budget exceeded: {e}")
        log.budget_summary(budget.summary())
        _log_run_result(log, "STOP", gate_log)
        log.close()
        print(f"budget exceeded: {e}", file=sys.stderr)
        return 3

    cost = log.cost_estimate()
    log.budget_summary(budget.summary())
    write_review_md(
        run_dir,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product_name=facts_pack["product"]["name"],
        selected=selected,
        pages=pages,
        budget=budget,
        cost=cost,
        gate_matched=gate_matched,
        emf_urls=emf_urls,
        gate_log=gate_log,
    )
    _log_run_result(log, "PASS", gate_log)
    log.close()

    print(f"Run complete: {run_dir}")
    for p in outputs:
        print(f" - {p}")
    return 0


# ---------------------------------------------------------------------------
# adv ingest
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    client = make_client()
    budget = Budget()
    run_id = make_run_id(slugify(args.input))
    out_dir = REPO_ROOT / "out" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog(run_id, REPO_ROOT / "runs" / f"{run_id}.log")

    try:
        ad_brief = run_ingest(
            input_arg=args.input,
            workdir=out_dir,
            client=client,
            model=config.DEFAULT_MODEL,
            budget=budget,
            log=log,
            ffmpeg_bin=args.ffmpeg_bin,
            whisper_bin=args.whisper_bin,
            whisper_model=args.whisper_model,
        )
    except BudgetExceeded as e:
        log.event("ingest", f"budget exceeded: {e}")
        log.close()
        print(f"budget exceeded: {e}", file=sys.stderr)
        return 3

    (out_dir / "ad_brief.json").write_text(json.dumps(ad_brief, indent=2))
    log.cost_estimate()
    log.close()
    print(json.dumps(ad_brief, indent=2))
    print(f"\nWrote {out_dir / 'ad_brief.json'}")
    return 0


# ---------------------------------------------------------------------------
# adv review (fix cycle 3 item 8): one self-contained review.html per
# cartridge, images inlined as data URIs, for sending to Caleb. Simple regex
# on src="assets/..." -- no HTML parser needed.
# ---------------------------------------------------------------------------

_ASSET_SRC_RE = re.compile(r'src="assets/([^"]+)"')


def inline_assets_as_data_uris(html_text, assets_dir):
    """Replace every `src="assets/<file>"` with a data: URI of that file's
    bytes, read from `assets_dir`. A referenced file that's missing on disk
    is left as-is (better a broken image than a crashed command)."""

    def replace(match):
        filename = match.group(1)
        asset_path = Path(assets_dir) / filename
        if not asset_path.exists():
            return match.group(0)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        b64 = base64.b64encode(asset_path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{b64}"'

    return _ASSET_SRC_RE.sub(replace, html_text)


def cmd_review(args):
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"no such run dir: {run_dir}", file=sys.stderr)
        return 1

    written = []
    for cartridge_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        index_path = cartridge_dir / "index.html"
        if not index_path.exists():
            continue
        html_text = index_path.read_text()
        review_html = inline_assets_as_data_uris(html_text, cartridge_dir / "assets")
        review_path = run_dir / f"{cartridge_dir.name}-review.html"
        review_path.write_text(review_html)
        written.append(review_path)

    if not written:
        print(f"no cartridge output (index.html) found under {run_dir}", file=sys.stderr)
        return 1

    for p in written:
        print(f"Wrote {p}")
    return 0


# ---------------------------------------------------------------------------
# adv claims add / list
# ---------------------------------------------------------------------------

def cmd_claims_add(args):
    verified_path = REPO_ROOT / "claims" / "verified.json"
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
        "approved_by": args.approved_by or "Caleb",
        "date": datetime.date.today().isoformat(),
    }
    verified.append(entry)
    verified_path.write_text(json.dumps(verified, indent=2) + "\n")
    print(f"Added claim {cid!r} to {verified_path}")
    return 0


def cmd_claims_list(args):
    verified_path = REPO_ROOT / "claims" / "verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else []
    for c in verified:
        print(f"{c['id']:32s} [{c['category']:10s}] {c['text']}  ({c['source']})")
    return 0


# ---------------------------------------------------------------------------
# adv score
# ---------------------------------------------------------------------------

def cmd_score(args):
    scores_path = REPO_ROOT / "evals" / "scores.jsonl"
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_dir": args.run_dir,
        "angle": args.angle,
        "brand": args.brand,
        "claims": args.claims,
        "publish": args.publish,
        "by": args.by or "Caleb",
        "note": args.note or "",
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(scores_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Recorded score for {args.run_dir} -> {scores_path}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="adv")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="ingest -> ground -> gate -> write -> render")
    p_run.add_argument("input")
    p_run.add_argument("--cartridges", help="comma-separated cartridge names; default: 3 random of the available set")
    p_run.add_argument("--seed", type=int)
    p_run.add_argument("--product", help="product slug or name; default: inferred from the ad, else products.json default")
    p_run.add_argument("--ffmpeg-bin", default=config.FFMPEG_BIN)
    p_run.add_argument("--whisper-bin", default=config.WHISPER_BIN)
    p_run.add_argument("--whisper-model", default=config.WHISPER_MODEL)
    p_run.set_defaults(func=cmd_run)

    p_ingest = sub.add_parser("ingest", help="ad -> ad_brief.json only, for debugging")
    p_ingest.add_argument("input")
    p_ingest.add_argument("--ffmpeg-bin", default=config.FFMPEG_BIN)
    p_ingest.add_argument("--whisper-bin", default=config.WHISPER_BIN)
    p_ingest.add_argument("--whisper-model", default=config.WHISPER_MODEL)
    p_ingest.set_defaults(func=cmd_ingest)

    p_review = sub.add_parser("review", help="write a self-contained review.html per cartridge (images inlined)")
    p_review.add_argument("run_dir")
    p_review.set_defaults(func=cmd_review)

    p_claims = sub.add_parser("claims", help="manage claims/verified.json")
    claims_sub = p_claims.add_subparsers(dest="claims_command", required=True)

    p_claims_add = claims_sub.add_parser("add")
    p_claims_add.add_argument("text")
    p_claims_add.add_argument("--category", required=True, choices=["spec", "price", "comparison", "health", "trust"])
    p_claims_add.add_argument("--source", required=True)
    p_claims_add.add_argument("--approved-by")
    p_claims_add.set_defaults(func=cmd_claims_add)

    p_claims_list = claims_sub.add_parser("list")
    p_claims_list.set_defaults(func=cmd_claims_list)

    p_score = sub.add_parser("score", help="record a human score for a run into evals/scores.jsonl")
    p_score.add_argument("run_dir")
    p_score.add_argument("--angle", type=int, required=True)
    p_score.add_argument("--brand", type=int, required=True)
    p_score.add_argument("--claims", type=int, required=True)
    p_score.add_argument("--publish", type=int, required=True)
    p_score.add_argument("--by")
    p_score.add_argument("--note")
    p_score.set_defaults(func=cmd_score)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
