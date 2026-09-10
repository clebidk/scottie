"""Runtime constants for the engine itself -- nothing company-specific.

Server paths default to the deployment layout; every path is overridable via a
CLI flag, so local dev and tests never depend on these existing. Anything that
names a company lives in tenant data, not here.
"""
import os

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

REPO_DIR = os.environ.get("HARNESS_REPO_DIR", os.path.expanduser("~/advertorial"))

FFMPEG_BIN = "/usr/bin/ffmpeg"
WHISPER_BIN = os.path.join(REPO_DIR, "vendor/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.path.join(REPO_DIR, "models/ggml-small.en.bin")
