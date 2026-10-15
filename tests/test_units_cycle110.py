"""Cycle 110: units delivered, counted read-only from Shopify orders, become
the claims units-delivered-12mo / units-delivered-all-time (rounded DOWN to a
thousand), cached with the fetch date and read by every run's claims step.

Orders are synthetic but in the Admin REST shape the count reads
(line_items sku/name/quantity, fulfillments status/created_at/tracking/
line_items); no real order is stored in the repo (they carry tracking
numbers)."""
import datetime
import json
import urllib.parse

from harness import cli, units
from harness.ground import LocalFactsSource
from harness.publishers.shopify import ShopifyPublisher
from tests.support import TENANT

TODAY = datetime.date(2026, 10, 15)
FUJI = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
BRIEF = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}


def _item(sku, name, qty=1):
    return {"sku": sku, "name": name, "quantity": qty}


def _order(oid, created, items, *, shipped=None, tracking="1Z999", cancelled=None, fulfilled_items=None,
           f_status="success"):
    order = {"id": oid, "created_at": f"{created}T10:00:00-07:00", "cancelled_at": cancelled, "line_items": items,
             "fulfillments": []}
    if shipped:
        order["fulfillments"].append({"status": f_status, "created_at": f"{shipped}T12:00:00-07:00",
                                      "tracking_number": tracking, "tracking_numbers": [tracking] if tracking else [],
                                      "line_items": fulfilled_items if fulfilled_items is not None else items})
    return order


ORDERS = [
    # delivered inside the 365-day window
    _order(1, "2026-09-01", [_item("PEAK-FUJI", "Peak Fuji 2-Person Full Spectrum Infrared Sauna")], shipped="2026-09-03"),
    _order(2, "2026-02-01", [_item("PEAK-MATTERHORN3", "Peak Matterhorn 3-Person", 2),
                             _item("PK-COVER", "Outdoor Sauna Cover")], shipped="2026-02-10"),
    # an accessory, a warranty and a third-party resale unit never count
    _order(3, "2026-05-01", [_item("", "Extended Warranty"), _item("PK-OIL", "Aromatherapy oil kit"),
                             _item("DYN-6203", "Dynamic Barcelona 2-Person Sauna")], shipped="2026-05-02"),
    # cancelled: neither sold nor delivered
    _order(4, "2026-06-01", [_item("PEAK-EVEREST", "Peak Everest")], shipped="2026-06-02", cancelled="2026-06-03"),
    # shipped with no carrier tracking: sold, not delivered
    _order(5, "2026-07-01", [_item("PEAK-SHASTA", "Peak Shasta")], shipped="2026-07-05", tracking=None),
    # a pre-order booked only as a deposit line: sold; the product line on the
    # same order would not count twice
    _order(6, "2026-08-01", [_item("", "Rainier deposit")]),
    _order(7, "2026-08-02", [_item("PEAK-RAINIER", "Peak Rainier"), _item("", "Rainier balance payment")],
           shipped="2026-08-20", fulfilled_items=[_item("PEAK-RAINIER", "Peak Rainier")]),
    # ordered before the window, delivered inside it
    _order(8, "2025-09-01", [_item("KING-CROWN", "Crown 2-Person")], shipped="2025-12-01"),
    # outside the window both ways (all time only)
    _order(9, "2024-06-15", [_item("", "Peak Mini 1-Person Indoor Infrared Sauna", 3)], shipped="2024-07-01"),
    # a cancelled fulfillment does not count
    _order(10, "2026-10-01", [_item("PEAK-FUJI", "Peak Fuji")], shipped="2026-10-02", f_status="cancelled"),
]


RULES = units.Rules.for_tenant(TENANT)


def test_classify_mirrors_the_owner_script():
    assert RULES.classify("PEAK-MATTERHORN3", "x") == "Matterhorn"
    assert RULES.classify("KING-CROWN", "x") == "Crown"
    assert RULES.classify("PEAK-WHITNEY", "x") == "Crown"
    assert RULES.classify("PEAK-FUJI", "Fuji with built-in RLT panel") == "Fuji"  # a model SKU wins over "panel"
    assert RULES.classify("", "Peak Mini 1-Person Indoor Infrared Sauna") == "Mini"
    assert RULES.classify("", "Sauna Backrest") is None
    assert RULES.classify("DYN-6203", "Dynamic 2-Person Sauna") is None
    assert RULES.classify("PEAK-UNKNOWNMODEL", "x") is None


def test_count_units_over_real_shaped_orders():
    c = units.count_units(ORDERS, TODAY, RULES)
    # sold in window: 1 + 2 + 1 (shasta) + 1 (deposit-only rainier) + 1 (rainier) + 1 (fuji, cancelled fulfillment)
    assert c["sold_12mo"] == 7
    # delivered in window: fuji 1 + matterhorn 2 + rainier 1 + crown 1
    assert c["delivered_12mo"] == 5
    assert c["sold_all"] == 11 and c["delivered_all"] == 8
    assert c["first_year"] == 2024 and c["orders"] == 10
    assert (c["window_start"], c["window_end"]) == ("2025-10-15", "2026-10-15")


def test_claims_round_down_never_up():
    src = "Shopify Admin API (test)"
    tpl = units.templates(TENANT)
    claims = units.unit_claims({"delivered_12mo": 2282, "delivered_all": 9999, "first_year": 2023}, "2026-10-15",
                               source=src, templates=tpl)
    assert [(c["id"], c["text"]) for c in claims] == [
        ("units-delivered-12mo", "2,000+ saunas delivered to US homes in the last 12 months (Shopify, Oct 2026)"),
        ("units-delivered-all-time", "9,000+ saunas delivered since 2023 (Shopify, Oct 2026)"),
    ]
    big = units.unit_claims({"delivered_12mo": 2999, "delivered_all": 10000, "first_year": 2023}, "2026-11-01",
                            source=src, templates=tpl)
    assert big[1]["text"] == "10,000+ saunas delivered since 2023 (Shopify, Nov 2026)"
    assert big[0]["text"].startswith("2,000+")
    assert units.unit_claims({"delivered_12mo": 999, "delivered_all": 999, "first_year": 2024}, "2026-10-15",
                             source=src) == []
    # the real 2026-10-15 count: PEAK's own saunas start in 2025, so all time
    # (2,296) rounds to the same 2,000 as the last 12 months (2,026)
    same = units.unit_claims({"delivered_12mo": 2026, "delivered_all": 2296, "first_year": 2025}, "2026-10-15",
                             source=src)
    assert [c["id"] for c in same] == ["units-delivered-12mo"]


def _publisher():
    """A ShopifyPublisher whose transport pages ORDERS by since_id/limit."""
    calls = []

    def tx(method, url, *, headers, body=None):
        calls.append({"method": method, "url": url})
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        since = int(query["since_id"][0])
        batch = [o for o in ORDERS if o["id"] > since][: int(query["limit"][0])]
        return 200, json.dumps({"orders": batch}).encode()

    return ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=tx, sleep=lambda s: None), calls


def test_refresh_pages_read_only_and_writes_the_cache(isolated_tenant_paths):
    publisher, calls = _publisher()
    data = units.refresh(TENANT, publisher, today=TODAY)
    assert {c["method"] for c in calls} == {"GET"}
    first = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]["url"]).query)
    assert first["fields"] == [units.ORDER_FIELDS] and first["limit"] == ["100"] and first["status"] == ["any"]
    assert data["counts"]["delivered_12mo"] == 5 and data["claims"] == []  # under 1,000: nothing to publish
    cached = json.loads(units.cache_path(TENANT).read_text())
    assert cached["fetched"] == "2026-10-15" and cached["counts"]["orders"] == 10


def test_cached_claims_reach_the_facts_pack_and_the_claim_universe(isolated_tenant_paths):
    units.cache_path(TENANT).parent.mkdir(parents=True, exist_ok=True)
    claims = units.unit_claims({"delivered_12mo": 2282, "delivered_all": 7400, "first_year": 2023}, "2026-10-15",
                               source="Shopify Admin API (test)", templates=units.templates(TENANT))
    units.cache_path(TENANT).write_text(json.dumps({"fetched": "2026-10-15", "claims": claims}))
    loaded = units.load_claims(TENANT)
    assert [c["id"] for c in loaded] == ["units-delivered-12mo", "units-delivered-all-time"]
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, live_claims=loaded)
    assert pack["units"] == {
        "last_12mo": {"text": "2,000+ saunas delivered to US homes in the last 12 months (Shopify, Oct 2026)",
                      "claim_ids": ["units-delivered-12mo"]},
        "all_time": {"text": "7,000+ saunas delivered since 2023 (Shopify, Oct 2026)",
                     "claim_ids": ["units-delivered-all-time"]},
    }
    assert {"units-delivered-12mo", "units-delivered-all-time"} <= {c["id"] for c in pack["verified_claims"]}


def test_no_cache_means_no_units_field(isolated_tenant_paths):
    assert units.load_claims(TENANT) == []
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, live_claims=[])
    assert "units" not in pack


def test_refresh_units_command(isolated_tenant_paths, monkeypatch, capsys):
    publisher, _ = _publisher()
    monkeypatch.setattr(cli, "ShopifyPublisher", lambda *a, **k: publisher)
    assert cli.main(["claims", "refresh-units", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "10 orders, first unit sold in 2024" in out and "5 delivered" in out and "no unit claim" in out
    assert units.cache_path(TENANT).exists()


def test_default_templates_are_tenant_neutral():
    claims = units.unit_claims({"delivered_12mo": 1500, "delivered_all": 1500, "first_year": 2024}, "2026-10-15",
                               source="s")
    assert claims[0]["text"] == "1,000+ units delivered in the last 12 months (Shopify, Oct 2026)"


def test_monthly_timer_runs_the_command():
    from tests.support import REPO_ROOT
    service = (REPO_ROOT / "crons" / "units-refresh.service").read_text()
    timer = (REPO_ROOT / "crons" / "units-refresh.timer").read_text()
    assert "harness claims refresh-units --tenant @@TENANT@@" in service
    assert "OnCalendar=*-*-01" in timer
