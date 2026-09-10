"""Runtime constants. Server paths default to the prod layout (SERVER FACTS in the
project brief); every path is overridable via CLI flags so local dev / tests never
depend on these existing.
"""
import os

DEFAULT_MODEL = "claude-sonnet-5"

FFMPEG_BIN = "/usr/bin/ffmpeg"
WHISPER_BIN = os.path.expanduser("~/advertorial/vendor/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.path.expanduser("~/advertorial/models/ggml-small.en.bin")

# Cost estimate rates, labeled as an estimate everywhere they're used (log.py).
INPUT_COST_PER_M = 3.0
OUTPUT_COST_PER_M = 15.0
