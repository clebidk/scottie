"""Fix cycle 16: soft checks (harness/repair.py since the R2 split). Every
one of these is a REVIEW.md advisory line only -- never a gate failure,
never a repair-loop trigger, and none of them ever raise."""
from harness.repair import (
    find_audience_headline_warning,
    find_headline_word_count_warning,
    find_missing_section_proof_warnings,
    find_soft_check_warnings,
)
from harness.review_md import write_review_md


# ---------------------------------------------------------------------------
# Design note 1 (item A1): article's headline formula, 8-14 words.
# ---------------------------------------------------------------------------


def test_find_headline_word_count_warning_flags_a_short_headline():
    page = {"headline": "The pricing problem"}
    warning = find_headline_word_count_warning(page, "article")
    assert warning is not None
    assert "3 word" in warning


def test_find_headline_word_count_warning_passes_a_headline_in_range():
    page = {"headline": "Why the checkout page decides more than the price"}
    assert find_headline_word_count_warning(page, "article") is None


def test_find_headline_word_count_warning_noop_for_a_non_article_cartridge():
    page = {"hero": {"headline": "See pricing"}}
    assert find_headline_word_count_warning(page, "longform") is None


# ---------------------------------------------------------------------------
# Swipe-file item 6: proof inside each reason/section.
# ---------------------------------------------------------------------------


def test_find_missing_section_proof_warnings_flags_a_section_with_no_proof():
    page = {"body_sections": [{"heading": "x", "paragraphs": [{"text": "Unsupported opinion only."}]}]}
    warnings = find_missing_section_proof_warnings(page, "article")
    assert len(warnings) == 1
    assert "body_sections[0]" in warnings[0]


def test_find_missing_section_proof_warnings_passes_with_a_claim_id():
    page = {"body_sections": [{"heading": "x", "paragraphs": [{"text": "y", "claim_ids": ["warranty-terms"]}]}]}
    assert find_missing_section_proof_warnings(page, "article") == []


def test_find_missing_section_proof_warnings_passes_with_attribution():
    page = {"body_sections": [{"heading": "x", "paragraphs": [{"text": "y", "attributed_to_customer": True}]}]}
    assert find_missing_section_proof_warnings(page, "article") == []


def test_find_missing_section_proof_warnings_covers_listicle_reasons():
    page = {"reasons": [{"number": 1, "text": "No proof here."}]}
    warnings = find_missing_section_proof_warnings(page, "listicle")
    assert len(warnings) == 1
    assert "reasons[0]" in warnings[0]


def test_find_missing_section_proof_warnings_noop_for_an_unlisted_cartridge():
    page = {"proof_bullets": [{"text": "no claim_ids here"}]}
    assert find_missing_section_proof_warnings(page, "product-page") == []


# ---------------------------------------------------------------------------
# Swipe-file item 7: audience named in the H1.
# ---------------------------------------------------------------------------


def test_find_audience_headline_warning_flags_a_missing_audience():
    page = {"headline": "5 Ways to Cut Sauna Shopping Time in Half"}
    ad_brief = {"audience": "busy parents"}
    warning = find_audience_headline_warning(page, "article", ad_brief)
    assert warning is not None
    assert "busy parents" in warning


def test_find_audience_headline_warning_passes_when_named():
    page = {"headline": "5 Reasons Busy Parents Are Finally Switching Their Sauna Brand"}
    ad_brief = {"audience": "busy parents"}
    assert find_audience_headline_warning(page, "article", ad_brief) is None


def test_find_audience_headline_warning_noop_when_ad_brief_names_no_audience():
    page = {"headline": "5 Ways to Cut Sauna Shopping Time in Half"}
    ad_brief = {"audience": ""}
    assert find_audience_headline_warning(page, "article", ad_brief) is None


def test_find_audience_headline_warning_noop_for_a_cartridge_with_no_plain_headline():
    page = {"hero": {"headline": "5 Ways to Cut Sauna Shopping Time in Half"}}
    ad_brief = {"audience": "busy parents"}
    assert find_audience_headline_warning(page, "longform", ad_brief) is None


# ---------------------------------------------------------------------------
# find_soft_check_warnings aggregation + REVIEW.md wiring.
# ---------------------------------------------------------------------------


def test_find_soft_check_warnings_aggregates_across_pages():
    pages = {
        "article": {
            "headline": "Bad",
            "body_sections": [{"heading": "x", "paragraphs": [{"text": "no proof"}]}],
        },
    }
    ad_brief = {"audience": "busy parents"}
    warnings = find_soft_check_warnings(pages, ad_brief)
    assert any("headline" in w for w in warnings)
    assert any("body_sections[0]" in w for w in warnings)
    assert any("audience" in w for w in warnings)


def test_find_soft_check_warnings_empty_when_everything_passes():
    pages = {
        "article": {
            "headline": "5 Reasons Busy Parents Are Finally Switching Their Sauna Brand",
            "body_sections": [{"heading": "x", "paragraphs": [{"text": "y", "claim_ids": ["warranty-terms"]}]}],
        },
    }
    ad_brief = {"audience": "busy parents"}
    assert find_soft_check_warnings(pages, ad_brief) == []


def test_write_review_md_includes_a_soft_check_warnings_section(tmp_path):
    ad_brief = {"angle": "a", "audience": "busy parents", "speaker_pov": "brand"}
    facts_pack = {"verified_claims": [], "assets": []}
    pages = {
        "article": {
            "headline": "Bad",
            "body_sections": [{"heading": "x", "paragraphs": [{"text": "no proof"}]}],
        },
    }

    class _FakeBudget:
        def summary(self):
            return {}

    write_review_md(
        tmp_path,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product_name="Fuji",
        selected=["article"],
        pages=pages,
        budget=_FakeBudget(),
        cost=0.0,
        gate_matched=[],
    )
    text = (tmp_path / "REVIEW.md").read_text()
    assert "## Soft-check warnings (non-blocking)" in text
    assert "headline" in text


def test_write_review_md_soft_check_section_says_none_when_clean(tmp_path):
    ad_brief = {"angle": "a", "audience": "", "speaker_pov": "brand"}
    facts_pack = {"verified_claims": [], "assets": []}
    pages = {
        "article": {
            "headline": "5 Reasons Busy Parents Are Finally Switching Their Sauna Brand",
            "body_sections": [{"heading": "x", "paragraphs": [{"text": "y", "claim_ids": ["warranty-terms"]}]}],
        },
    }

    class _FakeBudget:
        def summary(self):
            return {}

    write_review_md(
        tmp_path,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product_name="Fuji",
        selected=["article"],
        pages=pages,
        budget=_FakeBudget(),
        cost=0.0,
        gate_matched=[],
    )
    text = (tmp_path / "REVIEW.md").read_text()
    section = text.split("## Soft-check warnings (non-blocking)")[1].split("## Review checklist")[0]
    assert "none" in section


# ---------------------------------------------------------------------------
# Cycle 30: the warm-up window report is unconditional (word index of first
# brand mention/price/CTA), independent of cartridges.article.warmup_mode.
# ---------------------------------------------------------------------------

def test_write_review_md_reports_warmup_window_word_indices_for_article(tmp_path):
    ad_brief = {"angle": "a", "audience": "", "speaker_pov": "brand"}
    facts_pack = {"verified_claims": [], "assets": []}
    pages = {
        "article": {
            "headline": "Why the checkout page decides more than the price",
            "dek": "A look at trust.",
            "open": [{"text": "Peak Saunas is mentioned right away, priced at $8,250, see the models today."}],
        },
    }

    class _FakeBudget:
        def summary(self):
            return {}

    write_review_md(
        tmp_path, ad_brief=ad_brief, facts_pack=facts_pack, product_name="Fuji",
        selected=["article"], pages=pages, budget=_FakeBudget(), cost=0.0, gate_matched=[],
    )
    text = (tmp_path / "REVIEW.md").read_text()
    section = text.split("## Warm-up window (article)")[1].split("## Soft-check warnings")[0]
    assert "first brand mention: word" in section
    assert "first price: word" in section
    assert "first CTA: word" in section


def test_write_review_md_warmup_window_section_is_not_applicable_without_an_article_page(tmp_path):
    ad_brief = {"angle": "a", "audience": "", "speaker_pov": "brand"}
    facts_pack = {"verified_claims": [], "assets": []}
    pages = {"longform": {"headline": "x"}}

    class _FakeBudget:
        def summary(self):
            return {}

    write_review_md(
        tmp_path, ad_brief=ad_brief, facts_pack=facts_pack, product_name="Fuji",
        selected=["longform"], pages=pages, budget=_FakeBudget(), cost=0.0, gate_matched=[],
    )
    text = (tmp_path / "REVIEW.md").read_text()
    section = text.split("## Warm-up window (article)")[1].split("## Soft-check warnings")[0]
    assert "not applicable" in section
