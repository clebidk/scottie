"""The post-render leaked-claim-id backstop lowercased the visible text, so a
headline audience "Price-Conscious Shoppers" read as a price-* claim id and
stopped run 7q2w (cycle 71) with no repair; the page.json check is
case-sensitive and passed it. Claim ids are lowercase, so is a real leak."""
from harness import claims


def test_title_case_prose_that_looks_like_an_id_prefix_is_not_a_leak():
    html = "<h1>7 Mistakes Price-Conscious Shoppers Make</h1>"
    assert claims.find_leaked_claim_ids_visible_text(html, {"price-fuji"}) == []


def test_a_real_lowercase_claim_id_in_visible_text_is_still_caught():
    html = "<p>It costs $8,250 (price-fuji).</p>"
    hits = claims.find_leaked_claim_ids_visible_text(html, {"price-fuji"})
    assert [h["term"] for h in hits] == ["price-fuji"]
