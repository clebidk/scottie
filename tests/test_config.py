"""harness/config.py -- review 2026-09-11 R24: the deployment root resolves
at call time, so HARNESS_REPO_DIR set after import still wins (it used to be
read once at import time)."""

from harness import config


def test_repo_dir_follows_harness_repo_dir_set_after_import(monkeypatch):
    monkeypatch.setenv("HARNESS_REPO_DIR", "/tmp/somewhere-else")
    assert config.repo_dir() == "/tmp/somewhere-else"
    assert config.whisper_bin() == "/tmp/somewhere-else/vendor/whisper.cpp/build/bin/whisper-cli"
    assert config.whisper_model() == "/tmp/somewhere-else/models/ggml-small.en.bin"


def test_repo_dir_defaults_to_the_deployment_layout(monkeypatch):
    monkeypatch.delenv("HARNESS_REPO_DIR", raising=False)
    assert config.repo_dir().endswith("/advertorial")
    assert config.FFMPEG_BIN == "/usr/bin/ffmpeg"


def test_default_models_cover_every_stage():
    assert set(config.DEFAULT_MODELS) == {"write", "repair_first", "repair_next", "ingest", "matcher"}
