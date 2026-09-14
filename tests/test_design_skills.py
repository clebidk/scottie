"""The design-skills pack: a separate harness component
(elayadesign/ai-design-skills, vendored) plus the improvements wired into
the existing gate / blocks / CLI.

Hard checks have the phase-2 discrimination shape (known-good passes,
planted-bad fails). Soft checks are advisory. The adapter's
take/adapt/decline table is loadable and the CLI prints it.
"""
import json

from harness import cli
from harness import pagechecks
from harness.blocks import gate as block_gate
from harness.design_skills import (
    LANDING_CARTRIDGES,
    LANDING_PAGE_SKILL,
    adapter,
    load_rules,
    load_source,
    skill_names,
)
from harness.design_skills import gate as design_gate
from harness.render import render_page
from harness.repair import check_page_gates, find_soft_check_warnings
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, LONGFORM_PAGE
from tests.test_tenant import ENGINE_TENANT_WORDS
from tests.support import REPO_ROOT

ALL_TENANT_WORDS = tuple(set(ENGINE_TENANT_WORDS))


def _check(page, cartridge_name="article"):
    return check_page_gates(
        page, FACTS_PACK, cartridge_name,
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
    )


# ---------------------------------------------------------------------------
# The pack itself
# ---------------------------------------------------------------------------

def test_pack_ships_the_upstream_skill_and_its_license():
    source = load_source()
    assert source["repo"] == "https://github.com/elayadesign/ai-design-skills"
    assert source["license"] == "MIT"
    assert LANDING_PAGE_SKILL.is_file()
    text = LANDING_PAGE_SKILL.read_text()
    assert "PART A" in text and "PART B" in text
    assert "one offer → one audience → one primary action" in text
    assert (REPO_ROOT / "harness" / "design_skills" / "LICENSE").is_file()
    assert skill_names() == ["landing-page-design"]


def test_every_rule_has_a_take_adapt_or_decline_decision():
    data = load_rules()
    assert data["rules"]
    for rule in data["rules"]:
        assert rule["action"] in ("take", "adapt", "decline"), rule["id"]
        assert rule["id"] and rule["title"] and rule["harness"]
    counts = adapter.counts()
    assert counts["take"] >= 4
    assert counts["decline"] >= 4
    assert counts["adapt"] >= 4


def test_declined_rules_include_the_conflicts_with_the_block_gate():
    declined = {r["id"] for r in adapter.rules(action="decline")}
    assert "B1-fonts" in declined
    assert "B1-no-hyphens" in declined
    assert "B5-hero-680" in declined
    assert "B4-dark-hex" in declined
    taken = {r["id"] for r in adapter.rules(action="take")}
    assert "B8-filler" in taken
    assert "B8-cliches" in taken
    assert "B9-dead-hash" in taken


# ---------------------------------------------------------------------------
# Hard checks: discrimination
# ---------------------------------------------------------------------------

def test_known_good_pages_pass_the_design_skill_hard_checks():
    assert design_gate.find_filler_copy_violations(ARTICLE_PAGE) == []
    assert design_gate.find_ai_cliche_violations(ARTICLE_PAGE) == []
    assert design_gate.find_dead_link_violations(ARTICLE_PAGE) == []
    assert pagechecks.find_design_skill_violations(ARTICLE_PAGE) == []
    assert pagechecks.find_design_skill_violations(LONGFORM_PAGE) == []


def test_filler_copy_is_a_hard_fail():
    page = {"headline": "Lorem ipsum dolor sit amet, the rest is real"}
    problems = design_gate.find_filler_copy_violations(page)
    assert len(problems) == 1
    assert "lorem ipsum" in problems[0]["issue"]
    page = {"body": "A quote from John Doe about the product."}
    assert design_gate.find_filler_copy_violations(page)
    page = {"body": "Acme Corp ships overnight."}
    assert design_gate.find_filler_copy_violations(page)


def test_acme_alone_is_not_filler():
    """The suite uses Acme as a fixture tenant name; only 'Acme Corp' is the tell."""
    assert design_gate.find_filler_copy_violations({"headline": "Why Acme buyers wait"}) == []


def test_ai_cliche_is_a_hard_fail():
    page = {"headline": "A seamless checkout for busy households"}
    problems = design_gate.find_ai_cliche_violations(page)
    assert len(problems) == 1
    assert "seamless" in problems[0]["issue"]
    page = {"text": "In the world of backyard wellness, wait times matter."}
    assert design_gate.find_ai_cliche_violations(page)


def test_overlapping_tenant_hype_words_are_not_double_gated_here():
    """elevate / unleash / game-changer stay with vocab + synonym repair."""
    page = {"headline": "This will elevate your evenings"}
    assert design_gate.find_ai_cliche_violations(page) == []


def test_dead_hash_link_is_a_hard_fail_bare_hash_only():
    assert design_gate.find_dead_link_violations({"cta_url": "#"})
    assert design_gate.find_dead_link_violations({"cta": {"text": "x", "url": "javascript:void(0)"}})
    assert design_gate.find_dead_link_violations({"cta_url": "#faq"}) == []
    assert design_gate.find_dead_link_violations({"cta_url": "/products/x"}) == []


def test_hard_checks_are_wired_into_the_repair_loop_gate():
    planted = json.loads(json.dumps(ARTICLE_PAGE))
    planted["headline"] = "A seamless way to stop guessing"
    problems = _check(planted, "article")
    assert any("seamless" in p["issue"] for p in problems)


# ---------------------------------------------------------------------------
# Soft checks
# ---------------------------------------------------------------------------

def test_generic_cta_is_a_soft_warning():
    page = {"cta_text": "Learn more", "cta_url": "/x"}
    warnings = design_gate.find_generic_cta_warnings(page, "article")
    assert warnings and "Learn more" in warnings[0]


def test_tagline_warning_only_on_landing_cartridges():
    page = {"headline": "x"}
    assert design_gate.find_tagline_warnings(page, "article") == []
    warnings = design_gate.find_tagline_warnings(page, "longform")
    assert warnings and "tagline" in warnings[0]
    assert "longform" in LANDING_CARTRIDGES
    page_ok = {"tagline": {"lines": ["Heat that reaches the seats.", "Without a sales call."]}}
    assert design_gate.find_tagline_warnings(page_ok, "longform") == []
    page_short = {"tagline": {"lines": ["Only one line"]}}
    assert design_gate.find_tagline_warnings(page_short, "longform")


def test_soft_checks_surface_through_find_soft_check_warnings():
    pages = {"longform": {"hero": {"headline": "A long enough headline for this page"}}}
    warnings = find_soft_check_warnings(pages, {"audience": ""})
    assert any("tagline" in w for w in warnings)


# ---------------------------------------------------------------------------
# Blocks + render
# ---------------------------------------------------------------------------

def test_new_blocks_pass_the_block_gate():
    problems = block_gate.validate_registry(tenant_words=ALL_TENANT_WORDS, min_blocks=20, max_blocks=30)
    assert problems == [], "block gate failures:\n" + "\n".join(problems)
    from harness import blocks
    names = blocks.block_names()
    assert "tagline-reveal" in names
    assert "risk-reversal" in names


def test_longform_renders_a_tagline_through_the_block(tmp_path):
    page = json.loads(json.dumps(LONGFORM_PAGE))
    page["tagline"] = {"lines": ["Heat that reaches the seats.", "Without a sales call."]}
    index_path = render_page(
        cartridge_name="longform",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "longform",
        published="2026-09-14",
        updated="2026-09-14",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "bk-tagline-reveal" in html
    assert "Heat that reaches the seats." in html
    assert ".bk-tagline-reveal" in html  # css inlined


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_design_skills_list_and_explain(capsys):
    assert cli.main(["design-skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "landing-page-design" in out
    assert "take=" in out
    assert cli.main(["design-skills", "explain", "--action", "decline"]) == 0
    out = capsys.readouterr().out
    assert "B1-fonts" in out
    assert "B5-hero-680" in out


def test_cli_design_skills_check_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(ARTICLE_PAGE))
    assert cli.main(["design-skills", "check", str(good), "--cartridge", "article"]) == 0
    assert "ok" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"headline": "Lorem ipsum from SmartFlow"}))
    assert cli.main(["design-skills", "check", str(bad)]) == 2
    assert "FAIL" in capsys.readouterr().out
