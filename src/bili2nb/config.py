"""Paths and limits. Runtime data lives outside the project (never in git)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# NotebookLM per-uploaded-file limit (Google help, checked 2026-11-08): 200 MB, 500k words.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# Audio containers/codecs we upload as-is (m4a/AAC verified live in Gate 1; mp3 is documented).
KEEP_AUDIO = {"aac": ".m4a", "mp3": ".mp3"}
MAX_FRAMES = 150
VIDEO_MAX_HEIGHT = 720


def default_data_dir() -> Path:
    """Per-OS default work directory (jobs db, temp work, default output)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Bili2NotebookLM"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Bili2NotebookLM"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "bili2notebook"


def config_file() -> Path:
    """Fixed location (inside the DEFAULT dir) so a moved work dir is still discoverable.

    With BILI2NB_HOME set (tests, sandboxes) the file lives there instead, so such runs can
    never touch the user's real configuration."""
    env = os.environ.get("BILI2NB_HOME")
    return (Path(env) if env else default_data_dir()) / "config.json"


def _configured_work_dir() -> Path | None:
    try:
        raw = json.loads(config_file().read_text(encoding="utf-8")).get("work_dir")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(raw).expanduser() if isinstance(raw, str) and raw.strip() else None


def is_configured() -> bool:
    """True once the user chose a work dir (setup) or forces one via BILI2NB_HOME."""
    return bool(os.environ.get("BILI2NB_HOME")) or _configured_work_dir() is not None


def save_work_dir(path: Path) -> Path:
    """Persist the chosen work dir (paths only - never credentials)."""
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    cfg = config_file()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"work_dir": str(path)}, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def data_dir() -> Path:
    """Precedence: BILI2NB_HOME env > saved setup > per-OS default."""
    env = os.environ.get("BILI2NB_HOME")
    base = Path(env) if env else (_configured_work_dir() or default_data_dir())
    base.mkdir(parents=True, exist_ok=True)
    return base


def work_root() -> Path:
    p = data_dir() / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_root() -> Path:
    return data_dir() / "output"
