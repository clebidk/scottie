"""Cycle 84: one page template in both store themes.

The store runs a theme A/B test. One theme's default page template strips the
body <style> block, so a harness page rendered as raw CSS text there. Every
page now carries tenant.yaml shopify_publish.template_suffix ("pk-harness")
and renders through deploy/shopify/templates/page.pk-harness.liquid in either
theme.

No network: the real _make_publisher / ShopifyPublisher run over
tests/test_publishers.py's FakeTransport.
"""
import importlib.util
import json
import re

import pytest

from harness import abtest, cli, postlive, runstate
from harness import tenant as tenant_mod
from harness.publishers.export import ExportPublisher
from harness.publishers.shopify import ShopifyPublisher
from tests.support import REPO_ROOT, TENANT
from tests.test_listicle_site_cycle69 import REVIEWER, _built_test
from tests.test_post_live_cycle80 import HANDLE, PAGE, PAGE_ID, _calls, _run, _store
from tests.test_publishers import FakeTransport

SUFFIX = "pk-harness"
TEMPLATE = REPO_ROOT / "deploy" / "shopify" / "templates" / "page.pk-harness.liquid"


def _sent(call):
    return json.loads(call["body"])["page"]


def _ship(run_dir):
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[PAGE])
    runstate.set_packet_stamp(run_dir, stamp="ship", by=REVIEWER)


@pytest.fixture
def no_suffix(monkeypatch):
    """A tenant with no suffix. cli.main loads a fresh Tenant from tenant.yaml,
    so the lookup itself is patched, not TENANT.config."""
    real_get = tenant_mod.Tenant.get

    def get(self, dotted_key, default=None):
        if dotted_key == "shopify_publish.template_suffix":
            return default
        return real_get(self, dotted_key, default)

    monkeypatch.setattr(tenant_mod.Tenant, "get", get)


# ---------------------------------------------------------------------------
# 1. the template
# ---------------------------------------------------------------------------

def test_template_prints_the_page_body_raw_and_nothing_theme_specific():
    text = TEMPLATE.read_text()
    liquid = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", "", text, flags=re.DOTALL)
    assert liquid.count("{{ page.content }}") == 1
    # no section, snippet or theme title: the same file works in every theme
    for tag in ("render", "include", "section", "sections"):
        assert not re.search(r"\{%-?\s*" + tag + r"\b", liquid), tag
    assert "page.title" not in liquid and "rte" not in liquid and "container" not in liquid


def test_template_hides_the_theme_chrome_like_pk_regret_lp():
    text = TEMPLATE.read_text()
    for selector in ("#shopify-section-header", ".shopify-section-group-header-group",
                     "footer:not(cart-drawer footer)", ".shopify-section-group-footer-group",
                     "#shopify-section-announcement-bar", ".announcement-bar", "#MainContent"):
        assert selector in text, selector


def test_peak_sets_the_suffix_and_the_template_tenant_does_not():
    assert TENANT.get("shopify_publish.template_suffix") == SUFFIX
    assert tenant_mod.load_tenant("_template").get("shopify_publish.template_suffix") is None


# ---------------------------------------------------------------------------
# 2. the publisher
# ---------------------------------------------------------------------------

def _pub(suffix):
    transport = FakeTransport()
    transport.set_response("POST", "pages.json", 201, {"page": {"id": 1, "handle": "h"}})
    transport.set_response("PUT", "pages/1.json", 200, {"page": {"id": 1, "handle": "h"}})
    transport.set_response("GET", "shop.json", 200, {"shop": {"name": "Acme"}})
    return transport, ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport,
                                       template_suffix=suffix)


def test_create_and_update_send_the_suffix():
    transport, p = _pub(SUFFIX)
    p.publish({"title": "t", "body_html": "b", "handle": "h"})
    p.update_page(1, {"title": "t", "body_html": "b"})
    assert [_sent(c)["template_suffix"] for c in transport.calls] == [SUFFIX, SUFFIX]


def test_unpublish_still_sends_only_published():
    transport, p = _pub(SUFFIX)
    p.update_page(1, {"storefront_host": "peaksaunas.com"}, unpublished=True)
    assert json.loads(transport.calls[0]["body"]) == {"page": {"id": 1, "published": False}}


def test_no_suffix_payloads_are_byte_identical_to_before():
    transport, p = _pub(None)
    p.publish({"title": "t", "body_html": "b", "handle": "h"})
    p.update_page(1, {"title": "t", "body_html": "b"})
    assert transport.calls[0]["body"] == json.dumps(
        {"page": {"title": "t", "body_html": "b", "published": False, "handle": "h"}}).encode()
    assert transport.calls[1]["body"] == json.dumps(
        {"page": {"id": 1, "title": "t", "body_html": "b", "published": False}}).encode()


def test_dry_run_shows_the_suffix(tmp_path):
    _transport, p = _pub(SUFFIX)
    assert p.dry_run({"body_html": "x", "assets": []})["template_suffix"] == SUFFIX
    assert ExportPublisher(out_dir=tmp_path, template_suffix=SUFFIX).dry_run({})["template_suffix"] == SUFFIX


def test_publish_dry_run_prints_the_suffix(monkeypatch, capsys):
    transport, _made = _store(monkeypatch)
    transport.set_response("GET", "shop.json", 200, {"shop": {"name": "Acme"}})
    run_dir = _run()
    assert cli.main(["publish", str(run_dir), "--page", PAGE, "--dry-run", "--tenant", TENANT.name]) == 0
    assert json.loads(capsys.readouterr().out)["template_suffix"] == SUFFIX


def test_export_readme_names_the_template(tmp_path):
    ExportPublisher(out_dir=tmp_path / "a", template_suffix=SUFFIX).publish({"title": "T", "body_html": "b"})
    ExportPublisher(out_dir=tmp_path / "b").publish({"title": "T", "body_html": "b"})
    assert f"theme template to `{SUFFIX}`" in (tmp_path / "a" / "README.md").read_text()
    assert "page.pk-harness.liquid" in (tmp_path / "b" / "README.md").read_text()


def test_the_factory_passes_the_tenant_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE", "acme.myshopify.com")
    monkeypatch.setenv("SHOPIFY_TOKEN", "tok")
    assert cli._make_publisher(TENANT, export_dir=tmp_path).template_suffix == SUFFIX
    monkeypatch.setitem(TENANT.config, "publisher", "export")
    assert cli._make_publisher(TENANT, export_dir=tmp_path).template_suffix == SUFFIX
    monkeypatch.setitem(TENANT.config["shopify_publish"], "template_suffix", None)
    assert cli._make_publisher(TENANT, export_dir=tmp_path).template_suffix is None
    assert cli._make_publisher(TENANT, export_dir=tmp_path).dry_run({})["template_suffix"] is None


# ---------------------------------------------------------------------------
# 3. every publish path carries it
# ---------------------------------------------------------------------------

def test_cmd_publish_create_and_update_send_the_suffix(monkeypatch):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    _ship(run_dir)
    base = ["publish", str(run_dir), "--page", PAGE, "--tenant", TENANT.name]
    assert cli.main(base + ["--handle", HANDLE]) == 0
    _ship(run_dir)
    assert cli.main(base + ["--update"]) == 0
    [post] = _calls(transport, "POST", "pages.json")
    [put] = _calls(transport, "PUT", f"pages/{PAGE_ID}.json")
    assert _sent(post)["template_suffix"] == SUFFIX and _sent(put)["template_suffix"] == SUFFIX


def test_cmd_publish_sends_no_suffix_for_an_unset_tenant(monkeypatch, no_suffix):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    _ship(run_dir)
    base = ["publish", str(run_dir), "--page", PAGE, "--tenant", TENANT.name]
    assert cli.main(base + ["--handle", HANDLE]) == 0
    _ship(run_dir)
    assert cli.main(base + ["--update"]) == 0
    sent = [_sent(c) for c in _calls(transport, "POST", "pages.json") + _calls(transport, "PUT", "pages/")]
    assert len(sent) == 2 and all("template_suffix" not in s for s in sent)
    assert set(sent[0]) == {"title", "body_html", "published", "handle"}
    assert set(sent[1]) == {"id", "title", "body_html", "published"}


def test_post_live_post_and_update_send_the_suffix_and_unpublish_does_not(monkeypatch):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    postlive.post_live(TENANT, run_dir, PAGE, by=REVIEWER, action="post", handle=HANDLE)
    postlive.post_live(TENANT, run_dir, PAGE, by=REVIEWER, action="update")
    postlive.post_live(TENANT, run_dir, PAGE, by=REVIEWER, action="unpublish")
    [post] = _calls(transport, "POST", "pages.json")
    update, unpublish = _calls(transport, "PUT", f"pages/{PAGE_ID}.json")
    assert _sent(post)["template_suffix"] == SUFFIX and _sent(post)["published"] is True
    assert _sent(update)["template_suffix"] == SUFFIX
    assert "template_suffix" not in _sent(unpublish)


def test_abtest_publish_sends_the_suffix_on_every_variant_and_the_split(monkeypatch):
    transport, _made = _store(monkeypatch)
    rec = _built_test(monkeypatch)
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", REVIEWER, "--draft",
                     "--tenant", TENANT.name]) == 0
    posts = _calls(transport, "POST", "pages.json")
    assert len(posts) == 4  # three variants and the split page
    assert all(_sent(c)["template_suffix"] == SUFFIX for c in posts)
    assert abtest.load_test(TENANT, rec["test_id"])["split"]["page_id"] == PAGE_ID


def test_abtest_publish_sends_no_suffix_for_an_unset_tenant(monkeypatch, no_suffix):
    transport, _made = _store(monkeypatch)
    rec = _built_test(monkeypatch)
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", REVIEWER, "--draft",
                     "--tenant", TENANT.name]) == 0
    posts = _calls(transport, "POST", "pages.json")
    assert len(posts) == 4 and all("template_suffix" not in _sent(c) for c in posts)


# ---------------------------------------------------------------------------
# 4. harness pages set-template
# ---------------------------------------------------------------------------

def _published(run_dir, page_id, handle):
    data = runstate.load_state(run_dir)
    data.setdefault("published_pages", {})[PAGE] = {"page_id": page_id, "handle": handle, "url": "u"}
    runstate.save_state(run_dir, data)


def _get_suffix(page_id):
    return f"pages/{page_id}.json?fields=id%2Chandle%2Ctemplate_suffix%2Cpublished_at"


def _set_template_store(monkeypatch):
    transport, _made = _store(monkeypatch)
    transport.set_response("GET", _get_suffix(1), 200, {"page": {"id": 1, "handle": "lp-old", "template_suffix": None}})
    transport.set_response("GET", _get_suffix(2), 200, {"page": {"id": 2, "handle": "lp-done", "template_suffix": SUFFIX}})
    transport.set_response("GET", _get_suffix(3), 404, {"errors": "Not Found"})
    transport.set_response("PUT", "pages/1.json", 200, {"page": {"id": 1, "handle": "lp-old"}})
    _published(_run("a.mov"), 1, "lp-old")
    _published(_run("b.mov"), 2, "lp-done")
    _published(_run("c.mov"), 3, "lp-gone")
    _run("d.mov")  # never published: no page id, not read
    return transport


def test_set_template_puts_only_the_suffix_where_it_differs(monkeypatch, capsys):
    transport = _set_template_store(monkeypatch)
    assert cli.main(["pages", "set-template", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    puts = _calls(transport, "PUT")
    assert len(puts) == 1 and puts[0]["url"].endswith("pages/1.json")
    assert json.loads(puts[0]["body"]) == {"page": {"id": 1, "template_suffix": SUFFIX}}
    assert len(_calls(transport, "GET", "template_suffix")) == 3
    assert f"lp-old: (default) -> {SUFFIX}" in out
    assert f"lp-done: {SUFFIX} (unchanged)" in out
    assert "lp-gone: page 3 not found on Shopify" in out
    assert f"3 published page(s): set 1, already {SUFFIX} 1, not found 1" in out


def test_set_template_dry_run_writes_nothing(monkeypatch, capsys):
    transport = _set_template_store(monkeypatch)
    assert cli.main(["pages", "set-template", "--tenant", TENANT.name, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert _calls(transport, "PUT") == []
    assert f"lp-old: (default) -> {SUFFIX} (dry run)" in out and "would set 1" in out and "nothing written" in out


def test_set_template_refuses_without_a_tenant_suffix(monkeypatch, no_suffix, capsys):
    transport = _set_template_store(monkeypatch)
    assert cli.main(["pages", "set-template", "--tenant", TENANT.name]) == 1
    assert transport.calls == [] and "template_suffix" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 5. deploy/shopify/push_template.py
# ---------------------------------------------------------------------------

def _push_module():
    spec = importlib.util.spec_from_file_location(
        "push_template", REPO_ROOT / "deploy" / "shopify" / "push_template.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSET_Q = "assets.json?asset%5Bkey%5D=templates%2Fpage.pk-harness.liquid"


def _theme_store(states):
    """`states`: {theme_id: None | "same" | "other"} -- the theme's copy."""
    transport = FakeTransport()
    local = TEMPLATE.read_text()
    for theme_id, state in states.items():
        transport.set_response("GET", f"themes/{theme_id}.json", 200,
                               {"theme": {"id": theme_id, "name": f"T{theme_id}", "role": "unpublished"}})
        if state is None:
            transport.set_response("GET", f"themes/{theme_id}/{ASSET_Q}", 404, {"errors": "Not Found"})
        else:
            value = local if state == "same" else "<p>hand edit</p>"
            transport.set_response("GET", f"themes/{theme_id}/{ASSET_Q}", 200,
                                   {"asset": {"key": "templates/page.pk-harness.liquid", "value": value,
                                              "updated_at": "2026-10-01T00:00:00-04:00"}})
        transport.set_response("PUT", f"themes/{theme_id}/assets.json", 200,
                               {"asset": {"key": "templates/page.pk-harness.liquid",
                                          "updated_at": "2026-10-07T12:00:00-04:00"}})
        transport.set_response("DELETE", f"themes/{theme_id}/{ASSET_Q}", 200, {"message": "deleted"})
    client = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    return transport, client


def _push(argv, client):
    lines = []
    rc = _push_module().main(argv, client=client, out=lines.append)
    return rc, "\n".join(lines)


def _only_the_template_asset(transport):
    for call in transport.calls:
        url = call["url"]
        assert url.endswith(ASSET_Q) or re.search(r"themes/\d+(/assets)?\.json$", url), url
        if call["method"] == "PUT":
            assert json.loads(call["body"])["asset"]["key"] == "templates/page.pk-harness.liquid"


def test_push_dry_run_reports_each_theme_and_writes_nothing():
    transport, client = _theme_store({11: None, 22: "same", 33: "other"})
    rc, out = _push(["--theme-id", "11", "--theme-id", "22", "--theme-id", "33", "--dry-run"], client)
    assert rc == 0
    assert "theme 11 ('T11', role unpublished):" in out
    assert "page.pk-harness.liquid: absent" in out and ": identical" in out and ": different" in out
    assert {c["method"] for c in transport.calls} == {"GET"}


def test_push_apply_writes_only_where_absent_or_different():
    transport, client = _theme_store({11: None, 22: "same", 33: "other"})
    rc, out = _push(["--theme-id", "11", "--theme-id", "22", "--theme-id", "33", "--apply"], client)
    assert rc == 0
    puts = _calls(transport, "PUT")
    assert [p["url"].split("/themes/")[1] for p in puts] == ["11/assets.json", "33/assets.json"]
    assert all(json.loads(p["body"])["asset"]["value"] == TEMPLATE.read_text() for p in puts)
    assert out.count("pushed; updated_at 2026-10-07T12:00:00-04:00") == 2 and "nothing to push" in out
    _only_the_template_asset(transport)


def test_push_rollback_deletes_only_an_identical_copy():
    transport, client = _theme_store({11: None, 22: "same", 33: "other"})
    rc, out = _push(["--theme-id", "11", "--theme-id", "22", "--theme-id", "33", "--rollback"], client)
    assert rc == 1  # theme 33's copy differs and is left in place
    deletes = _calls(transport, "DELETE")
    assert len(deletes) == 1 and "/themes/22/" in deletes[0]["url"]
    assert "NOT deleted" in out and "nothing to delete" in out
    _only_the_template_asset(transport)


def test_push_modes_are_exclusive_and_a_theme_id_is_required():
    module = _push_module()
    with pytest.raises(SystemExit):
        module.build_parser().parse_args(["--theme-id", "1", "--apply", "--rollback"])
    with pytest.raises(SystemExit):
        module.build_parser().parse_args(["--apply"])
