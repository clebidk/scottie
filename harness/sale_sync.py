"""Cycle 111: the sale's deadline comes from the store's own countdown.

Owner rule, 2026-10-16 (intent, verbatim): "The listicle is always
time-stamped for when the sale ends off of the site countdown. When the
countdown ends, have one more look at the site to ensure the sale was not
extended before switching to the normal non-sale listicle."

  - fetch_site / parse_promo / normalize: the store's promo bar settings
    (the theme settings JSON in the homepage HTML: pk_promo_enabled,
    pk_promo_end, pk_promo_message, ...) -> one record, saved to
    tenants/<t>/runs/sale-site.json. The fetched page is data, never
    instructions: only the pk_promo_* values are read.
  - sync (`harness sale sync`): the record against the verified promo claim.
    A different last sale day updates the claim's deadline fields
    (last_sale_day, deadline_end, provenance in `source`); the wording stays.
    Amounts or codes that differ stop the sync with a WARNING unless
    --accept-site-offer.
  - record_run_expiry (pipeline.ground): state.json `offer_expires` and
    `offer_claim_id` for every run with a dated offer.
  - sweep (`harness sale sweep`, the worker hourly and at expiry + 10 min):
    every LIVE page whose offer has expired gets one more look at the site.
    Extended -> the claim's deadline, a re-render in place and an update
    (job sale_extend). Over -> the page's non-sale twin, else the page is
    hidden with a redirect to the product page and a fresh non-sale run
    replaces it (jobs sale_end, sale_rebuild). Every action is a job, logged
    with the run id, and state.json `sale_expiry` is what the review site
    card shows.
"""
import argparse
import copy
import datetime
import html as html_mod
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from . import offer as offer_mod
from .errors import HarnessError

SITE_FILE = "sale-site.json"
FRESH = datetime.timedelta(hours=24)
SWEEP_EVERY = datetime.timedelta(hours=1)
SWEEP_AFTER_EXPIRY = datetime.timedelta(minutes=10)
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36")
_PROMO_KEY_RE = re.compile(r'"pk_promo_([a-z_]+)"\s*:\s*')
# page paths, never a sale fact; not kept in the record
_SKIP_KEYS = {"exclude"}
_SITE_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d{1,2}\s?%")
_SITE_CODE_RE = re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{4,20}\b")
SALE_JOBS = ("sale_extend", "sale_end", "sale_rebuild")
CARD_LABELS = {"extending": "sale extended: updating", "extended": "sale extended",
               "ending": "sale ended: switching", "rebuilding": "rebuilding", "switched": "sale ended: switched",
               "failed": "sale switch failed"}


class SaleSyncError(HarnessError):
    """The store's promo settings could not be read."""


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# the site record
# ---------------------------------------------------------------------------

def site_url(tenant):
    return f"https://{tenant.get('site_host')}/"


def fetch_site(url, timeout=20):
    """The homepage HTML (a plain GET with a browser User-Agent)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (OSError, ValueError) as e:
        raise SaleSyncError(f"could not read {url}: {e}") from e


def parse_promo(text):
    """{key: value} for every "pk_promo_<key>": <JSON scalar> in `text`
    (first one wins), without the page-path list."""
    out, decoder = {}, json.JSONDecoder()
    for m in _PROMO_KEY_RE.finditer(str(text or "")):
        key = m.group(1)
        if key in out or key in _SKIP_KEYS:
            continue
        try:
            value, _end = decoder.raw_decode(text, m.end())
        except ValueError:
            continue
        if value is None or isinstance(value, (str, bool, int, float)):
            out[key] = value
    return out


def _dedupe(items):
    return list(dict.fromkeys(i for i in items if i))


def site_offer(raw):
    """(amounts, codes) the bar states: "$300", "INDOOR300" ..."""
    message = str(raw.get("message") or "")
    amounts = _dedupe(offer_mod._norm_amount(a) for a in _SITE_AMOUNT_RE.findall(message))
    codes = _dedupe([str(raw.get("code") or "").strip()] + _SITE_CODE_RE.findall(message))
    return amounts, codes


def normalize(raw, *, tz, source_url, fetched_at):
    """The record saved to sale-site.json. `end` is the countdown's instant
    as the site gives it; `end_local` the same instant in the tenant's
    timezone; `last_sale_day` the local date the sale's last second falls
    on."""
    end = offer_mod._instant(raw.get("end"))
    amounts, codes = site_offer(raw)
    local = end.astimezone(tz) if end else None
    return {
        "enabled": raw.get("enabled") is True,
        "end": end.isoformat() if end else None,
        "end_local": local.isoformat() if local else None,
        "last_sale_day": (local - datetime.timedelta(seconds=1)).date().isoformat() if local else None,
        "timezone": str(tz),
        "message": str(raw.get("message") or ""),
        "codes": codes,
        "amounts": amounts,
        "cta_url": str(raw.get("cta_url") or ""),
        "after_end": str(raw.get("after_end") or ""),
        "count_label": str(raw.get("count_label") or ""),
        "fetched_at": fetched_at.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source_url": source_url,
    }


def site_path(tenant):
    return Path(tenant.runs_dir) / SITE_FILE


def load_site(tenant, now=None):
    """The saved record when it is fresh (fetched less than 24 h before
    `now`), else None."""
    try:
        record = json.loads(site_path(tenant).read_text())
    except (OSError, ValueError):
        return None
    fetched = offer_mod._instant(record.get("fetched_at"))
    if fetched is None or (now or _utcnow()) - fetched >= FRESH:
        return None
    return record


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# the claim
# ---------------------------------------------------------------------------

def claim_offer(claim):
    """(amounts, codes) the promo claim's text states."""
    text = str(claim.get("text") or "")
    pairs = offer_mod._pairs(text)
    amounts = _dedupe(p["amount"] for p in pairs)
    codes = _dedupe([p["code"] for p in pairs] + [m.group("code") for m in offer_mod._CODE_RE.finditer(text)])
    return amounts, codes


def claim_last_day(claim):
    """The claim's last sale day (a date) or None."""
    when, raises = offer_mod._claim_date(claim.get("text"), claim)
    if when is None:
        return None
    return when - datetime.timedelta(days=1) if raises else when


def find_claim(claims, record):
    """The verified promo claim the site's sale is about: one that shares a
    code with the bar, else one with the bar's sale name; the newest wins."""
    promos = [c for c in claims or [] if c.get("category") == offer_mod.PROMO_CATEGORY]
    name = record["message"].split(":", 1)[0].strip().lower() if ":" in record["message"] else ""
    for test in (lambda c: set(claim_offer(c)[1]) & set(record["codes"]),
                 lambda c: name and str(c.get("text") or "").lower().startswith(name)):
        hits = [c for c in promos if test(c)]
        if hits:
            return sorted(hits, key=lambda c: str(c.get("date") or ""))[-1]
    return None


def _text_with_date(text, target):
    """`text` with the date of its deadline clause set to `target`
    ("October 16, 2026"); unchanged when it already says that day."""
    for clause in re.split(r"[;.]\s+|;", str(text or "")):
        m = offer_mod._DATE_RE.search(clause)
        if not m or not offer_mod._DEADLINE_CUE_RE.search(clause):
            continue
        if (offer_mod._month_index(m.group("month")), int(m.group("day"))) == (target.month, target.day):
            return text
        label = f"{offer_mod._MONTHS[target.month - 1].capitalize()} {target.day}, {target.year}"
        return text.replace(m.group(0), label, 1)
    return text


def _record_tz(record):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(record.get("timezone") or "UTC")
    except (ValueError, KeyError):
        return datetime.timezone.utc


def deadline_update(claim, record):
    """The claim fields that change so the claim says what the site's
    countdown says, or {} when it already does. "prices go up on <date>"
    keeps the form and names the day the countdown ends; "ends <date>" names
    the last sale day."""
    if record["enabled"]:
        end, day, why = record["end"], record["last_sale_day"], "deadline from site promo bar"
    else:   # the bar is off: the sale ended when it was seen off (if the claim still runs)
        seen = offer_mod._instant(record["fetched_at"]).astimezone(_record_tz(record))
        if claim_last_day(claim) is None or offer_mod.claim_expired(claim, None, seen) or \
                claim_last_day(claim) < seen.date():
            return {}
        end, why = record["fetched_at"], "sale off on site promo bar"
        day = (seen.date() - datetime.timedelta(days=1)).isoformat()
    if end is None or (claim.get("deadline_end") == end and claim.get("last_sale_day") == day):
        return {}
    fields = {"last_sale_day": day, "deadline_end": end,
              "source": f"{claim.get('source') or ''}; {why}, fetched {record['fetched_at']}".lstrip("; ")}
    _when, raises = offer_mod._claim_date(claim.get("text"), {"date": claim.get("date")})
    if record["enabled"] and _when is not None:
        local = offer_mod._instant(record["end_local"])
        target = local.date() if raises else datetime.date.fromisoformat(day)
        text = _text_with_date(claim["text"], target)
        if text != claim["text"]:
            fields["text"] = text
    return fields


def _accept_offer(claim, record):
    """The claim text with the site's amounts and codes, in order, when both
    state the same number of each; else None."""
    amounts, codes = claim_offer(claim)
    if len(amounts) != len(record["amounts"]) or len(codes) != len(record["codes"]):
        return None
    text = claim["text"]
    for old, new in list(zip(amounts, record["amounts"])) + list(zip(codes, record["codes"])):
        text = re.sub(re.escape(old) + r"(?![\d,A-Z])", new, text)
    return text


def sync(tenant, *, dry_run=False, accept_site_offer=False, fetch=None, now=None, log=print):
    """One look at the site, compared with the promo claim. Returns
    {record, claim_id, action, fields}; action is "unchanged", "updated",
    "would update" (dry run), "stopped" (the site's offer differs) or
    "no claim". Writes sale-site.json and the claim only when not a dry
    run. Raises SaleSyncError when the site cannot be read."""
    now = now or _utcnow()
    url = site_url(tenant)
    raw = parse_promo((fetch or fetch_site)(url))
    if "enabled" not in raw:
        raise SaleSyncError(f"no pk_promo_ settings found on {url}")
    tz = offer_mod.tenant_tz(tenant)
    record = normalize(raw, tz=tz, source_url=url, fetched_at=now)
    state = "on" if record["enabled"] else "off"
    log(f"sale sync: site bar {state}; end {record['end']} = {record['end_local']} ({record['timezone']}); "
        f"last sale day {record['last_sale_day']}; amounts {record['amounts']}; codes {record['codes']}; "
        f"after_end {record['after_end']!r}")
    path = Path(tenant.claims_dir) / "verified.json"
    claims = json.loads(path.read_text()) if path.exists() else []
    claim = find_claim(claims, record)
    result = {"record": record, "claim_id": claim.get("id") if claim else None, "action": "no claim", "fields": {}}
    if claim is None:
        log("sale sync: WARNING no verified promo claim states the site's sale; add one in the owner's wording")
        if not dry_run:
            _write_json(site_path(tenant), record)
        return result
    c_amounts, c_codes = claim_offer(claim)
    fields = {}
    if record["enabled"] and (set(c_amounts) != set(record["amounts"]) or set(c_codes) != set(record["codes"])):
        new_text = _accept_offer(claim, record) if accept_site_offer else None
        if new_text is None:
            log(f"sale sync: WARNING the site's offer ({record['amounts']}, {record['codes']}) differs from claim "
                f"{claim['id']} ({c_amounts}, {c_codes}); stopped -- the owner approves the wording"
                + ("" if accept_site_offer else " (or run with --accept-site-offer)"))
            if not dry_run:
                _write_json(site_path(tenant), record)
            return dict(result, action="stopped")
        fields["text"] = new_text
        log(f"sale sync: --accept-site-offer: claim {claim['id']} text takes the site's amounts and codes")
    record["claim_id"] = claim["id"]
    candidate = dict(claim, **fields)
    fields.update(deadline_update(candidate, record))
    old_day = claim_last_day(claim)
    if not fields:
        log(f"sale sync: claim {claim['id']} already says last sale day {old_day}; unchanged")
        action = "unchanged"
    else:
        new_day = fields.get("last_sale_day") or old_day
        log(f"sale sync: claim {claim['id']} last sale day {old_day} -> {new_day}, ends {fields.get('deadline_end')}"
            + (f"; text {fields['text']!r}" if "text" in fields else "; text unchanged")
            + (" (dry run: nothing written)" if dry_run else ""))
        action = "would update" if dry_run else "updated"
        if not dry_run:
            claim.update(fields)
            _write_json(path, claims)
    if not dry_run:
        _write_json(site_path(tenant), record)
    return dict(result, action=action, fields=fields, claim=dict(claim, **fields))


# ---------------------------------------------------------------------------
# runs: when their offer ends
# ---------------------------------------------------------------------------

def run_expiry(tenant, facts_pack, now=None):
    """(ISO instant, source) for a run's dated offer: the site's countdown
    when sale-site.json is fresh, on, and about this claim; else the
    claim's. (None, None) without an offer."""
    offer = (facts_pack or {}).get("offer") or {}
    if not offer.get("claim_id"):
        return None, None
    site = load_site(tenant, now)
    if site and site.get("enabled") and site.get("end") and site.get("claim_id") == offer["claim_id"]:
        return site["end"], "site countdown"
    return offer_mod.expires_iso(dict(offer, expires=None), tenant), "claim"


def record_run_expiry(tenant, run_dir, facts_pack, now=None, log=None):
    """pipeline.ground: facts_pack.offer.expires and state.json
    offer_expires / offer_claim_id. Returns the instant or None."""
    from . import runstate

    expires, source = run_expiry(tenant, facts_pack, now)
    if not expires:
        return None
    facts_pack["offer"]["expires"] = expires
    data = runstate.load_state(run_dir)
    data["offer_expires"], data["offer_claim_id"] = expires, facts_pack["offer"]["claim_id"]
    runstate.save_state(run_dir, data)
    if log is not None:
        log.event("ground", f"offer {facts_pack['offer']['claim_id']} expires {expires} ({source})")
    return expires


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _item(tenant, run_dir, page, **extra):
    state = _read(Path(run_dir) / "state.json")
    expires, claim_id = state.get("offer_expires"), state.get("offer_claim_id")
    source = "state.json"
    if not expires:   # a run from before cycle 111: from its facts_pack
        offer = (_read(Path(run_dir) / "facts_pack.json").get("offer") or {})
        if offer.get("claim_id"):
            expires, claim_id, source = offer_mod.expires_iso(offer, tenant), offer["claim_id"], "facts_pack"
    if not expires:
        return None
    return dict(extra, run_dir=str(run_dir), run_id=Path(run_dir).name, page=page, offer_expires=expires,
                offer_claim_id=claim_id, expires_from=source, sale_expiry=state.get("sale_expiry") or {})


def live_offer_pages(tenant):
    """Every LIVE published page with a dated offer: A/B/C arms of live tests
    and unlinked post-live pages."""
    from . import abtest, postlive

    items, seen = [], set()
    for rec in abtest.list_tests(tenant):
        if rec.get("status") != "live":
            continue
        for v in rec.get("variants") or []:
            shop = v.get("shopify") or {}
            if abtest.is_external(v) or not v.get("run_dir") or not shop.get("page_id"):
                continue
            item = _item(tenant, v["run_dir"], v.get("cartridge"), kind="abtest", test_id=rec["test_id"],
                         key=v.get("key"), url=shop.get("url"), page_id=shop.get("page_id"), handle=shop.get("handle"))
            seen.add((Path(v["run_dir"]).name, v.get("cartridge")))
            if item:
                items.append(item)
    out_dir = Path(tenant.out_dir)
    for state_file in sorted(out_dir.glob("*/state.json")) if out_dir.is_dir() else []:
        state = _read(state_file)
        for page, record in (state.get("published_pages") or {}).items():
            if (state_file.parent.name, page) in seen or not isinstance(record, dict):
                continue
            status = postlive.page_status(state_file.parent, state, page)
            if status["live"] is True and status["unlinked"]:
                item = _item(tenant, state_file.parent, page, kind="postlive", url=record.get("url"),
                             page_id=record.get("page_id"), handle=record.get("handle"))
                if item:
                    items.append(item)
    return items


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

def decide(item, site, now):
    """What to do with one live page: wait (its offer has not ended), extend
    (the site's countdown moved later), end (the bar is off, or its end
    passed and it hides after the end) or unknown (no action, a WARNING)."""
    expires = offer_mod._instant(item["offer_expires"])
    if expires is None or now < expires:
        return {"action": "wait"}
    if site is None:
        return {"action": "unknown", "reason": "the site could not be read"}
    end = offer_mod._instant(site.get("end"))
    if site.get("enabled") and end is not None and end > now:
        return {"action": "extend", "end": site["end"], "end_local": site["end_local"]}
    if not site.get("enabled") or (end is not None and site.get("after_end") == "hide"):
        return {"action": "end"}
    return {"action": "unknown", "reason": f"the bar is on, its end {site.get('end')} has passed and after_end is "
                                           f"{site.get('after_end')!r}"}


def _target(item):
    return f"run:{item['run_id']}/{item['page']}"


def set_sale_expiry(run_dir, **fields):
    """state.json `sale_expiry` (what the review site card shows)."""
    from . import runstate

    data = runstate.load_state(run_dir)
    data["sale_expiry"] = dict(fields, at=datetime.datetime.now().isoformat(timespec="seconds"))
    runstate.save_state(run_dir, data)
    return data["sale_expiry"]


def offer_with_claim(facts_pack, claim, end):
    """A copy of facts_pack whose offer takes the claim's text and synced
    deadline and ends at `end`."""
    fp = copy.deepcopy(facts_pack)
    fp["offer"].update({k: claim[k] for k in ("text", "last_sale_day", "deadline_end") if claim.get(k)})
    fp["offer"]["expires"] = end
    fp["offer"]["deadline_short"] = offer_mod.deadline_short(fp["offer"]["text"], fp["offer"])
    return fp


def _shown(text):
    return " ".join(html_mod.unescape(text).replace("\u2019", "'").split()).lower()


def extend_changes(run_dir, page, facts_pack, end):
    """What a re-render with the moved deadline would change on the live
    page: a deadline text the page does not show yet (the claim's deadline
    sentence; the short form, the day and the date when the page prints
    them), or an instant on the page other than `end`. Empty: the page
    already says what the countdown says, and the extend is a no-op (owner
    decision, 2026-10-16)."""
    from . import landing_sell

    path = Path(run_dir) / page / "index.html"
    raw = path.read_text() if path.is_file() else ""
    shown = _shown(raw)
    view = offer_mod.offer_view(facts_pack)
    if view is None:
        return ["the sale would not show"]
    texts = [view["deadline"]] if view.get("deadline") else []
    deadline = landing_sell.deadline(facts_pack)
    page_json = json.dumps(_read(Path(run_dir) / page / "page.json"))
    if deadline:
        if "today's price ends" in shown:
            texts.append(deadline["short"])
        texts += [deadline[k] for k, token in (("day", "{deadline_day}"), ("date", "{deadline_date}"))
                  if token in page_json]
    changes = [f"text {t!r}" for t in texts if t and _shown(t) not in shown]
    instants = set(re.findall(r'data-offer-expires="([^"]+)"', raw))
    if instants and instants != {end}:
        changes.append(f"instant {', '.join(sorted(instants))} -> {end}")
    return changes


def record_unchanged(run_dir, facts_pack, end):
    """An extend that changes nothing on the page: the run's offer and
    state.json take the new end (the next sweep waits for it); no render,
    no publish."""
    from . import runstate

    (Path(run_dir) / "facts_pack.json").write_text(json.dumps(facts_pack, indent=2))
    data = runstate.load_state(run_dir)
    data["offer_expires"] = end
    runstate.save_state(run_dir, data)
    set_sale_expiry(run_dir, status="unchanged", end=end)


def sweep(tenant, *, now=None, fetch=None, dry_run=False, by=None, log=print):
    """One sweep. Returns {"due", "decisions": [(item, decision, job_id)]}.
    Fetches the site only when a live page's offer has ended."""
    from . import abtest_inbox, jobs

    now = now or _utcnow()
    due = [i for i in live_offer_pages(tenant)
           if offer_mod._instant(i["offer_expires"]) and offer_mod._instant(i["offer_expires"]) <= now]
    out = {"due": len(due), "decisions": []}
    if not due:
        log("sale sweep: no live offer page has reached its end")
        return out
    site, stopped, claim = None, False, None
    try:
        result = sync(tenant, dry_run=dry_run, fetch=fetch, now=now, log=log)
        site, stopped, claim = result["record"], result["action"] in ("stopped", "no claim"), result.get("claim")
    except SaleSyncError as e:
        log(f"sale sweep: WARNING {e}; no page changed (the page fail-safe hides the offer)")
    by = by or abtest_inbox.default_by(tenant)
    for item in due:
        decision = decide(item, site, now)
        if decision["action"] == "extend" and stopped:
            decision = {"action": "unknown", "reason": "the site's offer differs from the claim (sync stopped)"}
        done = item["sale_expiry"]
        if decision["action"] == "extend" and done.get("end") == decision["end"] and \
                done.get("status") in ("extending", "extended", "unchanged"):
            decision = {"action": "wait", "reason": f"already extended to {decision['end_local']}"}
        if decision["action"] == "end" and done.get("status") in ("ending", "rebuilding", "switched"):
            decision = {"action": "wait", "reason": f"already {done['status']}"}
        job_id = None
        label = f"run {item['run_id']} page {item['page']} ({item['kind']}" + \
            (f" {item['test_id']} {item['key']}" if item["kind"] == "abtest" else "") + ")"
        fp = _read(Path(item["run_dir"]) / "facts_pack.json")
        if decision["action"] == "extend" and claim and (fp.get("offer") or {}).get("claim_id") == claim.get("id"):
            fp = offer_with_claim(fp, claim, decision["end"])
            if not extend_changes(item["run_dir"], item["page"], fp, decision["end"]):
                if not dry_run:
                    record_unchanged(item["run_dir"], fp, decision["end"])
                log(f"sale sweep: {label}: extend: unchanged (the page already shows the deadline); expires "
                    f"{decision['end_local']}" + (" (dry run)" if dry_run else ""))
                out["decisions"].append((item, dict(decision, action="unchanged"), None))
                continue
        if decision["action"] in ("extend", "end"):
            job_type = "sale_extend" if decision["action"] == "extend" else "sale_end"
            what = f"sale extended to {decision['end_local']}" if decision["action"] == "extend" else "sale ended"
            if dry_run:
                log(f"sale sweep: {label}: {what}; would queue {job_type} (dry run)")
            elif jobs.active_job(tenant, job_type=job_type, target=_target(item)):
                log(f"sale sweep: {label}: {what}; {job_type} already queued")
            else:
                payload = {k: item.get(k) for k in ("run_id", "page", "kind", "test_id", "key")}
                payload.update(by=by, end=decision.get("end"), end_local=decision.get("end_local"))
                job_id = jobs.enqueue(tenant, job_type, payload, by=by, target=_target(item))
                set_sale_expiry(item["run_dir"], status="extending" if job_type == "sale_extend" else "ending",
                                end=decision.get("end"), job_id=job_id)
                log(f"sale sweep: {label}: {what}; queued {job_type} job {job_id}")
        else:
            log(f"sale sweep: {label}: {decision['action']}"
                + (f" ({decision['reason']})" if decision.get("reason") else "")
                + ("; WARNING no action" if decision["action"] == "unknown" else ""))
        out["decisions"].append((item, decision, job_id))
    return out


def next_sweep_at(tenant, now):
    """The next sweep: in an hour, or 10 minutes after the next live offer
    ends, whichever is sooner."""
    soon = now + SWEEP_EVERY
    for item in live_offer_pages(tenant):
        at = offer_mod._instant(item["offer_expires"])
        if at is not None and now < at + SWEEP_AFTER_EXPIRY < soon:
            soon = at + SWEEP_AFTER_EXPIRY
    return soon


class SweepClock:
    """The worker loop's sweep (tenant.yaml `sale_sweep: true`): runs when
    due, never raises."""

    def __init__(self, tenant, *, sweeper=None, log=print):
        self.tenant, self.sweeper, self.log, self.next_at = tenant, sweeper or sweep, log, None

    def __call__(self, now=None):
        now = now or _utcnow()
        if self.next_at is not None and now < self.next_at:
            return None
        result = None
        try:
            result = self.sweeper(self.tenant, now=now, log=self.log)
            self.next_at = next_sweep_at(self.tenant, now)
        except Exception as e:  # noqa: BLE001 -- a sweep error never stops the worker
            self.log(f"sale sweep failed: {type(e).__name__}: {e}")
            self.next_at = now + SWEEP_EVERY
        return result


# ---------------------------------------------------------------------------
# the jobs
# ---------------------------------------------------------------------------

def _publish(tenant, run_dir, payload, note):
    """Re-publishes the run's page over its live page: an A/B/C arm through
    the replace path without a stats reset, a post-live page through its
    update action."""
    from . import jobs, postlive

    if payload["kind"] == "abtest":
        return jobs._publish_update(tenant, run_dir, payload["page"], by=payload["by"], live=True, seo_hidden=True,
                                    note=note)
    try:
        return postlive.post_live(tenant, run_dir, payload["page"], by=payload["by"], action="update", note=note)
    except HarnessError as e:
        raise jobs.JobFailed(str(e)) from e


def _failed(run_dir, reason):
    set_sale_expiry(run_dir, status="failed", reason=str(reason)[:300])


def handle_sale_extend(tenant, payload):
    """The site's countdown moved later: the run's offer takes the claim's
    new deadline, the page is re-rendered in place (no model call) and
    re-published at the same address."""
    from . import cli, jobs

    run_dir = jobs._run_dir(tenant, payload["run_id"])
    try:
        claims = json.loads((Path(tenant.claims_dir) / "verified.json").read_text())
        fp_path = run_dir / "facts_pack.json"
        fp = json.loads(fp_path.read_text())
        claim = next((c for c in claims if c.get("id") == (fp.get("offer") or {}).get("claim_id")), None)
        if claim is None:
            raise jobs.JobFailed("the run's promo claim is gone from verified.json")
        fp = offer_with_claim(fp, claim, payload["end"])
        changes = extend_changes(run_dir, payload["page"], fp, payload["end"])
        if not changes:
            record_unchanged(run_dir, fp, payload["end"])
            print(f"run {run_dir.name}: extend: unchanged", flush=True)
            return {"run_id": run_dir.name, "end": payload["end"], "unchanged": True}
        fp_path.write_text(json.dumps(fp, indent=2))
        from . import runstate

        data = runstate.load_state(run_dir)
        data["offer_expires"] = payload["end"]
        runstate.save_state(run_dir, data)
        args = argparse.Namespace(run_dir=str(run_dir), page=payload["page"], look=None, hero_style=None,
                                  tenant=tenant.name, note=f"sale extended to {payload['end_local']}")
        rc, err = jobs._call_cli(cli.cmd_rerender, args)
        if rc != 0:
            raise jobs.JobFailed(f"re-render failed (exit {rc}): {err}")
        note = f"sale extended to {payload['end_local']}"
        record = _publish(tenant, run_dir, payload, note)
    except Exception as e:
        _failed(run_dir, e)
        raise
    set_sale_expiry(run_dir, status="extended", end=payload["end"])
    jobs.audit(tenant, by=payload["by"], action="sale_extended", target=f"run:{run_dir.name}/{payload['page']}",
               detail={"end": payload["end"], "url": record.get("url")})
    print(f"run {run_dir.name}: {note}", flush=True)
    return {"run_id": run_dir.name, "end": payload["end"], "url": record.get("url")}


def twin_dir(run_dir, page):
    return Path(run_dir) / f"{page}-nosale"


def _product_path(run_dir):
    url = ((_read(Path(run_dir) / "facts_pack.json").get("product") or {}).get("url") or "")
    return urllib.parse.urlsplit(url).path or "/"


def handle_sale_end(tenant, payload):
    """The sale is over: the run's non-sale twin replaces the page in place
    (the sale page is kept as <page>-sale/); with no twin the page is hidden
    with a redirect to the product page and a sale_rebuild job is queued."""
    from . import cli, jobs
    from .publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher

    run_dir = jobs._run_dir(tenant, payload["run_id"])
    page = payload["page"]
    twin = twin_dir(run_dir, page)
    try:
        if (twin / "index.html").is_file():
            kept = run_dir / f"{page}-sale"
            if not kept.exists():
                shutil.copytree(run_dir / page, kept)
            shutil.rmtree(run_dir / page)
            shutil.copytree(twin, run_dir / page)
            record = _publish(tenant, run_dir, payload, "sale ended: switched to the non-sale twin")
            set_sale_expiry(run_dir, status="switched", via="twin")
            jobs.audit(tenant, by=payload["by"], action="sale_switched", target=f"run:{run_dir.name}/{page}",
                       detail={"via": "twin", "url": record.get("url")})
            print(f"run {run_dir.name}: sale ended: switched to the non-sale twin", flush=True)
            return {"run_id": run_dir.name, "via": "twin", "url": record.get("url")}
        published = (_read(run_dir / "state.json").get("published_pages") or {}).get(page) or {}
        if not published.get("page_id") or not published.get("handle"):
            raise jobs.JobFailed(f"page {page} of {run_dir.name} has no Shopify page to redirect")
        tenant.load_env()
        publisher = cli._make_publisher(tenant, export_dir=run_dir / page / "export")
        if not isinstance(publisher, ShopifyPublisher):
            raise jobs.JobFailed("the redirect needs the shopify publisher")
        source, target = f"/pages/{published['handle']}", _product_path(run_dir)
        try:
            # hidden first: a store redirect applies only to an address with no page
            publisher.update_page(published["page_id"], {"storefront_host": tenant.get("site_host")}, unpublished=True)
            publisher.create_redirect(source, target)
        except (ShopifyCredentialsMissing, HarnessError) as e:
            raise jobs.JobFailed(f"redirect failed: {e}") from e
        job_id = jobs.enqueue(tenant, "sale_rebuild", dict(payload), by=payload["by"],
                              target=f"run:{run_dir.name}/{page}")
    except Exception as e:
        _failed(run_dir, e)
        raise
    set_sale_expiry(run_dir, status="rebuilding", redirect={"from": source, "to": target}, job_id=job_id)
    jobs.audit(tenant, by=payload["by"], action="sale_redirected", target=f"run:{run_dir.name}/{page}",
               detail={"from": source, "to": target, "rebuild_job": job_id})
    print(f"run {run_dir.name}: sale ended: {source} hidden, redirect to {target}; rebuilding (job {job_id})",
          flush=True)
    return {"run_id": run_dir.name, "via": "redirect", "from": source, "to": target, "rebuild_job": job_id}


def handle_sale_rebuild(tenant, payload, runner=None):
    """A fresh run of the same ad (the claim has ended, so grounding sees no
    sale), then the live page is updated with it."""
    from . import abtest, abtest_rebuild, jobs, postlive, runstate

    run_dir = jobs._run_dir(tenant, payload["run_id"])
    page = payload["page"]
    try:
        if payload["kind"] == "abtest":
            try:
                result = abtest_rebuild.rebuild_arm(tenant, payload["test_id"], payload["key"], by=payload["by"],
                                                    runner=runner, log=lambda m: print(m, flush=True))
            except HarnessError as e:
                raise jobs.JobFailed(str(e)) from e
            new_run = Path(result["run_dir"])
        else:
            brief = _read(run_dir / "ad_brief.json")
            built = _read(run_dir / page / "page.json")
            look, hero = built.get("look"), built.get("hero_style")
            arm = abtest.parse_arm(":".join(x for x in (page, look, hero) if x), tenant)
            rc, new_run = (runner or abtest.default_runner)(tenant, brief.get("source_file"), arm,
                                                            int(datetime.datetime.now().timestamp()) % 100000)
            if rc != 0 or not new_run or not (Path(new_run) / page / "index.html").is_file():
                raise jobs.JobFailed(f"the non-sale run failed (exit {rc}); the page stays redirected")
            new_run = Path(new_run)
            runstate.carry_published(run_dir, new_run, page, note="sale ended: non-sale rebuild")
            try:
                postlive.post_live(tenant, new_run, page, by=payload["by"], action="update",
                                   note="sale ended: non-sale rebuild")
            except HarnessError as e:
                raise jobs.JobFailed(str(e)) from e
    except Exception as e:
        _failed(run_dir, e)
        raise
    set_sale_expiry(run_dir, status="switched", via="rebuild", run=new_run.name)
    set_sale_expiry(new_run, status="switched", via="rebuild", replaces=run_dir.name)
    jobs.audit(tenant, by=payload["by"], action="sale_switched", target=f"run:{run_dir.name}/{page}",
               detail={"via": "rebuild", "run": new_run.name})
    print(f"run {run_dir.name}: sale ended: switched to the non-sale run {new_run.name}", flush=True)
    return {"run_id": run_dir.name, "via": "rebuild", "new_run": new_run.name}


def card_line(state):
    """The review site card's sale line, or ""."""
    rec = (state or {}).get("sale_expiry") or {}
    label = CARD_LABELS.get(rec.get("status"))
    if not label:
        return ""
    return label + (f" ({rec['reason']})" if rec.get("status") == "failed" and rec.get("reason") else "")
