"""Cycle 30: the warm-up window gate (article cartridge only).

docs/BRIEF-2026-09-14-advertorial-playbook.md: the highest-converting
advertorials keep pricing, the buy button, and the brand name out of the
first N words. find_warmup_violations/warmup_first_mentions walk the
article's own reading order (headline -> dek -> open -> body_sections ->
alternatives_section -> how_it_works_section -> turn_section -> close).
"""
import copy

from harness.budget import Budget
from harness.claims import (
    default_warmup_window_words,
    find_warmup_violations,
    warmup_first_mentions,
)
from harness.log import RunLog
from harness.repair import check_page_gates, find_warmup_warning_lines, resolve_warmup_window, write_and_gate_page
from tests.conftest import FakeClient, block_text, json_response
from tests.support import REPO_ROOT, TENANT as REAL_TENANT
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK


class _FakeTenant:
    """Just enough of the Tenant interface find_warmup_violations reads --
    same pattern as tests/test_tenant.py's _FakeModelsTenant. claims_dir
    points at a directory with no products.json, so _tenant_product_short_names
    is a deliberate no-op (product-name coverage is exercised separately
    below, against the real tenant's real claims/products.json)."""

    def __init__(self, name="Acme", short_name=None, config=None, claims_dir=None):
        self.name = name
        self._short_name = short_name
        self._config = config or {}
        self.claims_dir = claims_dir or (REAL_TENANT.claims_dir.parent / "_no_such_dir")

    @property
    def display_name(self):
        return self.name

    def get(self, dotted_key, default=None):
        if dotted_key == "tenant_short_name":
            return self._short_name
        node = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


_HEADLINE = "A curiosity headline about the reader's problem"
_DEK = "One sentence about the reader's situation."
# Reading order counts headline + dek too (schema order: headline, dek,
# open, ...) -- every "word N" test below has to account for this prefix,
# not just the words inside "open".
_PREFIX_WORDS = len(_HEADLINE.split()) + len(_DEK.split())


def _n_filler_words(n, start=0):
    return " ".join(f"filler{i}" for i in range(start, start + n))


def _article_page(*, open_text=None, dek=None, close_text=None, filler_words=0):
    """A minimal article-shaped page.json: just enough keys for
    _article_warmup_strings to walk (headline, dek, open, body_sections,
    alternatives_section, how_it_works_section, turn_section, close)."""
    return {
        "headline": _HEADLINE,
        "dek": dek or _DEK,
        "open": [{"text": open_text or _n_filler_words(filler_words)}],
        "body_sections": [{"heading": "A question", "paragraphs": [{"text": "More filler education text here."}]}],
        "alternatives_section": {"heading": "Alternatives", "paragraphs": [{"text": "Why alternatives fall short."}]},
        "how_it_works_section": {"heading": "How it works", "paragraphs": [{"text": "The mechanism, generically."}]},
        "turn_section": {"heading": "What to look for", "intro": "A few criteria.", "criteria": [{"text": "Criterion one."}]},
        "close": {"paragraphs": [{"text": close_text or "A closing paragraph with nothing notable in it."}]},
    }


# ---------------------------------------------------------------------------
# find_warmup_violations / warmup_first_mentions -- unit level, fake tenant
# ---------------------------------------------------------------------------

def test_brand_inside_window_is_flagged():
    tenant = _FakeTenant(name="Acme")
    # Reading order is headline + dek (_PREFIX_WORDS words), then open --
    # this places "Acme" at word 120 overall.
    page = _article_page(open_text=_n_filler_words(120 - _PREFIX_WORDS - 1) + " Acme")
    violations = find_warmup_violations(page, tenant, window=600)
    assert violations, "brand at word 120 inside a 600-word window must be flagged"
    assert any("brand" in v["issue"] for v in violations)
    mentions = warmup_first_mentions(page, tenant)
    assert mentions["brand_word"] == 120


def test_brand_after_window_passes():
    tenant = _FakeTenant(name="Acme")
    # Places "Acme" at word 700 overall -- outside a 600-word window.
    page = _article_page(open_text=_n_filler_words(700 - _PREFIX_WORDS - 1) + " Acme")
    mentions = warmup_first_mentions(page, tenant)
    assert mentions["brand_word"] == 700
    assert find_warmup_violations(page, tenant, window=600) == []


def test_price_in_dek_is_flagged():
    tenant = _FakeTenant(name="Acme")
    page = _article_page(dek="This one costs $8,250 up front.")
    violations = find_warmup_violations(page, tenant, window=600)
    assert any("price" in v["issue"] for v in violations)


def test_price_outside_window_passes():
    tenant = _FakeTenant(name="Acme")
    page = _article_page(open_text=_n_filler_words(650), close_text="It costs $8,250.")
    mentions = warmup_first_mentions(page, tenant)
    assert mentions["price_word"] is not None and mentions["price_word"] > 600
    assert find_warmup_violations(page, tenant, window=600) == []


def test_cta_text_inside_window_is_flagged():
    tenant = _FakeTenant(name="Acme")
    # cartridges/article/schema.json's allowed_cta_texts includes "See the models".
    page = _article_page(open_text="Right away, see the models before anything else.")
    violations = find_warmup_violations(page, tenant, window=600)
    assert any("CTA" in v["issue"] for v in violations)


def test_no_violation_when_nothing_appears_early():
    tenant = _FakeTenant(name="Acme")
    page = _article_page(open_text=_n_filler_words(50))
    assert find_warmup_violations(page, tenant, window=600) == []


def test_noop_when_page_is_not_article_shaped():
    tenant = _FakeTenant(name="Acme")
    assert find_warmup_violations({"cta_text": "Acme"}, tenant, window=1) == []
    assert find_warmup_violations({}, tenant, window=600) == []


def test_short_name_also_counts_as_brand():
    tenant = _FakeTenant(name="Acme Saunas", short_name="Acme")
    page = _article_page(open_text=_n_filler_words(10) + " Acme")
    violations = find_warmup_violations(page, tenant, window=600)
    assert violations


def test_default_warmup_window_words_matches_schema():
    assert default_warmup_window_words() == 600


# ---------------------------------------------------------------------------
# tenant.yaml override
# ---------------------------------------------------------------------------

def test_resolve_warmup_window_tenant_override_wins():
    tenant = _FakeTenant(config={"cartridges": {"article": {"warmup_window_words": 50}}})
    assert resolve_warmup_window(tenant) == 50


def test_resolve_warmup_window_falls_back_to_schema_default():
    tenant = _FakeTenant(config={})
    assert resolve_warmup_window(tenant) == 600
    assert resolve_warmup_window(tenant, schema_default=600) == 600


def test_real_tenant_yaml_sets_the_cycle_30_warmup_config():
    # tenants/peak-saunas/tenant.yaml -- see [PART 1] of the cycle 30 brief.
    assert REAL_TENANT.get("cartridges.article.warmup_window_words") == 600
    assert REAL_TENANT.get("cartridges.article.warmup_mode") in ("warn", "enforce")


def test_real_tenant_product_short_names_are_covered():
    # tenants/peak-saunas/claims/products.json curates a short_name per
    # product -- _tenant_product_short_names (via warmup_first_mentions)
    # must catch one of those, not just the tenant's own display name.
    from harness.claims import _tenant_product_short_names

    a_product_short_name = _tenant_product_short_names(REAL_TENANT)[0]
    page = _article_page(open_text=_n_filler_words(5) + " the " + a_product_short_name + " is popular")
    violations = find_warmup_violations(page, REAL_TENANT, window=600)
    assert violations
    assert any("brand" in v["issue"] for v in violations)


# ---------------------------------------------------------------------------
# warn vs enforce, wired into check_page_gates / find_soft_check_warnings
# ---------------------------------------------------------------------------

def _gate_problems(page, tenant):
    problems = check_page_gates(
        page, FACTS_PACK, "article",
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
        tenant=tenant,
    )
    # Cycle 32: find_warmup_violations' issue text no longer contains the
    # literal substring "warm-up window" (it now states the exact word
    # budget remaining instead) -- the stable "key" it carries
    # ("warmup:brand"/"warmup:price"/"warmup:cta") is the reliable way to
    # pick these out of check_page_gates' combined problem list now.
    return [p for p in problems if p.get("key", "").startswith("warmup:")]


def test_enforce_mode_is_a_hard_gate_failure():
    tenant = _FakeTenant(name="Acme", config={"cartridges": {"article": {"warmup_mode": "enforce", "warmup_window_words": 600}}})
    page = _article_page(open_text=_n_filler_words(10) + " Acme")
    assert _gate_problems(page, tenant) != []


def test_warn_mode_never_fails_check_page_gates():
    tenant = _FakeTenant(name="Acme", config={"cartridges": {"article": {"warmup_mode": "warn", "warmup_window_words": 600}}})
    page = _article_page(open_text=_n_filler_words(10) + " Acme")
    assert _gate_problems(page, tenant) == []


def test_warn_mode_writes_a_review_md_style_warning_line():
    tenant = _FakeTenant(name="Acme", config={"cartridges": {"article": {"warmup_mode": "warn", "warmup_window_words": 600}}})
    page = _article_page(open_text=_n_filler_words(10) + " Acme")
    lines = find_warmup_warning_lines(page, "article", tenant)
    assert any(line.startswith("Warm-up window: brand appears at word") for line in lines)


def test_enforce_mode_produces_no_warn_line_double_report():
    tenant = _FakeTenant(name="Acme", config={"cartridges": {"article": {"warmup_mode": "enforce", "warmup_window_words": 600}}})
    page = _article_page(open_text=_n_filler_words(10) + " Acme")
    # enforce is a hard gate -- find_warmup_warning_lines (the "warn"-only
    # soft-check path) must stay silent so REVIEW.md never reports the same
    # failure twice under two different headings.
    assert find_warmup_warning_lines(page, "article", tenant) == []


def test_non_article_cartridge_never_gets_a_warmup_warning():
    tenant = _FakeTenant(config={"cartridges": {"article": {"warmup_mode": "warn"}}})
    assert find_warmup_warning_lines({"headline": "x"}, "longform", tenant) == []


# ---------------------------------------------------------------------------
# Cycle 32 (cycle 30 merge note follow-up): find_warmup_violations' stable
# "key" per category ("warmup:brand"/"warmup:price"/"warmup:cta"), and the
# repair-loop memory (harness/repair.py's write_and_gate_page) that dedups
# on it. The real enforce-mode run this fixes STOPped after three repairs
# because two consecutive attempts failed at different word indexes (brand
# at word 465, then 583) -- the old (path, issue) dedup key never recognized
# those as the same violation, since "issue" carried the word index. These
# tests drive write_and_gate_page directly, with a real Anthropic client
# fake standing in for the writer, the same way tests/test_repair_loop.py
# does.
# ---------------------------------------------------------------------------

def _tenant_with_warmup_mode(base_tenant, mode, window=600):
    """A copy of `base_tenant` (same class, same claims_dir/authors/render()
    -- only .config differs) with cartridges.article.warmup_mode/
    warmup_window_words overridden, so the rest of the real Peak Saunas
    tenant config (needed by write_page's prompt building) stays intact."""
    proxy = copy.copy(base_tenant)
    proxy.config = copy.deepcopy(base_tenant.config)
    proxy.config.setdefault("cartridges", {}).setdefault("article", {})
    proxy.config["cartridges"]["article"]["warmup_mode"] = mode
    proxy.config["cartridges"]["article"]["warmup_window_words"] = window
    return proxy


def _article_with_early_brand(n_filler_words):
    """ARTICLE_PAGE (tests/test_render.py -- already schema-valid, in word
    range, allowed CTA) with its brand-free `open` paragraph replaced by one
    that mentions the tenant's brand after `n_filler_words` digit-free filler
    words -- moves ONLY the brand's first-mention word index, so this fails
    the warm-up gate and nothing else."""
    open_text = " ".join(["padding"] * n_filler_words) + " Peak Saunas can help with that."
    return dict(ARTICLE_PAGE, open=[{"text": open_text}])


def test_warmup_repair_memory_dedups_on_key_keeping_latest_detail(tmp_path):
    tenant = _tenant_with_warmup_mode(REAL_TENANT, "enforce")
    attempt1_bad = _article_with_early_brand(50)
    attempt2_bad = _article_with_early_brand(90)
    attempt3_good = ARTICLE_PAGE  # real brand mention is at word 1063 -- outside the window

    word1 = warmup_first_mentions(attempt1_bad, tenant)["brand_word"]
    word2 = warmup_first_mentions(attempt2_bad, tenant)["brand_word"]
    assert word1 != word2
    assert word1 <= 600 and word2 <= 600

    client = FakeClient([json_response(attempt1_bad), json_response(attempt2_bad), json_response(attempt3_good)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, _det_fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
            tenant=tenant,
        )
    finally:
        log.close()

    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 3  # 1 initial + 2 repairs, both warmup failures

    # attempt 2's REVISION REQUIRED block (built from attempt 1's failure
    # alone) states attempt 1's word index/budget.
    second_user_msg = block_text(client.messages.calls[1]["messages"][0]["content"])
    assert f"brand at word {word1};" in second_user_msg
    assert f"you have {600 - word1} words to cut" in second_user_msg

    # attempt 3's REVISION REQUIRED block (built after attempt 2 also
    # failed) carries exactly ONE warm-up-brand entry -- deduped on the
    # stable "warmup:brand" key, not two -- and it states attempt 2's
    # (the latest) word index/budget, not attempt 1's stale one.
    third_user_msg = block_text(client.messages.calls[2]["messages"][0]["content"])
    assert third_user_msg.count("move the first brand mention past word") == 1
    assert f"brand at word {word2};" in third_user_msg
    assert f"you have {600 - word2} words to cut" in third_user_msg
    assert f"brand at word {word1};" not in third_user_msg
