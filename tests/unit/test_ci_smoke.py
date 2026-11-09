"""The CI smoke script itself must be able to FAIL (a check that cannot fail proves nothing)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_smoke.py"
CMD = [sys.executable, "-m", "bili2nb"]


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("ci_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_smoke_passes_against_the_dev_environment(smoke):
    assert smoke.run(CMD) == []


def test_wrong_expected_exit_code_is_reported_as_a_failure(smoke):
    problems = smoke.run(CMD, expect_auth_exit=21)  # deliberately wrong: real code is 20
    assert problems and any("exit" in p.lower() and "20" in p for p in problems)


def test_cli_exit_code_of_the_script_is_nonzero_on_failure(smoke, capsys):
    assert smoke.main(["--cmd", " ".join(CMD), "--expect-auth-exit", "21"]) == 1
    assert smoke.main(["--cmd", " ".join(CMD)]) == 0


def test_stdout_pollution_is_detected(smoke):
    assert smoke.parse_json_stdout("x", "{\"a\": 1}\n") == {"a": 1}
    for bad in ("progress...\n{\"a\": 1}\n", "{\"a\": 1}\nextra\n", "", "not json\n", "[1]\n{\"a\":1}\n"):
        with pytest.raises(smoke.SmokeFailure):
            smoke.parse_json_stdout("x", bad)


def test_credential_like_text_in_output_is_detected(smoke):
    for leak in ("SAPISID=abc", "Set-Cookie: x", "__Secure-1PSID=zzz", "ya29.token", "Cookie: a=b"):
        with pytest.raises(smoke.SmokeFailure):
            smoke.assert_no_secrets("cmd", leak)
    smoke.assert_no_secrets("cmd", "AUTH_REQUIRED 需要重新登录")


def test_required_doctor_checks_must_be_ok_but_login_may_warn(smoke):
    ok = {"status": "warn", "checks": [{"name": n, "status": "ok"} for n in smoke.REQUIRED_DOCTOR_OK]
          + [{"name": "google-login", "status": "warn"}, {"name": "login-browser", "status": "warn"}]}
    assert smoke.doctor_problems(ok) == []
    broken = {"status": "warn", "checks": [dict(c) for c in ok["checks"]]}
    broken["checks"][0]["status"] = "warn"
    assert smoke.doctor_problems(broken)
    missing = {"status": "ok", "checks": ok["checks"][1:]}
    assert smoke.doctor_problems(missing)


def test_bare_python_in_cmd_means_the_interpreter_running_the_script(smoke):
    """Live CI failure (Windows): a bare `python` was resolved to the runner's system Python, which
    does not have bili2nb installed. `python` must mean sys.executable (the uv venv interpreter)."""
    assert smoke.resolve_cmd(["python", "-m", "bili2nb"]) == [sys.executable, "-m", "bili2nb"]
    assert smoke.resolve_cmd(["python3", "-m", "bili2nb"]) == [sys.executable, "-m", "bili2nb"]
    assert smoke.resolve_cmd(["bili2nb"]) == ["bili2nb"]  # a launcher is resolved through PATH
    assert smoke.resolve_cmd(["/abs/python", "-m", "x"]) == ["/abs/python", "-m", "x"]  # explicit path kept
