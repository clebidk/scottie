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
