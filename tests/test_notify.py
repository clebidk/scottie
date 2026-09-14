"""Cycle 20: harness/notify.py -- message formatting (including the 120-char
overclaim truncation) and the skip-when-unconfigured fail-closed behavior.
No network call is ever made when a channel is off or a credential is
missing; every test here proves that by never faking a working transport for
the skip path."""


from harness import notify


class FakeTenant:
    def __init__(self, *, notifications=None, display_name="Acme"):
        self._notifications = notifications or {}
        self.display_name = display_name
        self.name = "acme"

    def get(self, key, default=None):
        if key == "notifications":
            return self._notifications
        return default


class FakeLog:
    def __init__(self):
        self.events = []

    def event(self, stage, message):
        self.events.append((stage, message))


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_truncate_claim_leaves_short_text_untouched():
    assert notify._truncate_claim("short claim") == "short claim"


def test_truncate_claim_cuts_long_text_to_120_chars():
    long_claim = "x" * 300
    out = notify._truncate_claim(long_claim)
    assert len(out) == 120
    assert out.endswith("…")


def test_format_needs_review_message_never_exceeds_120_chars_per_claim():
    long_message = "OVERCLAIM " * 30  # far over 120 chars
    text = notify.format_needs_review_message(
        tenant=FakeTenant(),
        run_id="20260910-0000-test",
        input_name="fixtures/x.mov",
        pages=["article"],
        gate_log={},
        ad_not_repeated=[{"message": long_message}],
        review_paths=["out/run/REVIEW.md"],
        approve_command="harness approve out/run --by <email>",
    )
    for line in text.splitlines():
        if line.strip().startswith("- "):
            assert len(line.strip()[2:]) <= 120


def test_format_needs_review_message_includes_claim_count_not_just_text():
    text = notify.format_needs_review_message(
        tenant=FakeTenant(),
        run_id="20260910-0000-test",
        input_name="fixtures/x.mov",
        pages=["article", "longform"],
        gate_log={},
        ad_not_repeated=[{"message": "a"}, {"message": "b"}],
        review_paths=["out/run/REVIEW.md"],
        approve_command="harness approve out/run --by <email>",
    )
    assert "AD CLAIMS NOT REPEATED ON PAGE: 2" in text
    assert "article" in text and "longform" in text


# ---------------------------------------------------------------------------
# Skip when unconfigured -- no network call attempted
# ---------------------------------------------------------------------------

def test_send_slack_skips_and_logs_when_no_webhook_url(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("urlopen must not be called when no webhook url is configured")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    log = FakeLog()
    result = notify.send_slack(None, "hello", log=log)
    assert result is False
    assert ("notify", "notification skipped: no channel configured (slack)") in log.events


def test_send_email_skips_and_logs_when_smtp_unconfigured(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SMTP must not be called when unconfigured")

    monkeypatch.setattr("smtplib.SMTP", boom)
    log = FakeLog()
    result = notify.send_email(
        host=None, port=None, user=None, password=None, from_addr=None, to_addrs=[],
        subject="s", body="b", log=log,
    )
    assert result is False
    assert ("notify", "notification skipped: no channel configured (email)") in log.events


def test_notify_needs_review_skips_both_channels_when_tenant_notifications_off(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network call should happen with notifications off")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("smtplib.SMTP", boom)
    tenant = FakeTenant(notifications={"slack": False, "email": []})
    log = FakeLog()
    sent = notify.notify_needs_review(
        tenant, run_id="r1", input_name="x.mov", pages=["article"], gate_log={},
        ad_not_repeated=[], run_dir="out/r1", log=log,
    )
    assert sent == {"slack": False, "email": False}


def test_notify_approved_skips_when_slack_disabled_even_if_webhook_env_set(monkeypatch):
    """tenant.yaml's notifications.slack: false wins even if SLACK_WEBHOOK_URL
    happens to be set -- the tenant's own opt-in gates the channel, not just
    the presence of a credential."""

    def boom(*a, **k):
        raise AssertionError("slack disabled for this tenant -- must not be called")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/T000/B000/xyz")
    tenant = FakeTenant(notifications={"slack": False})
    sent = notify.notify_approved(tenant, run_id="r1", by="a@b.com", pages=["article"], run_dir="out/r1")
    assert sent["slack"] is False


def test_send_slack_posts_when_webhook_configured(monkeypatch):
    calls = []

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        calls.append(req)
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = notify.send_slack("https://hooks.slack.example/T000/B000/xyz", "hello")
    assert result is True
    assert len(calls) == 1


def test_send_slack_skips_non_https_webhook(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("urlopen must not run for a non-https webhook")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    log = FakeLog()
    assert notify.send_slack("http://hooks.example/webhook", "hello", log=log) is False
    assert any("must be https" in e[1] for e in log.events)
    assert notify.send_slack("file:///tmp/x", "hello", log=log) is False

