"""Login delegation. Credentials stay in notebooklm-py's own profile dir (never in the project).

V1.2: one default on every OS - Playwright's own Chromium (`chromium`). No system Chrome/Edge is
required; `--browser chrome|msedge` remain as advanced options. We own the one-time Chromium
download (visible progress, long timeout, actionable errors) instead of relying on the SDK's silent
300-second auto-install. Playwright keeps the browser in a per-user cache directory; nothing in the
user's own Chrome / Edge / Safari is read or changed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable

from bili2nb.errors import Bili2nbError

DEFAULT_BROWSER = "chromium"
BROWSERS = ("chromium", "chrome", "msedge")
CHROMIUM_DOWNLOAD_MB = 170  # approximate; shown to the user before the one-time download
INSTALL_TIMEOUT_SEC = 1800

_PROBE = (
    "import os, sys\n"
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as p:\n"
    "    path = p.chromium.executable_path\n"
    "    sys.stdout.write('PRESENT' if path and os.path.exists(path) else 'MISSING')\n"
)


def default_browser() -> str:
    """Same on every OS: Playwright-managed Chromium (downloaded once if absent)."""
    return DEFAULT_BROWSER


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    """Single seam for subprocesses (tests replace it; nothing else here spawns processes)."""
    return subprocess.run(cmd, check=False, **kw)


def chromium_installed() -> bool:
    """Is Playwright's bundled Chromium already downloaded? (Probed in a child process.)"""
    try:
        r = _run([sys.executable, "-c", _PROBE], capture_output=True, text=True, encoding="utf-8",
                 errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "PRESENT" in (r.stdout or "")


def system_browsers() -> list[str]:
    """Chrome / Edge found on this computer (advisory only: used for hints and the fallback offer)."""
    cands: list[tuple[str, str]] = []
    if sys.platform == "darwin":
        cands = [("chrome", "/Applications/Google Chrome.app"), ("msedge", "/Applications/Microsoft Edge.app")]
    elif sys.platform.startswith("win"):
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for r in filter(None, roots):
            cands.append(("chrome", os.path.join(r, "Google", "Chrome", "Application", "chrome.exe")))
            cands.append(("msedge", os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe")))
    else:
        for n, exe in (("chrome", "google-chrome"), ("msedge", "microsoft-edge")):
            if shutil.which(exe):
                cands.append((n, exe))
    found: list[str] = []
    for name, path in cands:
        if (os.path.exists(path) or shutil.which(path)) and name not in found:
            found.append(name)
    return found


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _alternatives() -> str:
    return "或改用已安装的系统浏览器：bili2nb auth login --browser chrome（或 --browser msedge）。"


def install_chromium() -> None:
    """One-time Chromium download with visible progress. Re-running resumes/retries safely."""
    _say(
        f"首次登录需要下载浏览器组件 Chromium（约 {CHROMIUM_DOWNLOAD_MB} MB，只下载一次）。\n"
        "它存放在 Playwright 的用户级缓存目录，不会改动你已有的 Chrome / Edge / Safari 及其设置。"
    )
    try:
        rc = _run([sys.executable, "-m", "playwright", "install", "chromium"],
                  timeout=INSTALL_TIMEOUT_SEC).returncode  # inherits the terminal: progress is shown
    except subprocess.TimeoutExpired:
        rc = -1
    if rc != 0:
        raise Bili2nbError(
            "AUTH_REQUIRED",
            "Chromium 下载失败（多半是网络问题）。直接重新运行 `bili2nb auth login` 即可重试；"
            "若所在网络需要代理，请先设置环境变量 HTTPS_PROXY；" + _alternatives(),
            detail=f"playwright install chromium exit {rc}",
            extra={"reason": "browser_download_failed"},
        )


def run_login(browser: str | None = None, timeout_sec: int = 300) -> None:
    """Run the SDK's interactive browser login. The USER signs in; we never see the cookies."""
    browser = browser or default_browser()
    if browser not in BROWSERS:
        raise Bili2nbError("INTERNAL", f"unsupported browser: {browser}")
    if browser == "chromium" and not chromium_installed():
        install_chromium()
    cmd = [
        sys.executable, "-m", "notebooklm", "login",
        "--browser", browser, "--browser-timeout", str(timeout_sec),
    ]
    rc = _run(cmd).returncode  # inherits the terminal (interactive)
    if rc != 0:
        raise Bili2nbError(
            "AUTH_REQUIRED",
            f"登录未完成（exit {rc}）：可能是浏览器没能启动，或在 {timeout_sec} 秒内没有完成 Google 登录。"
            "重新运行 `bili2nb auth login` 重试；" + _alternatives(),
            extra={"reason": "login_not_completed"},
        )


def login_with_fallback(
    browser: str | None,
    *,
    interactive: bool,
    confirm: Callable[[str], bool],
    timeout_sec: int = 300,
) -> None:
    """`run_login`, plus ONE interactive offer to retry with an installed Chrome/Edge.

    Offered only when: the user did not pick a browser explicitly, the terminal is interactive
    (never for --json / pipes), and a system browser actually exists. Never switches silently.
    """
    try:
        run_login(browser, timeout_sec=timeout_sec)
        return
    except Bili2nbError as e:
        if browser is not None or not interactive or e.code != "AUTH_REQUIRED":
            raise
        alts = system_browsers()
        if not alts:
            raise
        _say(f"[AUTH_REQUIRED] {e.message}")
        if not confirm(f"改用本机已安装的 {alts[0]} 再试一次？"):
            raise
        run_login(alts[0], timeout_sec=timeout_sec)
