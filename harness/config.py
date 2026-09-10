"""Runtime constants for the engine itself -- nothing company-specific.

Server paths default to the deployment layout; every path is overridable via a
CLI flag, so local dev and tests never depend on these existing. Anything that
names a company lives in tenant data, not here.
"""
import os

DEFAULT_MODEL = "claude-sonnet-5"

REPO_DIR = os.environ.get("HARNESS_REPO_DIR", os.path.expanduser("~/advertorial"))

FFMPEG_BIN = "/usr/bin/ffmpeg"
WHISPER_BIN = os.path.join(REPO_DIR, "vendor/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.path.join(REPO_DIR, "models/ggml-small.en.bin")

# Cost estimate rates, labeled as an estimate everywhere they're used (log.py).
INPUT_COST_PER_M = 3.0
OUTPUT_COST_PER_M = 15.0
