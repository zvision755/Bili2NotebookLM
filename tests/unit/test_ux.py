"""yt-dlp-style UX: `bili2notebook <URL|BV号>`, first-run setup, per-OS default work dir."""

from __future__ import annotations

import json
import sys
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from bili2nb import cli, config
from bili2nb.bilibili.parser import normalize_input
from bili2nb.errors import Bili2nbError
from tests.conftest import plain

runner = CliRunner()


# ---------------------------------------------------------------- input normalisation
@pytest.mark.parametrize("raw,expected", [
    ("BV1VV4y1V7Pz", "https://www.bilibili.com/video/BV1VV4y1V7Pz"),
    ("  bv1VV4y1V7Pz  ", "https://www.bilibili.com/video/BV1VV4y1V7Pz"),
    ("BV1VV4y1V7Pz?p=3", "https://www.bilibili.com/video/BV1VV4y1V7Pz?p=3"),
    ("av170001", "https://www.bilibili.com/video/av170001"),
    ("AV170001", "https://www.bilibili.com/video/av170001"),
    ("https://www.bilibili.com/video/BV1VV4y1V7Pz?p=2", "https://www.bilibili.com/video/BV1VV4y1V7Pz?p=2"),
    ("https://b23.tv/abc123", "https://b23.tv/abc123"),
])
def test_normalize_input(raw, expected):
    assert normalize_input(raw) == expected


@pytest.mark.parametrize("raw", ["", "hello", "BV123", "BV1VV4y1V7Pz/../x", "https://evil.com/BV1VV4y1V7Pz",
                                 "BV1VV4y1V7Pz extra"])
def test_normalize_input_leaves_garbage_for_strict_validation(raw):
    out = normalize_input(raw)
    assert out == raw.strip()  # untouched; parse_url then rejects with INVALID_URL


# ---------------------------------------------------------------- default dir per OS + config
def test_default_dir_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    assert config.default_data_dir() == tmp_path / "Library" / "Application Support" / "Bili2NotebookLM"


def test_default_dir_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    assert config.default_data_dir() == tmp_path / "AppData" / "Roaming" / "Bili2NotebookLM"


def test_default_dir_windows_without_appdata_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    assert config.default_data_dir() == tmp_path / "AppData" / "Roaming" / "Bili2NotebookLM"


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """Isolated default dir + no env override."""
    default = tmp_path / "default"
    monkeypatch.setattr(config, "default_data_dir", lambda: default)
    monkeypatch.delenv("BILI2NB_HOME", raising=False)
    return default


def test_data_dir_precedence_env_over_config_over_default(iso, monkeypatch, tmp_path):
    assert config.data_dir() == iso  # nothing configured -> default
    custom = tmp_path / "mywork"
    config.save_work_dir(custom)
    assert config.data_dir() == custom  # config file wins over default
    assert (iso / "config.json").is_file()  # config lives in the fixed default dir (findable)
    env_dir = tmp_path / "envdir"
    monkeypatch.setenv("BILI2NB_HOME", str(env_dir))
    assert config.data_dir() == env_dir  # env wins over config


def test_is_configured_and_save_creates_dir(iso, tmp_path):
    assert config.is_configured() is False
    target = tmp_path / "a" / "b"
    config.save_work_dir(target)
    assert config.is_configured() is True and target.is_dir()


def test_corrupt_config_falls_back_to_default(iso):
    iso.mkdir(parents=True)
    (iso / "config.json").write_text("{not json")
    assert config.data_dir() == iso and config.is_configured() is False


def test_config_never_stores_credentials(iso, tmp_path):
    config.save_work_dir(tmp_path / "w")
    blob = (iso / "config.json").read_text().lower()
    assert "cookie" not in blob and "token" not in blob
    assert set(json.loads(blob)) == {"work_dir"}


# ---------------------------------------------------------------- command dispatch
class FakeImporter:
    calls: ClassVar[list] = []

    def run(self, url, **kw):
        FakeImporter.calls.append((url, kw))
        return {"status": "completed", "notebook_url": "https://notebooklm.google.com/notebook/x",
                "reused": False, "source_count": 2, "job_id": "j1"}


@pytest.fixture
def fake(monkeypatch, iso):
    FakeImporter.calls = []
    monkeypatch.setattr(cli, "_make_importer", lambda: FakeImporter())
    return FakeImporter


def test_bare_bv_number_means_import(fake):
    r = runner.invoke(cli.app, ["BV1VV4y1V7Pz", "--json"])
    assert r.exit_code == 0, r.output
    assert fake.calls[0][0] == "https://www.bilibili.com/video/BV1VV4y1V7Pz"


def test_bare_url_means_import(fake):
    r = runner.invoke(cli.app, ["https://www.bilibili.com/video/BV1VV4y1V7Pz?p=2", "--json"])
    assert r.exit_code == 0 and fake.calls[0][0].endswith("BV1VV4y1V7Pz?p=2")


def test_options_before_url_work_like_ytdlp(fake):
    r = runner.invoke(cli.app, ["--json", "--keep-files", "BV1VV4y1V7Pz"])
    assert r.exit_code == 0, r.output
    assert fake.calls[0][1]["keep_files"] is True


def test_explicit_import_still_works_and_normalizes_bv(fake):
    r = runner.invoke(cli.app, ["import", "BV1VV4y1V7Pz", "--json"])
    assert r.exit_code == 0 and fake.calls[0][0] == "https://www.bilibili.com/video/BV1VV4y1V7Pz"


def test_known_subcommands_are_not_hijacked(fake, monkeypatch):
    monkeypatch.setattr(cli, "_make_store", lambda: type("S", (), {"list": lambda self, n=50: []})())
    r = runner.invoke(cli.app, ["jobs", "--json"])
    assert r.exit_code == 0 and fake.calls == []


def test_help_still_works():
    r = runner.invoke(cli.app, ["--help"])
    out = plain(r.output)
    assert r.exit_code == 0 and "import" in out and "setup" in out and "doctor" in out


def test_invalid_bare_argument_reports_invalid_url(fake, monkeypatch):
    def boom():
        raise Bili2nbError("INVALID_URL", "bad")
    monkeypatch.setattr(cli, "_make_importer", boom)
    r = runner.invoke(cli.app, ["not-a-video", "--json"])
    assert r.exit_code == 10 and "INVALID_URL" in r.stdout


# ---------------------------------------------------------------- setup command
def test_setup_noninteractive_sets_work_dir_and_skips_login(iso, tmp_path):
    target = tmp_path / "work"
    r = runner.invoke(cli.app, ["setup", "--work-dir", str(target), "--no-login", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["work_dir"] == str(target) and out["login"] == "skipped"
    assert config.data_dir() == target and target.is_dir()


def test_setup_interactive_prompts_for_dir_and_login(iso, tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr("bili2nb.notebook.auth.run_login", lambda browser=None, **k: logged.append(browser))
    target = tmp_path / "w2"
    # answers: work dir, then confirm login
    r = runner.invoke(cli.app, ["setup", "--browser", "chrome"], input=f"{target}\ny\n")
    assert r.exit_code == 0, r.output
    assert config.data_dir() == target and logged == ["chrome"]


def test_setup_interactive_enter_keeps_default_dir(iso, monkeypatch):
    monkeypatch.setattr("bili2nb.notebook.auth.run_login", lambda *a, **k: None)
    r = runner.invoke(cli.app, ["setup"], input="\nn\n")  # Enter = default dir, n = skip login
    assert r.exit_code == 0 and config.data_dir() == iso and config.is_configured() is True


# ---------------------------------------------------------------- first-run guidance
def test_first_run_interactive_launches_setup(iso, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(cli, "_run_setup_wizard", lambda: asked.append(1))
    cli._ensure_setup(interactive=True)
    assert asked == [1]


def test_first_run_noninteractive_never_prompts(iso, monkeypatch):
    monkeypatch.setattr(cli, "_run_setup_wizard", lambda: pytest.fail("must not prompt"))
    cli._ensure_setup(interactive=False)


def test_configured_user_is_never_asked_again(iso, monkeypatch, tmp_path):
    config.save_work_dir(tmp_path / "w")
    monkeypatch.setattr(cli, "_run_setup_wizard", lambda: pytest.fail("must not prompt"))
    cli._ensure_setup(interactive=True)


def test_env_override_counts_as_configured(iso, monkeypatch, tmp_path):
    monkeypatch.setenv("BILI2NB_HOME", str(tmp_path / "e"))
    monkeypatch.setattr(cli, "_run_setup_wizard", lambda: pytest.fail("must not prompt"))
    cli._ensure_setup(interactive=True)


# ---------------------------------------------------------------- platform install hint
@pytest.mark.parametrize("plat,expect", [("darwin", "brew install ffmpeg"), ("win32", "winget install"),
                                         ("linux", "ffmpeg")])
def test_ffmpeg_install_hint_per_platform(monkeypatch, plat, expect):
    from bili2nb.media import audio

    monkeypatch.setattr(sys, "platform", plat)
    assert expect in audio.ffmpeg_install_hint()


def test_setup_under_env_override_never_touches_real_config(iso, monkeypatch, tmp_path):
    """Regression: a sandboxed run (BILI2NB_HOME) once wrote the user's REAL config.json."""
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("BILI2NB_HOME", str(sandbox))
    config.save_work_dir(tmp_path / "elsewhere")
    assert (sandbox / "config.json").is_file()
    assert not (iso / "config.json").exists()  # real default dir untouched


# ---------------------------------------------------------------- command names
def test_installed_command_names_match_project_name():
    import tomllib
    from pathlib import Path

    scripts = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["bili2notebooklm"] == "bili2nb.cli:app"  # matches the project name
    assert scripts["bili2nb"] == "bili2nb.cli:app"  # short alias kept
    assert "bili2notebook" not in scripts  # the old, shorter name is gone


def test_help_advertises_the_new_command_name():
    r = runner.invoke(cli.app, ["--help"])
    assert "bili2notebooklm" in plain(r.output)
