"""Runtime constants for the engine itself -- nothing company-specific.

Server paths default to the deployment layout; every path is overridable via a
CLI flag, so local dev and tests never depend on these existing. Anything that
names a company lives in tenant data, not here.
"""
import os
from pathlib import Path

# The repository root, as one definition. harness/cli.py, harness/pipeline.py,
# harness/tenant.py and harness/workflows.py each derived this from their own
# __file__ (four identical expressions); every one of them now imports it.
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "claude-sonnet-5"

# Fix cycle 17 (model tiering): per-stage default model ids, overridable per
# tenant via tenant.yaml's `models:` section (see Tenant.model_for). Initial
# page writes stay on sonnet; a first repair tries the cheaper haiku model
# before falling back to sonnet for a second repair; ingest's ad_brief/vision
# calls and the claims semantic matcher are bounded extraction/classification
# tasks that don't need sonnet's judgment.
DEFAULT_MODELS = {
    "write": "claude-sonnet-5",
    "repair_first": "claude-haiku-4-5",
    "repair_next": "claude-sonnet-5",
    "ingest": "claude-haiku-4-5",
    "matcher": "claude-haiku-4-5",
}

# Review 2026-09-11 R24: the deployment root is resolved at CALL time, not
# import time -- HARNESS_REPO_DIR set (or changed) after import now wins, and
# tests can point the tool defaults at a tmp root with monkeypatch.setenv.
def repo_dir():
    """The deployment root the whisper binary/model defaults hang off of."""
    return os.environ.get("HARNESS_REPO_DIR", os.path.expanduser("~/advertorial"))


FFMPEG_BIN = "/usr/bin/ffmpeg"


def whisper_bin():
    return os.path.join(repo_dir(), "vendor/whisper.cpp/build/bin/whisper-cli")


def whisper_model():
    return os.path.join(repo_dir(), "models/ggml-small.en.bin")
