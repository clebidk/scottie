from pathlib import Path

from adv.budget import Budget
from adv.log import RunLog
from adv.vocab import ALLOWED_FINANCING_SENTENCE_NO_LENDER, ALWAYS_FORBIDDEN_TERMS
from adv.write import load_exemplars, write_page
from tests.conftest import FakeClient, json_response
from tests.test_render import ARTICLE_PAGE, AD_BRIEF, FACTS_PACK

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_exemplars_reads_markdown_reference_articles():
    exemplars = load_exemplars(REPO_ROOT / "cartridges" / "article")
    assert len(exemplars) == 2
    assert all("reference_article" in e for e in exemplars)
    assert "Peak Saunas" in exemplars[0]["reference_article"]


def test_load_exemplars_missing_dir_returns_empty(tmp_path):
    assert load_exemplars(tmp_path / "no-such-cartridge") == []


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
