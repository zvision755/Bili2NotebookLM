"""ffprobe/ffmpeg helpers: verify, keep-or-remux-or-transcode, size/duration checks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bili2nb.config import KEEP_AUDIO, MAX_UPLOAD_BYTES
from bili2nb.errors import Bili2nbError


def ffmpeg_install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    if sys.platform.startswith("win"):
        return "winget install ffmpeg（装完请重新打开终端）"
    return "用系统包管理器安装 ffmpeg（如 apt install ffmpeg）"


def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise Bili2nbError(
            "TOOL_MISSING", f"缺少系统工具: {', '.join(missing)}。请运行: {ffmpeg_install_hint()}"
        )


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise Bili2nbError("MEDIA_ERROR", f"{cmd[0]} 超时。") from exc
    except FileNotFoundError as exc:
        raise Bili2nbError("TOOL_MISSING", f"找不到 {cmd[0]}。") from exc


def probe(path: Path) -> dict:
    r = _run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 60
    )
    if r.returncode != 0:
        raise Bili2nbError("MEDIA_ERROR", "ffprobe 无法读取媒体文件。", detail=r.stderr[-200:])
    return json.loads(r.stdout)


def audio_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)


def decode_check(path: Path) -> None:
    """Full decode to null: proves the file is complete and decodable."""
    r = _run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "null", "-"], 900)
    if r.returncode != 0 or r.stderr.strip():
        raise Bili2nbError("MEDIA_ERROR", "音频解码校验失败（文件可能不完整）。", detail=r.stderr[-200:])


def transcode_aac(src: Path, dst: Path, bitrate: str) -> None:
    r = _run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", bitrate,
         "-movflags", "+faststart", str(dst)],
        3600,
    )
    if r.returncode != 0:
        raise Bili2nbError("MEDIA_ERROR", "ffmpeg 转码失败。", detail=r.stderr[-200:])


@dataclass
class AudioResult:
    path: Path
    codec: str
    duration: float
    size: int
    action: str  # kept | transcoded | transcoded_for_size


def prepare_audio(src: Path, out_dir: Path, expected_duration: float) -> AudioResult:
    """Return a verified NotebookLM-compatible audio file in out_dir (named audio.<ext>)."""
    require_tools()
    info = probe(src)
    st = audio_stream(info)
    if st is None:
        raise Bili2nbError("MEDIA_ERROR", "下载的文件没有音频流。")
    codec = st.get("codec_name", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    action = "kept"
    if codec in KEEP_AUDIO and src.stat().st_size <= MAX_UPLOAD_BYTES:
        dst = out_dir / f"audio{KEEP_AUDIO[codec]}"
        shutil.move(str(src), dst)
    else:
        dst = out_dir / "audio.m4a"
        reason = "transcoded"
        bitrate = "192k"
        if codec in KEEP_AUDIO:  # too large: lower bitrate to fit, never truncate
            reason, bitrate = "transcoded_for_size", "96k"
        transcode_aac(src, dst, bitrate)
        action = reason
        src.unlink(missing_ok=True)
    final = probe(dst)
    dur = float(final["format"].get("duration", 0))
    tol = max(2.0, 0.01 * expected_duration)
    if abs(dur - expected_duration) > tol:
        raise Bili2nbError(
            "MEDIA_ERROR",
            f"音频时长 {dur:.1f}s 与视频 {expected_duration:.1f}s 差异超过 {tol:.1f}s（疑似截断）。",
        )
    size = dst.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise Bili2nbError(
            "MEDIA_ERROR",
            f"音频 {size / 1e6:.0f} MB 超过 NotebookLM 单文件 200 MB 上限；V1 不会截断或分割音频。",
        )
    decode_check(dst)
    return AudioResult(dst, codec, dur, size, action)
