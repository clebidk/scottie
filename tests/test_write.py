from pathlib import Path

from harness.budget import Budget
from harness.log import RunLog
import json

from harness.vocab import (
    ALLOWED_FINANCING_SENTENCE_NO_LENDER,
    ALLOWED_WARRANTY_SENTENCE,
    ALWAYS_FORBIDDEN_TERMS,
)
from tests.support import REPO_ROOT, TENANT
from harness.write import load_exemplars, resolve_allowed_cta_texts, write_page
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, AD_BRIEF, FACTS_PACK

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_exemplars_reads_markdown_reference_articles():
    exemplars = load_exemplars(TENANT.exemplars_dir("article"))
    assert len(exemplars) == 2
    assert all("reference_article" in e for e in exemplars)
    assert "Peak Saunas" in exemplars[0]["reference_article"]


def test_load_exemplars_missing_dir_returns_empty(tmp_path):
    assert load_exemplars(tmp_path / "no-such-exemplars-dir") == []


# ---------------------------------------------------------------------------
# Fix cycle 12 item 1: exemplars are the largest piece of a writer call's
# prompt (one real exemplar, cartridges/article/exemplars/
# best-sauna-brands-2026.md, is 5,600+ words on its own) -- trimmed to the
# first 700 words each, at most 2 exemplars (the pre-existing `limit`
# default, unchanged), never on a repair call (test_write_page_omits_
# exemplars_on_a_repair_attempt above, unchanged).
# ---------------------------------------------------------------------------

def test_load_exemplars_trims_each_reference_article_to_700_words():
    exemplars = load_exemplars(TENANT.exemplars_dir("article"))
    assert len(exemplars) == 2
    for e in exemplars:
        assert len(e["reference_article"].split()) <= 700


def test_load_exemplars_trims_a_short_file_not_at_all(tmp_path):
    ex_dir = tmp_path / "exemplars"
    ex_dir.mkdir(parents=True)
    short_text = "word " * 50
    (ex_dir / "short.md").write_text(short_text)
    exemplars = load_exemplars(ex_dir)
    assert len(exemplars) == 1
    assert exemplars[0]["reference_article"] == short_text


def test_load_exemplars_trims_a_long_file_to_exactly_700_words(tmp_path):
    ex_dir = tmp_path / "exemplars"
    ex_dir.mkdir(parents=True)
    long_text = " ".join(f"word{i}" for i in range(2000))
    (ex_dir / "long.md").write_text(long_text)
    exemplars = load_exemplars(ex_dir)
    assert len(exemplars) == 1
    trimmed_words = exemplars[0]["reference_article"].split()
    assert len(trimmed_words) == 700
    assert trimmed_words == [f"word{i}" for i in range(700)]


def test_load_exemplars_still_caps_at_2_files(tmp_path):
    ex_dir = tmp_path / "exemplars"
    ex_dir.mkdir(parents=True)
    for name in ("a.md", "b.md", "c.md"):
        (ex_dir / name).write_text("short reference text")
    exemplars = load_exemplars(ex_dir)
    assert len(exemplars) == 2


# ---------------------------------------------------------------------------
# Fix cycle 12 item 1: "Log prompt token size per call."
# ---------------------------------------------------------------------------

def test_write_page_logs_an_approximate_prompt_size(tmp_path):
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log_path = tmp_path / "run.log"
    log = RunLog("test-run", log_path)
    write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    log_text = log_path.read_text()
    assert "prompt size:" in log_text
    assert "tokens (estimate," in log_text


def test_write_page_valid_on_first_try(tmp_path):
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    page = write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 1
    # exemplars for article should have made it into the user message
    sent_user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "reference_article" in sent_user_msg


def test_write_page_omits_exemplars_on_a_repair_attempt(tmp_path):
    # Fix cycle 4: a revision_note means this is a repair attempt, which
    # already saw the exemplars once (on the initial attempt) -- resending
    # them just burns budget without changing what the writer needs to fix.
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    page = write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
        revision_note="## REVISION REQUIRED (repair attempt 1 of 2)\n- some failure",
    )
    log.close()
    assert page == ARTICLE_PAGE
    sent_user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "reference_article" not in sent_user_msg
    assert "REVISION REQUIRED" in sent_user_msg


def test_write_page_retries_once_on_bad_json(tmp_path):
    client = FakeClient(["not json", json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    page = write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2


# ---------------------------------------------------------------------------
# Fix cycle 6 item 3/4: the forbidden-word list sits at the very top of the
# system prompt, verbatim; the financing prompt states the one sentence
# that's allowed when no lender is configured.
# ---------------------------------------------------------------------------

def test_write_page_puts_forbidden_word_list_at_top_of_system_prompt(tmp_path):
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    system = client.messages.calls[0]["system"]
    for word in ALWAYS_FORBIDDEN_TERMS:
        assert word in system
    # verbatim, one per line, before the cartridge.md content that follows it
    first_line = system.splitlines()[0]
    assert "Forbidden words" in first_line
    assert system.index(ALWAYS_FORBIDDEN_TERMS[0]) < system.index("## JSON schema for page.json")


def test_write_page_system_prompt_states_the_exact_financing_sentence(tmp_path):
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    system = client.messages.calls[0]["system"]
    assert ALLOWED_FINANCING_SENTENCE_NO_LENDER in system


def test_write_page_system_prompt_states_the_exact_warranty_sentence(tmp_path):
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    system = client.messages.calls[0]["system"]
    assert ALLOWED_WARRANTY_SENTENCE in system


# ---------------------------------------------------------------------------
# Fix cycle 7 item 3: "Shop the {model_name}" is now one of the real
# product-page/longform schema.json's allowed_cta_texts, alongside the
# existing {short_name} templates.
# ---------------------------------------------------------------------------

def test_real_product_page_and_longform_schemas_allow_the_model_name_only_cta():
    for cartridge_name in ("product-page", "longform"):
        schema = json.loads((REPO_ROOT / "cartridges" / cartridge_name / "schema.json").read_text())
        templates = schema["allowed_cta_texts"]
        assert "Shop the {model_name}" in templates
        # existing entries are kept, not replaced
        assert "Shop the {short_name}" in templates
        resolved = resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna", model_name="Fuji")
        assert "Shop the Fuji" in resolved


# ---------------------------------------------------------------------------
# Cycle 6 verification: the bad-JSON retry used to resend the exact same
# messages, blindly re-rolling with no feedback about what went wrong.
# ---------------------------------------------------------------------------

def test_write_page_retry_feeds_the_parse_error_back_as_a_correction(tmp_path):
    client = FakeClient(["not json", json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    page = write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    assert page == ARTICLE_PAGE
    second_call_messages = client.messages.calls[1]["messages"]
    assert len(second_call_messages) == 3  # original user turn, the bad assistant reply, the correction
    assert second_call_messages[1]["content"] == "not json"
    assert "not valid JSON" in second_call_messages[2]["content"]


def test_write_page_rejects_missing_required_key(tmp_path):
    bad_page = dict(ARTICLE_PAGE)
    del bad_page["cta"]
    client = FakeClient([json_response(bad_page), json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    page = write_page(
        cartridge_name="article",
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        client=client,
        model="claude-sonnet-5",
        budget=budget,
        log=log,
    )
    log.close()
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2


# ---------------------------------------------------------------------------
# Fix cycle 16 item 9 (Thursday queue item 3): consult-CTA variant per
# tenant.yaml's cta_mode/cta_variants.
# ---------------------------------------------------------------------------

from harness.write import resolve_cta_mode


class _FakeCtaTenant:
    """Just enough of the Tenant interface resolve_allowed_cta_texts reads."""

    def __init__(self, config):
        self._config = config
        self.display_name = "Acme Co"

    def get(self, dotted_key, default=None):
        node = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


LONGFORM_SCHEMA = json.loads((REPO_ROOT / "cartridges" / "longform" / "schema.json").read_text())


def test_resolve_cta_mode_explicit_buy_and_consult_win_regardless_of_angle():
    assert resolve_cta_mode("buy", ad_angle="a custom outdoor installation") == "buy"
    assert resolve_cta_mode("consult", ad_angle="a simple low-cost purchase") == "consult"


def test_resolve_cta_mode_auto_picks_consult_for_a_high_consideration_angle():
    assert resolve_cta_mode("auto", ad_angle="A custom outdoor installation for a commercial property.") == "consult"


def test_resolve_cta_mode_auto_picks_buy_for_an_ordinary_angle():
    assert resolve_cta_mode("auto", ad_angle="Transparent pricing beats a sales call.") == "buy"


def test_resolve_cta_mode_unknown_value_defaults_to_buy():
    assert resolve_cta_mode("something-else", ad_angle="a custom commercial installation") == "buy"


def test_resolve_allowed_cta_texts_buy_mode_uses_the_cartridges_own_schema_list():
    tenant = _FakeCtaTenant({"cta_mode": "buy", "cta_variants": {"consult": ["Book a consult"]}})
    resolved = resolve_allowed_cta_texts(LONGFORM_SCHEMA, "Fuji", model_name="Fuji", tenant=tenant)
    assert resolved == [t.format(short_name="Fuji", model_name="Fuji") for t in LONGFORM_SCHEMA["allowed_cta_texts"]]
    assert "Book a consult" not in resolved


def test_resolve_allowed_cta_texts_consult_mode_uses_tenant_cta_variants():
    tenant = _FakeCtaTenant({
        "cta_mode": "consult",
        "tenant_short_name": "Acme",
        "cta_variants": {"consult": ["Book a consult", "Talk to {tenant_short_name}"]},
    })
    resolved = resolve_allowed_cta_texts(LONGFORM_SCHEMA, "Fuji", model_name="Fuji", tenant=tenant)
    assert resolved == ["Book a consult", "Talk to Acme"]


def test_resolve_allowed_cta_texts_auto_mode_picks_consult_for_a_high_consideration_angle():
    tenant = _FakeCtaTenant({
        "cta_mode": "auto",
        "tenant_short_name": "Acme",
        "cta_variants": {"consult": ["Book a consult"]},
    })
    resolved = resolve_allowed_cta_texts(
        LONGFORM_SCHEMA, "Fuji", model_name="Fuji", tenant=tenant,
        ad_angle="A custom commercial installation.",
    )
    assert resolved == ["Book a consult"]


def test_resolve_allowed_cta_texts_peak_saunas_default_is_unchanged_buy_mode():
    # Regression: the real tenant's own default (cta_mode: buy) must leave
    # every cartridge's allowed_cta_texts exactly as before this cycle.
    resolved = resolve_allowed_cta_texts(LONGFORM_SCHEMA, "Fuji", model_name="Fuji", tenant=TENANT)
    assert resolved == [t.format(short_name="Fuji", model_name="Fuji") for t in LONGFORM_SCHEMA["allowed_cta_texts"]]
