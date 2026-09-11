"""harness/textutil.py: NON_PROSE_KEYS / DOLLAR_AMOUNT_RE / product_name_slug
are exercised through their callers all over the suite; this file pins the
R11 page walker itself (the one recursion that replaced ten hand-written
copies in claims.py/render.py/repair.py/pagechecks.py)."""

from harness.textutil import NON_PROSE_KEYS, path_keys, walk_page


# ---------------------------------------------------------------------------
# Review 2026-09-11 R11: the one page walker (harness/textutil.py) that
# replaced ten hand-written recursions in claims.py/render.py/repair.py.
# ---------------------------------------------------------------------------



def test_walk_page_yields_every_node_preorder_with_paths():
    page = {"a": {"b": [{"c": "x"}, "y"]}, "d": "z"}
    walked = list(walk_page(page))
    assert walked[0] == ("$", page)
    paths = [p for p, _ in walked]
    assert paths == ["$", "$.a", "$.a.b", "$.a.b[0]", "$.a.b[0].c", "$.a.b[1]", "$.d"]
    assert ("$.a.b[0].c", "x") in walked
    assert ("$.a.b[1]", "y") in walked


def test_walk_page_skip_keys_prunes_whole_subtrees():
    page = {"url": "https://x.example/emf", "body": {"text": "fine", "claim_ids": ["a"]}}
    paths = [p for p, _ in walk_page(page, skip_keys=NON_PROSE_KEYS)]
    assert "$.url" not in paths
    assert "$.body.claim_ids" not in paths
    assert "$.body.text" in paths


def test_path_keys_returns_dict_keys_only():
    assert path_keys("$") == []
    assert path_keys("$.social_proof.quotes[0].text") == ["social_proof", "quotes", "text"]
