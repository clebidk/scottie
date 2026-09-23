"""Env-guard: a module- or session-scoped fixture that loads a tenant .env
cannot leak it into later modules, and does not do so silently.

Each case runs an inner pytest session (pytester) with this suite's own
conftest, plus one line that makes a probe key count as a tenant .env key,
so no real tenant .env is read or loaded.
"""
from pathlib import Path

CONFTEST = (Path(__file__).parent / "conftest.py").read_text()
PROBE = "ENVGUARD_PROBE_TOKEN"

LEAKING_MODULE = f'''
import pytest
from dotenv import load_dotenv


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    env = tmp_path_factory.mktemp("tenant") / ".env"
    env.write_text("{PROBE}=probe-value\\n")
    load_dotenv(env, override=False)


def test_uses_the_loaded_env(loaded):
    import os
    assert os.environ["{PROBE}"] == "probe-value"
'''

LATER_MODULE = f'''
import os


def test_later_module_does_not_see_the_probe():
    assert "{PROBE}" not in os.environ
'''


def _run(pytester, probe_counts_as_tenant_key):
    conftest = CONFTEST.replace('pytest_plugins = ["pytester"]\n', "")
    if probe_counts_as_tenant_key:
        conftest += f"\n\ndef _tenant_env_key_names():\n    return {{{PROBE!r}}}\n"
    pytester.makeconftest(conftest)
    pytester.makepyfile(test_a_leaks=LEAKING_MODULE, test_b_later=LATER_MODULE)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:randomly")


def test_a_module_fixture_that_loads_an_env_does_not_leak_into_the_next_module(pytester):
    result = _run(pytester, probe_counts_as_tenant_key=False)
    result.assert_outcomes(passed=2)


def test_a_leaked_tenant_env_key_fails_the_module_that_leaked_it_by_name(pytester):
    result = _run(pytester, probe_counts_as_tenant_key=True)
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines([f"*module-scoped fixture*{PROBE}*"])
    assert "probe-value" not in result.stdout.str()
