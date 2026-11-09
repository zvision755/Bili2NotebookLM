"""Login delegation. Credentials stay in notebooklm-py's own profile dir (never in the project)."""

from __future__ import annotations

import subprocess
import sys

from bili2nb.errors import Bili2nbError


def default_browser() -> str:
    """Windows ships Edge; elsewhere assume Chrome. (`chromium` needs a Playwright download.)"""
    return "msedge" if sys.platform.startswith("win") else "chrome"


def run_login(browser: str = "chromium", timeout_sec: int = 300) -> None:
    """Run the SDK's interactive browser login. The USER signs in; we never see the cookies."""
    if browser not in {"chromium", "chrome", "msedge"}:
        raise Bili2nbError("INTERNAL", f"unsupported browser: {browser}")
    cmd = [
        sys.executable, "-m", "notebooklm", "login",
        "--browser", browser, "--browser-timeout", str(timeout_sec),
    ]
    rc = subprocess.run(cmd, check=False).returncode  # inherits the terminal (interactive)
    if rc != 0:
        raise Bili2nbError("AUTH_REQUIRED", f"登录未完成 (exit {rc})。")
