"""`harness doctor` -- can this tenant actually run?

Answers, in one pass and one table, the questions an operator otherwise finds
out the hard way in the middle of a run: are the tenant's files there and
valid, is every credential this tenant needs present in the environment, are
the configured model ids reachable, are whisper and ffmpeg installed where the
flags say, and can the harness write to the directories a run needs.

A credential is only ever checked BY NAME. This module reads
`os.environ` to ask whether a key is set and never prints, logs, or returns its
value -- the same rule as every other module that touches a secret.

The model check calls the models endpoint (`GET /v1/models`), which spends no
tokens and costs nothing. `--offline` skips it.
"""
import os
import shutil
from pathlib import Path

from .config import REPO_ROOT


def _short(path):
    """A path relative to the repo root when it is inside it -- the absolute
    form makes every row of the table wider than a terminal."""
    path = Path(path)
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


class Check:
    def __init__(self, name, status, detail=""):
        self.name = name
        self.status = status
        self.detail = detail


# Files a configured tenant is expected to have. `required` means a run cannot
# work without it; the rest degrade (the renderer falls back to the harness's
# own CSS/byline and logs a warning).
TENANT_FILES = (
    ("tenant.yaml", True),
    ("authors.yaml", True),
    ("vocab.yaml", True),
    ("claims/verified.json", True),
    ("claims/products.json", True),
    ("claims/config.json", False),
    ("brand/base.css", False),
    ("brand/byline.html", False),
)


def check_files(tenant):
    checks = []
    for rel, required in TENANT_FILES:
        path = tenant.root / rel
        if path.exists():
            checks.append(Check(f"file {rel}", PASS, _short(path)))
        else:
            checks.append(Check(f"file {rel}", FAIL if required else WARN, f"missing: {_short(path)}"))
    return checks


def check_config(tenant):
    """Runs the same validation `load_tenant` runs, and reports each problem as
    its own row so an operator sees all of them at once instead of only the
    first one to raise."""
    checks = []
    try:
        errors, warnings = tenant.validate()
    except Exception as e:
        return [Check("tenant.yaml parses", FAIL, str(e))]
    checks.append(Check("tenant.yaml parses", PASS, ""))
    for message in errors:
        checks.append(Check("tenant.yaml valid", FAIL, message))
    for message in warnings:
        checks.append(Check("tenant.yaml valid", WARN, message))
    if not errors and not warnings:
        checks.append(Check("tenant.yaml valid", PASS, f"schema_version {tenant.schema_version}"))

    missing = tenant.missing_pieces()
    checks.append(
        Check("tenant configured", PASS, "") if not missing
        else Check("tenant configured", FAIL, "missing " + ", ".join(missing))
    )
    # R23: a key set in both tenant.yaml and claims/config.json with different
    # values is legal (config.json wins) but must be visible.
    for key, yaml_value, json_value in tenant.config_disagreements():
        checks.append(Check(
            f"config {key}",
            WARN,
            f"tenant.yaml says {yaml_value!r}; claims/config.json says {json_value!r} and wins",
        ))
    return checks


def required_env_keys(tenant):
    """[(name, blocks_a_run, what_it_blocks)] for the environment variables
    THIS tenant needs, given what its tenant.yaml turns on. Names only -- no
    value is read here or anywhere else in this module.

    Only the model key blocks a run. A missing publish credential or
    notification channel is a WARN: `harness run` works fine without them, and
    both `harness publish` and `harness/notify.py` already fail closed with
    their own clear message."""
    keys = [("ANTHROPIC_API_KEY", True, "every run")]
    if (tenant.get("publisher") or "export") == "shopify":
        keys += [
            ("SHOPIFY_STORE", False, "harness publish"),
            ("SHOPIFY_TOKEN", False, "harness publish"),
        ]
    notifications = tenant.get("notifications") or {}
    if notifications.get("slack"):
        keys.append(("SLACK_WEBHOOK_URL", False, "slack notifications"))
    if notifications.get("email"):
        keys += [
            (name, False, "email notifications")
            for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "NOTIFY_FROM")
        ]
    return keys


def check_env(tenant, environ=None):
    environ = os.environ if environ is None else environ
    checks = []
    env_file = tenant.env_path
    checks.append(
        Check("tenant .env", PASS, f"{_short(env_file)} present")
        if env_file.exists()
        else Check("tenant .env", WARN, f"no {_short(env_file)}; keys come from the environment")
    )
    for name, blocking, blocks in required_env_keys(tenant):
        if environ.get(name):
            # Set, and that is all this ever reports -- never the value, never
            # a prefix of it, never its length.
            checks.append(Check(f"env {name}", PASS, "set"))
        else:
            checks.append(
                Check(f"env {name}", FAIL if blocking else WARN, f"not set; blocks {blocks}")
            )
    return checks


def check_models(tenant, client=None):
    """Every model id this tenant's stages are configured to use, against the
    models endpoint. Listing models spends no tokens.

    A configured id that the endpoint does not list is a WARN, not a FAIL: the
    API accepts alias ids that the listing does not always echo back, so a
    missing entry is worth an operator's attention but is not proof the run
    will fail."""
    from .config import DEFAULT_MODELS

    stages = sorted(DEFAULT_MODELS)
    configured = {stage: tenant.model_for(stage) for stage in stages}

    if client is None:
        from .anthropic_client import make_client

        client = make_client()
    try:
        listed = {m.id for m in client.models.list(limit=100).data}
    except Exception as e:
        return [Check("models endpoint", FAIL, f"unreachable: {type(e).__name__}: {e}")]

    checks = [Check("models endpoint", PASS, f"{len(listed)} model(s) listed")]
    for stage, model in configured.items():
        if model in listed:
            checks.append(Check(f"model {stage}", PASS, model))
        else:
            checks.append(Check(f"model {stage}", WARN, f"{model} not in the listing (alias?)"))
    return checks


def check_tools(*, ffmpeg_bin, whisper_bin, whisper_model):
    """ffmpeg and whisper are only needed for a video ad -- a still or a text
    fixture never touches either -- so a missing one is a WARN."""
    checks = []
    for name, path in (("ffmpeg", ffmpeg_bin), ("whisper", whisper_bin)):
        resolved = path if Path(path).exists() else shutil.which(str(path))
        checks.append(
            Check(f"tool {name}", PASS, str(resolved)) if resolved
            else Check(f"tool {name}", WARN, f"not found at {path}; video ads will fail")
        )
    model_path = Path(whisper_model)
    checks.append(
        Check("whisper model", PASS, str(model_path)) if model_path.exists()
        else Check("whisper model", WARN, f"not found at {model_path}; video ads will fail")
    )
    return checks


def check_dirs(tenant):
    """out/ and runs/ are created on demand by a run, so the real question is
    whether they CAN be created and written to."""
    checks = []
    for label, path in (("out", tenant.out_dir), ("runs", tenant.runs_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-write-probe"
            probe.write_text("")
            probe.unlink()
            checks.append(Check(f"dir {label}", PASS, f"{_short(path)} writable"))
        except OSError as e:
            checks.append(Check(f"dir {label}", FAIL, f"{_short(path)} not writable: {e}"))
    return checks


def run_checks(tenant, *, ffmpeg_bin, whisper_bin, whisper_model, client=None, offline=False,
               environ=None):
    checks = []
    checks += check_files(tenant)
    checks += check_config(tenant)
    checks += check_env(tenant, environ=environ)
    if offline:
        checks.append(Check("models endpoint", WARN, "skipped (--offline)"))
    else:
        checks += check_models(tenant, client=client)
    checks += check_tools(ffmpeg_bin=ffmpeg_bin, whisper_bin=whisper_bin, whisper_model=whisper_model)
    checks += check_dirs(tenant)
    return checks


def format_table(tenant, checks):
    width = max([len(c.name) for c in checks] + [10])
    lines = [f"harness doctor -- tenant {tenant.name}", ""]
    lines.append(f"{'CHECK'.ljust(width)}  STATUS  DETAIL")
    lines.append(f"{'-' * width}  ------  ------")
    for c in checks:
        lines.append(f"{c.name.ljust(width)}  {c.status:<6}  {c.detail}")
    failed = sum(1 for c in checks if c.status == FAIL)
    warned = sum(1 for c in checks if c.status == WARN)
    lines.append("")
    lines.append(
        f"{len(checks)} check(s): {len(checks) - failed - warned} pass, {warned} warn, {failed} fail"
    )
    return "\n".join(lines)
