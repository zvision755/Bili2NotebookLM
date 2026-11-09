"""V1.2 login: one default (Playwright Chromium) on every OS; we own the Chromium download step.

Nothing here starts a browser, touches the network or reads Google credentials.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from bili2nb import doctor
from bili2nb.errors import Bili2nbError
from bili2nb.notebook import auth


@pytest.fixture
def calls(monkeypatch):
    """Record every subprocess the login code would run; each returns rc from `rcs` (default 0)."""
    rec: dict = {"cmds": [], "rcs": []}

    def fake_run(cmd, **kw):
        rec["cmds"].append(list(cmd))
        rc = rec["rcs"].pop(0) if rec["rcs"] else 0
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(auth, "_run", fake_run)
    return rec


def _is_install(cmd):
    return cmd[-3:] == ["-m", "playwright", "install"] or cmd[-4:] == ["-m", "playwright", "install", "chromium"]


def _login_cmds(rec):
    return [c for c in rec["cmds"] if "notebooklm" in c and "login" in c]


@pytest.mark.parametrize("plat", ["darwin", "win32", "linux"])
def test_default_browser_is_chromium_on_every_platform(monkeypatch, plat):
    monkeypatch.setattr(sys, "platform", plat)
    assert auth.default_browser() == "chromium"


def test_login_defaults_to_chromium_and_skips_install_when_present(calls, monkeypatch):
    monkeypatch.setattr(auth, "chromium_installed", lambda: True)
    auth.run_login()
    (cmd,) = _login_cmds(calls)
    assert cmd[cmd.index("--browser") + 1] == "chromium"
    assert not any(_is_install(c) for c in calls["cmds"])


def test_missing_chromium_is_installed_first_with_visible_progress(calls, monkeypatch, capsys):
    state = {"installed": False}
    monkeypatch.setattr(auth, "chromium_installed", lambda: state["installed"])

    real = auth._run

    def run(cmd, **kw):
        if _is_install(cmd):
            state["installed"] = True
            assert "capture_output" not in kw  # inherits the terminal so progress is visible
        return real(cmd, **kw)

    monkeypatch.setattr(auth, "_run", run)
    auth.run_login()
    kinds = ["install" if _is_install(c) else "login" for c in calls["cmds"]]
    assert kinds == ["install", "login"]
    err = capsys.readouterr().err
    assert "170" in err and "Chrome" in err  # size + "your own browsers are not touched"


def test_install_failure_is_actionable(calls, monkeypatch):
    monkeypatch.setattr(auth, "chromium_installed", lambda: False)
    calls["rcs"] = [1]  # the install command fails
    with pytest.raises(Bili2nbError) as ei:
        auth.run_login()
    e = ei.value
    assert e.code == "AUTH_REQUIRED" and e.extra["reason"] == "browser_download_failed"
    text = e.message + (e.detail or "")
    assert "bili2nb auth login" in text and "--browser chrome" in text and "msedge" in text
    assert not _login_cmds(calls)  # never tries to log in without a browser


def test_launch_or_unfinished_login_failure_is_reported_as_such(calls, monkeypatch):
    monkeypatch.setattr(auth, "chromium_installed", lambda: True)
    calls["rcs"] = [1]
    with pytest.raises(Bili2nbError) as ei:
        auth.run_login()
    e = ei.value
    assert e.code == "AUTH_REQUIRED" and e.extra["reason"] == "login_not_completed"
    assert "bili2nb auth login" in e.message  # how to retry


def test_explicit_system_browsers_are_passed_through_without_chromium_probe(calls, monkeypatch):
    monkeypatch.setattr(auth, "chromium_installed", lambda: pytest.fail("must not probe for chromium"))
    for b in ("chrome", "msedge"):
        calls["cmds"].clear()
        auth.run_login(b)
        (cmd,) = _login_cmds(calls)
        assert cmd[cmd.index("--browser") + 1] == b


def test_unknown_browser_is_rejected(calls):
    with pytest.raises(Bili2nbError) as ei:
        auth.run_login("firefox")
    assert ei.value.code == "INTERNAL" and not calls["cmds"]


def test_login_timeout_is_forwarded(calls, monkeypatch):
    monkeypatch.setattr(auth, "chromium_installed", lambda: True)
    auth.run_login(timeout_sec=123)
    (cmd,) = _login_cmds(calls)
    assert cmd[cmd.index("--browser-timeout") + 1] == "123"


# ----------------------------------------------------------------- interactive fallback
def _failing_chromium(monkeypatch, calls, system=("chrome",)):
    monkeypatch.setattr(auth, "chromium_installed", lambda: True)
    monkeypatch.setattr(auth, "system_browsers", lambda: list(system))
    calls["rcs"] = [1]  # first (chromium) login fails, the next succeeds


def test_fallback_offers_system_browser_only_when_interactive_and_asked(calls, monkeypatch):
    _failing_chromium(monkeypatch, calls)
    asked = []
    auth.login_with_fallback(None, interactive=True, confirm=lambda msg: asked.append(msg) or True)
    browsers = [c[c.index("--browser") + 1] for c in _login_cmds(calls)]
    assert browsers == ["chromium", "chrome"] and len(asked) == 1


def test_fallback_declined_keeps_the_error(calls, monkeypatch):
    _failing_chromium(monkeypatch, calls)
    with pytest.raises(Bili2nbError):
        auth.login_with_fallback(None, interactive=True, confirm=lambda msg: False)
    assert len(_login_cmds(calls)) == 1


def test_fallback_never_asks_non_interactively(calls, monkeypatch):
    _failing_chromium(monkeypatch, calls)
    with pytest.raises(Bili2nbError):
        auth.login_with_fallback(None, interactive=False, confirm=lambda m: pytest.fail("must not ask"))


def test_no_fallback_without_a_system_browser_or_for_explicit_choice(calls, monkeypatch):
    _failing_chromium(monkeypatch, calls, system=())
    with pytest.raises(Bili2nbError):
        auth.login_with_fallback(None, interactive=True, confirm=lambda m: pytest.fail("nothing to offer"))
    _failing_chromium(monkeypatch, calls, system=("chrome",))
    with pytest.raises(Bili2nbError):
        auth.login_with_fallback("msedge", interactive=True, confirm=lambda m: pytest.fail("explicit choice"))


def test_download_failure_offers_system_browser_once(calls, monkeypatch):
    """If chromium could not be downloaded, offering chrome is right (and still bounded to one retry)."""
    monkeypatch.setattr(auth, "chromium_installed", lambda: False)
    monkeypatch.setattr(auth, "system_browsers", lambda: ["msedge"])
    calls["rcs"] = [1]  # install fails
    asked = []
    auth.login_with_fallback(None, interactive=True, confirm=lambda m: asked.append(m) or True)
    assert [c[c.index("--browser") + 1] for c in _login_cmds(calls)] == ["msedge"] and len(asked) == 1


# ----------------------------------------------------------------- doctor
def test_doctor_reports_bundled_chromium_state(monkeypatch):
    monkeypatch.setattr(auth, "system_browsers", list)
    monkeypatch.setattr(auth, "chromium_installed", lambda: True)
    ok = next(c for c in doctor.run_checks()["checks"] if c["name"] == "login-browser")
    assert ok["status"] == "ok" and "Chromium" in ok["detail"]
    monkeypatch.setattr(auth, "chromium_installed", lambda: False)
    warn = next(c for c in doctor.run_checks()["checks"] if c["name"] == "login-browser")
    assert warn["status"] == "warn" and "170" in warn["detail"] + warn["hint"]
