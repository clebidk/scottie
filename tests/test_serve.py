"""Cycle 26: harness/serve.py -- the reviewer web app. Flask test client, no
network, no real server bound. Builds a real run dir with `harness run`
against a fake client (tests/test_cli_run.py's pattern), then exercises auth,
the run list/detail pages, the feedback POST route, and path-safety.
"""
import base64
import json

import pytest

from harness import cli, runstate, serve
from harness.review import build_reviews
from tests.conftest import FakeClient, json_response
from tests.test_cli_run import AD_BRIEF_RESPONSE, _base_args, _patch_network
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE
from tests.support import TENANT, newest_run_dir

REVIEWER = "caleb@peaksaunas.com"
PASSWORD = "correct-horse-battery-staple"


def _basic_auth_header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def review_env(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)


@pytest.fixture
def run_dir(monkeypatch):
    responses = [
        json_response(AD_BRIEF_RESPONSE),
        json_response({}),
        json_response(ARTICLE_PAGE),
        json_response(PRODUCT_PAGE_PAGE),
        json_response(LONGFORM_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)
    assert cli.cmd_run(_base_args()) == 0
    rd = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")
    build_reviews(rd)
    return rd


@pytest.fixture
def app_client(review_env):
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


# ---------------------------------------------------------------------------
# App refuses to build without REVIEW_PASSWORD.
# ---------------------------------------------------------------------------

def test_build_app_refuses_without_review_password(monkeypatch):
    monkeypatch.delenv("REVIEW_PASSWORD", raising=False)
    with pytest.raises(serve.ReviewServerNotConfigured):
        serve.build_app(TENANT)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_no_auth_is_401(app_client):
    resp = app_client.get("/")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_wrong_password_is_401(app_client):
    resp = app_client.get("/", headers=_basic_auth_header(REVIEWER, "wrong-password"))
    assert resp.status_code == 401


def test_non_reviewer_is_403(app_client):
    resp = app_client.get("/", headers=_basic_auth_header("nobody@example.com", PASSWORD))
    assert resp.status_code == 403


def test_correct_reviewer_and_password_is_200(app_client):
    resp = app_client.get("/", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 200


def test_cf_access_header_trusted_when_enabled(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.setenv("REVIEW_TRUST_CF_ACCESS", "true")
    app = serve.build_app(TENANT)
    client = app.test_client()
    resp = client.get("/", headers={"Cf-Access-Authenticated-User-Email": REVIEWER})
    assert resp.status_code == 200


def test_cf_access_header_rejects_unknown_email(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.setenv("REVIEW_TRUST_CF_ACCESS", "true")
    app = serve.build_app(TENANT)
    client = app.test_client()
    resp = client.get("/", headers={"Cf-Access-Authenticated-User-Email": "nobody@example.com"})
    assert resp.status_code == 403


def test_cf_access_falls_back_to_basic_auth_when_header_missing(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.setenv("REVIEW_TRUST_CF_ACCESS", "true")
    app = serve.build_app(TENANT)
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 401
    resp = client.get("/", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Pages render.
# ---------------------------------------------------------------------------

def test_run_list_renders_the_run(app_client, run_dir):
    resp = app_client.get("/", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert run_dir.name in body
    assert "needs_review" in body


def test_run_detail_renders_iframe_and_form(app_client, run_dir):
    resp = app_client.get(f"/run/{run_dir.name}", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<iframe" in body
    assert "<form" in body
    assert "Notes and cuts" in body
    assert "Approve page" in body
    assert "Request changes" in body


def test_run_detail_unknown_run_is_404(app_client):
    resp = app_client.get("/run/does-not-exist", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 404


def test_page_review_serves_the_review_html(app_client, run_dir):
    resp = app_client.get(f"/run/{run_dir.name}/review/article", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 200
    assert b"Advertisement" in resp.data


# ---------------------------------------------------------------------------
# Feedback POST -- through the same functions the CLI uses.
# ---------------------------------------------------------------------------

def test_approve_action_writes_scores_and_state(app_client, run_dir):
    resp = app_client.post(
        f"/run/{run_dir.name}/page/article/action",
        data={"action": "approve", "angle": "4", "brand": "5", "claims": "5", "publish": "4", "notes": "looks good"},
        headers=_basic_auth_header(REVIEWER, PASSWORD),
    )
    assert resp.status_code == 302

    state = runstate.load_state(run_dir)
    assert state["pages"]["article"] == "approved"

    scores = [json.loads(line) for line in TENANT.evals_path.read_text().splitlines() if line.strip()]
    matching = [s for s in scores if s.get("run_dir") == str(run_dir) and s.get("page") == "article"]
    assert matching
    assert matching[-1]["angle"] == 4
    assert matching[-1]["by"] == REVIEWER


def test_request_changes_action_records_feedback_and_cuts(app_client, run_dir):
    resp = app_client.post(
        f"/run/{run_dir.name}/page/article/action",
        data={
            "action": "changes",
            "notes": "cut: Buyers move on when the price is hidden.\nShorten the opening paragraph by half.",
        },
        headers=_basic_auth_header(REVIEWER, PASSWORD),
    )
    assert resp.status_code == 302

    state = runstate.load_state(run_dir)
    assert state["pages"]["article"] == runstate.PAGE_CHANGES_REQUESTED
    feedback = runstate.latest_feedback(run_dir, "article")
    assert feedback["cuts"] == ["Buyers move on when the price is hidden."]
    assert feedback["notes"] == "Shorten the opening paragraph by half."
    assert feedback["by"] == REVIEWER


def test_reject_action_rejects_the_run(app_client, run_dir):
    resp = app_client.post(
        f"/run/{run_dir.name}/page/article/action",
        data={"action": "reject", "notes": "not on brand"},
        headers=_basic_auth_header(REVIEWER, PASSWORD),
    )
    assert resp.status_code == 302
    state = runstate.load_state(run_dir)
    assert state["state"] == "rejected"


def test_approve_all_requires_every_page_scored(app_client, run_dir):
    resp = app_client.get(f"/run/{run_dir.name}", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert "Approve all" not in resp.get_data(as_text=True)

    for page in ("article", "product-page", "longform"):
        app_client.post(
            f"/run/{run_dir.name}/page/{page}/action",
            data={"action": "approve", "angle": "5", "brand": "5", "claims": "5", "publish": "5"},
            headers=_basic_auth_header(REVIEWER, PASSWORD),
        )
    resp = app_client.get(f"/run/{run_dir.name}", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert "Approve all" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def test_page_review_path_traversal_is_rejected(app_client, run_dir):
    resp = app_client.get(
        f"/run/{run_dir.name}/review/..%2f..%2f..%2fetc%2fpasswd",
        headers=_basic_auth_header(REVIEWER, PASSWORD),
    )
    assert resp.status_code == 404


def test_run_id_path_traversal_is_rejected(app_client):
    resp = app_client.get("/run/..%2f..%2f..%2fetc", headers=_basic_auth_header(REVIEWER, PASSWORD))
    assert resp.status_code == 404


def test_safe_path_rejects_traversal_directly(run_dir):
    assert serve._safe_path(run_dir, "../../../etc/passwd") is None
    assert serve._safe_path(run_dir, "article/page.json") is not None
