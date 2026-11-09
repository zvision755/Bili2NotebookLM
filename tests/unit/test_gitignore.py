"""The .gitignore must cover every secret, runtime file, log and media cache we can produce."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MUST_IGNORE = [
    # credentials / sessions (notebooklm-py stores Playwright storage state under ~/.notebooklm)
    "storage_state.json", "my_storage_state.json", "profile/storage_state.json",
    "cookies.txt", "a.cookies", "cookies.sqlite", ".env", ".env.local", "secrets.json",
    "client_secret.json", "token.txt", "api_token", "auth.json", "credentials.json",
    "id.pem", "id.key", ".notebooklm/profiles/default/storage_state.json",
    "browser_profile/Default/Cookies", ".playwright/x", ".netrc",
    # local runtime data: job db (+ sqlite sidecars), work dir, outputs, logs, caches
    "jobs.sqlite", "jobs.sqlite-wal", "jobs.sqlite-shm", "jobs.sqlite-journal", "x.sqlite3", "x.db",
    "work/BV1xx/audio.tmp", "output/BV1xx/audio.m4a", "out/a", "tmp/a", "cache/a", "media/a",
    "run.log", "logs/a.txt", "keyframes.pdf.tmp", "BV1xx.old-ab12cd",
    # media / documents produced by convert
    "audio.m4a", "a.mp3", "a.opus", "a.aac", "a.ogg", "a.flac", "a.wav", "a.m4b",
    "a.mp4", "a.mkv", "a.webm", "a.flv", "a.mov", "a.avi", "a.ts", "a.m4v",
    "keyframes.pdf", "frame_0001.jpg", "frames/frame_0001.png",
    # tooling noise
    ".venv/x", "__pycache__/a.pyc", ".pytest_cache/x", ".ruff_cache/x", ".DS_Store",
    "dist/a", "build/a", "x.egg-info/PKG-INFO",
]

MUST_TRACK = [  # things that must NOT be swallowed by an over-broad pattern
    "README.md", "LICENSE", "SECURITY.md", "pyproject.toml", "uv.lock", "overrides.txt",
    "src/bili2nb/cli.py", "src/bili2nb/jobs/db.py", "tests/unit/test_ux.py",
    ".github/workflows/ci.yml", "docs/TEST_REPORT.md", "scripts/probe_curl_post_retry.py",
]


def _ignored(path: str) -> bool:
    r = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path],
        cwd=ROOT, capture_output=True, check=False,
    )
    return r.returncode == 0


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_sensitive_or_runtime_file_is_ignored(path):
    assert _ignored(path), f"{path} would be committable: extend .gitignore"


@pytest.mark.parametrize("path", MUST_TRACK)
def test_source_files_are_not_ignored(path):
    assert not _ignored(path), f"{path} must stay tracked"
