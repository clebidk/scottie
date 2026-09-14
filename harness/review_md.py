"""REVIEW.md rendering and the run-result log line.

Review 2026-09-11 R2: lifted out of harness/cli.py. Pure report generation
from run artifacts -- no argparse, no model calls.
"""
from . import tenant as tenant_mod
from .claims import collect_claim_ids, warmup_first_mentions
from .repair import count_words, find_soft_check_warnings


def log_run_result(log, result, gate_log):
    """Fix cycle 4 item 5: `run_result: PASS|STOP attempts=<n> repairs=<m>`,
    summed across every cartridge write_and_gate_page got to before the run
    ended -- attempts/repairs are 0 for a STOP that happened before any
    cartridge was written (e.g. the ad_claims gate)."""
    total_attempts = sum(len(v["attempts"]) for v in gate_log.values())
    total_repairs = sum(max(0, len(v["attempts"]) - 1) for v in gate_log.values())
    log.result(result, total_attempts, total_repairs)


def write_review_md(run_dir, *, ad_brief, facts_pack, product_name, selected, pages, budget, cost, gate_matched, forbidden_urls=None, gate_log=None, product_warning=None, ad_not_repeated=None, ad_alternative_claims=None):
    forbidden_urls = forbidden_urls or []
    lines = [
        f"# REVIEW: {run_dir.name}",
        "",
        f"- Angle: {ad_brief.get('angle', '')}",
        f"- Product: {product_name}",
        f"- Cartridges: {', '.join(selected)}",
        "",
    ]
    # Fix cycle 8 problem 1b: no model name was found in the ad, so the run
    # defaulted -- surface that prominently, it's the kind of thing an
    # operator needs to catch before a page ships grounded on the wrong SKU.
    if product_warning:
        lines.append(f"**WARNING: {product_warning}**")
        lines.append("")
    # Fix cycle 12 item 3: only ever present under ad_overclaim_policy "warn"
    # -- under "stop" any unmatched/overclaimed claim raises ClaimsGateFailure
    # before this function is ever called, so this run never reaches here
    # with one. The page itself was already written with instructions never
    # to repeat these; this is the record that the ad itself still needs a
    # correction. Broadened from fix cycle 10's "AD OVERCLAIMS" (locked-topic
    # only) to cover every unmatched-or-overclaimed claim, plain or locked.
    if ad_not_repeated:
        lines.append("**AD CLAIMS NOT REPEATED ON PAGE — ad needs fixing**")
        for item in ad_not_repeated:
            lines.append(f"- {item['message']}")
        lines.append("")
    # Fix cycle 12 item 3: a claim about the alternative/comparison option
    # (claims.classify_ad_claim_about) never goes through matching at all --
    # listed here purely for visibility, under either policy.
    if ad_alternative_claims:
        lines.append("**Ad statements about alternatives (not repeated)**")
        for item in ad_alternative_claims:
            lines.append(f'- "{item["claim"]}"')
        lines.append("")
    lines.append("## Ad claims matched")
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

    # Fix cycle 16: soft checks (headline formula, proof-inside-section,
    # audience-in-headline) -- advisory only, never a gate failure.
    # Cycle 30: reported unconditionally for every article page, regardless
    # of cartridges.article.warmup_mode (warn/enforce only change whether a
    # too-early mention is *flagged* -- this is a plain factual index).
    article_page = pages.get("article")
    lines.append("")
    lines.append("## Warm-up window (article)")
    if article_page:
        mentions = warmup_first_mentions(article_page, tenant_mod.active())
        for label, key in (("brand mention", "brand_word"), ("price", "price_word"), ("CTA", "cta_word")):
            value = mentions[key]
            lines.append(f"- first {label}: word {value}" if value is not None else f"- first {label}: none")
    else:
        lines.append("- not applicable (no article page in this run)")

    lines.append("")
    lines.append("## Soft-check warnings (non-blocking)")
    soft_warnings = find_soft_check_warnings(pages, ad_brief)
    if soft_warnings:
        for w in soft_warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- none")

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
    lines.append("## Banned-topic handling")
    dropped = ad_brief.get("_dropped_emf_claims") or []
    if dropped:
        lines.append("Dropped from ad_brief during ingest:")
        for text in dropped:
            lines.append(f"- dropped claim: {text}")
    else:
        lines.append("- no claims/features were dropped from ad_brief during ingest.")
    if forbidden_urls:
        lines.append("URLs that still contain a banned term (storefront handle; pages link to it as-is):")
        for url in forbidden_urls:
            lines.append(f"- {url}")
    else:
        lines.append("- no URL used by this run contains a banned term.")

    lines.append("")
    lines.append("## Gate history")
    lines.append("(fix cycle 4 item 5 -- attempts include the writer repair loop: attempt 1 is the")
    lines.append("initial write, attempts 2-3 are repairs made from a REVISION REQUIRED prompt.")
    lines.append("Fix cycle 6 item 5 -- \"deterministic fixes\" is how many fields the no-model-call")
    lines.append("pre-repair pass fixed on that attempt, before the gate was re-run.)")
    lines.append("")
    lines.append("| Cartridge | Attempts | Failures per attempt | Deterministic fixes | Result |")
    lines.append("|---|---|---|---|---|")
    for name in selected:
        entry = (gate_log or {}).get(name, {})
        attempts = entry.get("attempts", [[]])
        det_fixes = entry.get("deterministic_fixes", [0] * len(attempts))
        per_attempt = "; ".join(
            f"attempt {i + 1}: {len(failures)} failure(s)" for i, failures in enumerate(attempts)
        )
        det_str = "; ".join(f"attempt {i + 1}: {n}" for i, n in enumerate(det_fixes)) or "0"
        lines.append(f"| {name} | {len(attempts)} | {per_attempt} | {det_str} | PASS |")

    lines.append("")
    lines.append("## Budget use")
    summary = budget.summary()
    for k, v in summary.items():
        lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append(f"## Token totals / estimated cost\n- estimated_cost_usd (estimate): ${cost:.4f}")

    (run_dir / "REVIEW.md").write_text("\n".join(lines) + "\n")