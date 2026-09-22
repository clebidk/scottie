"""Kimi long-run phase 6 (docs/KIMI-LONG-RUN.md): the comparison cartridge
draft. A sourced spec table (a claim id on every cell, gate-enforced), deep
dives on wins, an honest alternative-strengths section, FAQ, verdict box,
one CTA. Own-lineup targets (the tenant's other active products) are always
available; named-competitor targets load only from approved
claims/competitors/*.json files.

COMPARISON_PAGE is the canned writer output the fake-client driver
(evals/fake_run.py) and the end-to-end test share -- it compares the run's
Fuji against the Mini, every cell cited.
"""
import json

import pytest

from harness.claims import ClaimsGateFailure, find_comparison_table_violations, gate_page_json
from harness.ground import LocalFactsSource
from harness.render import render_page
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT, newest_run_dir
from tests.test_render import AD_BRIEF, _filler_paragraphs

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
MINI_SLUG = "peak-saunas-mini-1-person-indoor-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"


def _deep_dive(heading, first_text, claim_ids):
    return {
        "heading": heading,
        "paragraphs": [{"text": first_text, "claim_ids": claim_ids}] + _filler_paragraphs(5),
    }


COMPARISON_PAGE = {
    "eyebrow": "Peak Fuji vs. the Peak Mini",
    "headline": "Fuji vs. Mini: which Peak fits",
    "subhead": "Both are full-spectrum infrared saunas; the difference is size, price, and where they fit in a home.",
    "proof_stats": [
        {"value": "$8,250", "label": "Fuji price, shown up front", "claim_ids": ["price-fuji"]},
        {"value": "Free", "label": "shipping, always", "claim_ids": ["gbrain-allowlist-free-shipping"]},
    ],
    "comparison_table": {
        "columns": ["Spec", "Peak Fuji", "Peak Mini"],
        "rows": [
            {
                "label": "Capacity",
                "cells": [
                    {"text": "2-Person", "claim_ids": ["spec-fuji-capacity"]},
                    {"text": "1-Person", "claim_ids": ["spec-mini-capacity"]},
                ],
            },
            {
                "label": "Price",
                "cells": [
                    {"text": "The Peak Saunas Fuji is priced at $8250.", "claim_ids": ["price-fuji"]},
                    {"text": "The Peak Saunas Mini is priced at $5450.", "claim_ids": ["price-mini"]},
                ],
            },
            {
                "label": "Cabin material",
                "cells": [
                    {"text": "Canadian red cedar", "claim_ids": ["spec-fuji-cabin-material"]},
                    {"text": "Canadian hemlock", "claim_ids": ["spec-mini-cabin-material"]},
                ],
            },
        ],
    },
    "deep_dives": [
        _deep_dive(
            "Where each one fits",
            "The Fuji seats two and is the brand's best-selling cabin; the Mini seats one and fits rooms the Fuji cannot.",
            ["spec-fuji-capacity", "spec-mini-capacity"],
        ),
        _deep_dive(
            "What both cabins share",
            "Both include medical-grade red light therapy standard, and both are built by a US-owned company.",
            ["gbrain-allowlist-red-light", "gbrain-allowlist-us-owned"],
        ),
    ],
    "alternative_strengths": {
        "heading": "Where the Mini is the better pick",
        "strengths": [
            {"text": "The Mini is priced at $5450, well under the Fuji -- a real savings if one seat is enough.", "claim_ids": ["price-mini"]},
            {"text": "The Mini's smaller cabin suits a spare room or a corner where a two-person cabin will not fit.", "claim_ids": ["spec-mini-capacity"]},
        ],
    },
    "faq": {
        "questions": [
            {
                "question": "Is the Mini's heater layout the same as the Fuji's?",
                "answer": "Both cabins use full-spectrum infrared heater placement, with red light therapy included standard on each.",
                "claim_ids": ["gbrain-allowlist-360-full-spectrum", "gbrain-allowlist-red-light"],
            },
            {
                "question": "What does the warranty cover?",
                "answer": "Limited lifetime warranty; full terms by component are published on the warranty page.",
                "claim_ids": ["warranty-terms"],
            },
            {
                "question": "Is shipping included?",
                "answer": "Yes -- shipping is free on both models.",
                "claim_ids": ["gbrain-allowlist-free-shipping"],
            },
        ],
    },
    "verdict": {
        "heading": "The bottom line",
        "paragraphs": [
            {"text": "Pick the Fuji if two people will use it; pick the Mini if one seat and the lower price fit your plans.", "claim_ids": ["price-fuji", "price-mini"]},
        ] + _filler_paragraphs(2),
    },
    "risk_reversal": {
        "text": "Limited lifetime warranty; full terms by component are published on the warranty page.",
        "claim_ids": ["warranty-terms"],
    },
    "images": [],
    "cta_text": "See the models",
    "cta_url": "https://peaksaunas.com/collections/all",
}


# ---------------------------------------------------------------------------
# The gate check: a claim id on every table cell.
# ---------------------------------------------------------------------------

def test_comparison_table_violations_noop_without_a_table():
    assert find_comparison_table_violations({"headline": "x"}) == []
    assert find_comparison_table_violations(COMPARISON_PAGE) == []


def test_comparison_table_violations_catches_a_cell_with_no_claim_id():
    page = json.loads(json.dumps(COMPARISON_PAGE))
    page["comparison_table"]["rows"][0]["cells"][1] = {"text": "1-Person", "claim_ids": []}
    problems = find_comparison_table_violations(page)
    assert len(problems) == 1
    assert problems[0]["path"] == "$.comparison_table.rows[0].cells[1]"
    page["comparison_table"]["rows"][0]["cells"][1] = "1-Person"
    assert len(find_comparison_table_violations(page)) == 1


def test_comparison_page_passes_the_full_gate():
    """The canned page against the real Fuji facts_pack plus the Mini's
    backing claims (what include_comparison adds)."""
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(FUJI_SLUG, AD_BRIEF, include_comparison=True)
    gate_page_json(COMPARISON_PAGE, facts_pack, "comparison", financing_lender="Bread Pay", speaker_pov="first_person", ad_brief=AD_BRIEF)


def test_comparison_page_fails_the_gate_on_an_unsourced_cell():
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(FUJI_SLUG, AD_BRIEF, include_comparison=True)
    page = json.loads(json.dumps(COMPARISON_PAGE))
    page["comparison_table"]["rows"][1]["cells"][1] = {"text": "Free", "claim_ids": []}
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, facts_pack, "comparison", financing_lender="Bread Pay", speaker_pov="first_person", ad_brief=AD_BRIEF)


# ---------------------------------------------------------------------------
# Grounding: comparison targets.
# ---------------------------------------------------------------------------

def test_comparison_targets_are_active_own_products_only():
    source = LocalFactsSource(TENANT.claims_dir)
    pack = source.facts_for(FUJI_SLUG, AD_BRIEF, include_comparison=True)
    targets = pack["comparison_targets"]
    names = [t["name"] for t in targets]
    assert "Mini" in names
    assert "Fuji" not in names  # the run's own product is not its own target
    assert "Crown" not in names  # discontinued models are never targets
    mini = next(t for t in targets if t["name"] == "Mini")
    mini_claim_ids = {cid for row in mini["rows"] for cid in row["claim_ids"]}
    assert "spec-mini-capacity" in mini_claim_ids
    # the backing claims joined the citable universe
    pack_ids = {c["id"] for c in pack["verified_claims"]}
    assert mini_claim_ids <= pack_ids


def test_no_comparison_targets_without_the_flag():
    source = LocalFactsSource(TENANT.claims_dir)
    pack = source.facts_for(FUJI_SLUG, AD_BRIEF)
    assert "comparison_targets" not in pack


def test_pending_competitor_files_never_load(tmp_path):
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    (claims_dir / "products.json").write_text(json.dumps({"products": {}}))
    (claims_dir / "verified.json").write_text("[]")
    competitors = claims_dir / "competitors"
    competitors.mkdir()
    pending = {
        "id": "some-alternative", "name": "Some Alternative", "kind": "competitor", "approved_by": None,
        "rows": [{"id": "cmp-x-y", "label": "Capacity", "text": "2-Person", "source": "https://example.com/x"}],
    }
    (competitors / "pending.json").write_text(json.dumps(pending))
    approved = dict(pending, id="approved-alt", name="Approved Alt", approved_by="Caleb")
    (competitors / "approved.json").write_text(json.dumps(approved))

    source = LocalFactsSource(claims_dir)
    targets, backing = source._comparison_targets({"slug": "anything", "name": "Anything"})
    assert [t["name"] for t in targets] == ["Approved Alt"]
    assert backing[0]["id"] == "cmp-x-y"
    assert backing[0]["category"] == "comparison"


# ---------------------------------------------------------------------------
# Render: blocks compose the page; JSON-LD is FAQPage.
# ---------------------------------------------------------------------------

def _facts_pack_with_comparison():
    return LocalFactsSource(TENANT.claims_dir).facts_for(FUJI_SLUG, AD_BRIEF, include_comparison=True)


def test_comparison_renders_through_blocks(tmp_path):
    out_dir = tmp_path / "comparison"
    index_path = render_page(
        cartridge_name="comparison",
        page=COMPARISON_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=_facts_pack_with_comparison(),
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-11",
        updated="2026-09-11",
        download_assets=False,
    )
    html = index_path.read_text()
    # cycle 55: Peak sets no disclosure_label, so no header label renders
    assert "Advertisement" not in html
    assert '"@type": "FAQPage"' in html
    # the table block rendered the sourced cells
    assert "Peak Mini" in html
    assert "1-Person" in html
    # the verdict box block rendered the shared CTA verbatim a second time
    assert html.count(">See the models<") == 2
    # the faq-accordion block rendered the questions
    assert "heater layout the same as the Fuji" in html
    # block css was inlined for the chosen blocks
    assert ".bk-verdict-box" in html
    assert ".bk-comparison-table" in html


def test_comparison_render_honors_a_block_pick(tmp_path):
    page = dict(COMPARISON_PAGE, blocks={"proof": "proof-stat-row-cards"})
    index_path = render_page(
        cartridge_name="comparison",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=_facts_pack_with_comparison(),
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "comparison",
        published="2026-09-11",
        updated="2026-09-11",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "bk-proof-stat-row-cards" in html
    assert 'class="adv-proof-stats"' not in html


# ---------------------------------------------------------------------------
# End to end: harness run --cartridges comparison with the fake client.
# ---------------------------------------------------------------------------

def test_run_comparison_cartridge_end_to_end(monkeypatch):
    from harness import cli
    from tests.test_cli_run import _base_args, _patch_network

    responses = [
        # test_render's AD_BRIEF predates the required audience field (cycle
        # 16); claims_made is empty, so the semantic matcher makes no call.
        json_response(dict(AD_BRIEF, source_file="founder-warranty-demo.txt", audience="")),
        json_response(COMPARISON_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)

    exit_code = cli.cmd_run(_base_args(input=str(TENANT.fixtures_dir / "founder-warranty-demo.txt"), cartridges="comparison"))
    assert exit_code == 0

    run_dir = newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")
    page = json.loads((run_dir / "comparison" / "page.json").read_text())
    assert page["comparison_table"]["columns"] == ["Spec", "Peak Fuji", "Peak Mini"]
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert "comparison_targets" in facts_pack  # the run selected comparison
    assert (run_dir / "comparison" / "index.html").exists()
