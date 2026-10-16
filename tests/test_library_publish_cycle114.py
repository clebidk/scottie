"""Cycle 114: the approved design library goes live on the store.

Real data: the pages are design/library/approved/<entry>/<variant>/index.html
as committed (A1-statement and A8-offer-kit, four variants each); the site
record is the real 2026-10-16 promo slice (tests/fixtures/c111), whose
countdown ends 2026-10-16T23:30:00-05:00 = 22:30 MDT. The store is a fake
Shopify transport behind the REAL ShopifyPublisher (staged upload, fileCreate,
pages.json), so the upload and page calls are the run publisher's own.
Clocks are explicit; no network.
"""
import datetime
import json
import re
import shutil
from urllib.parse import parse_qs, urlparse

import pytest

from harness import cli, jobs, library_publish as lp, sale_sync
from harness.publishers.shopify import ShopifyPublisher
from tests.support import TENANT, tenant_copy
from tests.test_sale_cycle111 import END, FETCHED, _home, _record

pytestmark = pytest.mark.frozen_tenant   # the promo claim of 9bdb996 (tests/conftest.py)

UTC = datetime.timezone.utc
BEFORE = datetime.datetime(2026, 10, 17, 4, 29, tzinfo=UTC)    # 22:29 MDT
SWEEP = datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)     # 22:40 MDT, the sweep 10 min after the end
REAL = lp.APPROVED_DIR
PAIRS = ("indoor", "indoor-sale", "outdoor", "outdoor-sale")


class FakeShop:
    """The Admin API of one store: staged uploads, files, pages."""

    def __init__(self):
        self.calls, self.pages, self.files = [], {}, 0

    def __call__(self, method, url, *, headers, body=None):
        path = urlparse(url).path.split("/admin/api/2024-10/")[1]
        data = json.loads(body) if body else None
        self.calls.append((method, path, data))
        if path == "graphql.json":
            q, v = data["query"], data["variables"]
            if "stagedUploadsCreate" in q:
                name = v["input"][0]["filename"]
                return 200, json.dumps({"data": {"stagedUploadsCreate": {"userErrors": [], "stagedTargets": [
                    {"url": "https://upload.example/", "resourceUrl": f"https://upload.example/{name}",
                     "parameters": [{"name": "key", "value": name}]}]}}}).encode()
            f = v["files"][0]
            self.files += 1
            cdn = f"https://cdn.shopify.com/s/files/1/0001/files/{f['filename']}?v={self.files}"
            node = {"id": f"gid://shopify/File/{self.files}", "fileStatus": "READY"}
            node.update({"image": {"url": cdn}} if f["contentType"] == "IMAGE" else {"url": cdn})
            return 200, json.dumps({"data": {"fileCreate": {"userErrors": [], "files": [node]}}}).encode()
        if method == "GET" and path.startswith("pages.json"):
            handle = parse_qs(urlparse(url).query)["handle"][0]
            return 200, json.dumps({"pages": [p for p in self.pages.values() if p["handle"] == handle]}).encode()
        if method == "GET":
            page = self.pages[int(re.search(r"pages/(\d+)", path).group(1))]
            return 200, json.dumps({"page": {k: page[k] for k in ("id", "handle", "published_at")}}).encode()
        if method == "POST" and path == "pages.json":
            page = dict(data["page"], id=9000 + len(self.pages), published_at="2026-10-16T16:00:00-06:00")
            self.pages[page["id"]] = page
            return 201, json.dumps({"page": page}).encode()
        if method == "PUT":
            page = self.pages[data["page"]["id"]]
            page.update(data["page"])
            return 200, json.dumps({"page": page}).encode()
        raise AssertionError(f"unexpected call {method} {path}")

    def upload(self, method, url, *, headers, body=None):
        self.calls.append(("UPLOAD", url, None))
        return 204, b""

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def _publisher(shop, suffix="pk-harness"):
    return ShopifyPublisher(store="peak-test.myshopify.com", token="test-token", transport=shop,
                            upload_transport=shop.upload, sleep=lambda s: None, template_suffix=suffix)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A copy of the real A8-offer-kit entry, the fresh real site record, a
    clock before the end, no .env."""
    root = tmp_path / "approved"
    shutil.copytree(REAL / "A8-offer-kit", root / "A8-offer-kit", ignore=shutil.ignore_patterns(lp.LIVE_FILE))
    monkeypatch.setattr(lp, "APPROVED_DIR", root)
    monkeypatch.setattr(lp, "_utcnow", lambda: FETCHED)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    sale_sync._write_json(sale_sync.site_path(TENANT), dict(_record(), claim_id=lp.CLAIM_ID))
    return root


def test_library_list_on_the_real_folder(monkeypatch, capsys):
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    found = {e["entry"]: e for e in lp.entries()}
    assert found["A1-statement"]["variants"] == sorted(PAIRS) and found["A8-offer-kit"]["variants"] == sorted(PAIRS)
    assert cli.main(["library", "list", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    for entry, e in found.items():
        if not e["variants"]:
            assert f"{entry}: no variant folders" in out
        for v in e["variants"]:
            kind = "sale" if v.endswith("sale") else "no-sale"
            assert re.search(rf"^  {re.escape(v)}\s+{kind}\s+{lp.handle_for(entry, v)}\s", out, re.MULTILINE)
    assert "lp-lib-a1-statement-indoor-sale" in out and "lp-lib-a8-offer-kit-outdoor" in out
    assert [v for v in PAIRS if lp.is_sale(v)] == ["indoor-sale", "outdoor-sale"]
    assert lp.is_sale("sale") and not lp.is_sale("nosale")


def test_publish_dry_run_plans_every_real_variant_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lp, "_utcnow", lambda: FETCHED)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    monkeypatch.setattr(lp, "load_live", lambda root, entry: {})      # the plan of a first publish
    monkeypatch.setattr(lp, "known_uploads", lambda root: {})
    sale_sync._write_json(sale_sync.site_path(TENANT), dict(_record(), claim_id=lp.CLAIM_ID))
    shop = FakeShop()
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: _publisher(shop))
    before = sorted((str(p), p.stat().st_mtime) for p in REAL.rglob("*"))
    assert cli.main(["library", "publish", "--all", "--dry-run", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    planned = re.findall(r"^(\S+/\S+) -> (lp-lib-\S+) \| create \| (sale|no-sale)", out, re.MULTILINE)
    expected = [(f"{e['entry']}/{v}", lp.handle_for(e["entry"], v), "sale" if lp.is_sale(v) else "no-sale")
                for e in lp.entries() for v in e["variants"]]
    assert planned == expected and len(planned) >= 8
    assert out.count(f"hidden after {END}") == sum(1 for t in expected if t[2] == "sale")
    assert "handle free" in out and "template_suffix pk-harness" in out
    assert {c[0] for c in shop.calls} == {"GET"}                     # read-only handle lookups only
    assert sorted((str(p), p.stat().st_mtime) for p in REAL.rglob("*")) == before   # nothing written


@pytest.mark.parametrize("target", [f"{e}/{v}" for e in ("A1-statement", "A8-offer-kit") for v in PAIRS])
def test_the_path_rewrite_replaces_every_assets_reference_of_a_real_page(target):
    entry, variant = target.split("/")
    html, files = lp.load_variant(REAL, entry, variant)
    refs = lp.asset_refs(html)
    assert refs and set(refs) == set(files)
    mapping = {r: f"https://cdn.shopify.com/s/files/1/0001/files/{i}-{r.rsplit('/', 1)[1]}?v=1" for i, r in enumerate(refs)}
    body = lp.rewrite_assets(lp.build_body(html), mapping)
    assert "assets/" not in body.replace("cdn.shopify.com/s/files", "")
    for ref, url in mapping.items():
        assert body.count(url) == len(re.findall(rf"(?<![\w/.:-]){re.escape(ref)}(?![^\s\"'()<>,])", html))
    # nothing added: no <title>, no theme promo text; the page's own h1, script and styles are there
    assert "<title" not in body and "Community Savings Sale: $300 off indoor with" not in body
    assert lp.h1_texts(body) == lp.h1_texts(html) and body.count("<script>") == html.count("<script>")
    assert body.count("<style") == html.count("<style")
    # the template hides <header>/<footer>: the page's own become divs, its CSS follows them
    assert not re.search(r"<(header|footer|nav)\b", body)
    assert body.count('<div data-lib-el="footer"') == html.count("<footer")
    assert not re.search(r"(?<![\w.#\[=\"'-])(header|footer)(?![\w-])[^{}]*\{", "".join(re.findall(r"<style[^>]*>(.*?)</style>", body, re.S)))
    with pytest.raises(lp.LibraryError):
        lp.rewrite_assets(lp.build_body(html), {})


def test_publish_creates_the_page_records_it_and_republish_is_a_no_op(lib):
    shop = FakeShop()
    pub = _publisher(shop)
    out = lp.publish_variant(TENANT, "A8-offer-kit", "indoor", publisher=pub, log=lambda m: None)
    assert out["result"] == "created"
    [create] = [c for c in shop.calls if c[:2] == ("POST", "pages.json")]
    page = create[2]["page"]
    assert page["handle"] == "lp-lib-a8-offer-kit-indoor" and page["published"] is True
    assert page["title"] == "PEAK Kit No Sale" and page["template_suffix"] == "pk-harness"
    assert page["metafields"] == [{"namespace": "seo", "key": "hidden", "value": 1, "type": "number_integer"}]
    assert "assets/" not in page["body_html"].replace("/s/files/", "") and "The Fuji, monthly." in page["body_html"]
    assert not any(c[1].startswith("redirects") for c in shop.calls)
    live = json.loads((lib / "A8-offer-kit" / "live.json").read_text())
    rec = live["indoor"]
    assert set(live) == {"indoor"} and rec["page_id"] == 9000 and rec["published_at"] == "2026-10-16T16:00:00-06:00"
    assert rec["url"] == "https://peaksaunas.com/pages/lp-lib-a8-offer-kit-indoor" and rec["sale"] is False
    assert rec["content_hash"] == lp.content_hash(*lp.load_variant(lib, "A8-offer-kit", "indoor"))
    assert len(rec["assets"]) == 5 and all(a["url"] in page["body_html"] for a in rec["assets"].values())
    assert shop.count("UPLOAD") == 5 and "offer_expires" not in rec
    readme = (lib / "A8-offer-kit" / "README.md").read_text()
    assert "Live (unlinked, hidden from search): indoor https://peaksaunas.com/pages/lp-lib-a8-offer-kit-indoor" in readme
    # the same content again: nothing is sent
    calls = len(shop.calls)
    assert lp.publish_variant(TENANT, "A8-offer-kit", "indoor", publisher=pub, log=lambda m: None)["result"] == "unchanged"
    assert len(shop.calls) == calls
    # the sale twin shares the cut-outs and fonts: only its own bytes go up
    lp.publish_variant(TENANT, "A8-offer-kit", "indoor-sale", publisher=pub, log=lambda m: None)
    sale = json.loads((lib / "A8-offer-kit" / "live.json").read_text())["indoor-sale"]
    assert shop.count("UPLOAD") == 5 + sum(1 for a in sale["assets"].values()
                                           if a["url"] not in {x["url"] for x in rec["assets"].values()})
    # changed content: the same page is updated in place, unchanged assets are not uploaded again
    index = lib / "A8-offer-kit" / "indoor" / "index.html"
    index.write_text(index.read_text().replace("One red cedar cabin for two", "One red cedar cabin for 2"))
    uploads = shop.count("UPLOAD")
    out = lp.publish_variant(TENANT, "A8-offer-kit", "indoor", publisher=pub, log=lambda m: None)
    assert out["result"] == "updated" and shop.count("UPLOAD") == uploads
    [put] = [c for c in shop.calls if c[0] == "PUT"]
    assert put[1] == "pages/9000.json" and put[2]["page"]["published"] is True and "handle" not in put[2]["page"]
    rec2 = json.loads((lib / "A8-offer-kit" / "live.json").read_text())["indoor"]
    assert rec2["page_id"] == 9000 and rec2["url"] == rec["url"] and rec2["content_hash"] != rec["content_hash"]


def test_a_taken_handle_or_a_no_sale_page_with_a_code_is_refused(lib):
    shop = FakeShop()
    shop.pages[1] = {"id": 1, "handle": "lp-lib-a8-offer-kit-outdoor", "published_at": None}
    with pytest.raises(lp.LibraryError, match="already used by Shopify page 1"):
        lp.publish_variant(TENANT, "A8-offer-kit", "outdoor", publisher=_publisher(shop), log=lambda m: None)
    index = lib / "A8-offer-kit" / "indoor" / "index.html"
    index.write_text(index.read_text().replace("Get your Fuji", "Get your Fuji with INDOOR300", 1))
    with pytest.raises(lp.LibraryError, match="INDOOR300"):
        lp.plan_variant(TENANT, "A8-offer-kit", "indoor")
    assert not [c for c in shop.calls if c[0] in ("POST", "PUT", "UPLOAD") and c[1] != "graphql.json"]


def test_a_sale_page_carries_the_site_deadline_and_the_sweep_hides_it_only(lib, monkeypatch):
    shop = FakeShop()
    pub = _publisher(shop)
    for v in ("indoor", "indoor-sale"):
        lp.publish_variant(TENANT, "A8-offer-kit", v, publisher=pub, log=lambda m: None)
    rec = json.loads((lib / "A8-offer-kit" / "live.json").read_text())["indoor-sale"]
    assert (rec["offer_claim_id"], rec["offer_expires"], rec["sale"]) == ("promo-community-savings-sale-2026-10", END, True)
    [item] = [i for i in sale_sync.live_offer_pages(TENANT) if i["kind"] == "library"]
    assert (item["target"], item["page_id"], item["offer_expires"]) == ("A8-offer-kit/indoor-sale", 9001, END)
    assert sale_sync.next_sweep_at(TENANT, BEFORE) == SWEEP                       # 22:40 MDT
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    lines = []
    # 22:29 MDT: nothing is due, the site is not read
    assert sale_sync.sweep(TENANT, now=BEFORE, fetch=lambda url: 1 / 0, log=lines.append)["due"] == 0
    # 22:40 MDT, dry run: would hide
    sale_sync.sweep(TENANT, now=SWEEP, fetch=lambda url: _home(), dry_run=True, log=lines.append)
    assert any("A8-offer-kit/indoor-sale" in x and "would hide" in x for x in lines)
    calls = len(shop.calls)
    out = sale_sync.sweep(TENANT, now=SWEEP, fetch=lambda url: _home(), log=lines.append)
    assert [d[2] for d in out["decisions"] if d[0]["kind"] == "library"] == ["hidden"]
    assert shop.calls[calls:] == [("PUT", "pages/9001.json", {"page": {"id": 9001, "published": False}})]
    assert any(x.startswith("sale sweep: library sale page hidden: A8-offer-kit/indoor-sale page 9001") for x in lines)
    assert lp.sale_state(TENANT)["A8-offer-kit/indoor-sale"]["status"] == "hidden"
    assert [r["action"] for r in jobs.audit_rows(TENANT)] == ["library_sale_hidden"] and not jobs.list_jobs(TENANT)
    # hidden: off the sweep's list, and a publish does not bring it back
    assert not [i for i in sale_sync.live_offer_pages(TENANT) if i["kind"] == "library"]
    calls = len(shop.calls)
    sale_sync.sweep(TENANT, now=SWEEP + datetime.timedelta(hours=1), fetch=lambda url: _home(), log=lines.append)
    assert len(shop.calls) == calls
    with pytest.raises(lp.LibraryError, match="sale ended"):
        lp.plan_variant(TENANT, "A8-offer-kit", "indoor-sale", now=SWEEP)
    # the no-sale variant was never on the sweep's list and is still live
    assert shop.pages[9000]["published"] is True and shop.pages[9001]["published"] is False


def test_an_extended_sale_leaves_the_static_page_with_a_warning(lib, monkeypatch):
    shop = FakeShop()
    lp.publish_variant(TENANT, "A8-offer-kit", "outdoor-sale", publisher=_publisher(shop), log=lambda m: None)
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: _publisher(shop))
    lines, calls = [], len(shop.calls)
    later = {"pk_promo_end": "2026-10-20T23:30:00-05:00"}
    sale_sync.sweep(TENANT, now=SWEEP, fetch=lambda url: _home(later), log=lines.append)
    assert len(shop.calls) == calls and any("WARNING the library page is static" in x for x in lines)
    assert lp.sale_state(TENANT)["A8-offer-kit/outdoor-sale"]["status"] == "extended"


def test_publish_locked_refuses_like_the_locked_publisher(lib, monkeypatch, capsys):
    tenant_copy(monkeypatch, {"publish_locked": True})
    assert cli.main(["library", "publish", "A8-offer-kit/indoor", "--tenant", TENANT.name]) == 1
    assert cli.LockedShopifyPublisher.MESSAGE in capsys.readouterr().err
    assert not (lib / "A8-offer-kit" / "live.json").exists()


def test_verify_reads_status_h1_assets_and_relative_paths(lib):
    shop = FakeShop()
    out = lp.publish_variant(TENANT, "A8-offer-kit", "outdoor", publisher=_publisher(shop), log=lambda m: None)
    rec = out["record"]
    page = f"<html><head><link href=\"//peaksaunas.com/cdn/shop/t/1/assets/base.css\"><style>/* QA: LIVE in " \
           f"assets/pkj-cvr-v21-home.css */</style></head><body>" \
           f"{shop.pages[rec['page_id']]['body_html']}</body></html>"
    answers = {rec["url"]: (200, page.encode())}
    fetch = lambda url: answers.get(url, (200, b"x"))  # noqa: E731
    assert lp.verify_live(rec, fetch=fetch, sleep=lambda s: None)["ok"] is True
    broken = next(iter(rec["assets"].values()))["url"]
    answers[broken] = (404, b"")
    check = lp.verify_live(rec, fetch=fetch, sleep=lambda s: None)
    assert check["ok"] is False and check["assets"][broken] == 404
    answers[rec["url"]] = (200, page.replace(broken, "assets/cut/x.webp").encode())
    check = lp.verify_live(rec, fetch=lambda url: answers.get(url, (200, b"")), attempts=1)
    assert check["relative"] == ["assets/cut/x.webp"] and not check["ok"]
