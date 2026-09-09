from pathlib import Path

from adv.budget import Budget
from adv.log import RunLog
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
