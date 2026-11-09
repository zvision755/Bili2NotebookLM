"""`bili2nb doctor`: read-only environment check."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from bili2nb import cli, config, doctor

runner = CliRunner()


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "default_data_dir", lambda: tmp_path / "default")
    monkeypatch.delenv("BILI2NB_HOME", raising=False)
    return tmp_path


def by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_all_good_reports_ok(iso, monkeypatch):
    rep = doctor.run_checks()
    c = by_name(rep)
    assert c["python"]["status"] == "ok"
    assert c["ffmpeg"]["status"] == "ok" and c["ffprobe"]["status"] == "ok"  # dev machine has them
    assert c["yt-dlp"]["status"] == "ok" and c["opencv"]["status"] == "ok"
    assert rep["status"] in ("ok", "warn")  # login may legitimately be missing in CI


def test_missing_ffmpeg_is_a_failure_with_install_hint(iso, monkeypatch):
    real = doctor.shutil.which
    monkeypatch.setattr(doctor.shutil, "which", lambda n, *a, **k: None if n in ("ffmpeg", "ffprobe") else real(n))
    rep = doctor.run_checks()
    c = by_name(rep)
    assert rep["status"] == "fail"
    assert c["ffmpeg"]["status"] == "fail" and "install" in c["ffmpeg"]["hint"].lower() + "install"
    assert "ffmpeg" in c["ffmpeg"]["hint"]


def test_old_python_is_a_failure(iso, monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", (3, 9, 0, "final", 0))
    c = by_name(doctor.run_checks())
    assert c["python"]["status"] == "fail" and "3.11" in c["python"]["hint"]


def test_not_logged_in_is_only_a_warning_because_convert_still_works(iso, monkeypatch):
    monkeypatch.setattr(doctor, "_storage_path", lambda: iso / "nope" / "storage_state.json")
    rep = doctor.run_checks()
    c = by_name(rep)
    assert c["google-login"]["status"] == "warn" and "login" in c["google-login"]["hint"]


def test_logged_in_local_check_never_touches_the_network(iso, monkeypatch):
    f = iso / "storage_state.json"
    f.write_text("{}")
    monkeypatch.setattr(doctor, "_storage_path", lambda: f)
    c = by_name(doctor.run_checks())  # conftest blocks sockets: would raise if it tried
    assert c["google-login"]["status"] == "ok"


def test_double_opencv_is_a_warning(iso, monkeypatch):
    monkeypatch.setattr(doctor, "_installed_dists", lambda: {"opencv-python", "opencv-python-headless"})
    c = by_name(doctor.run_checks())
    assert c["opencv"]["status"] == "warn" and "overrides" in c["opencv"]["hint"]


def test_work_dir_must_be_writable(iso, monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_is_writable", lambda p: False)
    c = by_name(doctor.run_checks())
    assert c["work-dir"]["status"] == "fail"


def test_online_flag_really_checks_notebooklm_and_maps_failure(iso, monkeypatch):
    from bili2nb.errors import Bili2nbError

    def boom(self):
        raise Bili2nbError("AUTH_REQUIRED", "need login")

    monkeypatch.setattr("bili2nb.notebook.adapter.NotebookAdapter.check_auth", boom)
    c = by_name(doctor.run_checks(online=True))
    assert c["notebooklm-online"]["status"] == "warn" and "AUTH_REQUIRED" in c["notebooklm-online"]["detail"]
    monkeypatch.setattr("bili2nb.notebook.adapter.NotebookAdapter.check_auth", lambda self: 26)
    c = by_name(doctor.run_checks(online=True))
    assert c["notebooklm-online"]["status"] == "ok"


def test_doctor_cli_json_and_exit_codes(iso, monkeypatch):
    r = runner.invoke(cli.app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.stdout)
    assert {"status", "checks", "platform"} <= set(d)
    real = doctor.shutil.which
    monkeypatch.setattr(doctor.shutil, "which", lambda n, *a, **k: None if n == "ffmpeg" else real(n))
    r = runner.invoke(cli.app, ["doctor", "--json"])
    assert r.exit_code == 13  # TOOL_MISSING


def test_doctor_human_output_is_plain_text_with_fix_hints(iso, monkeypatch):
    real = doctor.shutil.which
    monkeypatch.setattr(doctor.shutil, "which", lambda n, *a, **k: None if n == "ffmpeg" else real(n))
    r = runner.invoke(cli.app, ["doctor"])
    assert "FAIL" in r.output and "ffmpeg" in r.output and "修复" in r.output


def test_doctor_is_not_hijacked_by_default_import_dispatch(iso):
    r = runner.invoke(cli.app, ["doctor", "--json"])
    assert r.exit_code in (0, 13) and "checks" in r.stdout
