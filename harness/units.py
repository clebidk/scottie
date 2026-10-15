"""Cycle 110: how many units of its own the store has sold and delivered,
from Shopify.

Read-only (GET orders.json, read_orders / read_all_orders). The counting
rules mirror the owner's units script (units_sold_fulfilled.py on the owner's
machine, 2026-10-15), with the tenant's own words in tenant.yaml `units`:

  - a unit is a line item whose SKU is "<prefix><MODEL>[n]" for one of the
    tenant's models, or (no SKU) a "<n>-Person ... <category word>" title
    naming one; accessories, warranties, kits, covers, parts and shipping
    lines never count; third-party resale SKUs never count;
  - sold = units on non-cancelled orders (a deposit/balance line counts only
    when the order has no product line for that model);
  - delivered = units inside a non-cancelled fulfillment that has carrier
    tracking (house rule 2026-08-31), dated by the fulfillment.

"Last 12 months" is a trailing 365-day window ending on the fetch day (the
owner's script reports 12 full months plus the current part month). Every
published figure is rounded DOWN to the nearest thousand ("2,000+"); never up.

The claims live in a cache (tenants/<t>/runs/units-claims.json), refreshed by
`harness claims refresh-units` (crons/units-refresh.timer, monthly), and join
a run's claim universe like the live price claims -- never verified.json.
"""
import datetime
import json
import re

ORDER_FIELDS = "id,created_at,cancelled_at,line_items,fulfillments"
CACHE_NAME = "units-claims.json"

# Lines that carry the category word but are not a unit (generic retail words;
# the tenant's own model names and SKU rules live in tenant.yaml `units`).
NOT_A_UNIT = re.compile(r"warranty|toolkit|\bkit\b|protection|cover|backrest|cushion|cleaner|"
                        r"panel|crate|oil|aromatherapy|shipping|install|assembly|replacement|"
                        r"\bpart\b|gift box|ionizer|purifier", re.I)
IS_UNIT_NAME = re.compile(r"\d\s*[- ]?person", re.I)
DEPOSIT = re.compile(r"deposit|balance", re.I)


class Rules:
    """The tenant's unit rules (tenant.yaml `units`): its SKU prefixes, its
    model names (claims/products.json's names plus `extra_models`), SKU
    aliases, the third-party SKU pattern and the category word a no-SKU
    line's title must hold."""

    def __init__(self, *, sku_prefixes=(), models=(), sku_aliases=None, third_party_sku="", category_word=""):
        self.sku_prefixes = tuple(p.upper() for p in sku_prefixes)
        self.models = {}
        for name in models:
            self.models[name.upper().replace(" ", "-")] = name
        for key, name in (sku_aliases or {}).items():
            self.models[str(key).upper()] = name
        self.third_party = re.compile(third_party_sku, re.I) if third_party_sku else None
        self.category_word = category_word.lower()
        self.deposit_models = [(re.compile(r"\b" + r"[\s-]?".join(map(re.escape, name.split())) + r"\b", re.I), name)
                               for name in sorted(set(models), key=len, reverse=True)]

    @classmethod
    def for_tenant(cls, tenant):
        cfg = tenant.get("units") or {}
        names = [p["name"] for p in tenant.catalog_products() if p.get("name")]
        return cls(sku_prefixes=cfg.get("sku_prefixes") or (), models=names + list(cfg.get("extra_models") or ()),
                   sku_aliases=cfg.get("sku_aliases") or {}, third_party_sku=cfg.get("third_party_sku") or "",
                   category_word=cfg.get("category_word") or "")

    def classify(self, sku, name):
        """The model of a whole unit of the tenant's own, else None."""
        sku, name = (sku or "").strip().upper(), name or ""
        prefix = next((p for p in self.sku_prefixes if sku.startswith(p)), None)
        if prefix:
            return self.models.get(re.sub(r"\d+$", "", sku[len(prefix):]))
        if NOT_A_UNIT.search(name) or (self.third_party and self.third_party.match(sku)):
            return None
        if not sku and IS_UNIT_NAME.search(name) and self.category_word in name.lower():
            for key, model in self.models.items():
                if re.search(rf"\b{re.escape(key.replace('-', ' '))}\b", name, re.I):
                    return model
        return None

    def deposit_model(self, name):
        if not name or not DEPOSIT.search(name):
            return None
        for pat, model in self.deposit_models:
            if pat.search(name):
                return model
        return None


def _day(stamp):
    try:
        return datetime.date.fromisoformat(str(stamp or "")[:10])
    except ValueError:
        return None


def count_units(orders, today, rules):
    """{sold_12mo, delivered_12mo, sold_all, delivered_all, first_year,
    orders, window_start} over `orders` (Shopify order dicts with
    line_items and fulfillments)."""
    today = today if isinstance(today, datetime.date) else datetime.date.fromisoformat(str(today)[:10])
    start = today - datetime.timedelta(days=365)
    out = {"sold_12mo": 0, "delivered_12mo": 0, "sold_all": 0, "delivered_all": 0, "first_year": None,
           "orders": len(orders), "window_start": start.isoformat(), "window_end": today.isoformat()}
    for o in orders:
        if o.get("cancelled_at"):
            continue
        made = _day(o.get("created_at"))
        sold = 0
        priced = set()
        for it in o.get("line_items") or ():
            model = rules.classify(it.get("sku"), it.get("name"))
            if model:
                priced.add(model)
                sold += max(int(it.get("quantity") or 0), 0)
        for it in o.get("line_items") or ():
            model = rules.deposit_model(it.get("name"))
            if model and model not in priced:
                sold += max(int(it.get("quantity") or 0), 0)
        if sold and made:
            out["sold_all"] += sold
            if start < made <= today:
                out["sold_12mo"] += sold
            if out["first_year"] is None or made.year < out["first_year"]:
                out["first_year"] = made.year
        for f in o.get("fulfillments") or ():
            if (f.get("status") or "").lower() == "cancelled":
                continue
            if not (f.get("tracking_number") or f.get("tracking_numbers")):
                continue
            shipped = _day(f.get("created_at"))
            n = sum(max(int(it.get("quantity") or 0), 0) for it in f.get("line_items") or ()
                    if rules.classify(it.get("sku"), it.get("name")))
            if n and shipped:
                out["delivered_all"] += n
                if start < shipped <= today:
                    out["delivered_12mo"] += n
    return out


def floor_thousands(n):
    return (int(n) // 1000) * 1000


DEFAULT_TEMPLATES = {
    "last_12mo": "{count}+ units delivered in the last 12 months (Shopify, {month})",
    "all_time": "{count}+ units delivered since {year} (Shopify, {month})",
}


def unit_claims(counts, today_iso, *, source, templates=None):
    """The claims units-delivered-12mo and units-delivered-all-time, or []
    when the count is under 1,000 (nothing honest to round down to). The
    all-time claim is left out when it rounds to the 12-month figure."""
    today = datetime.date.fromisoformat(today_iso[:10])
    stamp = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[today.month - 1]) + f" {today.year}"
    templates = {**DEFAULT_TEMPLATES, **(templates or {})}
    claims = []
    last = floor_thousands(counts["delivered_12mo"])
    if last:
        claims.append({
            "id": "units-delivered-12mo",
            "text": templates["last_12mo"].format(count=f"{last:,}", month=stamp),
            "category": "trust", "source": source, "approved_by": "live-fetch", "date": today_iso,
            "count": counts["delivered_12mo"],
        })
    total = floor_thousands(counts["delivered_all"])
    # an all-time figure that rounds to the same thousand says nothing more
    if total and total != last and counts.get("first_year"):
        claims.append({
            "id": "units-delivered-all-time",
            "text": templates["all_time"].format(count=f"{total:,}", year=counts["first_year"], month=stamp),
            "category": "trust", "source": source, "approved_by": "live-fetch", "date": today_iso,
            "count": counts["delivered_all"],
        })
    return claims


def templates(tenant):
    cfg = tenant.get("units") or {}
    return {k: cfg[f"{k}_template"] for k in DEFAULT_TEMPLATES if cfg.get(f"{k}_template")}


def cache_path(tenant):
    return tenant.runs_dir / CACHE_NAME


def load_claims(tenant):
    """The cached unit claims ([] when there is no cache or it does not parse)."""
    path = cache_path(tenant)
    try:
        return list(json.loads(path.read_text()).get("claims") or [])
    except (OSError, ValueError, AttributeError):
        return []


def refresh(tenant, publisher, *, today=None, since="2000-01-01T00:00:00Z", log=None):
    """Counts every order since `since` (read-only) and writes the cache.
    Returns the cache dict."""
    today = today or datetime.date.today()
    orders = publisher.list_orders(created_at_min=since, fields=ORDER_FIELDS, max_pages=1000, page_size=100,
                                    pause_s=0.6)
    counts = count_units(orders, today, Rules.for_tenant(tenant))
    source = (f"Shopify Admin API, read-only count of units in non-cancelled fulfillments with carrier "
              f"tracking ({counts['orders']:,} orders scanned, first unit sold in {counts['first_year']}), fetched "
              f"{today.isoformat()}")
    data = {"fetched": today.isoformat(), "counts": counts,
            "claims": unit_claims(counts, today.isoformat(), source=source, templates=templates(tenant))}
    path = cache_path(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
    if log is not None:
        log.event("units", f"{counts['orders']} orders: {counts['delivered_12mo']} delivered in 12 months, "
                           f"{counts['delivered_all']} all time")
    return data
