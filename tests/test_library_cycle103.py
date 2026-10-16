"""Cycle 103: tenant.yaml `library: {hide: ["<cartridge>:<look>", ...]}`
takes structures off the Library page and out of `harness library build`
(their folders are pruned); a run that pins a hidden look still builds it.

conftest clears the list for every test; these tests set their own.
"""
import json

import pytest

from harness import abtest, library, looks, pipeline
from harness import tenant as tenant_mod
from tests.support import TENANT
from tests.test_listicle_site_cycle69 import _auth, client  # noqa: F401 (client is a fixture)


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    monkeypatch.setattr(library, "find_browser", lambda: None)


def _hide(monkeypatch, *entries):
    monkeypatch.setitem(TENANT.config, "library", {"hide": list(entries)})


def _built(key):
    version = library.find(TENANT, key)
    folder = TENANT.library_dir / version.key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps(library.describe(TENANT, version)))
    (folder / "review.html").write_text(f"review {key}")
    return folder


def test_a_hidden_look_has_no_card_and_no_routes(monkeypatch, client):  # noqa: F811
    _built("landing/proof")
    _hide(monkeypatch, "landing:proof", "article:default")
    assert not any(v.look == "proof" and v.cartridge == "landing" for v in library.versions(TENANT))
    assert {c["key"] for c in library.structures(TENANT)}.isdisjoint({"landing/proof", "article/default"})
    body = client.get("/library", headers=_auth()).get_data(as_text=True)
    assert 'id="lib-landing-proof"' not in body and 'id="lib-article-default"' not in body
    assert 'id="lib-group-article"' not in body   # its only structure is hidden
    assert 'id="lib-landing-open"' in body
    assert client.get("/library/landing/proof/review", headers=_auth()).status_code == 404
    # the hidden look still builds when a run pins it: only the Library is affected
    assert library.arm_from_fields("landing", "proof", tenant=TENANT).id == "landing:proof"
    assert abtest.parse_arm("landing:proof", TENANT).id == "landing:proof"


def test_a_hidden_looks_folders_are_pruned_by_the_build(monkeypatch):
    proof = _built("landing/proof")
    split = _built("landing/proof/split")
    quiz = _built("quiz/default")
    _hide(monkeypatch, "landing:proof", "quiz:default")
    lines = []
    summary = library.build(TENANT, only=["quiz"], log=lines.append)
    assert summary["pruned"] == ["quiz/default"] and summary["built"] == []
    assert "pruned quiz/default: no such version any more" in lines
    assert not quiz.exists() and proof.exists()   # --only quiz leaves landing alone
    removed = library.prune(TENANT, log=lambda _m: None)
    assert removed == ["landing/proof"] and not proof.exists() and not split.exists()
    assert (TENANT.library_dir / "landing").is_dir() is False


def test_an_unknown_entry_is_reported_once_at_build_time_and_ignored(monkeypatch):
    _hide(monkeypatch, "quiz:nope", "no-such-cartridge:open", "quiz:default")
    assert library.unknown_hidden(TENANT) == ["no-such-cartridge:open", "quiz:nope"]
    keys = {v.key for v in library.versions(TENANT)}
    assert "quiz/default" not in keys and "landing/open" in keys   # the known entry still applies
    lines = []
    summary = library.build(TENANT, only=["quiz"], log=lines.append)
    assert summary["built"] == [] and summary["failed"] == []
    for entry in ("quiz:nope", "no-such-cartridge:open"):
        assert sum(entry in line for line in lines) == 1, (entry, lines)
    assert "tenant.yaml library.hide: 'quiz:nope' names no Library structure; ignored" in lines


def test_peak_hides_the_three_merged_near_duplicates():
    peak = tenant_mod.load_tenant("peak-saunas", require=True)
    hidden = library.hidden_looks(peak)
    # the three 2026-10-12 merges stay hidden; the rest of the list is the
    # owner's (15 structures, same day) and is read from tenant.yaml
    assert {"article:default", "longform:classic", "product-page:classic"} <= hidden
    # every entry names a structure. Imported looks are gitignored: where this
    # checkout has none for a cartridge (a fresh worktree), an entry naming
    # one cannot be checked here and is accepted when it is not a harness
    # look; where the imports are present (prod, the Mac), it must be known.
    for entry in library.unknown_hidden(peak):
        cartridge, _, look = entry.partition(":")
        assert cartridge in pipeline.discover_cartridges(), entry
        assert look not in looks.cartridge_looks(cartridge) and look != library.DEFAULT_LOOK_DIR, entry
        assert not looks.imported_looks(cartridge, peak), f"{entry} names no structure"
    _errors, warnings = peak.validate()
    assert not any("never reads" in w for w in warnings), warnings
