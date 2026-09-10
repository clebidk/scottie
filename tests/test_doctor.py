"""`harness doctor` -- Cycle 22.

The check that matters most here is the one about secrets: doctor reports
whether a credential is SET, by name, and must never print, return, or leak the
value.
"""

from harness import doctor, exits, tenant as tenant_mod
from harness.doctor import FAIL, PASS, WARN


TENANT = tenant_mod.load_tenant("peak-saunas")

SECRET = "sk-ant-do-not-print-me-0123456789"


class _FakeModel:
    def __init__(self, model_id):
        self.id = model_id


class _FakeModelList:
    def __init__(self, ids):
        self.data = [_FakeModel(i) for i in ids]


class _FakeModels:
    def __init__(self, ids, error=None):
        self._ids = ids
        self._error = error
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _FakeModelList(self._ids)


class _FakeClient:
    def __init__(self, ids, error=None):
        self.models = _FakeModels(ids, error)


def _status(checks, name):
    return next(c.status for c in checks if c.name == name)


# ---------------------------------------------------------------------------
# Secrets: by name only
# ---------------------------------------------------------------------------

def test_a_set_credential_is_reported_without_its_value():
    checks = doctor.check_env(TENANT, environ={"ANTHROPIC_API_KEY": SECRET})
    row = next(c for c in checks if c.name == "env ANTHROPIC_API_KEY")
    assert row.status == PASS
    assert row.detail == "set"
    assert SECRET not in row.detail
    assert SECRET not in "".join(f"{c.name}{c.detail}" for c in checks)


def test_the_rendered_table_never_contains_a_credential():
    checks = doctor.check_env(TENANT, environ={"ANTHROPIC_API_KEY": SECRET, "SHOPIFY_TOKEN": SECRET})
    table = doctor.format_table(TENANT, checks)
    assert SECRET not in table
    assert "sk-ant" not in table


def test_a_missing_model_key_blocks_a_run_but_a_missing_publish_key_does_not():
    """A run works fine without publish credentials; `harness publish` fails
    closed on its own. Only the model key is a FAIL."""
    checks = doctor.check_env(TENANT, environ={})
    assert _status(checks, "env ANTHROPIC_API_KEY") == FAIL
    assert _status(checks, "env SHOPIFY_STORE") == WARN


def test_only_the_keys_this_tenants_config_turns_on_are_checked():
    names = [n for n, _blocking, _blocks in doctor.required_env_keys(TENANT)]
    assert "ANTHROPIC_API_KEY" in names
    # peak-saunas is publisher: shopify with notifications off
    assert "SHOPIFY_TOKEN" in names
    assert "SLACK_WEBHOOK_URL" not in names
    assert "SMTP_HOST" not in names


# ---------------------------------------------------------------------------
# Models: a zero-cost listing, never a completion
# ---------------------------------------------------------------------------

def test_the_model_check_lists_models_and_never_creates_a_message():
    client = _FakeClient(["claude-sonnet-5", "claude-haiku-4-5"])
    checks = doctor.check_models(TENANT, client=client)
    assert _status(checks, "models endpoint") == PASS
    assert all(c.status == PASS for c in checks)
    # models.list only -- no messages attribute is ever touched.
    assert client.models.calls == [{"limit": 100}]
    assert not hasattr(client, "messages")


def test_a_model_id_the_endpoint_does_not_list_is_a_warning_not_a_failure():
    """The API accepts alias ids the listing does not always echo back, so a
    missing entry is worth attention but is not proof the run will fail."""
    checks = doctor.check_models(TENANT, client=_FakeClient(["something-else"]))
    assert _status(checks, "models endpoint") == PASS
    assert any(c.status == WARN and c.name.startswith("model ") for c in checks)


def test_an_unreachable_endpoint_is_one_failed_row_not_a_traceback():
    checks = doctor.check_models(TENANT, client=_FakeClient([], error=OSError("no route to host")))
    assert len(checks) == 1
    assert checks[0].status == FAIL
    assert "unreachable" in checks[0].detail


# ---------------------------------------------------------------------------
# Files, config, tools, dirs
# ---------------------------------------------------------------------------

def test_a_configured_tenants_files_all_pass():
    assert all(c.status == PASS for c in doctor.check_files(TENANT))


def test_config_checks_report_the_schema_version():
    checks = doctor.check_config(TENANT)
    assert all(c.status == PASS for c in checks)
    assert any("schema_version 1" in c.detail for c in checks)


def test_a_missing_tool_is_a_warning_because_only_video_needs_it(tmp_path):
    checks = doctor.check_tools(
        ffmpeg_bin=str(tmp_path / "nope"),
        whisper_bin=str(tmp_path / "nope"),
        whisper_model=str(tmp_path / "nope.bin"),
    )
    assert [c.status for c in checks] == [WARN, WARN, WARN]
    assert all("video ads will fail" in c.detail for c in checks)


def test_an_unwritable_cache_dir_is_a_failure(tmp_path):
    """A run creates out/ and runs/ on demand, so the real question is whether
    it can. Here they sit under a regular file, so mkdir raises."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")

    class _FakeTenant:
        name = "acme"
        out_dir = blocker / "out"
        runs_dir = blocker / "runs"

    checks = doctor.check_dirs(_FakeTenant())
    assert [c.status for c in checks] == [FAIL, FAIL]
    assert all("not writable" in c.detail for c in checks)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def test_offline_skips_the_network_check_and_says_so():
    checks = doctor.run_checks(
        TENANT, ffmpeg_bin="/bin/sh", whisper_bin="/bin/sh", whisper_model="/bin/sh",
        offline=True, environ={"ANTHROPIC_API_KEY": SECRET},
    )
    row = next(c for c in checks if c.name == "models endpoint")
    assert row.status == WARN
    assert "--offline" in row.detail


def test_doctor_exits_1_when_something_failed(monkeypatch, capsys):
    from harness import cli

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    args = cli.build_parser().parse_args(
        ["doctor", "--tenant", "peak-saunas", "--offline",
         "--ffmpeg-bin", "/nope", "--whisper-bin", "/nope", "--whisper-model", "/nope"]
    )
    assert cli.cmd_doctor(args) == exits.USAGE
    out = capsys.readouterr().out
    assert "harness doctor -- tenant peak-saunas" in out
    assert "STATUS" in out


def test_doctor_exits_0_when_everything_that_blocks_a_run_passes(monkeypatch, capsys):
    from harness import cli

    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    args = cli.build_parser().parse_args(
        ["doctor", "--tenant", "peak-saunas", "--offline",
         "--ffmpeg-bin", "/bin/sh", "--whisper-bin", "/bin/sh", "--whisper-model", "/bin/sh"]
    )
    assert cli.cmd_doctor(args) == exits.OK
    assert SECRET not in capsys.readouterr().out
