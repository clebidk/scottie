#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the `harness` project.
# Safe to run repeatedly: every step is guarded or naturally re-runnable.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Python 3.13. pyproject.toml pins requires-python >=3.13, but the default
#    base image ships 3.12, so install 3.13 from deadsnakes when it is missing.
if ! command -v python3.13 >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3.13 python3.13-venv python3.13-dev
fi

# 2. ffmpeg. `harness doctor` checks for it and video-ad ingest needs it.
if ! command -v ffmpeg >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq ffmpeg
fi

# 3. Project virtualenv + editable install + dev tooling (pytest, ruff).
#    `python -m venv` reuses an existing .venv; the pip installs are idempotent.
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e . pytest ruff

echo "harness environment ready: $(.venv/bin/python --version)"
