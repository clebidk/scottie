"""Cycle 112: the harness version on every run, the backfill for older runs,
the read-only archive index, the /versions and /showcase pages and the
static export. No network, no model call, no browser: runs are written by
hand and the archives are folders under tmp_path.
"""
import datetime
import io
import json
import os
import subprocess

import pytest
from PIL import Image

from harness import capture, runstate, site_versions, versions
from tests.support import TENANT, tenant_copy
from tests.test_listicle_site_cycle69 import _auth, client  # noqa: F401 (client is a fixture)

FIXLOG = """# FIXLOG

## Cycle 10 (first look, 2026-09-01)
text
## Cycle 11 (second look, 2026-09-05)
text
## Cycle 12 A (third look, 2026-09-10)
text
"""
OLD = datetime.datetime(2026, 9, 1).timestamp()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No real archive, no browser, a fresh version cache per test."""
    tenant_copy(monkeypatch, {"versions.archives": {}})
    monkeypatch.setattr(capture, "find_browser", lambda: None)
    versions._current.cache_clear()
    yield
    versions._current.cache_clear()


def _png(color=(200, 30, 30), size=(390, 900)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _fixlog_repo(tmp_path, *, git=False):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "FIXLOG.md").write_text(FIXLOG)
    if git:
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run("add", "docs/FIXLOG.md")
        run("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "fixlog")
    return repo


def _run(root, run_id, *, at, state="needs_review", page="landing", version=None, look="peak", hero="",
         style="", shot=False, asset=False, brief=None):
    run_dir = root / run_id
    page_dir = run_dir / page
    page_dir.mkdir(parents=True)
    data = {"run_id": run_id, "state": state, "pages": {page: "generated"}, "dry_run": False,
            "history": [{"state": "generated", "at": at}],
            ("listicle" if page == "listicle" else page): {"look": look, "hero_style": hero, "style": style,
                                                           "quality": {"hook_match": 0.91, "cta_count": 3}}}
    if version:
        data["harness_version"] = version
    (run_dir / "state.json").write_text(json.dumps(data))
    (run_dir / "ad_brief.json").write_text(json.dumps(brief or {"input_type": "still", "hook": "a sauna"}))
    (page_dir / "index.html").write_text('<html><body><img src="assets/a.png">page</body></html>')
    if shot:
        (page_dir / "design").mkdir()
        (page_dir / "design" / "scores.json").write_text(json.dumps({"chosen": 1}))
        (page_dir / "design" / "variant-1.png").write_bytes(_png())
    if asset:
        (page_dir / "assets").mkdir()
        (page_dir / "assets" / "a.png").write_bytes(_png((10, 10, 200), (600, 400)))
    os.utime(run_dir / "state.json", (OLD, OLD))
    return run_dir


def _snapshot(folder):
    return {str(p.relative_to(folder)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b"")
            for p in sorted(folder.rglob("*"))}


# ---------------------------------------------------------------------------
# version recording and derivation
# ---------------------------------------------------------------------------

def test_fixlog_cycles_and_derivation_by_date():
    cycles = versions.parse_fixlog(FIXLOG)
    assert list(cycles) == ["10", "11", "12"]
    assert cycles["12"] == {"cycle": "12", "date": "2026-09-10", "summary": "third look"}
    rows = versions.cycle_times(FIXLOG, {})
    at = datetime.datetime.fromisoformat
    assert versions.derive_cycle(at("2026-09-03T10:00:00"), rows) == "10"
    assert versions.derive_cycle(at("2026-09-07T08:30:00"), rows) == "11"
    assert versions.derive_cycle(at("2026-09-12T23:00:00"), rows) == "12"
    assert versions.derive_cycle(at("2026-08-20T00:00:00"), rows) is None
    # a cycle's time on master (git) wins over its FIXLOG date
    late = versions.cycle_times(FIXLOG, {"11": at("2026-09-08T12:00:00")})
    assert versions.derive_cycle(at("2026-09-07T08:30:00"), late) == "10"


def test_a_new_run_records_the_harness_version(tmp_path, monkeypatch):
    repo = _fixlog_repo(tmp_path, git=True)
    monkeypatch.setattr(versions, "REPO_ROOT", repo)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    run_dir = TENANT.out_dir / "20261016-120000-new-run-abcd"
    run_dir.mkdir()
    runstate.init_state(run_dir, pages=["landing"])
    version = runstate.load_state(run_dir)["harness_version"]
    assert version == {"commit": commit, "date": datetime.date.today().isoformat(), "cycle": "12"}


def test_backfill_derives_the_cycle_from_the_run_time(tmp_path):
    repo = _fixlog_repo(tmp_path)  # no git: FIXLOG dates only
    out = TENANT.out_dir
    _run(out, "20260902-100000-a-aaaa", at="2026-09-02T10:00:00")
    _run(out, "20260906-100000-b-bbbb", at="2026-09-06T10:00:00")
    _run(out, "20260911-100000-c-cccc", at="2026-09-11T10:00:00")
    kept = {"commit": "abc1234", "date": "2026-09-11", "cycle": "12"}
    _run(out, "20260911-110000-d-dddd", at="2026-09-11T11:00:00", version=kept)

    dry = versions.backfill(TENANT, dry_run=True, repo=repo, log=lambda _m: None)
    assert dry["written"] == 3 and dry["kept"] == 1 and dry["by_cycle"] == {"10": 1, "11": 1, "12": 1}
    assert "harness_version" not in runstate.load_state(out / "20260902-100000-a-aaaa")
    assert not versions.index_path(TENANT).exists()

    versions.backfill(TENANT, repo=repo, log=lambda _m: None)
    got = {r: runstate.load_state(out / r)["harness_version"]
           for r in ("20260902-100000-a-aaaa", "20260906-100000-b-bbbb", "20260911-100000-c-cccc")}
    assert [v["cycle"] for v in got.values()] == ["10", "11", "12"]
    assert all(v["derived"] is True for v in got.values())
    assert runstate.load_state(out / "20260911-110000-d-dddd")["harness_version"] == kept


def test_backfill_leaves_a_run_being_written_alone(tmp_path):
    repo = _fixlog_repo(tmp_path)
    run_dir = _run(TENANT.out_dir, "20260911-100000-c-cccc", at="2026-09-11T10:00:00")
    os.utime(run_dir / "state.json")  # just now
    summary = versions.backfill(TENANT, repo=repo, log=lambda _m: None)
    assert summary["recent"] == 1 and "harness_version" not in runstate.load_state(run_dir)


def test_the_archive_is_indexed_and_never_written(tmp_path, monkeypatch):
    repo = _fixlog_repo(tmp_path)
    arch = tmp_path / "archive"
    _run(arch, "20260906-100000-old-a1b2", at="2026-09-06T10:00:00", page="listicle", look="open",
         hero="face", style="reasons", asset=True)
    _run(arch, "20260911-100000-old-c3d4", at="2026-09-11T10:00:00", state="generated")
    (arch / versions.PUBLISHED_FILE).write_text(
        "2 published pages\n20260906-100000-old-a1b2 listicle https://shop.example/pages/listicle-test-1\n")
    tenant_copy(monkeypatch, {"versions.archives": {"arch": str(arch)}})
    before = _snapshot(arch)

    versions.backfill(TENANT, repo=repo, log=lambda _m: None)
    index = json.loads(versions.index_path(TENANT).read_text())
    runs = {r["run_id"]: r for r in index["runs"]}
    assert set(runs) == {"20260906-100000-old-a1b2", "20260911-100000-old-c3d4"}
    first = runs["20260906-100000-old-a1b2"]
    assert (first["source"], first["cycle"], first["derived"], first["passed"]) == ("arch", "11", True, True)
    assert first["pages"][0]["style"] == "reasons" and first["pages"][0]["hero"] == "face"
    assert runs["20260911-100000-old-c3d4"]["passed"] is False

    # a capture stages a copy outside the archive (assets/ as a symlink)
    seen = {}

    def fake_screenshot(_browser, html_path, png_path, **_kw):
        seen["dir"] = html_path.parent
        seen["link"] = (html_path.parent / "assets").is_symlink()
        png_path.write_bytes(_png())
        return True

    monkeypatch.setattr(capture, "screenshot", fake_screenshot)
    path = versions.thumb(TENANT, "arch", "20260906-100000-old-a1b2", "listicle", browser="chrome")
    assert path is not None and path.parent == TENANT.runs_dir / "thumbs"
    assert seen["link"] and arch not in seen["dir"].parents
    with Image.open(path) as im:
        assert im.size == (240, 519)  # 390 x 844 at 240 wide
    assert versions.page_dir(TENANT, "arch", "..", "listicle") is None
    assert _snapshot(arch) == before


# ---------------------------------------------------------------------------
# the two pages
# ---------------------------------------------------------------------------

def test_the_pages_render_with_no_runs(client):  # noqa: F811
    body = client.get("/versions", headers=_auth()).get_data(as_text=True)
    assert "<h1>Versions</h1>" in body and "No runs recorded on this cycle." in body
    assert 'href="/versions"' in body and 'href="/showcase"' in body
    assert 'aria-current="page"' in body.split('href="/versions"')[1][:40]
    page = client.get("/showcase", headers=_auth())
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert text.count("Example missing: not generated on this version yet.") == len(versions.capabilities(TENANT))


def _fixture_runs(tmp_path, monkeypatch):
    v110 = {"commit": "2c554b2", "date": "2026-10-15", "cycle": "110"}
    _run(TENANT.out_dir, "20261015-120000-meta-1-abcd", at="2026-10-15T12:00:00", version=v110, shot=True,
         brief={"input_type": "video", "hook": "Pay $458 a month with Bread Pay", "offer": {"amount": "$300"}})
    _run(TENANT.out_dir, "20261015-130000-meta-2-efgh", at="2026-10-15T13:00:00", version=v110, state="generated")
    (TENANT.runs_dir / "spend-ledger.jsonl").write_text(
        json.dumps({"run_id": "20261015-120000-meta-1-abcd", "cost_estimate": 0.21}) + "\n")
    TENANT.abtests_dir.mkdir(parents=True, exist_ok=True)
    (TENANT.abtests_dir / "t1.json").write_text(json.dumps({"test_id": "t1", "variants": [
        {"key": "A", "run_dir": str(TENANT.out_dir / "20261015-120000-meta-1-abcd"), "cartridge": "landing",
         "shopify": {"url": "https://shop.example/pages/lp-a"}}]}))
    arch = tmp_path / "archive"
    _run(arch, "20260906-100000-old-a1b2", at="2026-09-06T10:00:00", page="listicle", look="open", hero="face",
         style="reasons", asset=True)
    tenant_copy(monkeypatch, {"versions.archives": {"arch": str(arch)}})
    versions.backfill(TENANT, repo=_fixlog_repo(tmp_path), log=lambda _m: None)


def test_the_pages_render_with_runs(client, tmp_path, monkeypatch):  # noqa: F811
    _fixture_runs(tmp_path, monkeypatch)
    body = client.get("/versions", headers=_auth()).get_data(as_text=True)
    row = body.split('id="cycle-110"')[1].split('class="card ver-row')[0]
    assert "<h2>Cycle 110</h2>" in row and "pre-sell rebuild" in row
    assert "Runs <b>2</b>" in row and "Passed <b>50%</b>" in row and "Median cost <b>$0.21</b>" in row
    assert '/versions/thumb/out/20261015-120000-meta-1-abcd/landing' in row
    assert 'href="/gen/20261015-120000-meta-1-abcd/landing"' in row
    assert 'href="https://shop.example/pages/lp-a"' in row and "Store page" in row
    assert '/versions/archive/arch/20260906-100000-old-a1b2/listicle' in body

    show = client.get("/showcase", headers=_auth()).get_data(as_text=True)
    card = show.split('id="cap-landing-peak"')[1].split('class="card sc-card"')[0]
    assert "20261015-120000-meta-1-abcd" in card and "Cost <b>$0.21</b>" in card and "Hook <b>0.91</b>" in card
    for cap in ("offer-led", "video-ad", "financing-ad"):
        assert "20261015-120000-meta-1-abcd" in show.split(f'id="cap-{cap}"')[1].split('class="card sc-card"')[0]
    older = show.split('id="cap-listicle-reasons-face"')[1].split('class="card sc-card"')[0]
    assert "20260906-100000-old-a1b2" in older and "(older)" in older
    assert "Example missing" in show.split('id="cap-quiz"')[1].split('class="card sc-card"')[0]

    thumb = client.get("/versions/thumb/out/20261015-120000-meta-1-abcd/landing", headers=_auth())
    assert thumb.status_code == 200 and thumb.mimetype == "image/jpeg"
    page = client.get("/versions/archive/arch/20260906-100000-old-a1b2/listicle/", headers=_auth())
    assert page.status_code == 200 and b"assets/a.png" in page.data
    asset = client.get("/versions/archive/arch/20260906-100000-old-a1b2/listicle/assets/a.png", headers=_auth())
    assert asset.status_code == 200
    assert client.get("/versions/archive/arch/20260906-100000-old-a1b2/listicle/../../state.json",
                      headers=_auth()).status_code == 404
    assert client.get("/versions/archive/out/20261015-120000-meta-1-abcd/landing/",
                      headers=_auth()).status_code == 404
    assert client.get("/versions", headers={}).status_code == 401


def test_the_export_writes_static_pages_and_thumbs(tmp_path, monkeypatch):
    _fixture_runs(tmp_path, monkeypatch)
    out = tmp_path / "export"
    count = site_versions.export(TENANT, out, review_url="https://review.example/", log=lambda _m: None)
    index = (out / "index.html").read_text()
    show = (out / "showcase.html").read_text()
    assert count == 2 and len(list((out / "thumbs").glob("*.jpg"))) == 2
    assert 'href="https://review.example/gen/20261015-120000-meta-1-abcd/landing"' in index
    assert 'src="thumbs/out-20261015-120000-meta-1-abcd-landing.jpg"' in index
    assert 'href="https://review.example/versions/archive/arch/20260906-100000-old-a1b2/listicle/"' in show
    assert 'href="showcase.html"' in index and 'href="index.html"' in show
    assert "/site-static/" not in index and "<aside" not in index
