"""`bili2nb doctor`: read-only environment check with a fix hint for every problem.

Nothing here changes anything or touches the network unless `online=True` (which makes one
real, read-only NotebookLM call).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from bili2nb import config
from bili2nb.media import audio, pdf
from bili2nb.notebook import auth, sdk_compat

MIN_PYTHON = (3, 11)
EXPECTED_NOTEBOOKLM_PY = "0.8.6"
MIN_FREE_GB = 5.0
REQUIRED_FAIL_EXIT = {"ffmpeg": 13, "ffprobe": 13}  # TOOL_MISSING; anything else fails with 1


def _check(name: str, status: str, detail: str = "", hint: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _installed_dists() -> set[str]:
    return {(d.metadata["Name"] or "").lower() for d in metadata.distributions()}


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
            pass
        return True
    except OSError:
        return False


def _storage_path() -> Path:
    from notebooklm import paths

    return paths.get_storage_path(os.environ.get("NOTEBOOKLM_PROFILE") or None)


def _tool_version(exe: str) -> str:
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, check=False,
        ).stdout
        return (out.splitlines() or [""])[0][:80]
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffmpeg_checks() -> list[dict]:
    out = []
    for exe in ("ffmpeg", "ffprobe"):
        path = shutil.which(exe)
        if path:
            out.append(_check(exe, "ok", _tool_version(path) or path))
        else:
            out.append(_check(exe, "fail", "未找到", f"安装 ffmpeg：{audio.ffmpeg_install_hint()}（ffmpeg 已含 ffprobe）"))
    return out


def run_checks(online: bool = False) -> dict:
    checks: list[dict] = []
    vi = tuple(sys.version_info)
    py = ".".join(str(x) for x in vi[:3])
    if vi[:2] < MIN_PYTHON:
        checks.append(_check("python", "fail", py, "需要 Python ≥ 3.11；用 uv tool install 安装时 uv 会自动准备合适版本"))
    else:
        checks.append(_check("python", "ok", py))

    checks += _ffmpeg_checks()

    try:
        import yt_dlp

        checks.append(_check("yt-dlp", "ok", f"{yt_dlp.version.__version__}（作为 Python 库使用，无需单独的 yt-dlp 命令）"))
    except ImportError:
        checks.append(_check("yt-dlp", "fail", "未安装", "重新安装：uv tool install --force . --overrides overrides.txt"))

    dists = _installed_dists()
    if "opencv-python" in dists and "opencv-python-headless" in dists:
        checks.append(_check(
            "opencv", "warn", "同时装了 opencv-python 和 opencv-python-headless（共用 cv2 目录，可能冲突）",
            "按 README 重装：uv tool install --force . --overrides overrides.txt",
        ))
    else:
        try:
            import cv2

            checks.append(_check("opencv", "ok", f"cv2 {cv2.__version__}"))
        except ImportError:
            checks.append(_check("opencv", "fail", "无法导入 cv2", "重新安装：uv tool install --force . --overrides overrides.txt"))

    try:
        ver = metadata.version("notebooklm-py")
        if ver == EXPECTED_NOTEBOOKLM_PY:
            checks.append(_check("notebooklm-py", "ok", ver))
        else:
            checks.append(_check("notebooklm-py", "warn", ver, f"本项目只验证过 {EXPECTED_NOTEBOOKLM_PY}，其他版本可能不兼容"))
    except metadata.PackageNotFoundError:
        checks.append(_check("notebooklm-py", "fail", "未安装", "重新安装本工具"))

    problems = sdk_compat.check()
    if problems:
        checks.append(_check("sdk-compat", "fail", "；".join(problems),
                             "保持 notebooklm-py==0.8.6（uv tool install --force . --overrides overrides.txt）；"
                             "不要手动升级 SDK。详见 docs/SDK_PRIVATE_API.md"))
    else:
        checks.append(_check("sdk-compat", "ok", f"私有接口检查通过（已验证版本 {sdk_compat.PINNED_SDK}）"))

    if pdf.cjk_font_embedded():
        checks.append(_check("pdf-font", "ok", f"中文字体将嵌入 PDF（{pdf.FONT}）"))
    else:
        checks.append(_check(
            "pdf-font", "warn", "没找到可嵌入的中文字体",
            "PDF 封面中文在 NotebookLM 预览里可能乱码；用环境变量 BILI2NB_PDF_FONT 指定一个 .ttf 字体文件",
        ))

    wd = config.data_dir()
    if not _is_writable(wd):
        checks.append(_check("work-dir", "fail", f"{wd} 不可写", "用 bili2nb setup --work-dir <可写目录> 换一个位置"))
    else:
        free_gb = shutil.disk_usage(wd).free / 1e9
        if free_gb < MIN_FREE_GB:
            checks.append(_check("work-dir", "warn", f"{wd}（剩余 {free_gb:.1f} GB）", "磁盘空间偏少，长视频可能不够；换个目录或清理空间"))
        else:
            checks.append(_check("work-dir", "ok", f"{wd}（剩余 {free_gb:.0f} GB）"))

    storage = _storage_path()
    if storage.exists():
        checks.append(_check("google-login", "ok", "本机已有登录状态（只检查文件是否存在，未联网）"))
    else:
        checks.append(_check("google-login", "warn", "尚未登录 Google", "运行：bili2nb setup（或 bili2nb auth login）。未登录时 convert 仍可用，import 不可用"))

    system = auth.system_browsers()
    extra = f"（另检测到系统浏览器：{'、'.join(system)}，可用 --browser 选用）" if system else ""
    if auth.chromium_installed():
        checks.append(_check("login-browser", "ok", f"Playwright Chromium 已就绪（默认登录浏览器）{extra}"))
    else:
        checks.append(_check(
            "login-browser", "warn",
            f"Chromium 尚未下载（首次登录时自动下载，约 {auth.CHROMIUM_DOWNLOAD_MB} MB）{extra}",
            "运行 bili2nb setup（或 bili2nb auth login）；网络受限时可用 --browser chrome / msedge 改用系统浏览器",
        ))

    if online:
        from bili2nb.errors import Bili2nbError
        from bili2nb.notebook.adapter import NotebookAdapter

        try:
            n = NotebookAdapter().check_auth()
            checks.append(_check("notebooklm-online", "ok", f"已连通 NotebookLM，账号下有 {n} 个笔记本"))
        except Bili2nbError as e:
            checks.append(_check("notebooklm-online", "warn", f"{e.code}: {e.message}", "AUTH_REQUIRED 时请运行 bili2nb auth login；其他错误检查网络"))

    statuses = {c["status"] for c in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"
    return {
        "status": overall,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "checks": checks,
    }


def exit_code(report: dict) -> int:
    codes = [REQUIRED_FAIL_EXIT.get(c["name"], 1) for c in report["checks"] if c["status"] == "fail"]
    return max(codes) if codes else 0
