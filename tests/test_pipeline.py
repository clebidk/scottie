"""Fix cycle 23 (R29): run ids used to truncate to the minute
(`%Y%m%d-%H%M`), so two runs of the same input inside one wall-clock minute
collided into the same run directory. `make_run_id` now carries seconds plus
a random suffix, and `make_run_dir` backstops uniqueness with a real
filesystem check (os.makedirs(..., exist_ok=False), one retry).
"""
import re

import pytest

from harness import pipeline

_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]+-[a-z2-7]{4}$")


def test_make_run_id_has_seconds_and_a_4char_base32_suffix():
    run_id = pipeline.make_run_id("hidden-costs-v2")
    assert _RUN_ID_RE.match(run_id), run_id
    # slug is still readable in the middle, unchanged from before this fix
    assert "-hidden-costs-v2-" in run_id


def test_make_run_id_is_still_one_path_component():
    run_id = pipeline.make_run_id("some-slug")
    assert "/" not in run_id
    assert ".." not in run_id


def test_two_calls_at_the_same_frozen_instant_still_differ(monkeypatch):
    import datetime as dt_mod

    fixed = dt_mod.datetime(2026, 9, 10, 12, 0, 0)

    class FrozenDatetime(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(pipeline, "datetime", type("_dt", (), {"datetime": FrozenDatetime}))

    ids = {pipeline.make_run_id("slug") for _ in range(50)}
    assert all(i.startswith("20260910-120000-slug-") for i in ids)
    # 50 draws over a 32^4 (~1M) suffix space: collisions are possible but
    # exceedingly unlikely -- this is really asserting the suffix is random,
    # not fixed.
    assert len(ids) > 1


def test_make_run_dir_creates_a_real_unique_directory(tmp_path):
    run_id, run_dir = pipeline.make_run_dir(tmp_path, "hidden-costs-v2")
    assert run_dir == tmp_path / run_id
    assert run_dir.is_dir()


def test_make_run_dir_retries_once_on_a_real_collision(tmp_path, monkeypatch):
    ids = iter(["20260910-120000-slug-aaaa", "20260910-120000-slug-bbbb"])
    monkeypatch.setattr(pipeline, "make_run_id", lambda slug: next(ids))
    (tmp_path / "20260910-120000-slug-aaaa").mkdir()  # simulate an existing collision

    run_id, run_dir = pipeline.make_run_dir(tmp_path, "slug")

    assert run_id == "20260910-120000-slug-bbbb"
    assert run_dir == tmp_path / "20260910-120000-slug-bbbb"
    assert run_dir.is_dir()


def test_make_run_dir_raises_after_exhausting_the_one_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "make_run_id", lambda slug: "20260910-120000-slug-aaaa")
    (tmp_path / "20260910-120000-slug-aaaa").mkdir()

    with pytest.raises(FileExistsError):
        pipeline.make_run_dir(tmp_path, "slug")


def test_make_run_dir_leaves_older_run_dirs_readable(tmp_path):
    """Old minute-granularity run dirs (no seconds, no suffix) must not be
    touched or mistaken for a collision by the new scheme."""
    legacy = tmp_path / "20260909-2038-hidden-costs-v2"
    legacy.mkdir()
    (legacy / "marker.txt").write_text("old run, still here")

    run_id, run_dir = pipeline.make_run_dir(tmp_path, "hidden-costs-v2")

    assert run_dir != legacy
    assert legacy.is_dir()
    assert (legacy / "marker.txt").read_text() == "old run, still here"
