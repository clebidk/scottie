"""Cycle 110 hotfixes from the first fresh run on master (run 64wr, the sale
still ad, 2026-10-16 00:56 UTC): H1 the run date is the tenant's day; H2 an
unverified offer in the hook is not a dead end for landing:hook_match."""
import datetime

from harness import offer, pipeline
from tests.support import TENANT
from tests.test_presell_cycle110 import AIIR, PROMO

# the real offer.render_today (tests/conftest.py pins it for every test)
REAL_RENDER_TODAY = offer.render_today
RUN_64WR = datetime.datetime(2026, 10, 16, 0, 56, tzinfo=datetime.timezone.utc)


class _Zone:
    def __init__(self, name):
        self.name = name

    def get(self, key, default=None):
        return self.name if key == "timezone" and self.name else default


# ---------------------------------------------------------------------------
# H1: grounding uses the tenant's day
# ---------------------------------------------------------------------------

def test_h1_at_00_56_utc_oct_16_denver_is_still_oct_15_and_the_sale_matches():
    brief_offer = offer.extract_offer(AIIR)          # "$300 off every indoor sauna ... Prices go up October 16"
    denver = REAL_RENDER_TODAY(_Zone("America/Denver"), now=RUN_64WR)
    assert denver == datetime.date(2026, 10, 15)
    assert offer.match_offer(brief_offer, [PROMO], denver.isoformat())["id"] == PROMO["id"]
    utc = REAL_RENDER_TODAY(_Zone(None), now=RUN_64WR)
    assert utc == datetime.date(2026, 10, 16)
    assert offer.match_offer(brief_offer, [PROMO], utc.isoformat()) is None


def test_h1_the_run_date_comes_from_render_today(monkeypatch):
    # the conftest pin reaches the pipeline too
    assert pipeline.RunState(tenant=TENANT, args=None, client=None).today_iso == "2000-01-01"
    monkeypatch.setattr(offer, "render_today", lambda tenant=None, now=None: REAL_RENDER_TODAY(tenant, now=RUN_64WR))
    assert TENANT.get("timezone") == "America/Denver"
    assert pipeline.RunState(tenant=TENANT, args=None, client=None).today_iso == "2026-10-15"


# ---------------------------------------------------------------------------
# H2: an unverified offer in the hook falls back to the ad's promise
# ---------------------------------------------------------------------------

def _64wr(name):
    import json

    from tests.support import REPO_ROOT

    return json.loads((REPO_ROOT / "tests" / "fixtures" / "c110" / "64wr" / f"{name}.json").read_text())


def _hook_keys(headline, brief, fp):
    from harness import persuasion

    return [p["key"] for p in persuasion.find_hook_match_violations({"hero": {"headline": headline}}, brief,
                                                                     facts_pack=fp, exclude={"peak", "fuji"})]


def test_h2_the_64wr_hook_states_an_unverified_offer_so_the_promise_is_matched():
    from harness import landing

    brief, fp = _64wr("ad_brief"), _64wr("facts_pack")
    assert brief["hook"] == "$300 off every indoor sauna" and brief["offer"] and offer.offer_view(fp) is None
    assert _hook_keys("A full-spectrum infrared sauna built for two", brief, fp) == ["landing:hook_match"]  # attempt 2
    assert _hook_keys("Save on a two-person sauna before the price goes up", brief, fp) == []
    assert _hook_keys("$300 off every indoor sauna", brief, fp) == ["landing:hook_match"]   # the unverified offer
    text = " ".join(landing.writer_lines(fp, TENANT, look="peak", ad_brief=brief))
    assert "The ad's offer is not verified; do not state it; lead with the ad's promise instead." in text
