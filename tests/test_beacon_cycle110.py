"""Cycle 110: the beacon records where a click came from, how far a visitor
scrolled, how long they stayed, how fast the page loaded and on what screen;
"Text us" taps are their own event; results read it back per arm; every test
record carries the ad's angle. Old events and old databases stay readable."""
import json
import re
import sqlite3

import pytest

from harness import abevents, abtest, cli
from tests.support import TENANT
from tests.test_abtest_cycle67 import _beacon_prelude, _post, _run_js, _script_body, beacon_client, needs_node  # noqa: F401

URL = "https://listicle.example/e"
LONG_ID = "aiir-sale-fuji-price-static-oct-w2-fuji-5s4e"


def _events(out, name):
    return [b["d"] for b in out["beacons"] if b["d"]["e"] == name]


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------

def test_beacon_is_under_2kb_with_a_long_test_id_and_the_live_beacon_url():
    tag = abtest.beacon_script(LONG_ID, "A", "https://listicle.peaksaunasteam.com/e")
    assert len(tag.encode()) <= 2048
    assert "<script src" not in tag and "import" not in tag


def test_cta_class_regex_matches_every_cta_class_and_not_the_text_link():
    rx = re.compile(abtest.cta_class_regex()[1:-1])
    for cls in abtest.CTA_CLASSES:
        assert rx.search(f" {cls} "), cls
        assert rx.search(f" pk-x {cls} other "), cls
    for other in ("ld-text", "ld-btnx", "xld-btn", "op-linky", "adv-ctas", ""):
        assert not rx.search(f" {other} "), other


@needs_node
def test_cta_click_carries_the_element_and_the_scroll_depth():
    script = _script_body(abtest.beacon_script("t1", "B", URL))
    out = _run_js(_beacon_prelude(), script + "\nscrollTo(1200);\nclick(true, '/products/peak-fuji', 'ld-btn', 'mid');"
                  "\nclick(true, '/products/peak-fuji', 'pk-sticky op-btn', 'sticky');"
                  "\nclick(true, '/products/peak-fuji', 'ld-btn');"
                  "\nclick(true, '/products/peak-fuji', 'pk-card');")
    view = _events(out, "view")[0]
    assert view["vh"] == 800 and view["vw"] == 390 and view["cn"] == "4g"
    ctas = _events(out, "cta")
    # (1200 + 800) / 4000 = 50%
    assert [(c["el"], c["y"]) for c in ctas] == [("mid", 50), ("sticky", 50), ("other", 50)]


@needs_node
def test_sms_link_is_its_own_event_never_a_cta():
    script = _script_body(abtest.beacon_script("t1", "A", URL))
    out = _run_js(_beacon_prelude(), script + "\nclick(true, '', 'ld-text', 'closing', 'sms:+15555550123');"
                  "\nclick(true, '', 'ld-btn', null, 'SMS:+15555550123');")
    assert _events(out, "cta") == []
    assert [(e["el"], e["y"]) for e in _events(out, "sms_tap")] == [("closing", 20), ("other", 20)]
    assert out["fetches"] == []


@needs_node
def test_leave_on_hide_sends_deepest_scroll_time_and_load_timings():
    script = _script_body(abtest.beacon_script("t1", "A", URL))
    out = _run_js(_beacon_prelude(), script + "\nscrollTo(2400);\nscrollTo(400);\nfire('pointerdown');\nhide();"
                  "\nfire('pagehide');")
    leaves = _events(out, "leave")
    assert len(leaves) == 2  # the receiver keeps one row per visitor (max scroll, first timings)
    leave = leaves[0]
    assert leave["m"] == 80 and leave["lcp"] == 2711 and leave["fcp"] == 640 and leave["dcl"] == 812
    assert leave["s"] >= 0 and leave["hid"] >= 0


# ---------------------------------------------------------------------------
# the event store
# ---------------------------------------------------------------------------

def test_parse_event_keeps_bounded_extras_and_drops_bad_ones():
    ev, why = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "cta", "vid": "abc123def456",
                                               "el": "hero", "y": 37}))
    assert why is None and ev["extra"] == {"el": "hero", "y": 37}
    ev, _ = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "cta", "vid": "abc123def456",
                                             "el": "<script>", "y": 400}))
    assert ev["extra"] == {"el": "other"}
    ev, _ = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "leave", "vid": "abc123def456", "m": 64,
                                             "s": 31, "lcp": 2711, "fcp": 640, "dcl": 812, "hid": 900,
                                             "vh": 640, "vw": 360, "cn": "3g"}))
    assert ev["extra"] == {"max_scroll": 64, "secs": 31, "lcp_ms": 2711, "fcp_ms": 640, "dcl_ms": 812,
                           "hidden_ms": 900, "vh": 640, "vw": 360, "conn": "3g"}
    ev, _ = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "leave", "vid": "abc123def456",
                                             "m": True, "s": -1, "lcp": None, "cn": "5g"}))
    assert "extra" not in ev
    ev, _ = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "sms_tap", "vid": "abc123def456"}))
    assert ev["extra"] == {"el": "sms"}
    # the old shape still parses exactly as before
    ev, _ = abevents.parse_event(json.dumps({"t": "t1", "v": "A", "e": "view", "vid": "abc123def456"}))
    assert ev == {"t": "t1", "v": "A", "e": "view", "vid": "abc123def456"}


def _old_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, test_id TEXT NOT NULL,"
                 " variant TEXT NOT NULL, event TEXT NOT NULL, vid TEXT NOT NULL, iph TEXT NOT NULL,"
                 " UNIQUE (test_id, variant, vid, event))")
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    for i in range(4):
        conn.execute("INSERT INTO events (ts, test_id, variant, event, vid, iph) VALUES (1, 't1', 'A', 'view', ?, 'x')",
                     (f"old{i:06d}",))
    conn.execute("INSERT INTO events (ts, test_id, variant, event, vid, iph) VALUES (1, 't1', 'A', 'cta', 'old000000', 'x')")
    conn.commit()
    conn.close()


def test_an_old_database_gains_the_columns_and_keeps_its_counts(tmp_path):
    db = tmp_path / "events.sqlite"
    _old_db(db)
    assert abevents.counts(db, "t1") == {"A": {"views": 4, "clicks": 1}}
    conn = abevents.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert {"el", "y", "max_scroll", "secs", "lcp_ms", "fcp_ms", "dcl_ms", "hidden_ms", "vh", "vw", "conn"} <= cols
    abevents.record_event(conn, test_id="t1", key="A", event="cta", vid="new000001", iph="x",
                          extra={"el": "hero", "y": 20})
    conn.close()
    assert abevents.counts(db, "t1") == {"A": {"views": 4, "clicks": 2}}
    eng = abevents.engagement(db, "t1")["A"]
    assert eng["by_el"] == {"unknown": 1, "hero": 1}  # the old click has no element
    # connecting again changes nothing
    abevents.connect(db).close()


def test_repeat_leave_keeps_the_deepest_scroll_and_the_first_timings(tmp_path):
    db = tmp_path / "events.sqlite"
    conn = abevents.connect(db)
    kw = dict(test_id="t1", key="A", event="leave", vid="vis000001", iph="x")
    abevents.record_event(conn, **kw, extra={"max_scroll": 40, "secs": 12, "lcp_ms": 2100, "hidden_ms": 800})
    abevents.record_event(conn, **kw, extra={"max_scroll": 90, "secs": 8, "lcp_ms": 3000})
    abevents.record_event(conn, **kw, extra={"max_scroll": 30, "secs": 50})
    row = conn.execute("SELECT max_scroll, secs, lcp_ms, hidden_ms FROM events WHERE event = 'leave'").fetchall()
    conn.close()
    assert row == [(90, 50, 2100, 800)]
    # a duplicate cta stays a no-op (unique visitors)
    conn = abevents.connect(db)
    assert abevents.record_event(conn, test_id="t1", key="A", event="cta", vid="vis000001", iph="x",
                                 extra={"el": "hero"})
    assert not abevents.record_event(conn, test_id="t1", key="A", event="cta", vid="vis000001", iph="x",
                                     extra={"el": "sticky"})
    conn.close()


def test_receiver_stores_the_new_fields_and_sms_taps_are_not_clicks(beacon_client):  # noqa: F811
    base = {"t": "hc-ab12", "v": "B", "vid": "abc123def456"}
    assert _post(beacon_client, {**base, "e": "view", "vh": 640, "vw": 360, "cn": "4g"}).status_code == 204
    assert _post(beacon_client, {**base, "e": "sms_tap", "el": "closing", "y": 90}).status_code == 204
    assert _post(beacon_client, {**base, "e": "cta", "el": "hero", "y": 20}).status_code == 204
    assert _post(beacon_client, {**base, "e": "leave", "m": 95, "s": 44, "lcp": 1900}).status_code == 204
    counts = abevents.counts(abevents.db_path(TENANT), "hc-ab12")
    assert counts["B"] == {"views": 1, "clicks": 1, "sms_taps": 1}
    conn = abevents.connect(abevents.db_path(TENANT))
    rows = conn.execute("SELECT event, el, y, max_scroll, secs, lcp_ms, vh, vw, conn FROM events ORDER BY id").fetchall()
    conn.close()
    assert rows == [("view", None, None, None, None, None, 640, 360, "4g"),
                    ("sms_tap", "closing", 90, None, None, None, None, None, None),
                    ("cta", "hero", 20, None, None, None, None, None, None),
                    ("leave", None, None, 95, 44, 1900, None, None, None)]


# ---------------------------------------------------------------------------
# results + ad angle
# ---------------------------------------------------------------------------

def _record(conn, key, event, vid, **extra):
    abevents.record_event(conn, test_id="hc-ab12", key=key, event=event, vid=vid, iph="x", extra=extra)


def test_results_show_elements_scroll_lcp_and_small_screens(capsys):
    rec = {"test_id": "hc-ab12", "status": "live", "name": "x", "ad_angle": "sale",
           "variants": [{"key": "A", "arm": "landing:peak", "run_dir": None},
                        {"key": "B", "arm": "landing:proof", "run_dir": None},
                        {"key": "C", "arm": "external:home", "target": "home", "url": "https://x/"}]}
    abtest.save_test(TENANT, rec)
    conn = abevents.connect(abevents.db_path(TENANT))
    for i, (vh, lcp, m) in enumerate([(640, 1800, 30), (700, 2400, 60), (844, 3100, 100), (600, 5200, 10)]):
        vid = f"vis{i:06d}"
        _record(conn, "A", "view", vid, vh=vh, vw=390)
        _record(conn, "A", "leave", vid, max_scroll=m, secs=10 * (i + 1), lcp_ms=lcp)
    _record(conn, "A", "cta", "vis000000", el="hero", y=20)
    _record(conn, "A", "cta", "vis000001", el="sticky", y=60)
    _record(conn, "A", "cta", "vis000002", el="hero", y=30)
    _record(conn, "A", "sms_tap", "vis000003", el="closing", y=95)
    _record(conn, "B", "view", "old000001")
    conn.close()
    rows = abtest.results(TENANT, rec)
    a = rows[0]["engagement"]
    assert a["by_el"] == {"hero": 2, "sticky": 1}
    assert a["click_depth"] == {"0-24%": 1, "25-49%": 1, "50-74%": 1, "75-99%": 0, "100%": 0}
    assert a["max_scroll"] == {"0-24%": 1, "25-49%": 1, "50-74%": 1, "75-99%": 0, "100%": 1}
    assert a["sms_taps"] == 1 and a["leaves"] == 4
    assert a["lcp_p50"] == 2400 and a["lcp_p75"] == 3100 and a["lcp_slow_share"] == 0.5
    assert a["small_vh_share"] == 0.5  # 640 and 600 are under 700
    assert rows[0]["clicks"] == 3 and rows[0]["views"] == 4
    text = abtest.format_results(rec, rows)
    assert "ad angle: sale" in text
    assert "A  clicks by element: hero 2, sticky 1 | sms taps 1" in text
    assert "deepest scroll (4 left): 0-24% 1 25-49% 1 50-74% 1 75-99% 0 100% 1" in text
    assert "LCP (4): p50 2.4s p75 3.1s, over 2.5s 50% | viewport under 700px tall 50% of 4" in text
    assert "\nB  clicks by element" not in text  # an arm with only old-style events prints no detail line


def test_results_of_a_test_with_only_old_events_read_as_before():
    rec = {"test_id": "hc-ab12", "status": "live", "name": "x",
           "variants": [{"key": k, "arm": "landing:proof"} for k in "AB"]}
    conn = abevents.connect(abevents.db_path(TENANT))
    _record(conn, "A", "view", "vis000001")
    _record(conn, "A", "cta", "vis000001")
    conn.close()
    rows = abtest.results(TENANT, rec)
    assert (rows[0]["views"], rows[0]["clicks"]) == (1, 1)
    lines = abtest.format_results(rec, rows).splitlines()
    assert len(lines) == 4 and "ad angle: other" in lines[0]


@pytest.mark.parametrize("brief,angle", [
    ({"hook": "$300 off every indoor sauna. $500 off outdoor.", "offer": {"amounts": ["$300"]}}, "sale"),
    ({"hook": "Prices go up Thursday", "angle": "deadline", "promise": ""}, "sale"),
    ({"hook": "Own a sauna for $312/mo", "angle": "affordable", "promise": ""}, "financing"),
    ({"hook": "My legs used to be sore for days", "angle": "Post-workout recovery for runners"}, "recovery"),
    ({"hook": "It fits in my apartment", "angle": "small space", "promise": "plugs into a standard outlet"},
     "apartment"),
    ({"hook": "Why I bought a sauna at 52", "angle": "", "promise": ""}, "story"),
    ({"hook": "The evening ritual", "angle": "", "promise": "", "speaker_pov": "first_person"}, "story"),
    ({"hook": "Cedar, light and heat", "angle": "craft", "promise": "", "speaker_pov": "brand"}, "other"),
    ({}, "other"),
])
def test_ad_angle(brief, angle):
    assert abtest.ad_angle(brief) == angle


def test_ad_angle_is_read_from_the_arm_run_and_shown_in_status(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    (run / "ad_brief.json").write_text(json.dumps({"hook": "See monthly payments", "angle": "financing"}))
    rec = {"test_id": "hc-ab12", "status": "live", "name": "Fin", "created_at": "2026-10-15",
           "variants": [{"key": "A", "arm": "landing:peak", "run_dir": str(run)}]}
    assert abtest.ad_angle_of(rec) == "financing"
    abtest.save_test(TENANT, rec)
    assert cli.main(["abtest", "status", "--tenant", TENANT.name]) == 0
    assert re.search(r"hc-ab12\s+live\s+financing\s+A=landing:peak", capsys.readouterr().out)
    assert cli.main(["abtest", "status", "hc-ab12", "--tenant", TENANT.name]) == 0
    assert "ad angle: financing" in capsys.readouterr().out
